"""Fireworks-backed `AgentRuntime` (OpenAI-compatible API).

Fireworks serves open models behind an OpenAI-compatible endpoint, so this adapter drives it with
the `openai` SDK pointed at Fireworks' base URL and a standard function-calling loop. Model
selection uses opencode's `provider/model` `ModelRef` (`provider_id="fireworks"`).

Setup: `pip install -e ".[agent]"` and set `FIREWORKS_API_KEY` (e.g. in `.env`).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Sequence

from osf.engines._tools import (
    EDIT_TOOL_DESCRIPTION,
    EDIT_TOOL_NAME,
    EDIT_TOOL_PARAMETERS,
    READ_TOOL_DESCRIPTION,
    READ_TOOL_NAME,
    READ_TOOL_PARAMETERS,
    WRITE_TOOL_DESCRIPTION,
    WRITE_TOOL_NAME,
    WRITE_TOOL_PARAMETERS,
    Toolbox,
    worker_system,
)
from osf.planner import (
    CLARIFY_SYSTEM,
    ROUTE_SYSTEM,
    Answer,
    Decision,
    Exchange,
    ProposedPlan,
    parse_decision,
    parse_questions,
    propose_with_retry,
)
from osf.runtime import AgentEvent, AgentResult
from osf.types import ModelRef, SessionId, Workspace

BASE_URL = "https://api.fireworks.ai/inference/v1"
# Overridable via OSF_MODEL; confirm the exact id against your Fireworks account.
DEFAULT_MODEL = ModelRef(
    provider_id="fireworks", model_id="accounts/fireworks/models/kimi-k2p7-code"
)
_MAX_STEPS = 12
_KEY_VARS = ("FIREWORKS_API_KEY", "FIREWORKS")


def api_key() -> str | None:
    """The Fireworks key, if the environment (or a loaded `.env`) has one."""
    for var in _KEY_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def require_api_key() -> str:
    key = api_key()
    if not key:
        raise RuntimeError("set FIREWORKS_API_KEY (or FIREWORKS) in the environment or .env")
    return key

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": WRITE_TOOL_NAME,
            "description": WRITE_TOOL_DESCRIPTION,
            "parameters": WRITE_TOOL_PARAMETERS,
        },
    },
    {
        "type": "function",
        "function": {
            "name": READ_TOOL_NAME,
            "description": READ_TOOL_DESCRIPTION,
            "parameters": READ_TOOL_PARAMETERS,
        },
    },
    {
        "type": "function",
        "function": {
            "name": EDIT_TOOL_NAME,
            "description": EDIT_TOOL_DESCRIPTION,
            "parameters": EDIT_TOOL_PARAMETERS,
        },
    },
]


def make_client(client: object | None = None) -> object:
    """The OpenAI-compatible client to call Fireworks with.

    An injected client is used as-is, which is how the integration tests replay recorded responses
    through a mock transport instead of spending credits.
    """
    if client is not None:
        return client
    from openai import OpenAI  # lazy: keeps the dependency optional

    return OpenAI(base_url=BASE_URL, api_key=require_api_key())


class FireworksRuntime:
    """Runs a single worker turn against a Fireworks-hosted model, writing into the workspace."""

    def __init__(self, model: ModelRef = DEFAULT_MODEL, *, client: object | None = None) -> None:
        if model.provider_id != "fireworks":
            raise ValueError(f"FireworksRuntime only serves the 'fireworks' provider, got {model}")
        self._model = model
        self._client = client
        self._sessions: dict[SessionId, Workspace] = {}
        self._results: dict[SessionId, AgentResult] = {}
        self._counter = 0

    async def create_session(self, workspace: Workspace, role: str) -> SessionId:
        self._counter += 1
        session = f"fireworks-{self._counter}"
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
        client = make_client(self._client)
        toolbox = Toolbox(workspace)
        messages: list[dict] = [
            {"role": "system", "content": worker_system(workspace)},
            {"role": "user", "content": prompt},
        ]
        transcript: list[AgentEvent] = []

        for _ in range(_MAX_STEPS):
            response = client.chat.completions.create(
                model=self._model.model_id,
                messages=messages,
                tools=_TOOLS,
                max_tokens=16000,
            )
            message = response.choices[0].message
            messages.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                return AgentResult(outcome="completed", transcript=transcript, cost_usd=0.0)

            for call in message.tool_calls:
                args = json.loads(call.function.arguments)
                outcome, is_error, kind = toolbox.dispatch(call.function.name, args)
                transcript.append(AgentEvent(kind=kind, data={"path": args.get("path", "")}))
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": outcome}
                )
                if is_error:
                    transcript.append(AgentEvent(kind="error", data={"message": outcome}))

        return AgentResult(outcome="failed", transcript=transcript, cost_usd=0.0)


class FireworksPlanner:
    """The driver agent: asks what it needs to know, then plans.

    Both are plain completions against the same Fireworks model the workers use — no tools, no
    workspace — so planning is cheap next to a worker run.
    """

    def __init__(self, model: ModelRef = DEFAULT_MODEL, *, client: object | None = None) -> None:
        self._model = model
        self._client = client

    def route(self, request: str, catalog: str = "", context: str = "") -> Decision:
        system = "\n\n".join(part for part in (ROUTE_SYSTEM, catalog, context) if part)
        return parse_decision(self._complete(system, [{"role": "user", "content": request}], 500))

    def clarify(self, request: str, context: str = "") -> list[str]:
        system = f"{CLARIFY_SYSTEM}\n\n{context}" if context else CLARIFY_SYSTEM
        return parse_questions(self._complete(system, [{"role": "user", "content": request}], 500))

    def propose(
        self,
        request: str,
        exchanges: Sequence[Exchange] = (),
        answers: Sequence[Answer] = (),
        *,
        shared_workspace: bool = False,
        context: str = "",
    ) -> ProposedPlan:
        return propose_with_retry(
            self._complete,
            request,
            exchanges,
            answers,
            shared_workspace=shared_workspace,
            context=context,
        )

    def _complete(self, system: str, messages: list[dict], max_tokens: int) -> str:
        client = make_client(self._client)
        response = client.chat.completions.create(
            model=self._model.model_id,
            messages=[{"role": "system", "content": system}, *messages],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""
