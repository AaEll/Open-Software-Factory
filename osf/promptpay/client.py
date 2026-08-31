"""HTTP client for the PromptPay broker API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8090"


class PromptPayError(RuntimeError):
    """PromptPay returned an HTTP error or unreachable broker."""


@dataclass(frozen=True, slots=True)
class PromptPayClient:
    """Thin wrapper around PromptPay's OSF-facing HTTP endpoints."""

    base_url: str = DEFAULT_URL
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> PromptPayClient:
        return cls(base_url=os.environ.get("PROMPTPAY_URL", DEFAULT_URL))

    def healthz(self) -> bool:
        try:
            body = self._get("/healthz")
        except PromptPayError:
            return False
        return bool(body.get("healthy"))

    def authorize(
        self,
        *,
        objective_id: str,
        vendor: str,
        amount_cents: int,
        purpose: str = "",
        currency: str = "USD",
    ) -> dict[str, Any]:
        return self._post(
            "/authorize",
            {
                "objective_id": objective_id,
                "vendor": vendor,
                "amount_cents": amount_cents,
                "currency": currency,
                "purpose": purpose,
            },
        )

    def domain_preview(self, objective_id: str, domain: str) -> dict[str, Any]:
        return self._post(
            "/domains/preview",
            {"objective_id": objective_id, "domain": domain},
        )

    def publish_preview(
        self,
        files: dict[str, str],
        *,
        objective_id: str | None = None,
        script_name: str | None = None,
        accept_terms: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"files": files, "accept_terms": accept_terms}
        if objective_id is not None:
            body["objective_id"] = objective_id
        if script_name is not None:
            body["script_name"] = script_name
        return self._post("/previews", body)

    def _get(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}{path}",
            method="GET",
        )
        return self._request(req)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._request(req)

    def _request(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            try:
                parsed = json.loads(detail)
                detail = parsed.get("detail", detail)
            except json.JSONDecodeError:
                pass
            raise PromptPayError(f"{req.full_url} failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise PromptPayError(f"{req.full_url} unreachable: {exc.reason}") from exc

        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise PromptPayError(f"{req.full_url} returned non-object JSON")
        return parsed
