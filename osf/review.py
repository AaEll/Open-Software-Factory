"""Reviewers — the definition of "done" for a WorkItem.

The driver asks a `Reviewer` whether a change may merge. `AcceptanceReviewer` is the default the
CLI uses: it reads the objective's acceptance criteria, pulls the file paths named in them, and
approves once every one of those files exists in the workspace. Criteria that name no path are
informational and never block a merge, so a criterion-free objective is approved on the first round.

This is deliberately mechanical: it needs no model, no keys, and no network, so `osf` behaves the
same offline and against a real engine. An LLM-backed reviewer is a later, drop-in alternative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from osf.driver import Review
from osf.model import WorkItem
from osf.types import Workspace

# A criterion names a file when a token looks like a path: it contains a slash, carries a dotted
# extension, or is one of the conventional extensionless repo files. The last list is explicit
# rather than a heuristic so an ordinary capitalized word (MIT, CI) never becomes a requirement.
_TOKEN = re.compile(r"[\w.][\w./-]*")
_EXTENSION = re.compile(r"\.[A-Za-z]\w*$")
_WELL_KNOWN = frozenset(
    {"LICENSE", "LICENCE", "NOTICE", "CODEOWNERS", "Makefile", "Dockerfile", "CHANGELOG"}
)
# Quotes/brackets are stripped from both ends; sentence punctuation only from the right, so a
# leading dot (`.github/...`) survives.
_STRIP_BOTH = "`'\"()"
_STRIP_END = ".,;:"


def paths_in(criterion: str) -> list[str]:
    """Extract the file paths a criterion names (empty if it names none)."""
    found = []
    for raw in criterion.split():
        token = raw.strip(_STRIP_BOTH).rstrip(_STRIP_END)
        if not _TOKEN.fullmatch(token):
            continue
        if "/" in token or _EXTENSION.search(token) or token in _WELL_KNOWN:
            found.append(token)
    return found


@dataclass(frozen=True, slots=True)
class PlanReviewer:
    """Reviews each WorkItem against its own step's files.

    Steps run in separate workspaces, so item N is judged only on what step N promised; anything
    without a gate of its own is approved on the first green round.
    """

    criteria_by_item: dict[str, list[str]]

    async def review(self, work_item: WorkItem, workspace: Workspace) -> Review:
        criteria = self.criteria_by_item.get(work_item.id, [])
        return await AcceptanceReviewer(criteria).review(work_item, workspace)


@dataclass(frozen=True, slots=True)
class AcceptanceReviewer:
    """Approves when every file named in the acceptance criteria exists in the workspace."""

    criteria: list[str]

    async def review(self, work_item: WorkItem, workspace: Workspace) -> Review:
        required = [p for c in self.criteria for p in paths_in(c)]
        missing = [p for p in required if not Path(workspace.path, p).is_file()]
        if missing:
            return Review(approved=False, comment=f"missing required files: {', '.join(missing)}")
        return Review(approved=True)
