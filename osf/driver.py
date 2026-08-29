"""The driver control loop (Phase 2).

A Driver owns an objective and reconciles it: decompose into WorkItems, then for each one dispatch a
worker, open a PR, review it, and either merge (checks green + approved) or leave feedback and let
the worker try again — bounded by ``max_rounds``. The objective is *done* when every WorkItem
merges, otherwise *escalated*.

This is the in-memory walking skeleton of the reconcile loop: sequential per WorkItem, state held in
memory. Durable event-sourced ticks and cross-WorkItem concurrency (per the architecture) are later
work. The loop only touches the `AgentRuntime`/`IsolationBackend`/`Forge` contracts, so it runs the
same against local reference adapters or a real GitHub forge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from osf.forge import Forge
from osf.isolation import IsolationBackend
from osf.model import Objective, WorkItem
from osf.runtime import AgentRuntime
from osf.skills import SkillRegistry, apply_skills
from osf.types import PrRef, Workspace


@dataclass(frozen=True, slots=True)
class Review:
    approved: bool
    comment: str = ""


class Reviewer(Protocol):
    """Decides whether a WorkItem's change is good enough to merge."""

    async def review(self, work_item: WorkItem, workspace: Workspace) -> Review: ...


@dataclass(frozen=True, slots=True)
class WorkItemOutcome:
    work_item_id: str
    state: str  # "merged" | "failed"
    pr: PrRef
    rounds: int


@dataclass(frozen=True, slots=True)
class ObjectiveOutcome:
    objective_id: str
    state: str  # "done" | "escalated"
    items: list[WorkItemOutcome]


def default_decompose(objective: Objective) -> list[WorkItem]:
    """Trivial decomposition: one WorkItem for the whole objective (walking skeleton)."""
    return [WorkItem(id=f"{objective.id}-1", objective_id=objective.id, spec=objective.goal)]


class Driver:
    """Reconciles an objective into merged PRs via worker dispatch + review."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        isolation: IsolationBackend,
        forge: Forge,
        reviewer: Reviewer,
        decompose: Callable[[Objective], list[WorkItem]] = default_decompose,
        skills: SkillRegistry | None = None,
        max_rounds: int = 3,
    ) -> None:
        self._runtime = runtime
        self._isolation = isolation
        self._forge = forge
        self._reviewer = reviewer
        self._decompose = decompose
        self._skills = skills
        self._max_rounds = max_rounds

    async def run(self, objective: Objective) -> ObjectiveOutcome:
        outcomes = [await self._reconcile(objective, item) for item in self._decompose(objective)]
        done = all(o.state == "merged" for o in outcomes)
        return ObjectiveOutcome(objective.id, "done" if done else "escalated", outcomes)

    async def _reconcile(self, objective: Objective, item: WorkItem) -> WorkItemOutcome:
        branch = f"osf/{item.id}"
        workspace = await self._isolation.prepare(objective.repo, branch)
        pr: PrRef | None = None
        feedback = ""

        for round_ in range(1, self._max_rounds + 1):
            await self._dispatch_worker(workspace, item, feedback)
            pr = pr or await self._forge.open_pr(
                objective.repo, branch, title=f"feat: {item.spec}", body=item.spec
            )

            review = await self._reviewer.review(item, workspace)
            checks = await self._forge.checks(pr)
            if review.approved and checks.state == "success":
                await self._forge.merge(pr)
                return WorkItemOutcome(item.id, "merged", pr, round_)

            reason = review.comment or f"checks are {checks.state}"
            await self._forge.comment(pr, f"Changes requested: {reason}", review=True)
            feedback = reason

        return WorkItemOutcome(item.id, "failed", pr, self._max_rounds)

    async def _dispatch_worker(self, workspace: Workspace, item: WorkItem, feedback: str) -> None:
        session = await self._runtime.create_session(workspace, role="worker")
        prompt = _worker_prompt(item, feedback)
        if self._skills is not None and item.skills:
            prompt = apply_skills(prompt, self._skills, item.skills)
        await self._runtime.prompt(session, prompt)
        await self._runtime.result(session)
        await self._isolation.exec(workspace, ["git", "add", "-A"])
        # A no-op commit (nothing changed since last round) is fine; the forge state still advances.
        await self._isolation.exec(workspace, ["git", "commit", "-q", "-m", f"feat: {item.spec}"])


def _worker_prompt(item: WorkItem, feedback: str) -> str:
    if feedback:
        return f"{item.spec}\n\nAddress this review feedback and update the change:\n{feedback}"
    return item.spec
