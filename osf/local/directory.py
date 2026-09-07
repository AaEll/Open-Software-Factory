"""Directory isolation backend (local reference).

Runs an agent directly in a named on-disk directory instead of a throwaway temp repo — used when
you want to inspect the agent's output afterward (e.g. the live eval writing into ``eval/``).
Cleanup is intentionally a no-op so the produced artifacts survive the run.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from osf.isolation import ExecResult
from osf.types import RepoRef, Workspace


class DirectoryIsolation:
    """Prepares workspaces as subdirectories of a fixed base directory."""

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)

    async def prepare(self, repo: RepoRef, branch: str) -> Workspace:
        path = self._base / repo.name
        path.mkdir(parents=True, exist_ok=True)
        return Workspace(path=str(path), handle=str(path))

    async def exec(self, ws: Workspace, cmd: list[str]) -> ExecResult:
        proc = await asyncio.to_thread(
            subprocess.run, cmd, cwd=ws.path, capture_output=True, text=True
        )
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    async def checkpoint(self, ws: Workspace, message: str) -> None:
        return None  # a plain directory has no history to record into

    async def cleanup(self, ws: Workspace) -> None:
        return None
