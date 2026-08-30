"""The `sf` shell: command dispatch, the structured run wizard, and free-text objectives.

Every test drives the real loop by piping a script into stdin, which is exactly how a user drives
it by hand — so these cover the prompts as well as the commands. All offline: no keys, no network.
"""

import io
import sys

import pytest

from osf.config import (
    LOCAL_OWNER,
    default_owner,
    detected_owner,
    parse_repo,
    valid_repo_name,
)
from osf.planner import ProposedPlan, Step
from osf.prompts import Cancelled, Choice, Style, confirm, select, text
from osf.runs import get_run
from osf.shell import Session, Shell
from osf.types import ModelRef, RepoRef


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch):
    """Strip ANSI so assertions match on text, and pin the owner default to this machine."""
    monkeypatch.setattr("osf.prompts.STYLE", Style(enabled=False))
    monkeypatch.setattr("osf.shell.STYLE", Style(enabled=False))
    monkeypatch.setenv("OSF_OWNER", "me")


def run_shell(script: str, capsys, session: Session | None = None) -> str:
    """Feed `script` to a fresh shell and return everything it printed."""
    sys.stdin = io.StringIO(script)
    try:
        assert Shell(session).run() == 0
    finally:
        sys.stdin = sys.__stdin__
    return capsys.readouterr().out


# --- commands ---------------------------------------------------------------------------------


def test_help_lists_every_command(capsys):
    out = run_shell("/help\n/quit\n", capsys)
    for command in ("/new-repo", "/runs", "/repo", "/model", "/forge", "/rounds", "/smoke"):
        assert command in out


def test_status_shows_defaults(capsys):
    out = run_shell("/status\n/quit\n", capsys)
    assert "repo:   unset" in out
    assert "offline (scripted worker)" in out
    assert "forge:  memory" in out


def test_repo_model_forge_rounds_are_remembered(capsys):
    session = Session()
    out = run_shell(
        "/repo me/site\n/model fireworks/kimi\n/forge github\n/rounds 5\n/quit\n",
        capsys,
        session,
    )
    assert session.repo == RepoRef("me", "site")
    assert session.model == ModelRef("fireworks", "kimi")
    assert session.forge == "github"
    assert session.max_rounds == 5
    assert "fireworks/kimi" in out


def test_model_off_returns_to_the_offline_worker(capsys):
    session = Session(model=ModelRef("fireworks", "kimi"))
    run_shell("/model off\n/quit\n", capsys, session)
    assert session.model is None


def test_bad_settings_are_rejected_without_changing_state(capsys):
    session = Session()
    out = run_shell("/forge nope\n/rounds 0\n/quit\n", capsys, session)
    assert session.forge == "memory"
    assert session.max_rounds == 3
    assert "forge must be" in out
    assert "rounds must be" in out


def test_unknown_command(capsys):
    assert "unknown command /bogus" in run_shell("/bogus\n/quit\n", capsys)


def test_aliases_quit(capsys):
    assert run_shell("/exit\n", capsys) is not None


def test_eof_leaves_the_shell(capsys):
    assert "Open Software Factory" in run_shell("", capsys)


def test_runs_lists_the_builtin(capsys):
    assert "create-repo" in run_shell("/runs\n/quit\n", capsys)


def test_unknown_run_is_reported(capsys):
    assert "unknown run" in run_shell("/run nope\n/quit\n", capsys)


def test_smoke_command(capsys):
    assert "smoke: ok" in run_shell("/smoke\n/quit\n", capsys)


def test_a_failing_command_does_not_kill_the_shell(capsys):
    # An unusable name raises inside the handler; the loop explains it and keeps going.
    out = run_shell("/repo not a repo!\n/status\n/quit\n", capsys)
    assert "isn't a valid repository name" in out
    assert "repo:   unset" in out


def test_repo_command_accepts_a_bare_name(capsys):
    session = Session()
    run_shell("/repo site\n/quit\n", capsys, session)
    assert session.repo == RepoRef("me", "site")  # owner falls back to your account


# --- free-text objectives ---------------------------------------------------------------------


