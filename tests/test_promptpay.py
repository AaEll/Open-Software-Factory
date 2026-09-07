"""PromptPay integration helpers (offline — no broker required)."""

from pathlib import Path

import pytest

from osf.promptpay.client import PromptPayClient, PromptPayError
from osf.promptpay.files import collect_site_files
from osf.promptpay.publish import publish_objective_site
from osf.types import Workspace


def test_collect_site_files_normalizes_paths(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body{}", encoding="utf-8")

    files = collect_site_files(tmp_path)

    assert "/index.html" in files
    assert "/styles.css" in files


def test_collect_site_files_empty_workspace_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="no publishable"):
        collect_site_files(tmp_path)


def test_promptpay_client_http_error():
    import json
    import urllib.error

    client = PromptPayClient(base_url="http://example.test")

    def raise_http(req, timeout=0):  # noqa: ARG001
        err = urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )
        err.read = lambda: json.dumps({"detail": "denied"}).encode()
        raise err

    import urllib.request

    original = urllib.request.urlopen
    urllib.request.urlopen = raise_http
    try:
        with pytest.raises(PromptPayError, match="denied"):
            client.authorize(objective_id="x", vendor="cloudflare", amount_cents=100)
    finally:
        urllib.request.urlopen = original


def test_publish_objective_site_calls_endpoints(monkeypatch, tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        base_url = "http://fake"

        def domain_preview(self, objective_id: str, domain: str) -> dict:
            calls.append(("domain", {"objective_id": objective_id, "domain": domain}))
            return {"name": domain, "message": "ok", "policy_approved": True}

        def publish_preview(self, files, *, objective_id, script_name, accept_terms):  # noqa: ANN001
            calls.append(
                (
                    "preview",
                    {
                        "objective_id": objective_id,
                        "script_name": script_name,
                        "accept_terms": accept_terms,
                        "file_count": len(files),
                    },
                )
            )
            return {"preview_url": "https://preview.example", "claim_url": "https://claim.example"}

    result = publish_objective_site(
        Workspace(path=str(tmp_path), handle=str(tmp_path)),
        objective_id="mvp-demo",
        domain="example.com",
        script_name="example-com",
        client=FakeClient(),  # type: ignore[arg-type]
    )

    assert result.preview["preview_url"] == "https://preview.example"
    assert calls[0][0] == "domain"
    assert calls[1][0] == "preview"
    assert calls[1][1]["file_count"] == 1
