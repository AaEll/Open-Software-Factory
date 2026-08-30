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


def worker(name: str) -> list[str]:
    """Every recorded round trip of one worker turn, in order.

    A turn is however many steps the model took — the recordings show it writing a file twice
    before stopping — so tests replay the whole loop rather than assuming its length.
    """
    steps = sorted(FIXTURES.glob(f"worker_{name}_*.json"), key=lambda p: int(p.stem.split("_")[-1]))
    return [path.stem for path in steps]


class Replay:
    """Serves recorded chat-completion bodies in order, recording what was asked."""

    def __init__(self, *names: str) -> None:
        self.bodies = [load(name) for name in names]
        self.requests: list[dict] = []
        self.exhausted = False

    def client(self):
        pytest.importorskip("openai")
        from openai import OpenAI

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            if not self.bodies:
                # openai re-raises whatever the transport throws as APIConnectionError, which
                # hides the real cause; the flag is checked after the run instead.
                self.exhausted = True
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
        out = capsys.readouterr().out
        assert not replay.exhausted, f"ran out of recorded responses; got:\n{out}"
        return out

    return run


def test_interview_plan_and_merge(run_shell):
    """clarify → plan → accept → worker writes index.html → PR merges."""
    replay = Replay("route_plan", "clarify", "plan", *worker("index"))
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
    replay = Replay("route_plan", "clarify", "plan", *worker("index"))
    run_shell(
        "/repo me/pobrecita\nCreate a landing page for my dog Pobrecita\n"
        "a fun profile\nphotos and a bio\nplayful\n\n/quit\n",
        replay,
    )
    plan_request = replay.requests[2]["messages"][-1]["content"]
    assert "a fun profile" in plan_request
    assert "photos and a bio" in plan_request
    assert "What is the purpose" in plan_request  # the question, so the answer has context


def test_feedback_produces_a_two_step_plan_and_two_merges(run_shell):
    """The revised plan has two steps, each gated on its own file and built by its own worker."""
    replay = Replay(
        "route_plan", "clarify", "plan", "plan_revised", *worker("index"), *worker("gallery")
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
    from osf.planner import CLARIFY_SYSTEM, PLAN_SYSTEM, ROUTE_SYSTEM

    replay = Replay("route_plan", "clarify", "plan", *worker("index"))
    run_shell(
        "/repo me/pobrecita\nCreate a landing page for my dog Pobrecita\n\n\n\n\n/quit\n",
        replay,
    )
    assert replay.systems[0].startswith(ROUTE_SYSTEM)  # the catalog is appended to it
    assert replay.systems[1:3] == [CLARIFY_SYSTEM, PLAN_SYSTEM]
    assert all(system.startswith(WORKER_SYSTEM) for system in replay.systems[3:])


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

    replay = Replay("route_plan", "clarify", "plan", *worker("index"))
    run_shell(
        "/repo me/pobrecita\nCreate a landing page for my dog Pobrecita\n\n\n\n\n/quit\n",
        replay,
    )
    (workspace,) = workspaces
    written = (workspace / "index.html").read_text(encoding="utf-8")
    assert "Pobrecita" in written
    assert written.lstrip().startswith("<!DOCTYPE html>")


# --- the driver deciding what a message is ------------------------------------------------------


def test_a_greeting_is_answered_not_planned(run_shell):
    """"hi bot" used to become a plan whose single step was "hi bot"."""
    replay = Replay("route_reply")
    out = run_shell("/repo me/site\nhi bot\n/quit\n", replay)

    assert "autonomous software factory" in out  # the driver's own words, from the fixture
    assert "planning…" not in out
    assert "plan " not in out
    assert len(replay.requests) == 1  # it stopped after routing: no clarify, no plan, no worker


def test_a_request_matching_a_workflow_starts_it_prefilled(run_shell):
    """The driver may reach for a structured flow, carrying over what it already understood."""
    replay = Replay("route_run")
    out = run_shell(
        "/repo me/site\n"
        "make me a new repository called widgets, blank, no CI\n"
        "\n\n\n\n"  # every question already has the right answer as its default
        "n\n/quit\n",
        replay,
    )
    assert "this looks like create-repo" in out
    assert "Repository name (widgets)" in out  # prefilled from the sentence, as the default
    assert "README.md exists, .gitignore exists" in out  # the blank template it inferred
    assert "not run" in out  # declined at the confirm, so nothing was built
