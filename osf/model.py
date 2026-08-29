"""The OSF data model (event-sourced target).

Objective (1) -> (N) WorkItem [DAG] -> (N) PullRequest -> (N) AgentRun.

In Phase 0 these are plain records that name the domain. The orchestration engine will later
rebuild them from an append-only event log so the factory is replay-safe and auditable; these
dataclasses describe the *projected* shape of each entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from osf.types import AgentRunId, ObjectiveId, PrRef, RepoRef, WorkItemId

WorkItemState = Literal["pending", "in_progress", "in_review", "merged", "failed"]
ObjectiveState = Literal["open", "converging", "done", "escalated"]


@dataclass(frozen=True, slots=True)
class Objective:
    """Human intent — the only required input."""

    id: ObjectiveId
    repo: RepoRef
    goal: str
    acceptance_criteria: list[str] = field(default_factory=list)
    max_iterations: int | None = None
    budget_usd: float | None = None
    state: ObjectiveState = "open"


@dataclass(frozen=True, slots=True)
class WorkItem:
    """A unit of work. WorkItems form a dependency DAG via ``depends_on``."""

    id: WorkItemId
    objective_id: ObjectiveId
    spec: str
    depends_on: list[WorkItemId] = field(default_factory=list)
    state: WorkItemState = "pending"


@dataclass(frozen=True, slots=True)
class PullRequest:
    """A forge PR produced for a WorkItem, carrying its review/feedback rounds."""

    work_item_id: WorkItemId
    ref: PrRef
    review_rounds: int = 0


@dataclass(frozen=True, slots=True)
class AgentRun:
    """One agent execution attached to a WorkItem."""

    id: AgentRunId
    work_item_id: WorkItemId
    role: str
    engine: str
    outcome: Literal["completed", "failed", "interrupted"] | None = None
    cost_usd: float = 0.0
