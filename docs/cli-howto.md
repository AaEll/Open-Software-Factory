# CLI how-to: from install to a new repository

`sf` is a shell, not a set of flags. You open it, tell it what you want in plain language, and use
`/commands` for the structured flows. This guide walks install → first objective → creating a new
repository. For the contracts underneath — and for driving the loop from Python — see
[`running-the-loop.md`](running-the-loop.md).

## 1. Install

```bash
git clone https://github.com/AaEll/Open-Software-Factory
cd Open-Software-Factory
pip install -e .                     # the shell + offline reference adapters
```

Extras add the real backends, and can be combined:

| Extra | Adds | Needed for |
|---|---|---|
| `agent` | `openai`, `anthropic`, `python-dotenv` | `/model` (a real worker agent) |
| `github` | `httpx` | `/forge github` |
| `dev` | `pytest`, `ruff`, `httpx` | running the test suite |

```bash
pip install -e ".[agent,github]"     # everything needed for a live run
```

## 2. Open the shell

```console
$ sf
Open Software Factory
  offline (scripted worker) · forge memory · /help for commands

›
```

The second line is your session: which engine workers run on, and where PRs are opened. Both start
safe — a scripted offline worker and an in-memory forge — so nothing you type reaches the network
until you change them. `/help` lists the commands; Ctrl-D or `/quit` leaves.

Not sure the install is sound? `/smoke` drives the whole `objective → worker → PR → merge` pipeline
offline and reports `smoke: ok`.

## 3. Your first objective

Anything that isn't a `/command` becomes an objective. The shell asks for the target repository the
first time and remembers it:

```console
› Create a landing page for demo.osf
? Repository (owner/name) › me/site
? Acceptance criteria naming files, comma-separated › index.html exists
  me-site: done
  me-site-1: merged (PR#1, rounds=1)
```

The objective became one WorkItem, a worker ran in an isolated git workspace, the forge opened
PR #1, the reviewer checked that `index.html` existed, and it merged.

**Acceptance criteria are the merge gate.** Each one that names a file must exist in the workspace
before the PR merges; criteria naming no file (`the page looks professional`) are recorded but never
block. Leave the question blank to gate on nothing. See [`osf/review.py`](../osf/review.py).

Offline, the worker is `StaticSiteRuntime`, a scripted stand-in that only ever writes `index.html`.
Ask it for anything else and the objective escalates — that's the stub worker, not the loop failing.
Give it a real engine:

```console
› /model fireworks/accounts/fireworks/models/kimi-k2p7-code
  engine: fireworks/accounts/fireworks/models/kimi-k2p7-code
```

Keys are read from the environment or a `.env` in the directory you launched `sf` from
(`cp .env.example .env`, then set `FIREWORKS_API_KEY`; `FIREWORKS` also works). Setting `OSF_MODEL`
there selects the engine at startup so you don't have to type `/model` each time. `/model off`
returns to the offline worker.

## 4. Create a new repository

`/new-repo` runs the `create-repo` prepackaged workflow. It asks one question per parameter the run
declares, shows you the objective and the gates it derived, and asks before doing anything:

```console
› /new-repo
  create-repo — Create and scaffold a new repository for the user.
? Repository name › widgets
? What should it do? › A widget library
? Starting point
  › 1. Template with CI/CD  README, LICENSE, .gitignore, workflows
    2. Blank repository  README and .gitignore only
  › 1
? Primary language (python) ›
? Owner (the owner half of owner/name) (osf) › me
  objective: Create a new python repository 'widgets' with CI/CD: A widget library
  gates: README.md exists, LICENSE exists, .gitignore exists, .github/workflows/ci.yml exists
? Run it on me/widgets with fireworks/accounts/fireworks/models/kimi-k2p7-code? (Y/n) y
  create-repo-widgets: done
  create-repo-widgets-scaffold: merged (PR#1, rounds=1)
```

Questions in parentheses show a default — press Enter to take it. On a select, press Enter for the
`›` option or type its number.

**Starting point** is the choice that changes the work:

| Choice | The worker must produce | Skill |
|---|---|---|
| Template with CI/CD | `README.md`, `LICENSE`, `.gitignore`, `.github/workflows/ci.yml` (+ `release.yml`) | `new-repo-ci` |
| Blank repository | `README.md`, `.gitignore` | `new-repo-blank` |