def test_objective_merges_offline(capsys):
    out = run_shell("/repo me/site\nCreate a landing page for demo.osf\n\n/quit\n", capsys)
    assert "me-site: done" in out
    assert "merged" in out


def test_objective_escalates_when_a_step_gate_is_unmet(capsys, monkeypatch):
    # The scripted worker only ever writes index.html, so this gate can never be satisfied.
    _plan(monkeypatch, ProposedPlan("Build the app", [Step("Build it", ["app/main.py"])]))
    out = run_shell("/repo me/site\n/rounds 1\nBuild the app\n\n/quit\n", capsys)
    assert "escalated" in out
    assert "failed" in out


def test_objective_asks_only_for_a_name_and_detects_the_owner(capsys):
    session = Session()
    out = run_shell("Landing page for demo.osf\nsite\n\n/quit\n", capsys, session)
    assert "Repository name" in out
    assert "Owner" not in out  # never asked — OSF_OWNER/gh/local decides it
    assert session.repo == RepoRef("me", "site")
    assert "done" in out


def test_objective_accepts_a_full_owner_name_at_the_name_question(capsys):
    session = Session()
    run_shell("Landing page for demo.osf\nyou/site\n\n/quit\n", capsys, session)
    assert session.repo == RepoRef("you", "site")


def test_a_rejected_answer_is_re_asked_without_losing_the_objective(capsys):
    # The reported bug: a bad repo answer used to abort the turn and drop the objective.
    session = Session()
    out = run_shell(
        "Make a website for my dog\nmy site!\npobrecita\n\n/quit\n", capsys, session
    )
    assert "isn't a valid repository name" in out
    assert session.repo == RepoRef("me", "pobrecita")
    assert "done" in out  # the objective survived and still ran


# --- plan negotiation ---------------------------------------------------------------------------


def _plan(monkeypatch, *plans, questions=()):
    """Pin the session's planner to scripted questions and a sequence of proposals."""
    remaining = list(plans)
    last = plans[-1]

    class _Scripted:
        def clarify(self, request):
            return list(questions)

        def propose(self, request, exchanges=(), answers=()):
            self.answers = list(answers)
            _Scripted.seen = self.answers
            return remaining.pop(0) if remaining else last

    monkeypatch.setattr(Session, "planner", lambda _self: _Scripted())
    return _Scripted


def test_the_plan_is_shown_before_anything_runs(capsys, monkeypatch):
    _plan(monkeypatch, ProposedPlan("Build a dog site", [Step("Write index.html", ["index.html"])]))
    out = run_shell("/repo me/site\nsite for my dog\n\n/quit\n", capsys)
    assert "planning…" in out
    assert "plan  Build a dog site" in out
    assert "1. Write index.html" in out
    assert "→ index.html" in out
    assert "me-site: done" in out


def test_feedback_revises_the_plan_before_running(capsys, monkeypatch):
    first = ProposedPlan("A landing page", [Step("Write index.html", ["index.html"])])
    second = ProposedPlan(
        "A landing page and a gallery", [Step("Write index.html", ["index.html"])]
    )
    _plan(monkeypatch, first, second)
    out = run_shell("/repo me/site\nsite for my dog\nadd a gallery\n\n/quit\n", capsys)
    assert out.count("planning…") == 2
    assert "A landing page and a gallery" in out
    assert "me-site: done" in out


def test_declining_the_plan_runs_nothing(capsys, monkeypatch):
    _plan(monkeypatch, ProposedPlan("A landing page", [Step("Write index.html")]))
    out = run_shell("/repo me/site\nsite for my dog\nno\n/quit\n", capsys)
    assert "not run" in out
    assert "done" not in out


def test_a_planner_failure_falls_back_to_the_request(capsys, monkeypatch):
    class _Broken:
        def clarify(self, request):
            return []

        def propose(self, request, exchanges=(), answers=()):
            raise RuntimeError("no API key")

    monkeypatch.setattr(Session, "planner", lambda _self: _Broken())
    out = run_shell("/repo me/site\nCreate a landing page for demo.osf\n\n/quit\n", capsys)
    assert "planner unavailable" in out
    assert "falling back to the request as written" in out
    assert "me-site: done" in out  # still ran, ungated


