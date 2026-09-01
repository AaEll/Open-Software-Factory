"""Reviewers — the definition of "done" for a WorkItem.

The driver asks a `Reviewer` whether a change may merge. `AcceptanceReviewer` is the default the
CLI uses: it reads the objective's acceptance criteria, pulls the file paths named in them, and
approves once every one of those files exists in the workspace. Criteria that name no path are
informational and never block a merge, so a criterion-free objective is approved on the first round.

This is deliberately mechanical: it needs no model, no keys, and no network, so `osf` behaves the
same offline and against a real engine. An LLM-backed reviewer is a later, drop-in alternative.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
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


# A check is a command the *model* proposed, so it runs on a short leash: no shell metacharacters,
# a hard timeout, and output truncated before it reaches the worker as feedback.
CHECK_TIMEOUT_SECONDS = 60
CHECK_OUTPUT_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class CheckResult:
    passed: bool
    detail: str


def run_check(command: str, workspace: Workspace) -> CheckResult:
    """Run a step's check command in its workspace.

    Split with `shlex` and run without a shell: the plan's `check` is model-written text that the
    user approved as a *command*, not as a shell script, and `rm -rf x && curl …` should not be one
    approval away from running.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return CheckResult(False, f"check is not a runnable command ({exc})")
    if not argv:
        return CheckResult(False, "check is empty")
    # A check must not litter the workspace: bytecode caches would land in the diff the user is
    # asked to keep or revert, as changes they never asked for.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace.path,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            env=env,
        )
    except FileNotFoundError:
        return CheckResult(False, f"{argv[0]} is not installed here")
    except subprocess.TimeoutExpired:
        return CheckResult(False, f"check timed out after {CHECK_TIMEOUT_SECONDS}s")
    if completed.returncode == 0:
        return CheckResult(True, "")
    output = (completed.stdout + completed.stderr).strip()[:CHECK_OUTPUT_LIMIT]
    return CheckResult(False, f"`{command}` exited {completed.returncode}:\n{output}")


@dataclass(frozen=True, slots=True)
class PlanReviewer:
    """Reviews each WorkItem against its own step's files, then its own check command.

    Steps run in separate workspaces, so item N is judged only on what step N promised; anything
    without a gate of its own is approved on the first green round. Files existing is the weak
    half of "done" — a check command that exits 0 is the half that catches an entry point being
    gutted while its file still exists.
    """

    criteria_by_item: dict[str, list[str]]
    checks_by_item: dict[str, str] = field(default_factory=dict)

    async def review(self, work_item: WorkItem, workspace: Workspace) -> Review:
        criteria = self.criteria_by_item.get(work_item.id, [])
        review = await AcceptanceReviewer(criteria).review(work_item, workspace)
        if not review.approved:
            return review  # a missing file explains itself; running the check would only confuse
        command = self.checks_by_item.get(work_item.id, "")
        if not command:
            return review
        result = await asyncio.to_thread(run_check, command, workspace)
        if result.passed:
            return review
        return Review(approved=False, comment=result.detail)


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
