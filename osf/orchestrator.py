"""Minimal single-work-item orchestrator (walking skeleton).

Phase 1 shape, no driver yet: take an objective, run one worker agent in an isolated
workspace, commit what it produced, open a PR, and merge when checks pass. This is the
end-to-end path the eval exercises; the Phase 2 driver loop wraps decomposition and review
around it.
"""

from __future__ import annotations

from dataclasses import dataclass

from osf.forge import Forge
from osf.isolation import IsolationBackend
from osf.model import Objective
from osf.runtime import AgentResult, AgentRuntime
from osf.types import PrRef, Workspace


@dataclass(frozen=True, slots=True)
class ObjectiveResult:
    objective_id: str
    workspace: Workspace
    pr: PrRef
    agent: AgentResult
    merged: bool


async def run_objective(
    objective: Objective,
    *,
    runtime: AgentRuntime,
    isolation: IsolationBackend,
    forge: Forge,
) -> ObjectiveResult:
    branch = f"osf/{objective.id}"
    workspace = await isolation.prepare(objective.repo, branch)

    session = await runtime.create_session(workspace, role="worker")
    await runtime.prompt(session, _worker_prompt(objective))
    agent = await runtime.result(session)

    await isolation.exec(workspace, ["git", "add", "-A"])
    await isolation.exec(workspace, ["git", "commit", "-q", "-m", f"feat: {objective.goal}"])

    pr = await forge.open_pr(
        objective.repo,
        branch,
        title=f"feat: {objective.goal}",
        body="\n".join(f"- [ ] {c}" for c in objective.acceptance_criteria),
    )
    merged = (await forge.checks(pr)).state == "success"
    if merged:
        await forge.merge(pr)

    return ObjectiveResult(
        objective_id=objective.id,
        workspace=workspace,
        pr=pr,
        agent=agent,
        merged=merged,
    )


def _worker_prompt(objective: Objective) -> str:
    criteria = "\n".join(f"- {c}" for c in objective.acceptance_criteria)
    return f"{objective.goal}\n\nAcceptance criteria:\n{criteria}"
