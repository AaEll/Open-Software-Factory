"""Project instruction files — `AGENTS.md` / `CLAUDE.md` as baseline context.

A repository states its own conventions in `AGENTS.md`: how to run the tests, what the commit
format is, which directories are off limits. Both opencode and deepseek-harness treat that file as
context the model is owed on every request, and an agent that has not read it will confidently
violate house rules it was never shown.

Files are collected broadest-first — the user's global file, then the project's — so the most
specific instructions come last and win an argument. The budget is spent the other way round: when
there is not enough room, whole broader files are dropped before the project's own file is
truncated, since half a rule is worse than none.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Read in this order; the first name found in a directory wins, so a repo with both files does not
# get its conventions twice.
PROJECT_FILES = ("AGENTS.md", "CLAUDE.md")

# Room for instructions in one prompt. Generous for a normal AGENTS.md, small enough that a
# runaway file cannot crowd out the request itself.
DEFAULT_BUDGET = 8000


@dataclass(frozen=True, slots=True)
class Instructions:
    """The instruction files that apply to a project, in the order they should be read."""

    files: list[tuple[Path, str]]
    truncated: list[Path]

    def render(self) -> str:
        if not self.files:
            return ""
        blocks = [
            f"### {path}\n{text.strip()}" for path, text in self.files if text.strip()
        ]
        if not blocks:
            return ""
        body = "\n\n".join(blocks)
        return (
            "The project states its own conventions in these files. Follow them; they outrank "
            "your general habits, and the most specific file wins where they disagree.\n\n" + body
        )


def global_files() -> list[Path]:
    """Instruction files that apply to everything this user works on."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return [config_home / "osf" / "AGENTS.md", Path.home() / ".claude" / "CLAUDE.md"]


def project_files(root: Path) -> list[Path]:
    """The project's own instruction file, if it has one."""
    for name in PROJECT_FILES:
        candidate = root / name
        if candidate.is_file():
            return [candidate]
    return []


def load(root: Path | str | None, *, budget: int = DEFAULT_BUDGET) -> Instructions:
    """Collect the instruction files that apply to `root`, within a byte budget."""
    if root is None:
        return Instructions([], [])
    root = Path(root)
    candidates = [path for path in (*global_files(), *project_files(root)) if path.is_file()]

    texts: list[tuple[Path, str]] = []
    for path in candidates:
        try:
            texts.append((path, path.read_text(encoding="utf-8")))
        except OSError:  # unreadable is the same as absent, not a reason to fail a run
            continue

    # Drop whole files from the broad end until what remains fits; truncate only the last one.
    truncated: list[Path] = []
    while texts and sum(len(text) for _path, text in texts) > budget and len(texts) > 1:
        texts.pop(0)
    if texts and len(texts[-1][1]) > budget:
        path, text = texts[-1]
        texts[-1] = (path, text[:budget] + "\n… (truncated)")
        truncated.append(path)
    return Instructions(texts, truncated)
