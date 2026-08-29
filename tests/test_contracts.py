"""Phase 0 smoke tests: the contracts and data model import and are well-formed."""

from osf import forge, isolation, model, runtime, types


def test_protocols_are_runtime_checkable_shapes():
    # The three core seams exist as Protocols.
    assert hasattr(runtime, "AgentRuntime")
    assert hasattr(isolation, "IsolationBackend")
    assert hasattr(forge, "Forge")


def test_data_model_projects_an_objective_graph():
    repo = types.RepoRef(owner="acme", name="widget")
    objective = model.Objective(id="obj-1", repo=repo, goal="Add health endpoint")
    item = model.WorkItem(id="wi-1", objective_id=objective.id, spec="Implement /health")
    pr = model.PullRequest(work_item_id=item.id, ref=types.PrRef(repo=repo, number=1))
    run = model.AgentRun(id="run-1", work_item_id=item.id, role="worker", engine="opencode")

    assert objective.state == "open"
    assert item.depends_on == []
    assert pr.ref.number == 1
    assert run.cost_usd == 0.0
