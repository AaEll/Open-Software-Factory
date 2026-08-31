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
    "Create a new file, or replace an existing one wholesale. Paths are relative to the workspace "
    "root; parent directories are created as needed. To change part of a file that already "
    "exists, use edit_file instead — this replaces the whole thing."
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

EDIT_TOOL_NAME = "edit_file"
EDIT_TOOL_DESCRIPTION = (
    "Change part of an existing file by replacing an exact string. This is how you modify a file: "
    "it leaves everything you did not name untouched. `old_string` must appear exactly once "
    "unless replace_all is set."
)
EDIT_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Relative file path"},
        "old_string": {
            "type": "string",
            "description": "Exact text to replace, including indentation",
        },
        "new_string": {"type": "string", "description": "Text to put in its place"},
        "replace_all": {
            "type": "boolean",
            "description": "Replace every occurrence (default false)",
        },
    },
    "required": ["path", "old_string", "new_string"],
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
    "Use edit_file to change a file that already exists: it replaces one exact string and leaves "
    "the rest alone. write_file replaces the entire file, so reach for it only to create something "
    "new. Change what was asked for and leave the rest of the project as you found it — an "
    "unrequested rewrite is a worse outcome than a small change."
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


def apply_edit(
    workspace: Workspace, rel_path: str, old: str, new: str, *, replace_all: bool = False
) -> tuple[str, bool]:
    """Replace an exact string inside a file. Returns (message, is_error).

    Ambiguity is refused rather than guessed at: a match that appears twice could mean either
    place, and picking one silently is how an agent corrupts a file it was asked to improve.
    """
    if not old:
        return ("old_string must not be empty. Use write_file to create a new file.", True)
    if old == new:
        return ("old_string and new_string are identical, so there is nothing to change.", True)

    existing, failed = apply_read(workspace, rel_path)
    if failed:
        return existing, True

    occurrences = existing.count(old)
    if occurrences == 0:
        return (
            f"old_string does not appear in {rel_path}. It must match exactly, including "
            "whitespace and indentation. Read the file again and copy the text you mean.",
            True,
        )
    if occurrences > 1 and not replace_all:
        return (
            f"old_string appears {occurrences} times in {rel_path}. Include more surrounding "
            "text so it matches once, or set replace_all to true.",
            True,
        )

    updated = existing.replace(old, new) if replace_all else existing.replace(old, new, 1)
    Path(workspace.path, rel_path).write_text(updated, encoding="utf-8")
    return (f"Replaced {occurrences if replace_all else 1} occurrence(s) in {rel_path}", False)


class Toolbox:
    """The tools for one worker session, and the policy that governs them.

    The policy is read-before-overwrite: `write_file` may create anything, but it may only replace
    a file the session has already read. An agent that has not read a file cannot know what it is
    destroying, and the prompt asking it to be careful is advice — this is the part that holds.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.seen: set[str] = set()

    def dispatch(self, name: str, args: dict) -> tuple[str, bool, str]:
        """Run one tool call. Returns (message for the model, is_error, transcript event kind)."""
        path = args.get("path", "")
        if name == READ_TOOL_NAME:
            message, failed = apply_read(self.workspace, path)
            if not failed:
                self.seen.add(path)
            return message, failed, "file.read"
        if name == EDIT_TOOL_NAME:
            message, failed = apply_edit(
                self.workspace,
                path,
                args.get("old_string", ""),
                args.get("new_string", ""),
                replace_all=bool(args.get("replace_all")),
            )
            if not failed:
                self.seen.add(path)
            return message, failed, "file.edit"
        if name == WRITE_TOOL_NAME:
            if Path(self.workspace.path, path).is_file() and path not in self.seen:
                return (
                    f"{path} already exists and you have not read it. Overwriting it would throw "
                    "away whatever is in it. read_file it first, then edit_file the part you mean "
                    "to change.",
                    True,
                    "file.refused",
                )
            message, failed = apply_write(self.workspace, path, args.get("content", ""))
            if not failed:
                self.seen.add(path)
            return message, failed, "file.write"
        return f"Unknown tool {name}", True, "error"