def test_the_driver_asks_its_own_questions_first(capsys, monkeypatch):
    scripted = _plan(
        monkeypatch,
        ProposedPlan("A playful dog site", [Step("Write index.html", ["index.html"])]),
        questions=["What vibe?", "Photos or placeholders?"],
    )
    out = run_shell("/repo me/site\nsite for my dog\nplayful\nplaceholders\n\n/quit\n", capsys)
    assert "a few questions before I plan" in out
    assert "What vibe?" in out
    # the answers reach the planner, which is the whole point of asking
    assert scripted.seen == [("What vibe?", "playful"), ("Photos or placeholders?", "placeholders")]
    assert "me-site: done" in out


def test_skipped_questions_are_not_passed_on(capsys, monkeypatch):
    scripted = _plan(
        monkeypatch,
        ProposedPlan("A dog site", [Step("Write index.html", ["index.html"])]),
        questions=["What vibe?", "Photos or placeholders?"],
    )
    run_shell("/repo me/site\nsite for my dog\n\nplaceholders\n\n/quit\n", capsys)
    assert scripted.seen == [("Photos or placeholders?", "placeholders")]


def test_ask_off_skips_the_interview(capsys, monkeypatch):
    _plan(
        monkeypatch,
        ProposedPlan("A dog site", [Step("Write index.html", ["index.html"])]),
        questions=["What vibe?"],
    )
    out = run_shell("/repo me/site\n/ask off\nsite for my dog\n\n/quit\n", capsys)
    assert "clarifying questions: off" in out
    assert "What vibe?" not in out
    assert "me-site: done" in out


def test_a_plan_with_no_gate_says_so(capsys, monkeypatch):
    _plan(monkeypatch, ProposedPlan("Whatever you like", [Step("Do it")]))
    out = run_shell("/repo me/site\nsomething vague\n\n/quit\n", capsys)
    assert "no file gate" in out


# --- the structured run wizard ------------------------------------------------------------------


def test_new_repo_walks_the_declared_params(capsys):
    # name, description, template (2 = blank), language (default), owner, then decline to run.
    out = run_shell("/new-repo\nwidgets\nA widget library\n2\n\nme\nn\n/quit\n", capsys)
    assert "Repository name" in out
    assert "Starting point" in out
    assert "Template with CI/CD" in out and "Blank repository" in out
    assert "README.md exists, .gitignore exists" in out  # the blank template's gates
    assert "Run it on me/widgets" in out
    assert "not run" in out


def test_new_repo_ci_template_gates_on_workflows(capsys):
    out = run_shell("/new-repo\nwidgets\n\n1\n\nme\nn\n/quit\n", capsys)
    assert ".github/workflows/ci.yml exists" in out


def test_run_executes_the_plan_when_confirmed(capsys):
    # Offline the scripted worker cannot scaffold a repo, so this escalates — but it proves the
    # wizard's answers reach the driver and the run is actually executed.
    out = run_shell("/rounds 1\n/run create-repo\nwidgets\n\n2\n\nme\ny\n/quit\n", capsys)
    assert "create-repo-widgets: escalated" in out


def test_cancelling_a_wizard_returns_to_the_prompt(capsys):
    # stdin ends mid-wizard: the question raises Cancelled, the shell reports it and carries on.
    out = run_shell("/new-repo\n", capsys)
    assert "cancelled" in out


# --- prompt widgets -----------------------------------------------------------------------------


def _answers(monkeypatch, *lines):
    replies = iter(lines)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(replies))


OPTIONS = (Choice("ci-cd", "Template with CI/CD"), Choice("blank", "Blank repository"))


def test_select_accepts_a_number(monkeypatch, capsys):
    _answers(monkeypatch, "2")
    assert select("Starting point", OPTIONS) == "blank"


def test_select_accepts_the_value(monkeypatch, capsys):
    _answers(monkeypatch, "blank")
    assert select("Starting point", OPTIONS) == "blank"


