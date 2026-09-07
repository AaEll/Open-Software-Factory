"""Whole flows, driven by a scripted series of model responses.

Each test names the exact sequence the model gives back — routing decision, questions, plan, tool
calls — and asserts what the user ends up with. That covers the paths recorded fixtures cannot:
a model that answers badly and is corrected, a worker that has to recover from a refused tool call,
a check that fails and then passes. Real adapters run throughout; only the HTTP responses are ours.
"""

import io
import subprocess
import sys
from pathlib import Path

import pytest
from canned import CannedAgent, agent

from osf.engines.fireworks import DEFAULT_MODEL, FireworksPlanner, FireworksRuntime
from osf.prompts import Style
from osf.shell import Session, Shell


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch):
    monkeypatch.setattr("osf.prompts.STYLE", Style(enabled=False))
    monkeypatch.setattr("osf.shell.STYLE", Style(enabled=False))
    monkeypatch.setenv("OSF_OWNER", "me")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


@pytest.fixture
def run(monkeypatch, capsys):
    """Drive the shell against a scripted model, in a real project."""

    def go(script: CannedAgent, typed: str, *, project: Path | None = None, **session_kwargs):
        client = script.client()
        monkeypatch.setattr(
            Session, "planner", lambda _self: FireworksPlanner(DEFAULT_MODEL, client=client)
        )
        monkeypatch.setattr(
            Session, "runtime", lambda _self: FireworksRuntime(DEFAULT_MODEL, client=client)
        )
        monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
        session = Session(model=DEFAULT_MODEL, project=project, **session_kwargs)
        Shell(session).run()
        out = capsys.readouterr().out
        assert not script.exhausted, f"the script ran out of responses; got:\n{out}"
        return out

    return go


# --- the ordinary path ---------------------------------------------------------------------------


def test_a_request_becomes_a_plan_and_an_edit(run, repo: Path):
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks("What should the page say?"),
        agent.plans("A landing page", [("Write index.html", ["index.html"])]),
        agent.writes("index.html", "<h1>Hello</h1>\n"),
        agent.finishes(),
    )
    out = run(script, "build a landing page\nhello\n\n1\n/quit\n", project=repo)

    assert "What should the page say?" in out
    assert "plan  A landing page" in out
    assert "→ index.html" in out
    assert (repo / "index.html").read_text(encoding="utf-8") == "<h1>Hello</h1>\n"
    assert "kept" in out


def test_a_greeting_never_reaches_the_planner(run, repo: Path):
    script = CannedAgent(agent.replies("Hi! What would you like to build?"))
    out = run(script, "hi there\n/quit\n", project=repo)

    assert "What would you like to build?" in out
    assert "planning…" not in out
    assert not list(repo.glob("*.html"))  # nothing was built


def test_feedback_replans_before_anything_is_written(run, repo: Path):
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans("One page", [("Write index.html", ["index.html"])]),
        agent.plans("Two pages", [("Write index.html", ["index.html"])]),  # after the feedback
        agent.writes("index.html", "<h1>Two</h1>\n"),
        agent.finishes(),
    )
    out = run(script, "build a site\nalso add a gallery\n\n1\n/quit\n", project=repo)

    assert out.count("planning…") == 2
    assert "plan  Two pages" in out


def test_declining_the_plan_writes_nothing(run, repo: Path):
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans("A landing page", [("Write index.html", ["index.html"])]),
    )
    out = run(script, "build a site\nno\n/quit\n", project=repo)

    assert "not run" in out
    assert not (repo / "index.html").exists()


# --- the paths that are hard to record ----------------------------------------------------------


def test_a_plan_that_is_not_json_is_retried(run, repo: Path):
    """Models drift out of JSON; the planner nudges once rather than dropping the request."""
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.rambles("Sure! Here's what I'd do…"),
        agent.plans("A landing page", [("Write index.html", ["index.html"])]),
        agent.writes("index.html", "<h1>Hello</h1>\n"),
        agent.finishes(),
    )
    out = run(script, "build a site\n\n1\n/quit\n", project=repo)

    assert "plan  A landing page" in out
    assert "planner unavailable" not in out  # recovered, not fell back


