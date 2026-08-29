"""Live eval: run a real worker agent on the efactory.ai landing page.

Same objective and grader as ``efactory_webpage`` (the offline eval), but backed by a real engine
writing into ``eval/efactory-ai/`` so you can open the result. The engine is chosen from the
model's provider (opencode-style): default ``fireworks/accounts/fireworks/models/kimi-k2-instruct``,
override with ``OSF_MODEL`` (``provider/model`` form).

    pip install -e ".[agent]"
    # put FIREWORKS_API_KEY=... in .env (or the environment)
    python -m evals.efactory_live
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from evals.efactory_webpage import build_objective, grade
from osf.engines import resolve_runtime
from osf.engines.fireworks import DEFAULT_MODEL
from osf.local.directory import DirectoryIsolation
from osf.orchestrator import _worker_prompt
from osf.types import ModelRef

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


async def run():
    load_dotenv()
    override = os.environ.get("OSF_MODEL")
    model = ModelRef.parse(override) if override else DEFAULT_MODEL
    objective = build_objective()

    isolation = DirectoryIsolation(str(EVAL_DIR))
    workspace = await isolation.prepare(objective.repo, branch=f"osf/{objective.id}")

    runtime = resolve_runtime(model)
    session = await runtime.create_session(workspace, role="worker")
    await runtime.prompt(session, _worker_prompt(objective))
    result = await runtime.result(session)

    return model, result, workspace, grade(workspace.path)


def main() -> None:
    model, result, workspace, checks = asyncio.run(run())
    print(f"model: {model}")
    print(f"outcome: {result.outcome}  cost: ${result.cost_usd:.4f}  workspace: {workspace.path}")
    for check in checks:
        print(f"  [{'x' if check.passed else ' '}] {check.criterion}")
    passed = sum(c.passed for c in checks)
    print(f"{passed}/{len(checks)} criteria passed")


if __name__ == "__main__":
    main()
