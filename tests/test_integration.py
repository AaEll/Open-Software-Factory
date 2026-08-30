"""End-to-end integration: the whole shell driven by the real Fireworks adapter, offline.

These replay recorded API responses (`tests/fixtures/fireworks/*.json`, captured once by
`python -m evals.record_fireworks`) through an httpx mock transport, the same technique the
GitHubForge tests use. That means `FireworksPlanner` and `FireworksRuntime` run for real —
prompt assembly, JSON extraction, tool-call parsing, the sandboxed write — with no key, no
network, and no credits burned on every CI run.

The offline `StaticPlanner`/`StaticSiteRuntime` stay the cheapest smoke path; these cover the code
that actually talks to a model, which those stand-ins never exercise.
"""

import io
import json
import sys
from pathlib import Path

import httpx
import pytest

from osf.engines.fireworks import BASE_URL, DEFAULT_MODEL, FireworksPlanner, FireworksRuntime
from osf.prompts import Style
from osf.shell import Session, Shell

FIXTURES = Path(__file__).parent / "fixtures" / "fireworks"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class Replay:
    """Serves recorded chat-completion bodies in order, recording what was asked."""

    def __init__(self, *names: str) -> None:
        self.bodies = [load(name) for name in names]
        self.requests: list[dict] = []

    def client(self):
        pytest.importorskip("openai")
        from openai import OpenAI

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            if not self.bodies:
                raise AssertionError("the engine made more calls than there are fixtures")
            return httpx.Response(200, json=self.bodies.pop(0))

        return OpenAI(
            base_url=BASE_URL,
            api_key="test-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    @property
    def systems(self) -> list[str]:
        return [request["messages"][0]["content"] for request in self.requests]


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch):
    monkeypatch.setattr("osf.prompts.STYLE", Style(enabled=False))
    monkeypatch.setattr("osf.shell.STYLE", Style(enabled=False))
    monkeypatch.setenv("OSF_OWNER", "me")


@pytest.fixture
def run_shell(monkeypatch, capsys):
    """Drive the shell with the real Fireworks adapters, wired to the replayed client."""

    def run(script: str, replay: Replay) -> str:
        client = replay.client()
        monkeypatch.setattr(
            Session, "planner", lambda _self: FireworksPlanner(DEFAULT_MODEL, client=client)
        )
        monkeypatch.setattr(
            Session, "runtime", lambda _self: FireworksRuntime(DEFAULT_MODEL, client=client)
        )
        monkeypatch.setattr(sys, "stdin", io.StringIO(script))
        # forge="memory": these must never edit the repository the tests run in.
        Shell(Session(model=DEFAULT_MODEL, forge="memory")).run()
        return capsys.readouterr().out

    return run


def test_interview_plan_and_merge(run_shell):
    """clarify → plan → accept → worker writes index.html → PR merges."""
    replay = Replay("clarify", "plan", "worker_index_write", "worker_index_done")
    out = run_shell(
        "/repo me/pobrecita\n"
        "Create a landing page for my dog Pobrecita\n"
        "a fun profile\n"
        "photos and a bio\n"
        "playful\n"
        "\n"
        "/quit\n",
        replay,
    )

    # the driver's own questions, from the recorded reply
    assert "a few questions before I plan" in out
    assert "What is the purpose" in out
    # the plan it proposed, parsed out of a ```json fenced reply
    assert "Build a single-file playful landing page" in out
    assert "→ index.html" in out
    # and the work actually ran to a merge
    assert "me-pobrecita: done" in out
    assert "merged (PR#1, rounds=1)" in out


def test_the_answers_are_sent_to_the_planner(run_shell):
    replay = Replay("clarify", "plan", "worker_index_write", "worker_index_done")
    run_shell(
        "/repo me/pobrecita\nCreate a landing page for my dog Pobrecita\n"
        "a fun profile\nphotos and a bio\nplayful\n\n/quit\n",
        replay,
    )
    plan_request = replay.requests[1]["messages"][-1]["content"]
    assert "a fun profile" in plan_request
    assert "photos and a bio" in plan_request
    assert "What is the purpose" in plan_request  # the question, so the answer has context


def test_feedback_produces_a_two_step_plan_and_two_merges(run_shell):
    """The revised plan has two steps, each gated on its own file and built by its own worker."""
    replay = Replay(
        "clarify",
        "plan",
        "plan_revised",
        "worker_index_write",
        "worker_index_done",
        "worker_gallery_write",
        "worker_gallery_done",
    )
    out = run_shell(
        "/repo me/pobrecita\nCreate a landing page for my dog Pobrecita\n"
        "a fun profile\nphotos and a bio\nplayful\n"
        "also add a photo gallery page\n"
        "\n/quit\n",
        replay,
    )
    assert out.count("planning…") == 2
    assert "plus a dedicated photo gallery page" in out
    assert "→ index.html" in out and "→ gallery.html" in out
    assert "me-pobrecita-1: merged" in out
    assert "me-pobrecita-2: merged" in out
    assert "me-pobrecita: done" in out


def test_each_call_uses_the_right_system_prompt(run_shell):
    from osf.engines._tools import WORKER_SYSTEM
    from osf.planner import CLARIFY_SYSTEM, PLAN_SYSTEM

    replay = Replay("clarify", "plan", "worker_index_write", "worker_index_done")
    run_shell(
        "/repo me/pobrecita\nCreate a landing page for my dog Pobrecita\n\n\n\n\n/quit\n",
        replay,
    )
    assert replay.systems == [CLARIFY_SYSTEM, PLAN_SYSTEM, WORKER_SYSTEM, WORKER_SYSTEM]


def test_the_worker_writes_the_recorded_file_into_its_workspace(run_shell, monkeypatch):
    """The tool call in the fixture really lands on disk, sandboxed to the workspace."""
    workspaces = []
    from osf.local.isolation import TempdirIsolation

    original = TempdirIsolation.prepare

    async def spy(self, repo, branch):
        workspace = await original(self, repo, branch)
        workspaces.append(Path(workspace.path))
        return workspace

    monkeypatch.setattr(TempdirIsolation, "prepare", spy)

    replay = Replay("clarify", "plan", "worker_index_write", "worker_index_done")
    run_shell(
        "/repo me/pobrecita\nCreate a landing page for my dog Pobrecita\n\n\n\n\n/quit\n",
        replay,
    )
    (workspace,) = workspaces
    written = (workspace / "index.html").read_text(encoding="utf-8")
    assert "Pobrecita" in written
    assert written.lstrip().startswith("<!DOCTYPE html>")
