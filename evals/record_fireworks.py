"""Record real Fireworks responses as fixtures for the offline integration tests.

Run this once, deliberately, when the prompts or the expected shapes change:

    python -m evals.record_fireworks

It spends a few cents of real credit and writes `tests/fixtures/fireworks/*.json` — the raw
chat-completion bodies, exactly as the API returned them. `tests/test_integration.py` replays those
bodies through a mock transport, so the whole engine adapter (tool-call parsing included) is
exercised on every CI run without a key and without spending anything again.
"""

from __future__ import annotations

import json
from pathlib import Path

from osf.engines._tools import WORKER_SYSTEM
from osf.engines.fireworks import _TOOLS, BASE_URL, DEFAULT_MODEL, require_api_key
from osf.planner import CLARIFY_SYSTEM, PLAN_SYSTEM, build_messages

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fireworks"
REQUEST = "Create a landing page for my dog Pobrecita"
ANSWERS = [
    ("What is the main purpose of the page?", "a playful profile"),
    ("What sections must be included?", "a short bio and photos"),
]
FEEDBACK = "also add a photo gallery page"
# (fixture name, worker prompt, the file it is expected to write)
WORKER_TURNS = [
    (
        "index",
        "Create a self-contained index.html landing page for Pobrecita the dog.",
        "index.html",
    ),
    (
        "gallery",
        "Create a self-contained gallery.html photo gallery page for Pobrecita.",
        "gallery.html",
    ),
]


def _client():
    from openai import OpenAI

    return OpenAI(base_url=BASE_URL, api_key=require_api_key())


def _exists(name: str) -> bool:
    """Fixtures are recorded once and kept. Delete a file to re-record just that one."""
    if (FIXTURES / f"{name}.json").is_file():
        print(f"keeping {name}.json (delete it to re-record)")
        return True
    return False


def _save(name: str, response) -> None:
    body = response.model_dump(mode="json")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {name}.json")


def _complete(client, system: str, messages: list[dict], max_tokens: int, *, tools=None):
    kwargs = {"tools": tools} if tools else {}
    return client.chat.completions.create(
        model=DEFAULT_MODEL.model_id,
        messages=[{"role": "system", "content": system}, *messages],
        max_tokens=max_tokens,
        **kwargs,
    )


def main() -> None:
    try:  # keys live in .env alongside the other live evals
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    client = _client()

    if not _exists("clarify"):
        questions = _complete(client, CLARIFY_SYSTEM, [{"role": "user", "content": REQUEST}], 500)
        _save("clarify", questions)

    plan_messages = build_messages(REQUEST, (), ANSWERS)
    if not _exists("plan"):
        plan = _complete(client, PLAN_SYSTEM, plan_messages, 2000)
        _save("plan", plan)
        if not _exists("plan_revised"):
            revised = [
                *plan_messages,
                {"role": "assistant", "content": plan.choices[0].message.content},
                {"role": "user", "content": FEEDBACK},
            ]
            _save("plan_revised", _complete(client, PLAN_SYSTEM, revised, 2000))

    # Worker turns: the tool call that writes the file, then the turn that stops. One pair per
    # step of the recorded plan, so an integration test can drive a multi-step plan to merge.
    for name, spec, path in WORKER_TURNS:
        if _exists(f"worker_{name}_write"):
            continue
        messages = [{"role": "user", "content": spec}]
        call = _complete(client, WORKER_SYSTEM, messages, 16000, tools=_TOOLS)
        _save(f"worker_{name}_write", call)
        message = call.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        for tool_call in message.tool_calls or []:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Wrote {path} (1 bytes)",
                }
            )
        done = _complete(client, WORKER_SYSTEM, messages, 16000, tools=_TOOLS)
        _save(f"worker_{name}_done", done)


if __name__ == "__main__":
    main()
