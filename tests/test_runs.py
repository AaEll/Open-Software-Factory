"""Prepackaged runs: create-repo plan shape + end-to-end execution through the reconcile loop."""

import asyncio
from pathlib import Path

import pytest

from osf.driver import Review
from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.runs import all_runs, execute, get_run, register_run
from osf.runtime import AgentResult

REQUIRED = ["README.md", "LICENSE", ".gitignore", ".github/workflows/ci.yml"]


def test_create_repo_is_registered_and_builds_a_plan():
    assert "create-repo" in {r.name for r in all_runs()}
    plan = get_run("create-repo").build({"name": "widgets", "description": "A widget lib"})

    assert plan.objective.repo.name == "widgets"
    assert "widgets" in plan.objective.goal
    assert plan.provision_repo is True  # the run creates the repo
    assert ".github/workflows/ci.yml exists" in plan.objective.acceptance_criteria
    (item,) = plan.work_items
    assert item.skills == ["new-repo-ci"]
    assert plan.skills.get("new-repo-ci").name == "new-repo-ci"


def test_register_run_rejects_duplicates():
    with pytest.raises(ValueError):
        register_run(get_run("create-repo"))


class _ScaffoldRuntime:
    """A worker that scaffolds a starter repo (what the new-repo skill asks for)."""

    def __init__(self) -> None:
        self._ws: dict[str, object] = {}

    async def create_session(self, workspace, role):
        sid = str(len(self._ws))
        self._ws[sid] = workspace
        return sid

    async def prompt(self, session, text):
        d = Path(self._ws[session].path)
        (d / "README.md").write_text("# widgets\nA widget lib\n")
        (d / "LICENSE").write_text("MIT")
        (d / ".gitignore").write_text("__pycache__/\n")
        workflow = d / ".github" / "workflows" / "ci.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: ci\non: [push, pull_request]\n")

    async def stream_events(self, session):
        return
        yield

    async def interrupt(self, session):
        return None

    async def result(self, session):
        return AgentResult(outcome="completed", transcript=[], cost_usd=0.0)


class _FilesReviewer:
    """Approves only once the required files exist — the run's definition of done."""

    def __init__(self, required: list[str]) -> None:
        self._required = required

    async def review(self, work_item, workspace) -> Review:
        missing = [f for f in self._required if not Path(workspace.path, f).is_file()]
        if missing:
            return Review(approved=False, comment=f"missing files: {missing}")
        return Review(approved=True)


def test_execute_create_repo_end_to_end():
    plan = get_run("create-repo").build({"name": "widgets", "description": "A widget lib"})
    forge = InMemoryForge()

    outcome = asyncio.run(
        execute(
            plan,
            runtime=_ScaffoldRuntime(),
            isolation=TempdirIsolation(),
            forge=forge,
            reviewer=_FilesReviewer(REQUIRED),
        )
    )

    assert outcome.state == "done"
    (item,) = outcome.items
    assert item.state == "merged"
    assert forge.prs[item.pr.number].merged
    # The run provisioned the repo before the loop (CI/CD is scaffolded via the required files).
    assert plan.objective.repo in forge.repos
