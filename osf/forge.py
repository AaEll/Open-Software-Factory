"""Forge integration: ``Forge``.

The forge (GitHub first, GitLab later) is OSF's coordination substrate: PRs, review comments,
checks, and merges are the durable, human-observable medium through which drivers and workers
coordinate. No concrete adapter exists yet in Phase 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from osf.types import PrRef, RepoRef


@dataclass(frozen=True, slots=True)
class ChecksStatus:
    """Aggregate CI/check state for a PR."""

    state: Literal["pending", "success", "failure"]


class Forge(Protocol):
    """Creates repos, opens PRs, posts (review) comments, reads checks, and merges."""

    async def create_repo(
        self, repo: RepoRef, *, private: bool = True, description: str = ""
    ) -> RepoRef: ...

    async def open_pr(self, repo: RepoRef, branch: str, title: str, body: str) -> PrRef: ...

    async def comment(self, pr: PrRef, body: str, *, review: bool = False) -> None: ...

    async def checks(self, pr: PrRef) -> ChecksStatus: ...

    async def merge(self, pr: PrRef) -> None: ...
