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
class ModelRef:
    """A provider-scoped model selection, mirroring opencode's ``Model.Ref``.

    A model is always identified as ``provider_id`` + ``model_id`` (e.g. ``anthropic`` /
    ``claude-opus-4-8``), and rendered as the ``provider/model`` string agents reference. Keeping
    provider separate from model is what lets engines stay swappable.
    """

    provider_id: str
    model_id: str

    @classmethod
    def parse(cls, ref: str) -> ModelRef:
        """Parse a ``provider/model`` string. The model id may itself contain slashes."""
        provider, _, model = ref.partition("/")
        if not provider or not model:
            raise ValueError(f"invalid model ref {ref!r}; expected 'provider/model'")
        return cls(provider_id=provider, model_id=model)

    def __str__(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


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
