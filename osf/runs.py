"""Prepackaged runs — ready-to-execute workflows the user can trigger by name.

A `PrepackagedRun` turns a few parameters into a `Plan` (an objective + WorkItems + the skills they
need). `execute` drives a plan through the reconcile loop, so a user can go from "create a new repo"
to merged PRs with one call. Built-in: `create-repo`, which scaffolds a fresh repository.

Each run declares its parameters as `RunParam`s. That schema is what makes a run *structured*: the
`sf` shell walks it to ask one question per parameter (with defaults and fixed choices), and `build`
receives the collected answers. Adding a question to a run means adding a `RunParam`, not touching
the shell.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from osf.config import default_owner, valid_repo_name
from osf.driver import Driver, ObjectiveOutcome, Reviewer
from osf.forge import Forge
from osf.isolation import IsolationBackend
from osf.model import Objective, WorkItem
from osf.prompts import Choice
from osf.runtime import AgentRuntime
from osf.skills import Skill, SkillRegistry
from osf.types import RepoRef


@dataclass(frozen=True, slots=True)
class Plan:
    objective: Objective
    work_items: list[WorkItem]
    skills: SkillRegistry
    provision_repo: bool = False  # create the repo on the forge before the loop runs


@dataclass(frozen=True, slots=True)
class RunParam:
    """One question a run needs answered before it can build a plan.

    `choices` (non-empty) makes it a single-select; otherwise it is free text. `default` fills in a
    blank answer, or `default_factory` computes one when it depends on the environment. `required`
    parameters refuse an empty answer — everything else may be skipped. `validate` cleans up an
    answer or raises `ValueError`, which the dialog shows before asking again.
    """

    name: str
    prompt: str  # the question, phrased for a human
    default: str = ""
    choices: tuple[Choice, ...] = ()
    required: bool = True
    default_factory: Callable[[], str] | None = None
    validate: Callable[[str], str] | None = None

    def resolve_default(self) -> str:
        return self.default_factory() if self.default_factory else self.default


@dataclass(frozen=True, slots=True)
class PrepackagedRun:
    name: str
    description: str
    build: Callable[[dict], Plan]
    params: tuple[RunParam, ...] = field(default_factory=tuple)


_RUNS: dict[str, PrepackagedRun] = {}


def register_run(run: PrepackagedRun) -> None:
    if run.name in _RUNS:
        raise ValueError(f"duplicate run {run.name!r}")
    _RUNS[run.name] = run


def get_run(name: str) -> PrepackagedRun:
    return _RUNS[name]


def all_runs() -> list[PrepackagedRun]:
    return list(_RUNS.values())


async def execute(
    plan: Plan,
    *,
    runtime: AgentRuntime,
    isolation: IsolationBackend,
    forge: Forge,
    reviewer: Reviewer,
    max_rounds: int = 3,
) -> ObjectiveOutcome:
    """Drive a prepackaged plan through the reconcile loop.

    If the plan requests it, provision the repo on the forge first — the worker then scaffolds it
    (CI/CD included) and the driver opens/merges the PR against the freshly created repo.
    """
    if plan.provision_repo:
        await forge.create_repo(plan.objective.repo, description=plan.objective.goal)

    driver = Driver(
        runtime=runtime,
        isolation=isolation,
        forge=forge,
        reviewer=reviewer,
        decompose=lambda _objective: plan.work_items,
        skills=plan.skills,
        max_rounds=max_rounds,
    )
    return await driver.run(plan.objective)


# --- Built-in run: create-repo ---------------------------------------------------------------

CI_CD_SKILL = Skill(
    name="new-repo-ci",
    description="Scaffold a brand-new repository, including CI/CD, so it's ready to build on.",
    instructions=(
        "Create a complete starter repository:\n"
        "- README.md with the project name and a one-line description\n"
        "- LICENSE (MIT unless told otherwise)\n"
        "- a language-appropriate .gitignore\n"
        "- CI/CD as code under .github/workflows/: a `ci.yml` that lints and tests on push/PR, "
        "and a `release.yml` that builds/publishes on version tags\n"
        "Keep it minimal and correct; do not add application code beyond a placeholder."
    ),
)

BLANK_SKILL = Skill(
    name="new-repo-blank",
    description="Scaffold a minimal repository with no CI/CD wiring.",
    instructions=(
        "Create a minimal starter repository:\n"
        "- README.md with the project name and a one-line description\n"
        "- a language-appropriate .gitignore\n"
        "Do not add CI/CD workflows or application code."
    ),
)

# Starting points offered by `create-repo`, each with the files it must produce to merge.
_TEMPLATES = {
    "ci-cd": (
        CI_CD_SKILL,
        ["README.md", "LICENSE", ".gitignore", ".github/workflows/ci.yml"],
        "with CI/CD",
    ),
    "blank": (BLANK_SKILL, ["README.md", ".gitignore"], "blank"),
}


def _build_create_repo(params: dict) -> Plan:
    name = params["name"]
    description = params.get("description", "")
    language = params.get("language", "python")
    owner = params.get("owner") or default_owner()
    template = params.get("template", "ci-cd")
    if template not in _TEMPLATES:
        raise ValueError(f"unknown template {template!r}; expected one of {sorted(_TEMPLATES)}")
    skill, required_files, summary = _TEMPLATES[template]

    objective = Objective(
        id=f"create-repo-{name}",
        repo=RepoRef(owner=owner, name=name),
        goal=f"Create a new {language} repository '{name}' {summary}: {description}".rstrip(": "),
        acceptance_criteria=[f"{path} exists" for path in required_files],
    )
    spec = (
        f"Scaffold a new {language} repository named '{name}' {summary}. {description}".strip()
    )
    work_item = WorkItem(
        id=f"{objective.id}-scaffold",
        objective_id=objective.id,
        spec=spec,
        skills=[skill.name],
    )
    return Plan(objective, [work_item], SkillRegistry([skill]), provision_repo=True)


CREATE_REPO = PrepackagedRun(
    name="create-repo",
    description="Create and scaffold a new repository for the user.",
    build=_build_create_repo,
    params=(
        RunParam("name", "Repository name", validate=valid_repo_name),
        RunParam("description", "What should it do?", required=False),
        RunParam(
            "template",
            "Starting point",
            default="ci-cd",
            choices=(
                Choice("ci-cd", "Template with CI/CD", "README, LICENSE, .gitignore, workflows"),
                Choice("blank", "Blank repository", "README and .gitignore only"),
            ),
        ),
        RunParam("language", "Primary language", default="python"),
    ),
)

register_run(CREATE_REPO)