Those file lists *are* the acceptance criteria, so the PR cannot merge until they exist.

This flow needs a real engine — the offline scripted worker cannot scaffold a repository, and the
run will escalate. `/runs` lists the available workflows; `/run <name>` starts any of them.

### Finding what the worker wrote

With `/forge memory` (the default) the scaffold lands in a throwaway workspace under your temp
directory, named for the repo:

```bash
ls -dt "${TMPDIR:-/tmp}"/osf-widgets-* | head -1     # newest workspace
```

Inspect it with `git -C <path> show --stat HEAD`. Workspaces are not cleaned up, so delete them when
you're done. (The outcome does not yet print this path — see the gaps below.)

## 5. Session commands

| Command | What it does |
|---|---|
| `/help` | list the commands |
| `/new-repo` | create and scaffold a new repository |
| `/runs`, `/run [name]` | list the prepackaged runs; start one |
| `/repo [owner/name]` | set the target repository for free-text objectives |
| `/model [provider/model\|off]` | set the engine workers run on |
| `/forge [memory\|github\|github-org]` | where PRs are opened |
| `/rounds [n]` | review rounds before a WorkItem escalates (default 3) |
| `/status` | show all of the above |
| `/smoke` | offline pipeline self-check |
| `/quit` (`/exit`, Ctrl-D) | leave |

Called with no argument, the setting commands print the current value instead of changing it.

## 6. Against real GitHub

```console
› /forge github
  forge: github
```

Needs `pip install -e ".[github]"` and `GITHUB_TOKEN` (or `GH_TOKEN`) with repo scope. Use
`github-org` to create repositories under an organization instead of the authenticated user. When
the forge is not `memory`, the wizard warns you before the confirm step that the repository will be
created for real.

> ⚠️ **Not end-to-end yet, and this one has real side effects.** `/forge github` really does create
> the repository (`auto_init`, so it has a default branch). The run then fails at `open_pr` with an
> HTTP 422, because today's `TempdirIsolation` builds a *local* throwaway repo and never pushes the
> branch — so GitHub has no `head` branch to open a PR from. You are left with a created, empty
> repo; the shell reports the error and returns to the prompt. The missing piece is a **git-remote
> isolation backend** (clone → branch → push). Until it lands, use `github` only if you want the
> repo provisioned, and `memory` for the full loop.

## 7. Reading the result

| State | Meaning |
|---|---|
| `done` | every WorkItem merged |
| `escalated` | a WorkItem hit `/rounds` attempts without an approved, green PR |

Per WorkItem the shell prints its state, PR number, and how many review rounds it took.

## 8. Automation without a TTY

The shell is the only interactive entry point; there is no flag-driven equivalent. For CI, the
pass-through smoke test has its own console script that needs no terminal:

```bash
sf-smoke        # objective → worker → PR → merge, exits non-zero on failure
```

That is what the `build` and `image` CI jobs run against the installed wheel and the container
image, and it is the container's default `CMD`. To script anything richer, drive the driver from
Python — see [`running-the-loop.md`](running-the-loop.md).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `sf: command not found` | `pip install -e .` ran in a different environment than your shell's `python` |
| `set FIREWORKS_API_KEY (or FIREWORKS) ...` | no key in the environment, or you launched `sf` from a directory above your `.env` |
| `ModuleNotFoundError: No module named 'openai'` | `/model` without the `agent` extra |
| `set GITHUB_TOKEN or GH_TOKEN for GitHubForge` | `/forge github` without a token |
| `no engine adapter for provider 'x'` | `/model` provider is not `fireworks` or `anthropic` |
| `bad repo 'site'; expected OWNER/NAME` | `/repo` needs `owner/name` |
| everything escalates | usually the offline scripted worker — set `/model` |

Errors are reported and the prompt returns; a failed run never drops you out of the shell.

## Known gaps

- No live GitHub loop until the git-remote isolation backend lands (see §6).
- The outcome doesn't report the workspace path, so scaffolded files must be found by hand (§4).
- Free text is matched to an objective, not to a run: the shell won't infer "make me a repo" and
  open `/new-repo` for you. Prepackaged workflows are reached by command.
