"""Planning — the driver proposing what to build and what "done" means.

The user states a goal in their own words. Rather than making them spell out a merge gate, a
`Planner` turns that goal into a `ProposedPlan`: a restated objective, the steps to get there, and
the files the result must contain. The shell shows the plan and takes plain-language feedback until
the user accepts it, so the definition of done is *negotiated*, never dictated by a form.

`StaticPlanner` is the offline reference: one step, no gate. Engine-backed planners live with their
adapters (`osf.engines.resolve_planner`) and are a plain completion — no tools, no workspace — so
planning stays cheap next to a worker run.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from osf.model import Objective, WorkItem
from osf.types import ObjectiveId, RepoRef

PLAN_SYSTEM = (
    "You are the driver agent of an autonomous software factory. Turn the user's request into a "
    "short delivery plan.\n"
    "Reply with JSON only, no prose, in this exact shape:\n"
    '{"goal": "one sentence restating what to build", "steps": ['
    '{"spec": "one self-contained unit of work", "files": ["files this step produces"]}]}\n'
    "Rules: 1-3 steps. Each step is built by a separate agent in its own empty workspace, so a "
    "step must stand alone and must not depend on files another step writes. A step's `files` are "
    "its definition of done: list only files you are confident that step must produce (e.g. "
    "index.html), relative to the repo root, and keep the list short — prefer fewer, surer files. "
    "If the user gives feedback on a plan, return the whole revised plan, not a diff."
)

_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Step:
    """One unit of work and the files it is expected to produce."""

    spec: str
    files: list[str] = field(default_factory=list)

    @property
    def criteria(self) -> list[str]:
        return [f"{path} exists" for path in self.files]


@dataclass(frozen=True, slots=True)
class ProposedPlan:
    """What the driver intends to do, pending the user's approval."""

    goal: str
    steps: list[Step] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        """Every file the plan promises, in order, without duplicates."""
        seen: dict[str, None] = {}
        for step in self.steps:
            for path in step.files:
                seen.setdefault(path, None)
        return list(seen)

    @property
    def criteria(self) -> list[str]:
        return [f"{path} exists" for path in self.files]

    def objective(self, objective_id: ObjectiveId, repo: RepoRef) -> Objective:
        return Objective(
            id=objective_id, repo=repo, goal=self.goal, acceptance_criteria=self.criteria
        )

    def work_items(self, objective_id: ObjectiveId) -> list[WorkItem]:
        steps = self.steps or [Step(self.goal)]
        return [
            WorkItem(id=f"{objective_id}-{index}", objective_id=objective_id, spec=step.spec)
            for index, step in enumerate(steps, start=1)
        ]

    def criteria_by_item(self, objective_id: ObjectiveId) -> dict[str, list[str]]:
        """Each WorkItem's own gate.

        Every step runs in its own fresh workspace, so a step is judged only on what it was asked
        to produce — gating each one on the whole plan would make every worker rebuild everything.
        """
        steps = self.steps or [Step(self.goal)]
        return {
            f"{objective_id}-{index}": step.criteria for index, step in enumerate(steps, start=1)
        }


# One round of the negotiation: what was proposed, and what the user said about it.
Exchange = tuple[ProposedPlan, str]


class Planner(Protocol):
    """Proposes a plan for a request, revising it in light of the user's feedback."""

    def propose(self, request: str, exchanges: Sequence[Exchange] = ()) -> ProposedPlan: ...


class StaticPlanner:
    """Offline reference planner: take the request at face value and gate on nothing.

    Deliberately does not guess at files. Prose is full of things that look like paths — a domain
    like `demo.osf` reads as a filename — and a wrong gate is worse than none, because the run
    fails forever chasing a file that was never meant to exist.
    """

    def propose(self, request: str, exchanges: Sequence[Exchange] = ()) -> ProposedPlan:
        steps = [Step(request)]
        for _plan, feedback in exchanges:
            steps.append(Step(feedback))
        return ProposedPlan(goal=request, steps=steps)


def build_messages(request: str, exchanges: Sequence[Exchange]) -> list[dict[str, str]]:
    """Render the negotiation as a chat transcript the engines can replay."""
    messages = [{"role": "user", "content": request}]
    for plan, feedback in exchanges:
        messages.append({"role": "assistant", "content": render_json(plan)})
        messages.append({"role": "user", "content": feedback})
    return messages


def render_json(plan: ProposedPlan) -> str:
    steps = [{"spec": step.spec, "files": step.files} for step in plan.steps]
    return json.dumps({"goal": plan.goal, "steps": steps})


def parse_plan(text: str, *, fallback_goal: str) -> ProposedPlan:
    """Read a plan out of a model reply, tolerating code fences and stray commentary."""
    match = _JSON.search(text or "")
    if not match:
        raise ValueError("the planner did not return a plan")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"the planner returned malformed JSON: {exc}") from exc
    steps = [_parse_step(raw) for raw in data.get("steps") or []]
    steps = [step for step in steps if step.spec]
    # A planner that lists files at the top level instead of per step is only unambiguous when
    # there is one step to attach them to; with several we would have to guess who produces what.
    top_level = _strings(data.get("files"))
    if top_level and len(steps) == 1 and not steps[0].files:
        steps = [Step(steps[0].spec, top_level)]
    return ProposedPlan(goal=str(data.get("goal") or fallback_goal).strip(), steps=steps)


def _parse_step(raw: object) -> Step:
    if isinstance(raw, dict):
        spec = raw.get("spec") or raw.get("step") or raw.get("description") or ""
        return Step(str(spec).strip(), _strings(raw.get("files")))
    return Step(str(raw).strip())


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
