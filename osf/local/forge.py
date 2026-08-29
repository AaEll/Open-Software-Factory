"""In-memory forge (local reference).

Records PRs, comments, and merges in process memory instead of talking to GitHub. Checks
always report success, since the local walking skeleton has no CI. Useful for driving the
pipeline end-to-end and inspecting what the driver/worker would have done on a real forge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from osf.forge import ChecksStatus
from osf.types import PrRef, RepoRef


@dataclass
class PrState:
    ref: PrRef
    branch: str
    title: str
    body: str
    comments: list[str] = field(default_factory=list)
    merged: bool = False


class InMemoryForge:
    """A forge that keeps all state in memory; inspect ``prs`` after a run."""

    def __init__(self) -> None:
        self.prs: dict[int, PrState] = {}
        self.repos: list[RepoRef] = []
        self._next_number = 1

    async def create_repo(
        self, repo: RepoRef, *, private: bool = True, description: str = ""
    ) -> RepoRef:
        self.repos.append(repo)
        return repo

    async def open_pr(self, repo: RepoRef, branch: str, title: str, body: str) -> PrRef:
        ref = PrRef(repo=repo, number=self._next_number)
        self._next_number += 1
        self.prs[ref.number] = PrState(ref=ref, branch=branch, title=title, body=body)
        return ref

    async def comment(self, pr: PrRef, body: str, *, review: bool = False) -> None:
        self.prs[pr.number].comments.append(("review: " if review else "") + body)

    async def checks(self, pr: PrRef) -> ChecksStatus:
        return ChecksStatus(state="success")

    async def merge(self, pr: PrRef) -> None:
        self.prs[pr.number].merged = True
