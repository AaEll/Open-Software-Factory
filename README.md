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

### Live run (real agent)

The same eval, driven by a real worker agent that writes into `eval/efactory-ai/` via a sandboxed
`write_file` tool. The engine is chosen from the model's provider (opencode-style
`provider/model` selection, `osf.types.ModelRef`): **Fireworks** (OpenAI-compatible, default) or
Anthropic. See `osf/engines/`.

```bash
pip install -e ".[agent]"
cp .env.example .env              # then set FIREWORKS_API_KEY in .env
# optional: OSF_MODEL=fireworks/accounts/fireworks/models/kimi-k2-instruct
python -m evals.efactory_live
```

## CI/CD

Three build artifacts: **wheel + sdist** for local `pip install`, and a **container** for cloud
deployment. Each is gated by a **pass-through predeployment smoke test** — `osf-smoke` runs the whole
`objective → worker → PR → merge` pipeline (offline reference adapters) against the *installed*
artifact and exits non-zero on failure.

- **CI** (`.github/workflows/ci.yml`) on every push/PR: lint (`ruff`), test matrix (Python
  3.11–3.13), `build` (wheel+sdist, then `osf-smoke` on the installed wheel), and `image` (docker
  build, then `osf-smoke` in the container).
- **CD** (`.github/workflows/release.yml`) on version tags: publishes the **wheel + sdist** to a
  GitHub Release and pushes the **container** to GHCR (`ghcr.io/<owner>/open-software-factory`),
  each gated by the pass-through smoke. Artifact versions are derived from the git tag (`hatch-vcs`).
  Cut a release:

  ```bash
  git tag v0.1.0 && git push origin v0.1.0
  ```

Run the smoke locally with `osf-smoke` (after `pip install -e .`).
