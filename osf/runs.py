"""Prepackaged runs — ready-to-execute workflows the user can trigger by name.

A `PrepackagedRun` turns a few parameters into a `Plan` (an objective + WorkItems + the skills they
need). `execute` drives a plan through the reconcile loop, so a user can go from "create a new repo"
to merged PRs with one call. Built-in: `create-repo`, which scaffolds a fresh repository using the
`new-repo` skill.

The offline run scaffolds the repository's files; actually provisioning the repo on GitHub needs a
`Forge.create_repo` capability (future work), dropped in with no change to runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from osf.driver import Driver, ObjectiveOutcome, Reviewer
from osf.forge import Forge
from osf.isolation import IsolationBackend
from osf.model import Objective, WorkItem
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
class PrepackagedRun:
    name: str
    description: str
    build: Callable[[dict], Plan]


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

NEW_REPO_SKILL = Skill(
    name="new-repo",
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


def _build_create_repo(params: dict) -> Plan:
    name = params["name"]
    description = params.get("description", "")
    language = params.get("language", "python")
    owner = params.get("owner", "osf")

    objective = Objective(
        id=f"create-repo-{name}",
        repo=RepoRef(owner=owner, name=name),
        goal=f"Create a new {language} repository '{name}' with CI/CD: {description}".rstrip(": "),
        acceptance_criteria=[
            "README.md exists",
            "LICENSE exists",
            ".gitignore exists",
            ".github/workflows/ci.yml exists",
        ],
    )
    spec = f"Scaffold a new {language} repository named '{name}' with CI/CD. {description}".strip()
    work_item = WorkItem(
        id=f"{objective.id}-scaffold",
        objective_id=objective.id,
        spec=spec,
        skills=["new-repo"],
    )
    return Plan(objective, [work_item], SkillRegistry([NEW_REPO_SKILL]), provision_repo=True)


CREATE_REPO = PrepackagedRun(
    name="create-repo",
    description="Create and scaffold a new repository for the user.",
    build=_build_create_repo,
)

register_run(CREATE_REPO)
