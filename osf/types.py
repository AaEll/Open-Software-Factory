"""Shared value types used across OSF contracts.

Identifiers are newtype-style aliases so signatures read clearly. Refs are lightweight
frozen records that name external resources (repos, PRs, workspaces, sessions).
"""

from __future__ import annotations

from dataclasses import dataclass

# Identifier aliases. Distinct names document intent even though they are all strings.
ObjectiveId = str
WorkItemId = str
AgentRunId = str
SessionId = str


@dataclass(frozen=True, slots=True)
class RepoRef:
    """A target repository on a forge (e.g. ``owner/name`` on GitHub)."""

    owner: str
    name: str


@dataclass(frozen=True, slots=True)
class PrRef:
    """A pull request on a forge."""

    repo: RepoRef
    number: int


@dataclass(frozen=True, slots=True)
class Workspace:
    """An isolated working copy of a repo, produced by an ``IsolationBackend``.

    ``handle`` is backend-specific (a worktree path locally, a container id in the cloud);
    callers treat it as opaque.
    """

    path: str
    handle: str
