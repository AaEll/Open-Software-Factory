"""Project isolation — run agents in the user's own repository.

The opencode model: you launch the tool inside your project and it edits *that* working tree, in
place. No clone, no throwaway temp repo, no branch. Steps therefore share one workspace and can
build on each other, which a per-step tempdir could never do.

Safety comes from snapshots rather than isolation. Before a run we capture the working tree as a
content-addressed git tree; afterwards we can diff against it and, if the user doesn't want the
result, restore exactly the files that changed. Captures use a scratch `GIT_INDEX_FILE` under
`.git/`, so nothing here disturbs the user's own staging area — the trick opencode uses.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from osf.isolation import ExecResult
from osf.types import RepoRef, Workspace

SNAPSHOT_INDEX = "osf-snapshot.index"


class NotARepository(RuntimeError):
    """The chosen project directory is not a git repository."""


def repo_root(start: str | Path | None = None) -> Path | None:
    """The git worktree root containing `start` (default: the working directory)."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start or Path.cwd()),
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def git_init(path: str | Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)


class ProjectIsolation:
    """A workspace that *is* the user's repository. Every step shares it."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        if repo_root(self.root) is None:
            raise NotARepository(f"{self.root} is not a git repository")

    async def prepare(self, repo: RepoRef, branch: str) -> Workspace:
        """Hand back the project itself. The branch is ignored — we don't move the user's HEAD."""
        return Workspace(path=str(self.root), handle=str(self.root))

    async def exec(self, ws: Workspace, cmd: list[str]) -> ExecResult:
        proc = await asyncio.to_thread(
            subprocess.run, cmd, cwd=ws.path, capture_output=True, text=True
        )
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def exec_sync(self, cmd: list[str]) -> str:
        """Run a command in the project and return its output (used for `/diff`)."""
        proc = subprocess.run(cmd, cwd=str(self.root), capture_output=True, text=True)
        return proc.stdout

    async def checkpoint(self, ws: Workspace, message: str) -> None:
        """Record progress without committing — the user's history stays theirs to write."""
        await asyncio.to_thread(self.snapshot, ws)

    async def cleanup(self, ws: Workspace) -> None:
        return None  # the user's repo is not ours to delete

    # --- snapshots ------------------------------------------------------------------------

    def snapshot(self, ws: Workspace) -> str:
        """Capture the working tree as a git tree object and return its id."""
        self._git(ws, "add", "-A", scratch=True)
        return self._git(ws, "write-tree", scratch=True).stdout.strip()

    def changed_since(self, ws: Workspace, tree: str) -> list[str]:
        """Paths that differ between a captured tree and the working tree now."""
        now = self.snapshot(ws)
        out = self._git(ws, "diff", "--name-only", tree, now, scratch=True).stdout
        return [line for line in out.splitlines() if line]

    def diff_since(self, ws: Workspace, tree: str, *, stat: bool = False) -> str:
        now = self.snapshot(ws)
        flag = "--stat" if stat else "--patch"
        return self._git(ws, "diff", flag, tree, now, scratch=True).stdout

    def restore(self, ws: Workspace, tree: str) -> list[str]:
        """Put the working tree back to a captured snapshot. Returns the paths it touched."""
        now = self.snapshot(ws)
        listing = self._git(ws, "diff", "--name-status", tree, now, scratch=True).stdout
        touched = []
        for line in listing.splitlines():
            if not line:
                continue
            status, _, path = line.partition("\t")
            path = path.strip()
            if status.startswith("A"):  # added since the snapshot -> it should not exist
                Path(ws.path, path).unlink(missing_ok=True)
            else:  # modified or deleted -> take the snapshot's version back
                self._git(ws, "checkout", tree, "--", path)
            touched.append(path)
        return touched

    # --- plumbing -------------------------------------------------------------------------

    def _git(self, ws: Workspace, *args: str, scratch: bool = False) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if scratch:
            # A private index, so `git add -A` here never touches what the user has staged.
            env["GIT_INDEX_FILE"] = str(self._git_dir(ws) / SNAPSHOT_INDEX)
        result = subprocess.run(
            ["git", *args], cwd=ws.path, capture_output=True, text=True, env=env
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def _git_dir(self, ws: Workspace) -> Path:
        result = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=ws.path,
            capture_output=True,
            text=True,
        )
        return Path(result.stdout.strip() or Path(ws.path) / ".git")
