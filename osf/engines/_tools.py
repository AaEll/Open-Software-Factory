"""Shared worker-tool plumbing used by every engine adapter.

The single tool a worker needs for the walking skeleton is ``write_file``, sandboxed to the
workspace. Each engine wraps this in its own provider-native tool schema (Anthropic vs OpenAI),
but the parameter shape and the actual write live here so behavior stays identical across engines.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from osf.types import Workspace

WRITE_TOOL_NAME = "write_file"
WRITE_TOOL_DESCRIPTION = (
    "Create or overwrite a file in the project workspace. Paths are relative to the workspace "
    "root; parent directories are created as needed."
)
WRITE_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Relative file path, e.g. index.html"},
        "content": {"type": "string", "description": "Full file contents to write"},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}

READ_TOOL_NAME = "read_file"
READ_TOOL_DESCRIPTION = (
    "Read a file from the project workspace. Use this before changing an existing file, so you "
    "keep what should stay."
)
READ_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "Relative file path"}},
    "required": ["path"],
    "additionalProperties": False,
}

WORKER_SYSTEM = (
    "You are a worker agent in an autonomous software factory. You implement the requested change "
    "by writing files with the write_file tool, then stop. Keep the implementation simple and "
    "self-contained. Do not ask questions or explain at length — just build it.\n"
    "You are already inside the project's root directory. Write paths relative to it — "
    "`README.md`, `src/app.py` — and never create a folder named after the project itself; that "
    "buries the work one level down, where nothing will find it.\n"
    "write_file replaces a file wholesale, so read_file anything that already exists before you "
    "write it and carry over everything that should survive. Silently dropping the user's content "
    "is worse than not making the change."
)

# How many entries of the project to show. Enough to orient in a small repo, short enough not to
# crowd out the actual request in the prompt.
LISTING_LIMIT = 40


def workspace_listing(workspace: Workspace, *, limit: int = LISTING_LIMIT) -> list[str]:
    """The files already in the workspace, as the worker would refer to them.

    Uses git's own view when there is one, so ignored junk (`node_modules/`, `.env`) stays out of
    the prompt; falls back to a plain walk for a directory git doesn't know about yet.
    """
    root = Path(workspace.path)
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        paths = [line for line in result.stdout.splitlines() if line]
    else:
        paths = [
            str(path.relative_to(root))
            for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.parts
        ]
    return sorted(paths)[:limit]


def worker_system(workspace: Workspace) -> str:
    """The worker prompt, told where it is working and what is already there."""
    listing = workspace_listing(workspace)
    if listing:
        contents = "It already contains:\n" + "\n".join(f"- {path}" for path in listing)
        contents += "\nRead any of these you are about to change."
    else:
        contents = "It is empty."
    return f"{WORKER_SYSTEM}\n\nYour working directory is {workspace.path}. {contents}"


def apply_write(workspace: Workspace, rel_path: str, content: str) -> tuple[str, bool]:
    """Write ``content`` to ``rel_path`` inside the workspace. Returns (message, is_error)."""
    root = Path(workspace.path).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        return f"Refused: {rel_path} escapes the workspace", True
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {rel_path} ({len(content)} bytes)", False


def apply_read(workspace: Workspace, rel_path: str) -> tuple[str, bool]:
    """Read ``rel_path`` from inside the workspace. Returns (content or message, is_error)."""
    root = Path(workspace.path).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        return f"Refused: {rel_path} escapes the workspace", True
    if not target.is_file():
        return f"{rel_path} does not exist", True
    try:
        return target.read_text(encoding="utf-8"), False
    except UnicodeDecodeError:
        return f"{rel_path} is not a text file", True
