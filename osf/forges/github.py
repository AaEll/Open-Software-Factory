"""GitHub-backed `Forge` (REST API).

Implements the `Forge` protocol against api.github.com so the driver can create repos, open PRs,
comment, read checks, and merge for real. The httpx client is injectable, so this is unit-tested
offline with a mock transport; in production it authenticates with `GITHUB_TOKEN`/`GH_TOKEN`.
"""

from __future__ import annotations

import os

import httpx

from osf.forge import ChecksStatus
from osf.types import PrRef, RepoRef

API = "https://api.github.com"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


class GitHubForge:
    """Drives GitHub via REST. Set ``org=True`` to create repos under an organization."""

    def __init__(
        self,
        *,
        token: str | None = None,
        org: bool = False,
        client: httpx.AsyncClient | None = None,
        base_url: str = API,
    ) -> None:
        self._org = org
        if client is not None:
            self._client = client
        else:
            token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if not token:
                raise RuntimeError("set GITHUB_TOKEN or GH_TOKEN for GitHubForge")
            self._client = httpx.AsyncClient(
                base_url=base_url, headers=_headers(token), timeout=30.0
            )
        self._default_branch: dict[str, str] = {}

    async def create_repo(
        self, repo: RepoRef, *, private: bool = True, description: str = ""
    ) -> RepoRef:
        path = f"/orgs/{repo.owner}/repos" if self._org else "/user/repos"
        # auto_init creates an initial commit so a default branch exists for PR bases.
        resp = await self._client.post(
            path,
            json={
                "name": repo.name,
                "private": private,
                "description": description,
                "auto_init": True,
            },
        )
        resp.raise_for_status()
        return repo

    async def open_pr(self, repo: RepoRef, branch: str, title: str, body: str) -> PrRef:
        base = await self._base_branch(repo)
        resp = await self._client.post(
            f"/repos/{repo.owner}/{repo.name}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body},
        )
        resp.raise_for_status()
        return PrRef(repo=repo, number=resp.json()["number"])

    async def comment(self, pr: PrRef, body: str, *, review: bool = False) -> None:
        resp = await self._client.post(
            f"/repos/{pr.repo.owner}/{pr.repo.name}/issues/{pr.number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()

    async def checks(self, pr: PrRef) -> ChecksStatus:
        slug = f"{pr.repo.owner}/{pr.repo.name}"
        pull = await self._client.get(f"/repos/{slug}/pulls/{pr.number}")
        pull.raise_for_status()
        sha = pull.json()["head"]["sha"]
        status = await self._client.get(f"/repos/{slug}/commits/{sha}/status")
        status.raise_for_status()
        state = status.json().get("state", "pending")
        if state == "success":
            return ChecksStatus(state="success")
        if state == "pending":
            return ChecksStatus(state="pending")
        return ChecksStatus(state="failure")  # failure / error

    async def merge(self, pr: PrRef) -> None:
        resp = await self._client.put(
            f"/repos/{pr.repo.owner}/{pr.repo.name}/pulls/{pr.number}/merge", json={}
        )
        resp.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _base_branch(self, repo: RepoRef) -> str:
        slug = f"{repo.owner}/{repo.name}"
        if slug not in self._default_branch:
            resp = await self._client.get(f"/repos/{slug}")
            resp.raise_for_status()
            self._default_branch[slug] = resp.json().get("default_branch", "main")
        return self._default_branch[slug]
