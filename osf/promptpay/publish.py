"""High-level OSF → PromptPay publish flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from osf.promptpay.client import PromptPayClient
from osf.promptpay.files import collect_site_files
from osf.types import Workspace


@dataclass(frozen=True, slots=True)
class PublishResult:
    objective_id: str
    files: dict[str, str]
    preview: dict[str, Any]
    domain_preview: dict[str, Any] | None = None


def publish_objective_site(
    workspace: Workspace,
    *,
    objective_id: str,
    domain: str | None = None,
    script_name: str | None = None,
    client: PromptPayClient | None = None,
) -> PublishResult:
    """Publish a worker workspace to PromptPay (optional domain check + Cloudflare preview)."""
    broker = client or PromptPayClient.from_env()
    files = collect_site_files(workspace.path)

    domain_preview = None
    if domain:
        domain_preview = broker.domain_preview(objective_id, domain)

    preview = broker.publish_preview(
        files,
        objective_id=objective_id,
        script_name=script_name,
        accept_terms=True,
    )
    return PublishResult(
        objective_id=objective_id,
        files=files,
        preview=preview,
        domain_preview=domain_preview,
    )
