# AGENTS.md

Working agreement for agents (human or AI) contributing to Open Software Factory. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design and [`AgentHandoff.md`](AgentHandoff.md)
for current status.

## Pull requests — small, meaningful, atomic

- **One coherent change per PR.** Small enough to review at a glance; no unrelated changes riding
  along. Split distinct concerns (a convention doc vs a feature) into separate PRs.
- **Always commit and merge.** Branch → commit → push → merge to `main`. Don't let branches pile up.
- **Ship proactively.** When a unit of work is complete and green, that's the moment to push a PR.
- Conventional commit titles: `type(scope): summary` (`feat`, `fix`, `docs`, `chore`, `refactor`,
  `test`). Short branch names, no `type/` prefixes.
- A PR must be green before merge: `ruff check .` and `pytest` pass.

## Model selection

Models are selected with the opencode-style `provider/model` abstraction (`osf.types.ModelRef`).
The provider chooses the engine via `osf.engines.resolve_runtime`. **Fireworks is the default**
provider (`osf/engines/fireworks.py`, OpenAI-compatible); Anthropic/Claude is a second adapter.
Never hardcode a bare model string — thread a `ModelRef` through.

## Dev loop

```bash
pip install -e ".[dev]"     # ruff + pytest
ruff check .
pytest                       # offline; no network or keys

pip install -e ".[agent]"   # real engines (openai, anthropic, python-dotenv)
python -m evals.efactory_live   # live eval; needs FIREWORKS_API_KEY in .env
```

## Tests that touch a model

Never call a live API from the test suite. There are three offline layers, and a change usually
wants the one that matches what it risks breaking:

- **`osf/local/` stand-ins** — the cheapest smoke path, proving the pipeline holds together.
- **Recorded responses** (`tests/fixtures/fireworks/`, `tests/test_integration.py`) — real API
  bodies replayed through an httpx mock transport, proving our adapters cope with what a model
  actually returns, fences and all. Re-record deliberately with `python -m evals.record_fireworks`.
- **Canned scripts** (`tests/canned.py`, `tests/test_flows.py`) — a named series of responses for
  one flow, written by hand. Use these for the paths that are impractical to record: a model that
  answers badly and is corrected, a refused tool call it recovers from, a check that fails and then
  passes.

All three run the real adapters; only the HTTP responses differ in origin.

## What a test is for

A test earns its place by failing when a real promise breaks. Prefer a handful that guard
behaviour users depend on — `sf` never stages or commits for you, revert restores uncommitted work,
a rejected answer doesn't discard the request — over many that restate the implementation (command
lists, default values, parameter names). If you cannot name the regression a test catches, delete
it. When adding one for a bug, break the fix and watch the test fail before committing.

## Secrets

Keys live in `.env` (gitignored) — `FIREWORKS_API_KEY` (or `FIREWORKS`). Never commit secrets or the
agent's generated output under `eval/`.
