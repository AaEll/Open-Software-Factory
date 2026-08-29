# Open Software Factory

Autonomous multi-agent software factory: **objectives in, merged PRs out**, with minimal human input.

A human sets an objective; **driver agents** run an autonomous loop — write a spec, dispatch **worker
agents** that open PRs, review/comment/request changes, and merge when green — repeating until the
objective is met. The forge (GitHub) is the coordination substrate. Runs locally and in the cloud.

> Net-new product. Not a fork or wrapper of any agent engine — it borrows patterns from
> [`anomalyco/opencode`](https://github.com/anomalyco/opencode) but shares no source with it.

## Status

**Phase 0 — foundations & contracts.** The three core seams are defined as Python protocols with no
product logic yet:

- `osf/runtime.py` — `AgentRuntime` (the open agent API; adapters: opencode server, then others)
- `osf/isolation.py` — `IsolationBackend` (tiered: git worktrees local, containers cloud)
- `osf/forge.py` — `Forge` (GitHub first)
- `osf/model.py` — event-sourced data model: Objective → WorkItem (DAG) → PullRequest → AgentRun

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and roadmap, and
[`AgentHandoff.md`](AgentHandoff.md) if you're an agent starting work here.

## Develop

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

## Eval

An offline end-to-end eval drives the whole pipeline against local reference adapters
(`osf/local/`): objective → worker agent in an isolated git workspace → PR → merge, graded on
acceptance criteria. No API keys or network.

```bash
python -m evals.efactory_webpage   # builds a landing page for efactory.ai and grades it
```
