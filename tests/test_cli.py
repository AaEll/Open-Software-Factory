"""CLI tests — argument plumbing and the offline end-to-end commands.

Everything here runs against the local reference adapters, so no keys or network are needed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from osf.cli import _parse_params, _parse_repo, build_parser, main
from osf.driver import Review
from osf.model import WorkItem
from osf.review import AcceptanceReviewer, paths_in
from osf.types import RepoRef, Workspace


def test_parse_repo() -> None:
    assert _parse_repo("me/site") == RepoRef(owner="me", name="site")


@pytest.mark.parametrize("bad", ["site", "/site", "me/"])
def test_parse_repo_rejects_bad_refs(bad: str) -> None:
    with pytest.raises(SystemExit):
        _parse_repo(bad)


def test_parse_params() -> None:
    assert _parse_params(["name=widgets", "description=a b"]) == {
        "name": "widgets",
        "description": "a b",
    }


def test_parse_params_rejects_bare_token() -> None:
    with pytest.raises(SystemExit):
        _parse_params(["widgets"])


def test_parser_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_runs_lists_the_builtin_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["runs"]) == 0
    assert "create-repo" in capsys.readouterr().out


def test_unknown_run_exits_with_a_hint() -> None:
    with pytest.raises(SystemExit, match="unknown run"):
        main(["run", "nope"])


def test_run_missing_param_names_it() -> None:
    with pytest.raises(SystemExit, match="--param name"):
        main(["run", "create-repo"])


def test_objective_merges_offline(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "objective",
            "Create a landing page for demo.osf",
            "--repo",
            "me/site",
            "--criterion",
            "index.html exists",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "done" in out
    assert "merged" in out


def test_objective_escalates_when_criteria_are_unmet(capsys: pytest.CaptureFixture[str]) -> None:
    # The scripted runtime only ever writes index.html, so this criterion can never be satisfied.
    code = main(
        [
            "objective",
            "Create a landing page for demo.osf",
            "--repo",
            "me/site",
            "--criterion",
            "app/main.py exists",
            "--max-rounds",
            "1",
        ]
    )
    assert code == 1
    assert "escalated" in capsys.readouterr().out


def test_objective_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["objective", "Landing page for demo.osf", "--repo", "me/site", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "done"
    assert payload["items"][0]["state"] == "merged"


def test_smoke_command_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["smoke"])
    assert exc.value.code == 0


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
def test_paths_in(criterion: str, expected: list[str]) -> None:
    assert paths_in(criterion) == expected


async def _review(criteria: list[str], workspace: Workspace) -> Review:
    item = WorkItem(id="w1", objective_id="o1", spec="spec")
    return await AcceptanceReviewer(criteria).review(item, workspace)


def test_acceptance_reviewer(tmp_path: Path) -> None:
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")

    approved = asyncio.run(_review(["README.md exists"], workspace))
    assert approved.approved

    rejected = asyncio.run(_review(["README.md exists", "LICENSE exists"], workspace))
    assert not rejected.approved
    assert "LICENSE" in rejected.comment

    # No criterion names a path -> nothing to gate on.
    assert asyncio.run(_review(["Looks nice"], workspace)).approved