def test_a_worker_recovers_from_a_refused_overwrite(run, repo: Path):
    """Blind overwrite is refused, and the refusal must be enough to get the model back on track."""
    (repo / "index.html").write_text("<p>keep me</p>\n", encoding="utf-8")
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans("Add a footer", [("Edit index.html", ["index.html"])]),
        agent.writes("index.html", "<p>clobbered</p>\n"),  # refused: not read yet
        agent.reads("index.html"),
        agent.edits("index.html", "<p>keep me</p>", "<p>keep me</p>\n<footer>©</footer>"),
        agent.finishes(),
    )
    out = run(script, "add a footer\n\n1\n/quit\n", project=repo)

    result = (repo / "index.html").read_text(encoding="utf-8")
    assert "keep me" in result  # the refusal saved the content
    assert "<footer>" in result
    assert "clobbered" not in result
    assert "kept" in out


def test_a_failing_check_sends_the_worker_back(run, repo: Path):
    """The gate that catches work which produced a file but not a working one."""
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans(
            "A greeting module",
            [("Write greet.py", ["greet.py"], "python -c \"import greet; greet.hello()\"")],
        ),
        agent.writes("greet.py", "def helo():\n    print('hi')\n"),  # typo: check will fail
        agent.finishes(),
        # Round two is a fresh session, so the file it wrote last round is one it has not read —
        # read-before-overwrite applies, and the worker has to read before rewriting.
        agent.reads("greet.py"),
        agent.writes("greet.py", "def hello():\n    print('hi')\n"),
        agent.finishes(),
    )
    out = run(script, "write a greeting module\n\n1\n/quit\n", project=repo)

    assert "not yet:" in out  # the failure was reported as it happened
    assert "rounds=2" in out
    assert "def hello()" in (repo / "greet.py").read_text(encoding="utf-8")


def test_an_unsatisfiable_check_escalates_rather_than_looping(run, repo: Path):
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans("A module", [("Write x.py", ["x.py"], "python -c \"import x; x.missing()\"")]),
        agent.writes("x.py", "# nothing here\n"),
        agent.finishes(),
    )
    out = run(script, "write it\n\n1\n/quit\n", project=repo, max_rounds=1)

    assert "escalated" in out


def test_the_worker_is_offered_every_tool(run, repo: Path):
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans("A page", [("Write index.html", ["index.html"])]),
        agent.writes("index.html", "<h1>Hi</h1>\n"),
        agent.finishes(),
    )
    run(script, "build a site\n\n1\n/quit\n", project=repo)

    assert set(script.tools_offered) == {
        "write_file",
        "read_file",
        "edit_file",
        "find_files",
        "search_files",
    }


def test_reverting_puts_the_project_back(run, repo: Path):
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans("A page", [("Write index.html", ["index.html"])]),
        agent.writes("index.html", "<h1>Hi</h1>\n"),
        agent.finishes(),
    )
    out = run(script, "build a site\n\n3\n/quit\n", project=repo)  # 3 = revert everything

    assert "reverted 1 file(s)" in out
    assert not (repo / "index.html").exists()
    assert subprocess.run(
        ["git", "status", "--short"], cwd=repo, capture_output=True, text=True
    ).stdout.strip() == ""


def test_a_retry_must_read_before_it_rewrites(run, repo: Path):
    """Each round is a new session, so last round's own output is still "unread" to this one.

    Defensible — the policy cannot tell our previous round from someone else's edit — but it costs
    a round trip on every retry, which is worth knowing before blaming the model for dithering.
    """
    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks(),
        agent.plans("A module", [("Write x.py", ["x.py"], "python -c \"import x; x.go()\"")]),
        agent.writes("x.py", "# wrong\n"),
        agent.finishes(),
        agent.writes("x.py", "def go(): pass\n"),  # refused: this session has not read it
        agent.reads("x.py"),
        agent.writes("x.py", "def go(): pass\n"),
        agent.finishes(),
    )
    out = run(script, "write it\n\n1\n/quit\n", project=repo)

    assert "rounds=2" in out
    assert "def go()" in (repo / "x.py").read_text(encoding="utf-8")
