"""AcceptanceReviewer: the default definition of done, and the path detection behind it."""

import asyncio
from pathlib import Path

import pytest

from osf.model import WorkItem
from osf.review import AcceptanceReviewer, paths_in
from osf.types import Workspace


@pytest.mark.parametrize(
    ("criterion", "expected"),
    [
        ("README.md exists", ["README.md"]),
        (".github/workflows/ci.yml exists", [".github/workflows/ci.yml"]),
        ("Produces an `index.html` file", ["index.html"]),
        ("LICENSE exists", ["LICENSE"]),
        ("The landing page is visually appealing, licensed MIT", []),
    ],
)
def test_paths_in(criterion, expected):
    assert paths_in(criterion) == expected


def _review(criteria, workspace):
    item = WorkItem(id="w1", objective_id="o1", spec="spec")
    return asyncio.run(AcceptanceReviewer(criteria).review(item, workspace))


def test_acceptance_reviewer(tmp_path: Path):
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")

    assert _review(["README.md exists"], workspace).approved

    rejected = _review(["README.md exists", "LICENSE exists"], workspace)
    assert not rejected.approved
    assert "LICENSE" in rejected.comment

    # No criterion names a path -> nothing to gate on.
    assert _review(["Looks nice"], workspace).approved
