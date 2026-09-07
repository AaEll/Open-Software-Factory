"""MVP integration: OSF worker generates a site, PromptPay publishes the preview.

Requires PromptPay running (default http://127.0.0.1:8090). Configure via ``.env``:

    cp .env.example .env
    # FIREWORKS_API_KEY=...   (optional — uses scripted worker when unset)
    # PROMPTPAY_URL=http://127.0.0.1:8090
    # OSF_PROMPT=...
    # OSF_DOMAIN=example.com
    # OSF_OBJECTIVE_ID=mvp-demo

Run:

    pip install -e ".[agent]"
    python -m evals.mvp_promptpay
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from osf.engines import resolve_runtime
from osf.engines.fireworks import DEFAULT_MODEL
from osf.local.isolation import TempdirIsolation
from osf.local.runtime import StaticSiteRuntime
from osf.model import Objective
from osf.orchestrator import _worker_prompt
from osf.promptpay import PromptPayClient, PromptPayError, publish_objective_site
from osf.types import ModelRef, RepoRef


def _build_objective(prompt: str, objective_id: str) -> Objective:
    return Objective(
        id=objective_id,
        repo=RepoRef(owner="osf", name="mvp-site"),
        goal=prompt,
        acceptance_criteria=[
            "Produces an index.html file",
            "Is a valid HTML5 document",
        ],
    )


def _resolve_runtime():
    if os.environ.get("FIREWORKS_API_KEY") or os.environ.get("FIREWORKS"):
        override = os.environ.get("OSF_MODEL")
        model = ModelRef.parse(override) if override else DEFAULT_MODEL
        return resolve_runtime(model), str(model)
    return StaticSiteRuntime(), "local/static-site"


async def run() -> int:
    load_dotenv()

    prompt = os.environ.get(
        "OSF_PROMPT",
        "A landing page for a specialty coffee shop in Denver called Mountain Brew",
    )
    domain = os.environ.get("OSF_DOMAIN", "42protein.com")
    objective_id = os.environ.get("OSF_OBJECTIVE_ID", "mvp-demo")
    script_name = os.environ.get("OSF_SCRIPT_NAME") or domain.replace(".", "-")[:20]

    client = PromptPayClient.from_env()
    if not client.healthz():
        print(f"PromptPay not reachable at {client.base_url}", file=sys.stderr)
        print("Start it: uvicorn promptpay.app:default_app --factory --port 8090", file=sys.stderr)
        return 1

    objective = _build_objective(prompt, objective_id)
    runtime, runtime_label = _resolve_runtime()
    isolation = TempdirIsolation()
    branch = f"osf/{objective.id}"
    workspace = await isolation.prepare(objective.repo, branch)

    print(f"runtime: {runtime_label}")
    print(f"objective: {objective.id}")
    print(f"prompt: {prompt!r}")

    session = await runtime.create_session(workspace, role="worker")
    await runtime.prompt(session, _worker_prompt(objective))
    agent = await runtime.result(session)
    print(f"worker outcome: {agent.outcome}  cost: ${agent.cost_usd:.4f}")

    try:
        result = publish_objective_site(
            workspace,
            objective_id=objective_id,
            domain=domain,
            script_name=script_name,
            client=client,
        )
    except (PromptPayError, ValueError, FileNotFoundError) as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1

    print(f"files published: {len(result.files)}")
    if result.domain_preview:
        dp = result.domain_preview
        print(f"domain: {dp.get('name')} — {dp.get('message')}")
        cost = dp.get("cost_cents", 0) / 100
        print(f"  policy_approved={dp.get('policy_approved')} cost=${cost:.2f}")

    preview = result.preview
    print(f"preview_url: {preview.get('preview_url')}")
    print(f"claim_url:   {preview.get('claim_url')}")
    print(f"expires_at:  {preview.get('expires_at')}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
