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
import tempfile
from pathlib import Path

from osf.engines._tools import worker_system
from osf.engines.fireworks import _TOOLS, BASE_URL, DEFAULT_MODEL, require_api_key
from osf.planner import CLARIFY_SYSTEM, PLAN_SYSTEM, ROUTE_SYSTEM, build_messages, route_catalog
from osf.runs import all_runs
from osf.types import Workspace

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


# The three ways the driver can read a message, so the integration tests can drive each path.
ROUTE_CASES = [
    ("route_plan", REQUEST),
    ("route_reply", "hi bot"),
    ("route_run", "make me a new repository called widgets, blank, no CI"),
]


def main() -> None:
    try:  # keys live in .env alongside the other live evals
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    client = _client()

    catalog = route_catalog(
        [(run.name, run.description, [p.name for p in run.params]) for run in all_runs()]
    )
    for name, message in ROUTE_CASES:
        if _exists(name):
            continue
        system = f"{ROUTE_SYSTEM}\n\n{catalog}"
        _save(name, _complete(client, system, [{"role": "user", "content": message}], 500))

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

    # Worker turns. A turn is however many round trips the model takes to finish, so record the
    # whole loop — assuming "tool call, then stop" is exactly the assumption that broke before.
    for name, spec, _path in WORKER_TURNS:
        if _exists(f"worker_{name}_1"):
            continue
        _record_worker(client, name, spec)


def _record_worker(client, name: str, spec: str, *, max_steps: int = 6) -> None:
    # Record against a real (empty) workspace, so the fixture answers the prompt the engine
    # actually sends — working directory and contents included.
    with tempfile.TemporaryDirectory(prefix="osf-record-") as tmp:
        system = worker_system(Workspace(path=tmp, handle=tmp))
    messages: list[dict] = [{"role": "user", "content": spec}]
    for step in range(1, max_steps + 1):
        response = _complete(client, system, messages, 16000, tools=_TOOLS)
        _save(f"worker_{name}_{step}", response)
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            return
        for tool_call in message.tool_calls:
            args = json.loads(tool_call.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Wrote {args['path']} ({len(args['content'])} bytes)",
                }
            )
    print(f"warning: {name} did not stop within {max_steps} steps")


if __name__ == "__main__":
    main()
