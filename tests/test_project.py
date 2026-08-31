"""Project isolation: editing the user's own repository, with snapshots as the safety net."""

import asyncio
import io
import subprocess
import sys
from pathlib import Path

import pytest

from osf.local.project import NotARepository, ProjectIsolation, repo_root
from osf.prompts import Style
from osf.shell import Session, Shell
from osf.types import RepoRef, Workspace


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one commit."""
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


@pytest.fixture
def isolation(repo: Path) -> ProjectIsolation:
    return ProjectIsolation(repo)


@pytest.fixture
def workspace(repo: Path) -> Workspace:
    return Workspace(path=str(repo), handle=str(repo))


# --- resolving the project ----------------------------------------------------------------


def test_repo_root_finds_the_worktree_from_a_subdirectory(repo: Path):
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert repo_root(nested) == repo.resolve()


def test_a_plain_directory_is_rejected(tmp_path: Path):
    with pytest.raises(NotARepository):
        ProjectIsolation(tmp_path)


def test_prepare_returns_the_project_itself(isolation: ProjectIsolation, repo: Path):
    ws = asyncio.run(isolation.prepare(RepoRef("me", "demo"), "osf/some-branch"))
    assert Path(ws.path) == repo.resolve()  # no clone, no tempdir, and the branch is ignored
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() != "osf/some-branch"


# --- snapshots ------------------------------------------------------------------------------


def test_snapshot_sees_new_and_modified_files(isolation, workspace, repo: Path):
    before = isolation.snapshot(workspace)
    (repo / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")

    assert sorted(isolation.changed_since(workspace, before)) == ["README.md", "index.html"]
    assert "index.html" in isolation.diff_since(workspace, before, stat=True)


def test_snapshots_leave_the_users_staging_area_alone(isolation, workspace, repo: Path):
    """The whole point of the scratch index: `sf` must not stage or commit for you."""
    (repo / "staged.txt").write_text("mine", encoding="utf-8")
    git(repo, "add", "staged.txt")
    commits_before = git(repo, "rev-list", "--count", "HEAD").strip()

    isolation.snapshot(workspace)
    (repo / "written-by-agent.txt").write_text("theirs", encoding="utf-8")
    isolation.snapshot(workspace)

    status = git(repo, "status", "--short")
    assert "A  staged.txt" in status  # still staged, exactly as the user left it
    assert "?? written-by-agent.txt" in status  # the agent's file was never staged
    assert git(repo, "rev-list", "--count", "HEAD").strip() == commits_before


def test_checkpoint_does_not_commit(isolation, workspace, repo: Path):
    commits_before = git(repo, "rev-list", "--count", "HEAD").strip()
    (repo / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    asyncio.run(isolation.checkpoint(workspace, "feat: something"))
    assert git(repo, "rev-list", "--count", "HEAD").strip() == commits_before


def test_restore_undoes_additions_modifications_and_deletions(isolation, workspace, repo: Path):
    (repo / "keep.txt").write_text("original\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "second")

    before = isolation.snapshot(workspace)
    (repo / "added.txt").write_text("new\n", encoding="utf-8")
    (repo / "keep.txt").write_text("edited\n", encoding="utf-8")
    (repo / "README.md").unlink()

    restored = sorted(isolation.restore(workspace, before))
    assert restored == ["README.md", "added.txt", "keep.txt"]
    assert not (repo / "added.txt").exists()
    assert (repo / "keep.txt").read_text(encoding="utf-8") == "original\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "# demo\n"
    assert isolation.changed_since(workspace, before) == []


def test_restore_returns_uncommitted_work_not_the_last_commit(isolation, workspace, repo: Path):
    """The snapshot is the *working tree*, so revert must not reset to HEAD.

    A naive restore (`git checkout HEAD -- .`) passes every other test here and silently destroys
    whatever the user had not committed yet — the worst failure this module can have.
    """
    (repo / "README.md").write_text("# edited but not committed\n", encoding="utf-8")

    before = isolation.snapshot(workspace)
    (repo / "README.md").write_text("# the agent overwrote it\n", encoding="utf-8")
    isolation.restore(workspace, before)

    assert (repo / "README.md").read_text(encoding="utf-8") == "# edited but not committed\n"
    assert "# demo" not in (repo / "README.md").read_text(encoding="utf-8")  # not the commit


def test_gitignored_files_are_invisible_to_snapshots(isolation, workspace, repo: Path):
    """Pins a real limitation: ignored paths are outside the safety net entirely.

    `git add -A` skips ignored files, so anything an agent writes under an ignored path is not
    reported as changed and survives a revert. Documented in cli-howto.md; asserted here so the
    behaviour can't drift unnoticed in either direction.
    """
    (repo / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    (repo / "secrets").mkdir()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore secrets")

    before = isolation.snapshot(workspace)
    (repo / "secrets" / "leaked.txt").write_text("written by the agent", encoding="utf-8")
    (repo / "visible.txt").write_text("also written by the agent", encoding="utf-8")

    assert isolation.changed_since(workspace, before) == ["visible.txt"]
    isolation.restore(workspace, before)
    assert not (repo / "visible.txt").exists()
    assert (repo / "secrets" / "leaked.txt").is_file()  # untouched by revert


def test_a_second_run_is_measured_from_the_first(capsys, repo: Path, monkeypatch):
    """Each run reports only its own changes, not everything since the session began."""
    session = Session(project=repo, forge="local")
    run_shell("Create a landing page for demo.osf\n\ny\n/quit\n", capsys, session)
    assert (repo / "index.html").is_file()

    out = run_shell("Create a landing page for demo.osf\n\ny\n/quit\n", capsys, session)
    # The scripted worker rewrites the same file with the same content, so nothing differs.
    assert "no files changed" in out


def test_cleanup_never_deletes_the_users_repository(isolation, workspace, repo: Path):
    asyncio.run(isolation.cleanup(workspace))
    assert (repo / "README.md").is_file()


# --- the shell working in a project -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch):
    monkeypatch.setattr("osf.prompts.STYLE", Style(enabled=False))
    monkeypatch.setattr("osf.shell.STYLE", Style(enabled=False))
    monkeypatch.setenv("OSF_OWNER", "me")


def run_shell(script: str, capsys, session: Session) -> str:
    sys.stdin = io.StringIO(script)
    try:
        Shell(session).run()
    finally:
        sys.stdin = sys.__stdin__
    return capsys.readouterr().out


def test_a_local_run_edits_the_project_and_keeps_the_change(capsys, repo: Path):
    session = Session(project=repo, forge="local")
    out = run_shell("Create a landing page for demo.osf\n\ny\n/quit\n", capsys, session)
    assert "changed" in out
    assert "index.html" in out
    assert "kept" in out
    assert (repo / "index.html").is_file()  # the agent really wrote into the user's repo


def test_declining_puts_the_project_back(capsys, repo: Path):
    session = Session(project=repo, forge="local")
    out = run_shell("Create a landing page for demo.osf\n\nn\n/quit\n", capsys, session)
    assert "reverted 1 file(s)" in out
    assert not (repo / "index.html").exists()
    assert git(repo, "status", "--short") == ""


def test_local_work_reports_no_pull_request(capsys, repo: Path):
    session = Session(project=repo, forge="local")
    out = run_shell("Create a landing page for demo.osf\n\ny\n/quit\n", capsys, session)
    assert "merged (rounds=1)" in out  # no PR#, because there is no forge
    assert "PR#" not in out


def test_local_work_never_asks_for_a_repository_name(capsys, repo: Path):
    session = Session(project=repo, forge="local")
    out = run_shell("Create a landing page for demo.osf\n\ny\n/quit\n", capsys, session)
    assert "Repository name" not in out  # the project you are in *is* the target


def test_project_command_switches_repositories(capsys, repo: Path, tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    session = Session(project=repo, forge="local")
    out = run_shell(f"/project {other}\n/quit\n", capsys, session)
    assert session.project == other.resolve()
    assert str(other) in out


def test_project_command_rejects_a_non_repository(capsys, tmp_path: Path):
    session = Session(forge="local")
    out = run_shell(f"/project {tmp_path}\n/quit\n", capsys, session)
    assert "is not a git repository" in out
    assert session.project is None


def test_the_planner_is_told_steps_share_the_project(capsys, repo: Path, monkeypatch):
    """Locally, steps run in one repo — the planner must be allowed to build on earlier steps."""
    seen = {}

    class _Spy:
        def clarify(self, request, context=""):
            return []

        def propose(self, request, exchanges=(), answers=(), *, shared_workspace=False, context=""):
            seen["shared"] = shared_workspace
            from osf.planner import ProposedPlan, Step

            return ProposedPlan("Do it", [Step("Write index.html", ["index.html"])])

    monkeypatch.setattr(Session, "planner", lambda _self: _Spy())
    run_shell("build something\n\ny\n/quit\n", capsys, Session(project=repo, forge="local"))
    assert seen["shared"] is True

    seen.clear()
    run_shell("/repo me/site\nbuild something\n\n/quit\n", capsys, Session(forge="memory"))
    assert seen["shared"] is False


def test_diff_shows_the_working_tree(capsys, repo: Path):
    (repo / "scratch.txt").write_text("x", encoding="utf-8")
    session = Session(project=repo, forge="local")
    out = run_shell("/diff\n/quit\n", capsys, session)
    assert "scratch.txt" in out


# --- what the worker is told about where it is working ------------------------------------------


def test_the_worker_prompt_names_the_working_directory(repo: Path):
    from osf.engines._tools import worker_system

    prompt = worker_system(Workspace(path=str(repo), handle=str(repo)))
    assert str(repo) in prompt
    assert "README.md" in prompt  # and what is already in it


def test_an_empty_workspace_is_described_as_empty(tmp_path: Path):
    from osf.engines._tools import worker_system

    assert "It is empty." in worker_system(Workspace(path=str(tmp_path), handle=str(tmp_path)))


def test_the_listing_hides_ignored_files(repo: Path):
    """`.env` and `node_modules/` must not be paraded through the prompt."""
    from osf.engines._tools import workspace_listing

    (repo / ".gitignore").write_text(".env\nnode_modules/\n", encoding="utf-8")
    (repo / ".env").write_text("FIREWORKS_API_KEY=secret", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
    (repo / "app.py").write_text("print('hi')", encoding="utf-8")

    listing = workspace_listing(Workspace(path=str(repo), handle=str(repo)))
    assert "app.py" in listing
    assert ".env" not in listing
    assert not any(path.startswith("node_modules") for path in listing)


def test_the_listing_is_bounded(repo: Path):
    from osf.engines._tools import LISTING_LIMIT, workspace_listing

    for index in range(LISTING_LIMIT + 20):
        (repo / f"file{index:03d}.txt").write_text("x", encoding="utf-8")
    listing = workspace_listing(Workspace(path=str(repo), handle=str(repo)))
    assert len(listing) == LISTING_LIMIT  # a big repo must not crowd out the request


def test_a_directory_without_git_is_still_listed(tmp_path: Path):
    from osf.engines._tools import workspace_listing

    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    assert workspace_listing(Workspace(path=str(tmp_path), handle=str(tmp_path))) == ["notes.md"]


# --- what the driver is told about the project --------------------------------------------------


def test_the_planner_is_shown_the_project_files(capsys, repo: Path, monkeypatch):
    """A planner that cannot see the repo plans into files that don't exist.

    Observed for real: asked to add a flag to a single-file app, it planned the work into a new
    `cli.py`, and the worker duly moved the entry point there and broke it.
    """
    (repo / "todo.py").write_text("# the app\n", encoding="utf-8")
    seen = {}

    class _Spy:
        def route(self, request, catalog="", context=""):
            seen["route"] = context
            from osf.planner import Decision

            return Decision(action="plan")

        def clarify(self, request, context=""):
            seen["clarify"] = context
            return []

        def propose(self, request, exchanges=(), answers=(), *, shared_workspace=False, context=""):
            seen["propose"] = context
            from osf.planner import ProposedPlan, Step

            return ProposedPlan("Do it", [Step("Edit todo.py", ["todo.py"])])

    monkeypatch.setattr(Session, "planner", lambda _self: _Spy())
    run_shell("add a flag\n\ny\n/quit\n", capsys, Session(project=repo, forge="local"))

    assert "todo.py" in seen["propose"]  # the plan is made against the repository that exists
    assert "todo.py" in seen["route"]
    assert "todo.py" in seen["clarify"]
    assert str(repo) in seen["propose"]


def test_an_empty_project_is_described_as_empty(capsys, tmp_path: Path, monkeypatch):
    git(tmp_path, "init", "-q")
    seen = {}

    class _Spy:
        def route(self, request, catalog="", context=""):
            from osf.planner import Decision

            return Decision(action="plan")

        def clarify(self, request, context=""):
            return []

        def propose(self, request, exchanges=(), answers=(), *, shared_workspace=False, context=""):
            seen["context"] = context
            from osf.planner import ProposedPlan, Step

            return ProposedPlan("Do it", [Step("Make something")])

    monkeypatch.setattr(Session, "planner", lambda _self: _Spy())
    run_shell("build something\n\ny\n/quit\n", capsys, Session(project=tmp_path, forge="local"))
    assert "is empty" in seen["context"]


def test_no_project_context_when_the_work_is_not_local(capsys, monkeypatch):
    seen = {}

    class _Spy:
        def route(self, request, catalog="", context=""):
            from osf.planner import Decision

            return Decision(action="plan")

        def clarify(self, request, context=""):
            return []

        def propose(self, request, exchanges=(), answers=(), *, shared_workspace=False, context=""):
            seen["context"] = context
            from osf.planner import ProposedPlan, Step

            return ProposedPlan("Do it", [Step("Make something")])

    monkeypatch.setattr(Session, "planner", lambda _self: _Spy())
    run_shell("/repo me/site\nbuild something\n\n/quit\n", capsys, Session(forge="memory"))
    assert seen["context"] == ""  # a throwaway workspace has no project to describe
