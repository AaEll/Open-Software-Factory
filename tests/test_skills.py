"""Skills abstraction: registry behavior + skill instructions reach the worker prompt."""

import asyncio
from pathlib import Path

import pytest

from osf.driver import Driver, Review
from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.model import Objective, WorkItem
from osf.runtime import AgentResult
from osf.skills import Skill, SkillRegistry, apply_skills
from osf.types import RepoRef

NEW_REPO = Skill(
    name="new-repo",
    description="Scaffolding a brand-new repository.",
    instructions="Add README.md, LICENSE, and a .gitignore.",
)


def test_registry_register_get_all_and_duplicate():
    reg = SkillRegistry([NEW_REPO])
    assert reg.get("new-repo") is NEW_REPO
    assert [s.name for s in reg.all()] == ["new-repo"]
    with pytest.raises(ValueError):
        reg.register(NEW_REPO)


def test_render_and_apply_compose_instructions():
    reg = SkillRegistry([NEW_REPO])
    assert "Add README.md" in reg.render(["new-repo"])
    assert apply_skills("base", reg, []) == "base"  # no skills -> unchanged
    composed = apply_skills("base", reg, ["new-repo"])
    assert composed.startswith("base") and "## Skill: new-repo" in composed


class _RecordingRuntime:
    """Captures the prompts it receives and writes a file so the PR can merge."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._ws: dict[str, object] = {}

    async def create_session(self, workspace, role):
        sid = f"rec-{len(self._ws)}"
        self._ws[sid] = workspace
        return sid

    async def prompt(self, session, text):
        self.prompts.append(text)
        Path(self._ws[session].path, "index.html").write_text("<!doctype html><title>x</title>")

    async def stream_events(self, session):
        return
        yield  # make this an async generator

    async def interrupt(self, session):
        return None

    async def result(self, session):
        return AgentResult(outcome="completed", transcript=[], cost_usd=0.0)


class _Approve:
    async def review(self, work_item, workspace) -> Review:
        return Review(approved=True)


def test_driver_injects_skill_instructions_into_worker_prompt():
    runtime = _RecordingRuntime()
    driver = Driver(
        runtime=runtime,
        isolation=TempdirIsolation(),
        forge=InMemoryForge(),
        reviewer=_Approve(),
        decompose=lambda obj: [
            WorkItem(id="wi", objective_id=obj.id, spec="Make a repo", skills=["new-repo"])
        ],
        skills=SkillRegistry([NEW_REPO]),
    )
    outcome = asyncio.run(driver.run(Objective(id="o", repo=RepoRef("osf", "site"), goal="g")))

    assert outcome.state == "done"
    assert "Add README.md" in runtime.prompts[0]  # skill instructions were composed in
