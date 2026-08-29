# Agent Handoff

Notes for any agent starting work in this repo. Read `docs/ARCHITECTURE.md` for the full design.

## The two things you must not get wrong

1. **This is a net-new product, not a fork or wrapper of opencode.** Do NOT copy, vendor, import, or
   orchestrate opencode's source. We only *borrow patterns* from `anomalyco/opencode` (open agent
   API shape, durable event-sourced sessions, subagent delegation, location-scoped isolation). All
   OSF code is written clean-room in this repo.

2. **The forge (GitHub PRs/comments/merges) is the coordination substrate, and agents run in an
   autonomous loop.** Driver agents reconcile an objective by dispatching worker agents that open
   PRs, then reviewing/commenting/merging via the forge — minimal human input. Coordinate through
   PRs, not a bespoke message bus. Everything is event-sourced and resumable
   (Objective → WorkItem DAG → PullRequest → AgentRun).

## Status

Phase 0 (foundations & contracts). `docs/ARCHITECTURE.md` written; Python skeleton scaffolded
(`pyproject.toml`, `osf/` package, tests, CI) — contracts are protocol stubs with no product logic
yet. Core seams: `osf/runtime.py` (`AgentRuntime`), `osf/isolation.py` (`IsolationBackend`),
`osf/forge.py` (`Forge`), `osf/model.py` (data model). Isolation is tiered (worktree local /
container cloud); first agent engine adapter is opencode's headless server; language is Python.
Dev loop: `pip install -e ".[dev]"` then `ruff check .` and `pytest`.

Open questions before Phase 1: final language call, forge auth (GitHub App vs PAT), definition-of-done
mechanism, cost metering granularity.
