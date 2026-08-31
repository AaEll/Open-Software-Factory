"""Collect workspace artifacts for PromptPay preview/hosting APIs."""

from __future__ import annotations

from pathlib import Path

_TEXT_SUFFIXES = {
    ".html",
    ".htm",
    ".css",
    ".js",
    ".json",
    ".svg",
    ".txt",
    ".md",
    ".xml",
    ".webmanifest",
}


def collect_site_files(
    workspace_path: str | Path,
    *,
    max_bytes: int = 512_000,
) -> dict[str, str]:
    """Read text site files from a worker workspace for ``POST /previews``.

    Paths are normalized to PromptPay's ``/index.html`` form (leading slash).
    """
    root = Path(workspace_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"workspace not found: {root}")

    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        size = path.stat().st_size
        if size > max_bytes:
            continue
        rel = path.relative_to(root).as_posix()
        key = rel if rel.startswith("/") else f"/{rel}"
        files[key] = path.read_text(encoding="utf-8")

    if not files:
        raise ValueError(f"no publishable site files under {root}")
    return files
