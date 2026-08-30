"""Packaged pass-through predeployment smoke test.

Runs the whole objective -> worker -> PR -> merge pipeline against the offline local reference
adapters (no network, no keys) and exits non-zero on failure. Exposed as the ``sf-smoke`` console
script so it validates the **installed** artifact (wheel or container image), not just the source
tree — the gate a deploy pipeline runs before shipping.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.local.runtime import StaticSiteRuntime
from osf.model import Objective
from osf.orchestrator import run_objective
from osf.types import RepoRef


async def run_smoke() -> bool:
    """The smoke as a coroutine, so the `sf` shell's /smoke can reuse it."""
    objective = Objective(
        id="smoke",
        repo=RepoRef(owner="osf", name="smoke"),
        goal="Create a simple landing page for smoke.osf",
        acceptance_criteria=["Produces an index.html file"],
    )
    result = await run_objective(
        objective,
        runtime=StaticSiteRuntime(),
        isolation=TempdirIsolation(),
        forge=InMemoryForge(),
    )
    produced = (Path(result.workspace.path) / "index.html").is_file()
    return result.merged and produced


def main() -> None:
    ok = asyncio.run(run_smoke())
    print("sf smoke:", "ok" if ok else "FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
