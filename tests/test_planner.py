"""Planning: the proposal shape, tolerant parsing of model replies, and per-step gates."""

import asyncio

import pytest

from osf.model import WorkItem
from osf.planner import ProposedPlan, StaticPlanner, Step, parse_plan, render_json
from osf.review import PlanReviewer
from osf.types import RepoRef, Workspace


def test_static_planner_gates_on_nothing():
    # Prose is full of things that look like paths ("demo.osf"), so the offline planner never
    # guesses at files — a wrong gate can never be satisfied.
    plan = StaticPlanner().propose("Create a landing page for demo.osf")
    assert plan.files == []
    assert plan.steps == [Step("Create a landing page for demo.osf")]


def test_static_planner_folds_feedback_into_the_plan():
    first = StaticPlanner().propose("Make a site")
    revised = StaticPlanner().propose("Make a site", [(first, "add an about page")])
    assert [step.spec for step in revised.steps] == ["Make a site", "add an about page"]


def test_files_are_deduplicated_in_order():
    plan = ProposedPlan(
        "g", [Step("a", ["index.html", "app.css"]), Step("b", ["about.html", "index.html"])]
    )
    assert plan.files == ["index.html", "app.css", "about.html"]
    assert plan.criteria == ["index.html exists", "app.css exists", "about.html exists"]


def test_each_work_item_is_gated_on_its_own_step():
    plan = ProposedPlan("g", [Step("a", ["index.html"]), Step("b", ["about.html"])])
    assert plan.criteria_by_item("obj") == {
        "obj-1": ["index.html exists"],
        "obj-2": ["about.html exists"],
    }
    assert [item.spec for item in plan.work_items("obj")] == ["a", "b"]


def test_an_empty_plan_still_produces_one_work_item():
    plan = ProposedPlan("Build the thing")
    (item,) = plan.work_items("obj")
    assert item.spec == "Build the thing"
    assert plan.criteria_by_item("obj") == {"obj-1": []}


def test_objective_carries_the_plan_as_acceptance_criteria():
    plan = ProposedPlan("Build it", [Step("a", ["index.html"])])
    objective = plan.objective("obj", RepoRef("me", "site"))
    assert objective.goal == "Build it"
    assert objective.acceptance_criteria == ["index.html exists"]


# --- parsing model replies ------------------------------------------------------------------


def test_parse_plan_reads_steps_with_files():
    plan = parse_plan(
        '{"goal": "A site", "steps": [{"spec": "Write index.html", "files": ["index.html"]}]}',
        fallback_goal="x",
    )
    assert plan.goal == "A site"
    assert plan.steps == [Step("Write index.html", ["index.html"])]


def test_parse_plan_tolerates_fences_and_commentary():
    plan = parse_plan(
        'Sure!\n```json\n{"goal": "A site", "steps": [{"spec": "a", "files": []}]}\n```\nEnjoy.',
        fallback_goal="x",
    )
    assert plan.goal == "A site"


def test_parse_plan_accepts_plain_string_steps():
    plan = parse_plan('{"goal": "A site", "steps": ["do a", "do b"]}', fallback_goal="x")
    assert [step.spec for step in plan.steps] == ["do a", "do b"]
    assert plan.files == []


def test_top_level_files_attach_only_to_a_single_step():
    one = parse_plan('{"goal": "g", "steps": ["only"], "files": ["index.html"]}', fallback_goal="x")
    assert one.steps == [Step("only", ["index.html"])]

    # With several steps we cannot tell who produces what, so we refuse to guess.
    many = parse_plan(
        '{"goal": "g", "steps": ["a", "b"], "files": ["index.html"]}', fallback_goal="x"
    )
    assert many.files == []


def test_parse_plan_falls_back_to_the_request_for_a_missing_goal():
    assert parse_plan('{"steps": ["a"]}', fallback_goal="the request").goal == "the request"


@pytest.mark.parametrize("reply", ["", "no json here", "{not json}"])
def test_parse_plan_rejects_unusable_replies(reply):
    with pytest.raises(ValueError):
        parse_plan(reply, fallback_goal="x")


def test_render_json_round_trips():
    plan = ProposedPlan("g", [Step("a", ["index.html"])])
    assert parse_plan(render_json(plan), fallback_goal="x") == plan


# --- the reviewer that uses those gates ---------------------------------------------------------


def _review(reviewer, item_id, workspace):
    item = WorkItem(id=item_id, objective_id="obj", spec="spec")
    return asyncio.run(reviewer.review(item, workspace))


def test_plan_reviewer_judges_each_item_on_its_own_files(tmp_path):
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    (tmp_path / "index.html").write_text("hi", encoding="utf-8")
    reviewer = PlanReviewer({"obj-1": ["index.html exists"], "obj-2": ["about.html exists"]})

    assert _review(reviewer, "obj-1", workspace).approved
    rejected = _review(reviewer, "obj-2", workspace)
    assert not rejected.approved
    assert "about.html" in rejected.comment


def test_plan_reviewer_approves_an_ungated_item(tmp_path):
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    assert _review(PlanReviewer({}), "obj-1", workspace).approved
