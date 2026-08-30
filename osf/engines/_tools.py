"""Shared worker-tool plumbing used by every engine adapter.

The single tool a worker needs for the walking skeleton is ``write_file``, sandboxed to the
workspace. Each engine wraps this in its own provider-native tool schema (Anthropic vs OpenAI),
but the parameter shape and the actual write live here so behavior stays identical across engines.
"""

from __future__ import annotations

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

WORKER_SYSTEM = (
    "You are a worker agent in an autonomous software factory. You implement the requested change "
    "by writing files with the write_file tool, then stop. Keep the implementation simple and "
    "self-contained. Do not ask questions or explain at length — just build it.\n"
    "You are already inside the project's root directory. Write paths relative to it — "
    "`README.md`, `src/app.py` — and never create a folder named after the project itself; that "
    "buries the work one level down, where nothing will find it."
)


def apply_write(workspace: Workspace, rel_path: str, content: str) -> tuple[str, bool]:
    """Write ``content`` to ``rel_path`` inside the workspace. Returns (message, is_error)."""
    root = Path(workspace.path).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        return f"Refused: {rel_path} escapes the workspace", True
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {rel_path} ({len(content)} bytes)", False
