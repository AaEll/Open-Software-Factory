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

Phase 0 (foundations & contracts) + first live vertical slice. `docs/ARCHITECTURE.md` written;
Python skeleton scaffolded (`pyproject.toml`, `osf/` package, tests, CI). Core seams:
`osf/runtime.py` (`AgentRuntime`), `osf/isolation.py` (`IsolationBackend`), `osf/forge.py`
(`Forge`), `osf/model.py` (data model). Model selection follows opencode: `osf.types.ModelRef`
(`provider/model`); the provider picks the engine via `osf/engines/resolve_runtime`.

Engines (`osf/engines/`): **Fireworks is the default** (OpenAI-compatible, `osf/engines/fireworks.py`),
Anthropic/Claude is a second adapter. Live eval `python -m evals.efactory_live` runs a real worker
into `eval/<repo-name>/` and grades it — verified passing 4/4 on
`fireworks/accounts/fireworks/models/kimi-k2p7-code`. Secrets in `.env` (gitignored):
`FIREWORKS_API_KEY` (or `FIREWORKS`); override model with `OSF_MODEL`.

The `sf` CLI (`osf/cli.py`) drives all of this from the shell — `sf smoke`, `sf runs`,
`sf run <name>`, `sf objective <goal> --repo owner/name` — with `--model`/`--forge` choosing the
engine and forge, and `osf/review.py`'s `AcceptanceReviewer` as the default definition of done.

Isolation is tiered (worktree local / container cloud). Dev loop: `pip install -e ".[dev]"` then
`ruff check .` and `pytest`; real engines need `pip install -e ".[agent]"`.

Open questions before Phase 1: final language call, forge auth (GitHub App vs PAT), definition-of-done
mechanism, cost metering granularity.
