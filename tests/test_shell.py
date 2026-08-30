"""The `sf` shell: command dispatch, the structured run wizard, and free-text objectives.

Every test drives the real loop by piping a script into stdin, which is exactly how a user drives
it by hand — so these cover the prompts as well as the commands. All offline: no keys, no network.
"""

import io
import sys

import pytest

from osf.prompts import Cancelled, Choice, Style, confirm, select, text
from osf.runs import get_run
from osf.shell import Session, Shell, parse_repo
from osf.types import ModelRef, RepoRef


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch):
    """Strip ANSI so assertions match on text, not escape codes."""
    monkeypatch.setattr("osf.prompts.STYLE", Style(enabled=False))
    monkeypatch.setattr("osf.shell.STYLE", Style(enabled=False))


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
    # A malformed repo raises inside the handler; the loop reports it and keeps going.
    out = run_shell("/repo not-a-repo\n/status\n/quit\n", capsys)
    assert "bad repo" in out
    assert "repo:   unset" in out


# --- free-text objectives ---------------------------------------------------------------------


def test_objective_merges_offline(capsys):
    out = run_shell(
        "/repo me/site\nCreate a landing page for demo.osf\nindex.html exists\n/quit\n", capsys
    )
    assert "me-site: done" in out
    assert "merged" in out


def test_objective_escalates_when_a_criterion_is_unmet(capsys):
    # The scripted worker only ever writes index.html, so this gate can never be satisfied.
    out = run_shell(
        "/repo me/site\n/rounds 1\nBuild the app\napp/main.py exists\n/quit\n", capsys
    )
    assert "escalated" in out
    assert "failed" in out


def test_objective_asks_for_the_repo_when_unset(capsys):
    session = Session()
    out = run_shell("Landing page for demo.osf\nme/site\n\n/quit\n", capsys, session)
    assert session.repo == RepoRef("me", "site")  # asked once, then remembered
    assert "done" in out


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
    assert [p.name for p in run.params] == [
        "name",
        "description",
        "template",
        "language",
        "owner",
    ]
    assert run.params[0].required  # name has no default
    assert not run.params[1].required  # description is optional
    assert [c.value for c in run.params[2].choices] == ["ci-cd", "blank"]


def test_unknown_template_is_rejected():
    with pytest.raises(ValueError, match="unknown template"):
        get_run("create-repo").build({"name": "widgets", "template": "nope"})


def test_parse_repo():
    assert parse_repo("me/site") == RepoRef("me", "site")
    with pytest.raises(ValueError):
        parse_repo("site")
