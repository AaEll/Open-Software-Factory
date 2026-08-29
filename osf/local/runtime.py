"""Scripted static-site runtime (local reference).

A stand-in ``AgentRuntime`` that deterministically produces a simple landing page from the
prompt, with no LLM or network. It lets the end-to-end eval exercise the full pipeline shape
(session -> prompt -> produced files -> result) offline. A real engine adapter (opencode
headless server) replaces this.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

from osf.runtime import AgentEvent, AgentResult
from osf.types import SessionId, Workspace

_DOMAIN = re.compile(r"\b([a-z0-9][a-z0-9-]*\.[a-z]{2,})\b", re.IGNORECASE)


class StaticSiteRuntime:
    """Writes an ``index.html`` landing page for the brand named in the prompt."""

    def __init__(self) -> None:
        self._sessions: dict[SessionId, Workspace] = {}
        self._results: dict[SessionId, AgentResult] = {}
        self._counter = 0

    async def create_session(self, workspace: Workspace, role: str) -> SessionId:
        self._counter += 1
        session = f"local-{self._counter}"
        self._sessions[session] = workspace
        return session

    async def prompt(self, session: SessionId, text: str) -> None:
        workspace = self._sessions[session]
        match = _DOMAIN.search(text)
        brand = match.group(1) if match else "the site"
        (Path(workspace.path) / "index.html").write_text(_render(brand), encoding="utf-8")
        self._results[session] = AgentResult(
            outcome="completed",
            transcript=[AgentEvent(kind="file.write", data={"path": "index.html"})],
            cost_usd=0.0,
        )

    async def stream_events(self, session: SessionId) -> AsyncIterator[AgentEvent]:
        for event in self._results[session].transcript:
            yield event

    async def interrupt(self, session: SessionId) -> None:
        return None

    async def result(self, session: SessionId) -> AgentResult:
        return self._results[session]


def _render(brand: str) -> str:
    title = brand
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; color: #111; }}
    header {{ padding: 4rem 1.5rem; text-align: center; background: #0b1020; color: #fff; }}
    h1 {{ font-size: 2.5rem; margin: 0 0 0.5rem; }}
    p.tagline {{ font-size: 1.15rem; opacity: 0.85; margin: 0; }}
    main {{ max-width: 720px; margin: 0 auto; padding: 3rem 1.5rem; }}
    a.cta {{ display: inline-block; padding: 0.75rem 1.5rem; background: #4f46e5;
             color: #fff; text-decoration: none; border-radius: 6px; }}
  </style>
</head>
<body>
  <header>
    <h1>{brand}</h1>
    <p class="tagline">Ship software autonomously.</p>
  </header>
  <main>
    <p>{brand} runs a factory of AI agents that turn objectives into merged pull requests.</p>
    <p><a class="cta" href="#get-started">Get started</a></p>
  </main>
</body>
</html>
"""
