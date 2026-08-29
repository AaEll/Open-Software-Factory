"""Eval case: build a simple landing page for efactory.ai.

Runs the full pipeline end-to-end against the local reference stack and grades the produced
site against acceptance criteria. Run directly (`python -m evals.efactory_webpage`) for a
report, or via `tests/test_eval_efactory.py` in CI.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.local.runtime import StaticSiteRuntime
from osf.model import Objective
from osf.orchestrator import ObjectiveResult, run_objective
from osf.types import RepoRef

BRAND = "efactory.ai"


def build_objective() -> Objective:
    return Objective(
        id="efactory-webpage",
        repo=RepoRef(owner="efactory", name="site"),
        goal=f"Create a simple landing page for {BRAND}",
        acceptance_criteria=[
            "Produces an index.html file",
            "Is a valid HTML5 document",
            f"Mentions the {BRAND} brand",
            "Has a page title, a headline, and a call to action",
        ],
    )


@dataclass(frozen=True, slots=True)
class Check:
    criterion: str
    passed: bool


def grade(workspace_path: str) -> list[Check]:
    index = Path(workspace_path) / "index.html"
    exists = index.is_file()
    html = index.read_text(encoding="utf-8") if exists else ""
    lowered = html.lower()
    return [
        Check("Produces an index.html file", exists),
        Check(
            "Is a valid HTML5 document",
            lowered.startswith("<!doctype html>") and "<html" in lowered and "</html>" in lowered,
        ),
        Check(f"Mentions the {BRAND} brand", BRAND in html),
        Check(
            "Has a page title, a headline, and a call to action",
            "<title>" in lowered and "<h1" in lowered and 'class="cta"' in lowered,
        ),
    ]


async def run() -> tuple[ObjectiveResult, list[Check]]:
    result = await run_objective(
        build_objective(),
        runtime=StaticSiteRuntime(),
        isolation=TempdirIsolation(),
        forge=InMemoryForge(),
    )
    return result, grade(result.workspace.path)


def main() -> None:
    result, checks = asyncio.run(run())
    print(f"objective: {result.objective_id}")
    print(f"PR #{result.pr.number} merged={result.merged}  workspace={result.workspace.path}")
    for check in checks:
        print(f"  [{'x' if check.passed else ' '}] {check.criterion}")
    passed = sum(c.passed for c in checks)
    print(f"{passed}/{len(checks)} criteria passed")
    # Exit non-zero so this doubles as a predeployment smoke test (pass-through pipeline).
    if not result.merged or passed != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
