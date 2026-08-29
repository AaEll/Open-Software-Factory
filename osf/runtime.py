"""The open agent API: ``AgentRuntime``.

Engine-agnostic interface for running an agent session, modeled on opencode's session/event
API (create -> prompt -> stream -> interrupt -> result). Concrete adapters (opencode headless
server first, Claude Agent SDK later) implement this. No adapter exists yet in Phase 0.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from osf.types import SessionId, Workspace


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """A single streamed event from a running session (message, tool call, status, ...)."""

    kind: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Terminal state of a session."""

    outcome: Literal["completed", "failed", "interrupted"]
    transcript: list[AgentEvent]
    cost_usd: float


class AgentRuntime(Protocol):
    """Runs agent sessions inside a prepared workspace."""

    async def create_session(self, workspace: Workspace, role: str) -> SessionId: ...

    async def prompt(self, session: SessionId, text: str) -> None: ...

    def stream_events(self, session: SessionId) -> AsyncIterator[AgentEvent]: ...

    async def interrupt(self, session: SessionId) -> None: ...

    async def result(self, session: SessionId) -> AgentResult: ...
