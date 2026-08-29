"""Isolation backends: ``IsolationBackend``.

Gives each agent an isolated working copy of a repo. Tiered and pluggable: git worktrees
locally, containers in the cloud. Callers never know which backend is in use. No concrete
backend exists yet in Phase 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from osf.types import RepoRef, Workspace


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of running a command inside a workspace."""

    exit_code: int
    stdout: str
    stderr: str


class IsolationBackend(Protocol):
    """Prepares, runs commands in, and tears down isolated workspaces."""

    async def prepare(self, repo: RepoRef, branch: str) -> Workspace: ...

    async def exec(self, ws: Workspace, cmd: list[str]) -> ExecResult: ...

    async def cleanup(self, ws: Workspace) -> None: ...
