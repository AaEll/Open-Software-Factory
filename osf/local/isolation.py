"""Temp-dir isolation backend (local reference).

Each workspace is a throwaway git repo in a temp directory on a fresh branch. This is the
simplest stand-in for the worktree/container backends; it exercises real git so commits and
branches behave like production.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile

from osf.isolation import ExecResult
from osf.types import RepoRef, Workspace

# Committed as the workspace author so git never blocks on missing identity (e.g. in CI).
_GIT_IDENTITY = ["-c", "user.email=osf@local", "-c", "user.name=Open Software Factory"]


class TempdirIsolation:
    """Prepares isolated git workspaces under the system temp dir."""

    async def prepare(self, repo: RepoRef, branch: str) -> Workspace:
        path = tempfile.mkdtemp(prefix=f"osf-{repo.name}-")
        await self._git(path, "init", "-q", "-b", branch)
        return Workspace(path=path, handle=path)

    async def exec(self, ws: Workspace, cmd: list[str]) -> ExecResult:
        proc = await asyncio.to_thread(
            subprocess.run, cmd, cwd=ws.path, capture_output=True, text=True
        )
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    async def checkpoint(self, ws: Workspace, message: str) -> None:
        """Commit the work. A no-op commit (nothing changed this round) is fine and ignored."""
        await self._git(ws.path, "add", "-A")
        await self._git(ws.path, "commit", "-q", "-m", message, allow_failure=True)

    async def cleanup(self, ws: Workspace) -> None:
        await asyncio.to_thread(shutil.rmtree, ws.path, True)

    async def _git(self, path: str, *args: str, allow_failure: bool = False) -> None:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", *_GIT_IDENTITY, *args],
            cwd=path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and not allow_failure:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
