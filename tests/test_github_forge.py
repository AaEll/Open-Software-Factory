"""GitHubForge: request shapes and check-state mapping, driven by a mock httpx transport."""

import asyncio
import json

import httpx
import pytest

from osf.forges.github import GitHubForge
from osf.types import PrRef, RepoRef

REPO = RepoRef(owner="acme", name="widgets")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )


def _forge(handler) -> GitHubForge:
    return GitHubForge(client=_client(handler))


def test_missing_token_raises_without_injected_client(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        GitHubForge()


def test_create_repo_posts_to_user_repos():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(201, json={"name": "widgets"})

    result = asyncio.run(_forge(handler).create_repo(REPO, description="hi"))
    assert result == REPO
    assert seen == {"path": "/user/repos", "method": "POST"}


def test_create_repo_uses_org_endpoint_when_org():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/orgs/acme/repos"
        return httpx.Response(201, json={})

    asyncio.run(GitHubForge(client=_client(handler), org=True).create_repo(REPO))


def test_open_pr_resolves_base_and_returns_number():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/repos/acme/widgets":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path == "/repos/acme/widgets/pulls":
            body = json.loads(request.content)
            assert body["base"] == "main" and body["head"] == "feature"
            return httpx.Response(201, json={"number": 7})
        raise AssertionError(request.url.path)

    pr = asyncio.run(_forge(handler).open_pr(REPO, "feature", "t", "b"))
    assert pr == PrRef(repo=REPO, number=7)


@pytest.mark.parametrize(
    "state,expected",
    [("success", "success"), ("pending", "pending"), ("failure", "failure"), ("error", "failure")],
)
def test_checks_maps_combined_status(state, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/widgets/pulls/7":
            return httpx.Response(200, json={"head": {"sha": "deadbeef"}})
        if request.url.path == "/repos/acme/widgets/commits/deadbeef/status":
            return httpx.Response(200, json={"state": state})
        raise AssertionError(request.url.path)

    status = asyncio.run(_forge(handler).checks(PrRef(repo=REPO, number=7)))
    assert status.state == expected


def test_merge_puts_to_merge_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/repos/acme/widgets/pulls/7/merge"
        return httpx.Response(200, json={"merged": True})

    asyncio.run(_forge(handler).merge(PrRef(repo=REPO, number=7)))
