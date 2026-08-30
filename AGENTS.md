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

Never call a live API from the test suite. Engine adapters are covered by replaying recorded
responses (`tests/fixtures/fireworks/`, see its README) through an httpx mock transport, so CI
needs no key and costs nothing. Re-record deliberately with `python -m evals.record_fireworks`.
The offline `osf/local/` stand-ins remain the cheapest smoke path; the fixtures cover the code
that actually talks to a model, which the stand-ins never exercise.

## Secrets

Keys live in `.env` (gitignored) — `FIREWORKS_API_KEY` (or `FIREWORKS`). Never commit secrets or the
agent's generated output under `eval/`.