def test_select_empty_takes_the_default(monkeypatch, capsys):
    _answers(monkeypatch, "")
    assert select("Starting point", OPTIONS, default="blank") == "blank"


def test_select_reasks_on_a_bad_answer(monkeypatch, capsys):
    _answers(monkeypatch, "9", "1")
    assert select("Starting point", OPTIONS) == "ci-cd"
    assert "pick 1-2" in capsys.readouterr().out


def test_text_default_and_required(monkeypatch, capsys):
    _answers(monkeypatch, "")
    assert text("Language", default="python") == "python"
    _answers(monkeypatch, "", "widgets")
    assert text("Name") == "widgets"
    assert "value is required" in capsys.readouterr().out


def test_text_optional_may_be_blank(monkeypatch, capsys):
    _answers(monkeypatch, "")
    assert text("Description", required=False) == ""


def test_confirm(monkeypatch, capsys):
    _answers(monkeypatch, "y")
    assert confirm("Run it?") is True
    _answers(monkeypatch, "")
    assert confirm("Run it?", default=False) is False
    _answers(monkeypatch, "maybe", "n")
    assert confirm("Run it?") is False


def test_cancel_is_raised_on_eof(monkeypatch):
    def _eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    with pytest.raises(Cancelled):
        text("Name")


# --- run schema ----------------------------------------------------------------------------------


def test_create_repo_declares_its_questions():
    run = get_run("create-repo")
    assert [p.name for p in run.params] == ["name", "description", "template", "language"]
    assert run.params[0].required  # name has no default
    assert not run.params[1].required  # description is optional
    assert [c.value for c in run.params[2].choices] == ["ci-cd", "blank"]


def test_unknown_template_is_rejected():
    with pytest.raises(ValueError, match="unknown template"):
        get_run("create-repo").build({"name": "widgets", "template": "nope"})


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("site", "missing an owner"),
        ("me/", "isn't an owner/name pair"),
        ("/site", "isn't an owner/name pair"),
        ("me/a/b", "isn't an owner/name pair"),
        ("m e/site", "isn't a valid owner"),
    ],
)
def test_parse_repo_explains_what_is_wrong(bad, message):
    with pytest.raises(ValueError, match=message):
        parse_repo(bad)


def test_parse_repo():
    assert parse_repo("me/site") == RepoRef("me", "site")
    assert parse_repo("  me/site  ") == RepoRef("me", "site")


def test_default_owner_prefers_the_environment(monkeypatch):
    monkeypatch.setenv("OSF_OWNER", "acme")
    assert default_owner() == "acme"
    monkeypatch.delenv("OSF_OWNER")
    monkeypatch.setenv("GITHUB_OWNER", "widgets-inc")
    assert default_owner() == "widgets-inc"


def test_owner_falls_back_to_the_gh_cli_then_to_local(monkeypatch, tmp_path):
    for var in ("OSF_OWNER", "GITHUB_OWNER", "GH_OWNER", "GITHUB_USER"):
        monkeypatch.delenv(var, raising=False)

    hosts = tmp_path / "hosts.yml"
    hosts.write_text("github.com:\n    user: AaEll\n    oauth_token: x\n", encoding="utf-8")
    monkeypatch.setattr("osf.config.GH_HOSTS", hosts)
    assert detected_owner() == "AaEll"

    monkeypatch.setattr("osf.config.GH_HOSTS", tmp_path / "missing.yml")
    assert detected_owner() is None
    assert default_owner() == LOCAL_OWNER


def test_text_re_asks_on_a_rejected_answer(monkeypatch, capsys):
    _answers(monkeypatch, "not a repo!", "widgets")
    assert text("Repository name", validate=valid_repo_name) == "widgets"
    assert "isn't a valid repository name" in capsys.readouterr().out


def test_create_repo_uses_the_detected_owner_without_asking(monkeypatch):
    monkeypatch.setenv("OSF_OWNER", "acme")
    plan = get_run("create-repo").build({"name": "widgets"})
    assert plan.objective.repo == RepoRef("acme", "widgets")
