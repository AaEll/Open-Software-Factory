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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from osf.model import Objective, WorkItem
from osf.types import ObjectiveId, RepoRef

CLARIFY_SYSTEM = (
    "You are the driver agent of an autonomous software factory. Before planning, decide what you "
    "genuinely need to know about the user's request.\n"
    'Reply with JSON only, no prose: {"questions": ["...", "..."]}\n'
    "Rules: at most 3 questions, each one short and answerable in a few words. Ask only what would "
    "change the plan — scope, must-have content, constraints. Never ask what you can reasonably "
    "assume or decide yourself, and never ask about tooling, hosting, or the repository. If the "
    "request is already clear enough to plan, return an empty list."
)

_PLAN_BASE = (
    "You are the driver agent of an autonomous software factory. Turn the user's request into a "
    "short delivery plan.\n"
    "Reply with JSON only, no prose, in this exact shape:\n"
    '{"goal": "one sentence restating what to build", "steps": ['
    '{"spec": "one self-contained unit of work", "files": ["files this step produces"]}]}\n'
    "Rules: 1-3 steps. A step's `files` are its definition of done: list only files you are "
    "confident that step must produce (e.g. index.html), relative to the repo root, and keep the "
    "list short — prefer fewer, surer files. If the user gives feedback on a plan, return the "
    "whole revised plan, not a diff.\n"
)
# Whether steps share a workspace is a property of the isolation backend, and it changes what a
# plan may assume: in the user's own repo each step sees the last one's work, in throwaway
# per-step workspaces it does not.
_PLAN_SHARED = (
    "The steps run in order in one repository, each seeing the files the previous steps wrote, so "
    "a later step may extend or refine earlier work. Existing files in the repository may be "
    "edited — say so in the step's spec when that is the intent."
)
_PLAN_ISOLATED = (
    "Each step is built by a separate agent in its own empty workspace, so a step must stand alone "
    "and must not depend on files another step writes."
)


def plan_system(*, shared_workspace: bool = False) -> str:
    """The planning prompt, told whether steps can build on each other."""
    return _PLAN_BASE + (_PLAN_SHARED if shared_workspace else _PLAN_ISOLATED)


PLAN_SYSTEM = plan_system()  # the isolated default, kept for callers that don't care

RETRY_NUDGE = (
    "That reply was not usable. Reply with JSON only — no prose, no code fences — in exactly the "
    "shape described."
)

_JSON = re.compile(r"\{.*\}", re.DOTALL)

# How an engine adapter runs one completion: (system prompt, messages, max tokens) -> reply text.
Complete = Callable[[str, list[dict], int], str]


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
# A clarifying question and what the user said back.
Answer = tuple[str, str]


class Planner(Protocol):
    """Asks what it needs to know, then proposes a plan and revises it on feedback."""

    def clarify(self, request: str) -> list[str]: ...

    def propose(
        self,
        request: str,
        exchanges: Sequence[Exchange] = (),
        answers: Sequence[Answer] = (),
        *,
        shared_workspace: bool = False,
    ) -> ProposedPlan: ...


class StaticPlanner:
    """Offline reference planner: take the request at face value and gate on nothing.

    Deliberately does not guess at files. Prose is full of things that look like paths — a domain
    like `demo.osf` reads as a filename — and a wrong gate is worse than none, because the run
    fails forever chasing a file that was never meant to exist.
    """

    def clarify(self, request: str) -> list[str]:
        return []  # with no model there is nobody to ask, and nobody to use the answers

    def propose(
        self,
        request: str,
        exchanges: Sequence[Exchange] = (),
        answers: Sequence[Answer] = (),
        *,
        shared_workspace: bool = False,
    ) -> ProposedPlan:
        # Answers are deliberately dropped: with no model to interpret them, folding them into the
        # spec would just paste an interview transcript in as the work to do.
        steps = [Step(request)]
        for _plan, feedback in exchanges:
            steps.append(Step(feedback))
        return ProposedPlan(goal=request, steps=steps)


def compose_request(request: str, answers: Sequence[Answer] = ()) -> str:
    """Fold the answers to the driver's questions into the request it plans from."""
    if not answers:
        return request
    lines = "\n".join(f"- {question} {answer}" for question, answer in answers)
    return f"{request}\n\nAnswers to your questions:\n{lines}"


def build_messages(
    request: str, exchanges: Sequence[Exchange], answers: Sequence[Answer] = ()
) -> list[dict[str, str]]:
    """Render the negotiation as a chat transcript the engines can replay."""
    messages = [{"role": "user", "content": compose_request(request, answers)}]
    for plan, feedback in exchanges:
        messages.append({"role": "assistant", "content": render_json(plan)})
        messages.append({"role": "user", "content": feedback})
    return messages


def parse_questions(text: str) -> list[str]:
    """Read the driver's questions out of a model reply; no questions is a valid answer."""
    match = _JSON.search(text or "")
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [str(q).strip() for q in data.get("questions") or [] if str(q).strip()][:3]


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


def propose_with_retry(
    complete: Complete,
    request: str,
    exchanges: Sequence[Exchange] = (),
    answers: Sequence[Answer] = (),
    *,
    attempts: int = 2,
    shared_workspace: bool = False,
) -> ProposedPlan:
    """Ask an engine for a plan, nudging it once if the reply isn't a usable plan.

    Models drift out of JSON now and then — a stray sentence, a truncated object. One corrective
    round trip recovers nearly all of it, and is far cheaper than dropping the user's request.
    """
    system = plan_system(shared_workspace=shared_workspace)
    messages = build_messages(request, exchanges, answers)
    last: ValueError | None = None
    for _attempt in range(attempts):
        reply = complete(system, messages, 2000)
        try:
            return parse_plan(reply, fallback_goal=request)
        except ValueError as exc:
            last = exc
            messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {"role": "user", "content": RETRY_NUDGE},
            ]
    raise last if last else ValueError("the planner did not return a plan")
