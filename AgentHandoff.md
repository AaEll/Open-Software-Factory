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

The `sf` CLI (`osf/cli.py` → `osf/shell.py`) is an interactive shell, the only user entry point:
free text becomes an objective, `/commands` (`/new-repo`, `/run`, `/repo`, `/model`, `/forge`,
`/rounds`) reach the structured flows. Prepackaged runs declare their questions as `RunParam`s
(`osf/runs.py`) and the shell walks that schema — add a question to a run, not to the shell.
`osf/prompts.py` is the dependency-free text/select/confirm toolkit.

`osf/instructions.py` loads the project's `AGENTS.md`/`CLAUDE.md` (plus a global file) into both the
driver's planning context and the worker prompt — a repo's conventions are context the model is
owed, per opencode and deepseek-harness alike. `_tools.guidance_for(model_id)` adds a short
model-family overlay (Kimi today), our own wording rather than a copy, adapted to a worker that has
no shell and no user to ask.

Workers get three sandboxed tools (`osf/engines/_tools.py`): `read_file`, `edit_file` (exact string
replacement) and `write_file`. `Toolbox` owns the session policy, borrowed from opencode's `edit`
tool and deepseek-harness's `fs-observation-policy`: an existing file cannot be written until it has
been read this session, and an ambiguous `edit_file` is refused rather than guessed. Prompts are
built per run — `worker_system(workspace)` for the worker, `Shell.project_context()` for the driver
— both naming the directory and listing what is in it via `git ls-files`. Planning without that
listing produced work routed into files that did not exist.

**The driver owns the loop.** Free text goes to `Planner.route` first (`ROUTE_SYSTEM`), which
returns a `Decision`: `reply` (answer the user — a greeting is not a build request), `run` (start a
prepackaged workflow, prefilling any params it understood, the rest asked as usual), or `plan`.
Anything unusable, unreachable, or offline falls through to `plan`, so a request is never dropped.
The run catalog is rendered into the prompt, so the driver can only pick a workflow that exists.

**The definition of done is negotiated, not demanded.** `osf/planner.py` has the driver agent first
`clarify` (up to 3 questions it writes for that request, answered inline, skippable, `/ask off`),
then propose a `ProposedPlan` (goal + steps, each step carrying the files it must produce); the shell shows it and
folds plain-language feedback back into a re-plan until the user accepts. Those per-step files
become the acceptance criteria `osf/review.py`'s `PlanReviewer`/`AcceptanceReviewer` enforce. Engine
planners resolve like runtimes (`osf.engines.resolve_planner`) and are plain completions against
the worker's model, retried once when a reply isn't parseable; `StaticPlanner` is the offline
reference and deliberately gates on nothing. The shell picks its engine at startup from `OSF_MODEL`
or a Fireworks key, so a user with a key never lands on the scripted planner by accident. The forge account is detected (env, then `gh`'s
`hosts.yml`), never asked for — work is local `git init` until a forge is chosen. There are no flag-driven subcommands; the
non-interactive gate for CI and the container is the separate `sf-smoke` script.

Engine adapters take an injectable client, and `tests/test_integration.py` replays recorded
Fireworks responses (`tests/fixtures/fireworks/`) through an httpx mock transport — the full
clarify → plan → worker → merge path runs in CI with no key and no spend. Re-record with
`python -m evals.record_fireworks`.

**`sf` edits the user's own repository by default** (`osf/local/project.py`, the opencode model):
the workspace *is* the project you launched from, every step shares it, and safety comes from
snapshots rather than isolation — a git tree captured before the run, diffed after, restorable on
request. Captures use a scratch `GIT_INDEX_FILE` so the user's staging area is never touched, and
the driver checkpoints through `IsolationBackend.checkpoint` instead of committing, so we never
write to someone's history. `NoForge` is the default forge: the review loop still runs, there is
just no PR. `/forge memory|github` opts back into throwaway workspaces and real PRs.

Isolation is tiered (worktree local / container cloud). Dev loop: `pip install -e ".[dev]"` then
`ruff check .` and `pytest`; real engines need `pip install -e ".[agent]"`.

Open questions before Phase 1: final language call, forge auth (GitHub App vs PAT), definition-of-done
mechanism, cost metering granularity.
