"""Planning: the proposal shape, tolerant parsing of model replies, and per-step gates."""

import asyncio

import pytest

from osf.model import WorkItem
from osf.planner import (
    PLAN_SYSTEM,
    ProposedPlan,
    StaticPlanner,
    Step,
    parse_plan,
    parse_questions,
    propose_with_retry,
)
from osf.review import PlanReviewer, run_check
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


# --- clarifying questions ---------------------------------------------------------------------


def test_parse_questions_caps_the_list_at_three():
    reply = '{"questions": ["a", "b", "c", "d"]}'
    assert parse_questions(reply) == ["a", "b", "c"]


@pytest.mark.parametrize("reply", ["", "no json", '{"questions": []}', "{bad json}"])
def test_parse_questions_treats_anything_unusable_as_no_questions(reply):
    # An unparseable reply must not block planning — the driver simply asks nothing.
    assert parse_questions(reply) == []


def test_static_planner_asks_nothing_and_ignores_answers():
    planner = StaticPlanner()
    assert planner.clarify("Build a site") == []
    # With no model to interpret them, answers must not be pasted into the spec as work to do.
    plan = planner.propose("Build a site", (), [("What vibe?", "playful")])
    assert plan.steps == [Step("Build a site")]


# --- retrying an unusable plan -------------------------------------------------------------


def test_propose_retries_once_when_the_reply_is_not_a_plan():
    replies = ["Sure, I can help with that!", '{"goal": "g", "steps": ["a"]}']
    seen = []

    def complete(system, messages, max_tokens):
        seen.append(messages)
        return replies.pop(0)

    plan = propose_with_retry(complete, "Build a site")
    assert plan.goal == "g"
    # the second attempt shows the model its bad reply and asks again
    assert seen[1][-2]["content"] == "Sure, I can help with that!"
    assert "JSON only" in seen[1][-1]["content"]


def test_propose_gives_up_after_the_retry():
    def complete(system, messages, max_tokens):
        return "still not json"

    with pytest.raises(ValueError):
        propose_with_retry(complete, "Build a site")


def test_propose_passes_the_plan_system_prompt():
    def complete(system, messages, max_tokens):
        assert system == PLAN_SYSTEM
        return '{"goal": "g", "steps": ["a"]}'

    assert propose_with_retry(complete, "Build a site").goal == "g"


def test_the_prompt_reflects_whether_steps_share_a_workspace():
    """A plan that may extend earlier work is only legal when the steps share a repository."""
    seen = []

    def complete(system, messages, max_tokens):
        seen.append(system)
        return '{"goal": "g", "steps": ["a"]}'

    propose_with_retry(complete, "Build a site", shared_workspace=True)
    propose_with_retry(complete, "Build a site", shared_workspace=False)
    assert "each seeing the files the previous steps wrote" in seen[0]
    assert "must stand alone" not in seen[0]
    assert "must stand alone" in seen[1]


def test_answers_reach_the_model_as_context():
    def complete(system, messages, max_tokens):
        assert "playful" in messages[0]["content"]
        assert "What vibe?" in messages[0]["content"]
        return '{"goal": "g", "steps": ["a"]}'

    propose_with_retry(complete, "Build a site", (), [("What vibe?", "playful")])


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


# --- executable checks --------------------------------------------------------------------------


def test_a_step_may_declare_a_check():
    plan = parse_plan(
        '{"goal": "g", "steps": [{"spec": "a", "files": ["app.py"], '
        '"check": "python app.py --help"}]}',
        fallback_goal="x",
    )
    assert plan.steps[0].check == "python app.py --help"
    assert plan.checks_by_item("obj") == {"obj-1": "python app.py --help"}


def test_steps_without_a_check_are_simply_absent():
    plan = parse_plan(
        '{"goal": "g", "steps": [{"spec": "a", "files": ["x.py"]}]}', fallback_goal="x"
    )
    assert plan.checks_by_item("obj") == {}


def test_the_plan_prompt_asks_for_a_check():
    assert "check" in PLAN_SYSTEM
    assert "exits 0" in PLAN_SYSTEM
    assert "no installs, no network" in PLAN_SYSTEM  # the command runs on the user's machine


def test_check_passes_when_the_command_exits_zero(tmp_path):
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    assert run_check("python -c pass", workspace).passed


def test_check_fails_with_the_command_output_as_feedback(tmp_path):
    """The worker's next round needs to see why, not just that it failed."""
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    result = run_check("python -c \"import sys; print('boom'); sys.exit(3)\"", workspace)
    assert not result.passed
    assert "exited 3" in result.detail
    assert "boom" in result.detail


def test_a_check_runs_in_the_workspace(tmp_path):
    (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    assert run_check("python -c \"open('marker.txt')\"", workspace).passed


def test_a_missing_program_is_reported_not_raised(tmp_path):
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    result = run_check("definitely-not-a-real-program --version", workspace)
    assert not result.passed and "not installed" in result.detail


def test_a_check_is_not_run_through_a_shell(tmp_path):
    """The user approved a command, not a shell script; `&&` and `;` must not chain anything.

    The chained part becomes plain arguments to the first program — here python ignores them — so
    what matters is that nothing else *ran*, not that the check failed.
    """
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    run_check("python -c pass && touch pwned.txt", workspace)
    assert not (tmp_path / "pwned.txt").exists()

    run_check("python -c pass; touch semicolon.txt", workspace)
    assert not (tmp_path / "semicolon.txt").exists()

    # Redirection is likewise inert.
    run_check("python -c pass > redirected.txt", workspace)
    assert not (tmp_path / "redirected.txt").exists()


def test_an_unparseable_check_fails_cleanly(tmp_path):
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    assert not run_check('python -c "unclosed', workspace).passed


def test_the_reviewer_runs_the_check_only_after_the_files_exist(tmp_path):
    """A failing check on a step whose files are missing is noise; the missing file is the news."""
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    reviewer = PlanReviewer({"obj-1": ["app.py exists"]}, {"obj-1": "definitely-not-a-program"})
    item = WorkItem(id="obj-1", objective_id="obj", spec="spec")

    rejected = asyncio.run(reviewer.review(item, workspace))
    assert "missing required files" in rejected.comment


def test_the_reviewer_rejects_a_step_whose_check_fails(tmp_path):
    (tmp_path / "app.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    reviewer = PlanReviewer({"obj-1": ["app.py exists"]}, {"obj-1": "python app.py"})
    item = WorkItem(id="obj-1", objective_id="obj", spec="spec")

    rejected = asyncio.run(reviewer.review(item, workspace))
    assert not rejected.approved
    assert "exited 1" in rejected.comment


def test_the_reviewer_approves_when_the_check_passes(tmp_path):
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))
    reviewer = PlanReviewer({"obj-1": ["app.py exists"]}, {"obj-1": "python app.py"})
    item = WorkItem(id="obj-1", objective_id="obj", spec="spec")

    assert asyncio.run(reviewer.review(item, workspace)).approved


def test_a_check_does_not_litter_the_workspace(tmp_path):
    """Bytecode caches would otherwise land in the diff as changes the user never asked for."""
    (tmp_path / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    workspace = Workspace(path=str(tmp_path), handle=str(tmp_path))

    assert run_check('python -c "import mod; assert mod.VALUE == 1"', workspace).passed
    assert not (tmp_path / "__pycache__").exists()
