# Running the agent loop

How to start OSF's driver control loop — the autonomous
`decompose → dispatch worker → open PR → review → merge` reconcile loop.

> The `sf` shell (below) covers the common cases; anything else you assemble from a few lines of
> Python. Either way you supply four pieces, all behind swappable contracts:
>
> | Piece | What it is | Offline option | Real option |
> |---|---|---|---|
> | `runtime` | the agent engine that does the work | `osf.local.runtime.StaticSiteRuntime` | `osf.engines.resolve_runtime(ModelRef)` (Fireworks/Claude) |
> | `isolation` | where the worker runs | `osf.local.isolation.TempdirIsolation` | *(git-remote backend pending)* |
> | `forge` | PRs/reviews/merges | `osf.local.forge.InMemoryForge` | `osf.forges.github.GitHubForge` |
> | `reviewer` | the definition of "done" | you supply one (below) | you supply one |

## 0. The CLI

`pip install -e .` puts `sf` on your PATH. It opens a shell: plain language becomes an objective,
`/commands` reach the structured flows.

```console
$ sf
› /repo me/site
› Create a landing page for demo.osf
? Acceptance criteria naming files, comma-separated › index.html exists
  me-site: done
  me-site-1: merged (PR#1, rounds=1)
```

| Command | Sets |
|---|---|
| `/model provider/model` | the engine workers run on (default: the offline scripted runtime) |
| `/forge memory\|github\|github-org` | `memory` (default) is a dry run; `github` needs `GITHUB_TOKEN` |
| `/rounds N` | review iterations per WorkItem before escalating (default 3) |
| `/new-repo`, `/run [name]` | start a prepackaged run, question by question |

The reviewer is [`AcceptanceReviewer`](../osf/review.py): it approves once every file named in the
objective's acceptance criteria exists in the workspace. Criteria that name no file are
informational and never block a merge.

Task-first walkthrough: [`cli-howto.md`](cli-howto.md). The rest of this page is the Python API the
shell is built on — use it when you need something the shell doesn't offer, or automation without a
TTY.

## 1. Fastest check — the offline smoke

Proves the whole pipeline end-to-end with no keys or network:

```bash
pip install -e ".[dev]"
sf-smoke        # objective → worker → PR → merge, exits 0 on success
```

## 2. Run the loop on your own objective (offline)

Copy-paste and run — this works with no keys (the scripted worker writes `index.html`, the reviewer
approves once it exists, the in-memory forge merges):

```python
import asyncio
from pathlib import Path

from osf.driver import Driver, Review
from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.local.runtime import StaticSiteRuntime
from osf.model import Objective
from osf.types import RepoRef


class Reviewer:  # the definition of done — approve when the work is acceptable
    async def review(self, work_item, workspace) -> Review:
        ok = Path(workspace.path, "index.html").is_file()
        return Review(approved=ok, comment="" if ok else "need index.html")


async def main() -> None:
    driver = Driver(
        runtime=StaticSiteRuntime(),
        isolation=TempdirIsolation(),
        forge=InMemoryForge(),
        reviewer=Reviewer(),
        max_rounds=3,
    )
    outcome = await driver.run(
        Objective(id="demo", repo=RepoRef("me", "site"), goal="Create a landing page for demo.osf")
    )
    print("state:", outcome.state)  # "done" or "escalated"
    for item in outcome.items:
        print(item.work_item_id, item.state, f"PR#{item.pr.number}", f"rounds={item.rounds}")


asyncio.run(main())
```

Expected: `state: done` and one merged PR.

## 3. Run with a real agent engine (Fireworks)

Swap the scripted worker for a real one. Model selection uses opencode's `provider/model`
abstraction; default is Fireworks Kimi K2. Needs `FIREWORKS_API_KEY` in `.env`
(see [`README`](../README.md#live-run-real-agent)).

```python
from dotenv import load_dotenv
from osf.engines import resolve_runtime
from osf.engines.fireworks import DEFAULT_MODEL

load_dotenv()
runtime = resolve_runtime(DEFAULT_MODEL)  # or ModelRef.parse(os.environ["OSF_MODEL"])
# ...pass runtime=runtime into the Driver above.
```

## 4. Prepackaged run — create a repo with CI/CD

Trigger a named workflow instead of hand-building an objective. `create-repo` provisions the repo on
the forge and has the worker scaffold `README`/`LICENSE`/`.gitignore` **and** `.github/workflows/`
(CI/CD), then merges.

```python
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from osf.driver import Review
from osf.engines import resolve_runtime
from osf.engines.fireworks import DEFAULT_MODEL
from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.runs import execute, get_run

REQUIRED = ["README.md", "LICENSE", ".gitignore", ".github/workflows/ci.yml"]


class FilesReviewer:
    async def review(self, work_item, workspace) -> Review:
        missing = [f for f in REQUIRED if not Path(workspace.path, f).is_file()]
        return Review(approved=not missing, comment=f"missing: {missing}" if missing else "")


async def main() -> None:
    load_dotenv()  # FIREWORKS_API_KEY
    plan = get_run("create-repo").build({"name": "widgets", "description": "A widget library"})
    outcome = await execute(
        plan,
        runtime=resolve_runtime(DEFAULT_MODEL),
        isolation=TempdirIsolation(),
        forge=InMemoryForge(),  # dry run; see below for real GitHub
        reviewer=FilesReviewer(),
    )
    print("state:", outcome.state)


asyncio.run(main())
```

## 5. Against real GitHub

Swap the forge for `GitHubForge` (needs `GITHUB_TOKEN`/`GH_TOKEN`, `pip install -e ".[github]"`):

```python
from osf.forges.github import GitHubForge

forge = GitHubForge()          # user repos; GitHubForge(org=True) for an organization
```

`GitHubForge` creates the repo, opens/comments/merges PRs, and reads checks for real.

> ⚠️ **Not fully end-to-end yet.** The driver prepares the worker's workspace via the isolation
> backend, and today's `TempdirIsolation` is a throwaway *local* git repo — it does not push the
> branch to the created GitHub repo, so `open_pr` has nothing to target remotely. A **git-remote
> isolation backend** (clone the created repo → scaffold on a branch → push) is the remaining piece
> for live GitHub runs. Until then, use `GitHubForge` for repo creation / review / merge and the
> in-memory forge for the full offline loop.

## Reading the outcome

`driver.run(...)` / `execute(...)` return an `ObjectiveOutcome`:

- `state` — `"done"` (every WorkItem merged) or `"escalated"` (something hit `max_rounds`)
- `items` — per WorkItem: `state` (`merged`/`failed`), `pr`, and `rounds` (review iterations)
