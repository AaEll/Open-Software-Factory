"""Driver control loop: merge on approval, iterate on feedback, escalate on repeated rejection."""

import asyncio

from osf.driver import Driver, Review
from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.local.runtime import StaticSiteRuntime
from osf.model import Objective
from osf.types import RepoRef


class _ScriptedReviewer:
    """Approves/rejects per a fixed script; records how many reviews it ran."""

    def __init__(self, approvals: list[bool]) -> None:
        self._approvals = approvals
        self.calls = 0

    async def review(self, work_item, workspace) -> Review:
        i = self.calls
        self.calls += 1
        approved = self._approvals[i] if i < len(self._approvals) else self._approvals[-1]
        return Review(approved=approved, comment="" if approved else "add a tagline")


def _objective() -> Objective:
    return Objective(
        id="obj",
        repo=RepoRef(owner="osf", name="site"),
        goal="Create a simple landing page for driver.osf",
    )


def _driver(reviewer, max_rounds=3) -> tuple[Driver, InMemoryForge]:
    forge = InMemoryForge()
    driver = Driver(
        runtime=StaticSiteRuntime(),
        isolation=TempdirIsolation(),
        forge=forge,
        reviewer=reviewer,
        max_rounds=max_rounds,
    )
    return driver, forge


def test_merges_on_first_approval():
    driver, forge = _driver(_ScriptedReviewer([True]))
    outcome = asyncio.run(driver.run(_objective()))

    assert outcome.state == "done"
    (item,) = outcome.items
    assert item.state == "merged" and item.rounds == 1
    assert forge.prs[item.pr.number].merged
    assert forge.prs[item.pr.number].comments == []  # no changes requested


def test_iterates_on_feedback_then_merges():
    reviewer = _ScriptedReviewer([False, True])
    driver, forge = _driver(reviewer)
    outcome = asyncio.run(driver.run(_objective()))

    (item,) = outcome.items
    assert outcome.state == "done"
    assert item.state == "merged" and item.rounds == 2
    assert reviewer.calls == 2
    # Exactly one round of feedback was posted as a review comment before the merge.
    comments = forge.prs[item.pr.number].comments
    assert len(comments) == 1 and comments[0].startswith("review:")


def test_escalates_after_max_rounds():
    driver, forge = _driver(_ScriptedReviewer([False]), max_rounds=2)
    outcome = asyncio.run(driver.run(_objective()))

    (item,) = outcome.items
    assert outcome.state == "escalated"
    assert item.state == "failed" and item.rounds == 2
    assert not forge.prs[item.pr.number].merged
    assert len(forge.prs[item.pr.number].comments) == 2
