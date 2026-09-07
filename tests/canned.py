"""Scripted model responses for integration tests.

`tests/test_integration.py` replays *recorded* API bodies, which proves our adapters cope with what
a real model actually returns. This module is the other half: it builds those same bodies from a
short script, so a test can state the exact series of responses a flow should see — including the
ones we would rather not pay to record, like a model that gets it wrong twice and then right.

    script = CannedAgent(
        agent.routes_to_plan(),
        agent.asks("What should it emphasise?"),
        agent.plans("A dog site", [Step("Write index.html", ["index.html"])]),
        agent.writes("index.html", "<h1>Pobrecita</h1>"),
        agent.finishes("Done."),
    )

Both halves share one transport, so a canned test and a recorded test exercise identical code.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from osf.engines.fireworks import BASE_URL


def _body(message: dict[str, Any], *, finish: str = "stop") -> dict[str, Any]:
    """One chat-completion response, shaped exactly as the API returns it."""
    return {
        "id": "canned",
        "object": "chat.completion",
        "created": 0,
        "model": "canned-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def says(text: str) -> dict[str, Any]:
    """A plain text reply — what the planner's calls always return."""
    return _body({"role": "assistant", "content": text})


def calls(name: str, **arguments: Any) -> dict[str, Any]:
    """A single tool call — what a worker turn returns until it stops."""
    return _body(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"call-{name}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
        finish="tool_calls",
    )


class agent:  # noqa: N801 — reads as a sentence at the call site: agent.plans(...)
    """The responses a driver or worker can give, named for what they mean."""

    @staticmethod
    def replies(message: str) -> dict[str, Any]:
        """Routing: this was conversation, not work."""
        return says(json.dumps({"action": "reply", "message": message}))

    @staticmethod
    def routes_to_plan() -> dict[str, Any]:
        """Routing: this is work to plan."""
        return says(json.dumps({"action": "plan"}))

    @staticmethod
    def starts_run(name: str, **params: str) -> dict[str, Any]:
        """Routing: this matches a prepackaged workflow, with what it understood prefilled."""
        return says(json.dumps({"action": "run", "run": name, "params": params}))

    @staticmethod
    def asks(*questions: str) -> dict[str, Any]:
        """Clarifying questions before planning; no questions is a valid answer."""
        return says(json.dumps({"questions": list(questions)}))

    @staticmethod
    def plans(goal: str, steps: Sequence[tuple]) -> dict[str, Any]:
        """A plan. Each step is (spec, files) or (spec, files, check)."""
        rendered = [
            {"spec": step[0], "files": list(step[1]), "check": step[2] if len(step) > 2 else ""}
            for step in steps
        ]
        return says(json.dumps({"goal": goal, "steps": rendered}))

    @staticmethod
    def rambles(text: str = "Sure, I can help with that!") -> dict[str, Any]:
        """A reply that is not JSON — the failure the planner retries through."""
        return says(text)

    # --- worker turns ---------------------------------------------------------------------

    @staticmethod
    def writes(path: str, content: str) -> dict[str, Any]:
        return calls("write_file", path=path, content=content)

    @staticmethod
    def edits(path: str, old: str, new: str) -> dict[str, Any]:
        return calls("edit_file", path=path, old_string=old, new_string=new)

    @staticmethod
    def reads(path: str) -> dict[str, Any]:
        return calls("read_file", path=path)

    @staticmethod
    def searches(pattern: str) -> dict[str, Any]:
        return calls("search_files", pattern=pattern)

    @staticmethod
    def finishes(text: str = "Done.") -> dict[str, Any]:
        return says(text)


class ScriptedClient:
    """Serves prepared chat-completion bodies in order, recording what was asked for."""

    def __init__(self, bodies: Sequence[dict[str, Any]]) -> None:
        self.bodies = list(bodies)
        self.requests: list[dict] = []
        self.exhausted = False

    def client(self):
        pytest.importorskip("openai")
        from openai import OpenAI

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            if not self.bodies:
                # openai re-raises whatever the transport throws as APIConnectionError, which
                # hides the real cause; the flag is checked after the run instead.
                self.exhausted = True
                raise AssertionError("the engine made more calls than the script provides")
            return httpx.Response(200, json=self.bodies.pop(0))

        return OpenAI(
            base_url=BASE_URL,
            api_key="test-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    @property
    def systems(self) -> list[str]:
        return [request["messages"][0]["content"] for request in self.requests]

    @property
    def tools_offered(self) -> list[str]:
        """Tool names the last request advertised, for asserting what a worker could reach."""
        last = self.requests[-1] if self.requests else {}
        return [tool["function"]["name"] for tool in last.get("tools", [])]


class CannedAgent(ScriptedClient):
    """A model that gives exactly the responses a test names, in order."""

    def __init__(self, *bodies: dict[str, Any]) -> None:
        super().__init__(bodies)
