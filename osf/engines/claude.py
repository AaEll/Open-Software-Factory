"""Claude-backed `AgentRuntime` (Anthropic API).

A worker agent that edits files in its workspace via a sandboxed `write_file` tool, driven by a
manual tool-use loop on Claude Opus 4.8 with adaptive thinking. This is the first *real* engine
behind the `AgentRuntime` contract, replacing the offline `osf.local.runtime.StaticSiteRuntime`.

Setup: `pip install -e ".[agent]"` and set `ANTHROPIC_API_KEY`. The `anthropic` import is lazy so
the rest of OSF (and the offline eval) has no hard dependency on it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from osf.engines._tools import (
    WORKER_SYSTEM,
    WRITE_TOOL_DESCRIPTION,
    WRITE_TOOL_NAME,
    WRITE_TOOL_PARAMETERS,
    apply_write,
)
from osf.runtime import AgentEvent, AgentResult
from osf.types import ModelRef, SessionId, Workspace

# Default model selection, expressed with opencode's provider/model abstraction.
DEFAULT_MODEL = ModelRef(provider_id="anthropic", model_id="claude-opus-4-8")
# Opus 4.8 list price, USD per token (input / output). Used for rough per-run cost accounting.
_INPUT_COST = 5.0 / 1_000_000
_OUTPUT_COST = 25.0 / 1_000_000
_MAX_STEPS = 12

_WRITE_TOOL = {
    "name": WRITE_TOOL_NAME,
    "description": WRITE_TOOL_DESCRIPTION,
    "input_schema": WRITE_TOOL_PARAMETERS,
}


class ClaudeRuntime:
    """Runs a single worker turn against Claude, writing its output into the workspace."""

    def __init__(self, model: ModelRef = DEFAULT_MODEL) -> None:
        if model.provider_id != "anthropic":
            raise ValueError(f"ClaudeRuntime only serves the 'anthropic' provider, got {model}")
        self._model = model
        self._sessions: dict[SessionId, Workspace] = {}
        self._results: dict[SessionId, AgentResult] = {}
        self._counter = 0

    async def create_session(self, workspace: Workspace, role: str) -> SessionId:
        self._counter += 1
        session = f"claude-{self._counter}"
        self._sessions[session] = workspace
        return session

    async def prompt(self, session: SessionId, text: str) -> None:
        workspace = self._sessions[session]
        self._results[session] = await asyncio.to_thread(self._run_loop, workspace, text)

    async def stream_events(self, session: SessionId) -> AsyncIterator[AgentEvent]:
        for event in self._results[session].transcript:
            yield event

    async def interrupt(self, session: SessionId) -> None:
        return None

    async def result(self, session: SessionId) -> AgentResult:
        return self._results[session]

    def _run_loop(self, workspace: Workspace, prompt: str) -> AgentResult:
        import anthropic  # lazy: keeps the dependency optional

        client = anthropic.Anthropic()
        messages: list[dict] = [{"role": "user", "content": prompt}]
        transcript: list[AgentEvent] = []
        cost = 0.0

        for _ in range(_MAX_STEPS):
            response = client.messages.create(
                model=self._model.model_id,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=WORKER_SYSTEM,
                tools=[_WRITE_TOOL],
                messages=messages,
            )
            cost += response.usage.input_tokens * _INPUT_COST
            cost += response.usage.output_tokens * _OUTPUT_COST
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                return AgentResult(outcome="completed", transcript=transcript, cost_usd=cost)

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                path = block.input["path"]
                message, is_error = apply_write(workspace, path, block.input["content"])
                transcript.append(AgentEvent(kind="file.write", data={"path": path}))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": message,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": results})

        return AgentResult(outcome="failed", transcript=transcript, cost_usd=cost)
