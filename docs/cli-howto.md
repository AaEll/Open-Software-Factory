# CLI how-to: from install to a new repository

A task-first guide to the `sf` command. For the contracts underneath it — and for driving the loop
from Python instead — see [`running-the-loop.md`](running-the-loop.md).

## 1. Install

```bash
git clone https://github.com/AaEll/Open-Software-Factory
cd Open-Software-Factory
pip install -e .                     # CLI + offline reference adapters
```

Extras add the real backends, and can be combined:

| Extra | Adds | Needed for |
|---|---|---|
| `agent` | `openai`, `anthropic`, `python-dotenv` | `--model` (a real worker agent) |
| `github` | `httpx` | `--forge github` |
| `dev` | `pytest`, `ruff`, `httpx` | running the test suite |

```bash
pip install -e ".[agent,github]"     # everything needed for a live run
```

## 2. Check the install

```bash
sf smoke
```

Drives `objective → worker → PR → merge` against the offline adapters and prints `sf smoke: ok`.
No keys, no network. A non-zero exit means the install is broken, not your objective.

## 3. Your first objective (offline)

```bash
sf objective "Create a landing page for demo.osf" \
    --repo me/site \
    --criterion "index.html exists"
```

```
objective me-site: done
  me-site-1: merged (PR#1, rounds=1)
```

What just happened: the objective became one WorkItem, a worker ran in an isolated git workspace,
the in-memory forge opened PR #1, the reviewer checked the criterion, and the PR merged.

Two things to know about this offline mode:

- The worker is `StaticSiteRuntime`, a scripted stand-in that only ever writes `index.html`. Ask for
  anything else offline and the run escalates — that's the stub worker, not a failure of the loop.
- `--forge memory` (the default) is a dry run. Nothing is created on GitHub.

Each `--criterion` that names a file becomes a merge gate; criteria that name no file (e.g.
`"the page looks professional"`) are recorded but never block. See
[`osf/review.py`](../osf/review.py).

## 4. Add a real agent

Real workers need the `agent` extra and a provider key. Keys are read from the environment or a
`.env` file in the working directory (gitignored):

```bash
cp .env.example .env      # then set FIREWORKS_API_KEY (FIREWORKS also works)
```

Select the engine with `--model provider/model` — the provider half picks the adapter
(`fireworks` is the default engine, `anthropic` is the second). Pass the full ref:

```bash
sf objective "Create a landing page for demo.osf" \
    --repo me/site \
    --criterion "index.html exists" \
    --model fireworks/accounts/fireworks/models/kimi-k2p7-code
```

Run it from the directory holding your `.env` — `load_dotenv()` searches upward from the working
directory, not from the install location.

## 5. Create a new repository

`create-repo` is a prepackaged run: it provisions the repo on the forge, then has a worker scaffold
`README.md`, `LICENSE`, `.gitignore`, and `.github/workflows/` (CI **and** release), and merges when
all four exist.

```bash
sf runs                  # create-repo  Create and scaffold a new repository for the user.
```

```bash
sf run create-repo \
    -p name=widgets \
    -p description="A widget library" \
    -p language=python \
    -p owner=me \
    --model fireworks/accounts/fireworks/models/kimi-k2p7-code
```

```
objective create-repo-widgets: done
  create-repo-widgets-scaffold: merged (PR#1, rounds=1)
```

Parameters (`-p key=value`, repeatable):

| Key | Required | Default | Used for |
|---|---|---|---|
| `name` | yes | — | repository name |
| `description` | no | `""` | README/repo description |
| `language` | no | `python` | told to the worker; shapes the `.gitignore` and CI workflow |
| `owner` | no | `sf` | the owner half of `owner/name` |

Omitting `--model` runs the scripted worker, which cannot scaffold a repository — the run escalates
after `--max-rounds` rounds. This command needs a real agent.

### Finding what the worker wrote

The dry run scaffolds into a throwaway workspace under your temp directory, named for the repo:

```bash
ls -dt "${TMPDIR:-/tmp}"/osf-widgets-* | head -1     # newest workspace
```

Inspect it with `git -C <path> show --stat HEAD`. Workspaces are not cleaned up after a run, so
delete them when you're done. (The outcome does not yet print this path — see the gap below.)

## 6. Against real GitHub

```bash
pip install -e ".[github]"
export GITHUB_TOKEN=ghp_...          # or GH_TOKEN; needs repo scope
sf run create-repo -p name=widgets -p owner=me --forge github --model <ref>
```

Add `--org` to create under an organization instead of the authenticated user.

> ⚠️ **Not end-to-end yet, and this one has real side effects.** `--forge github` really does create
> the repository (`auto_init`, so it has a default branch). The run then fails at `open_pr` with an
> HTTP 422 traceback, because today's `TempdirIsolation` builds a *local* throwaway repo and never
> pushes the branch — so GitHub has no `head` branch to open a PR from. You are left with a created,
> empty repo. The missing piece is a **git-remote isolation backend** (clone → branch → push); until
> it lands, use `--forge github` only if you want the repo provisioned, and `--forge memory` for the
> full loop.

## Flag reference

Shared by `sf objective` and `sf run`:

| Flag | Default | Meaning |
|---|---|---|
| `--model PROVIDER/MODEL` | offline scripted runtime | engine the workers run on |
| `--forge memory\|github` | `memory` | in-memory dry run, or real GitHub |
| `--org` | off | with `--forge github`, create repos under an organization |
| `--max-rounds N` | `3` | review iterations per WorkItem before escalating |
| `--json` | off | print the outcome as JSON instead of text |

`sf objective` also takes `--repo OWNER/NAME` (required), `--criterion TEXT` (repeatable), and
`--id` (defaults to `owner-name`). `sf run` takes `-p/--param KEY=VALUE` (repeatable).

## Reading the result

| Exit code | State | Meaning |
|---|---|---|
| `0` | `done` | every WorkItem merged |
| `1` | `escalated` | a WorkItem hit `--max-rounds` without an approved, green PR |

`--json` emits the same information for scripting:

```json
{
  "objective_id": "create-repo-widgets",
  "state": "done",
  "items": [
    {"work_item_id": "create-repo-widgets-scaffold", "state": "merged", "pr": 1, "rounds": 1}
  ]
}
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `sf: command not found` | `pip install -e .` ran in a different environment than your shell's `python` |
| `set FIREWORKS_API_KEY (or FIREWORKS) ...` | no key in the environment, or you ran from a directory above your `.env` |
| `No module named 'openai'` | `--model` without the `agent` extra |
| `set GITHUB_TOKEN or GH_TOKEN for GitHubForge` | `--forge github` without a token |
| `no engine adapter for provider 'x'` | `--model` provider is not `fireworks` or `anthropic` |
| `sf: bad --repo 'site'` | `--repo` needs `OWNER/NAME` |
| run escalates immediately | usually the offline scripted worker — pass `--model` |

## Known gaps

- No live GitHub loop until the git-remote isolation backend lands (see §6).
- The outcome doesn't report the workspace path, so scaffolded files must be found by hand (§5).
- `--model` needs the full `provider/model` ref; there's no shorthand for the default model,
  and the CLI does not read the `OSF_MODEL` variable from `.env` (only the evals do).
