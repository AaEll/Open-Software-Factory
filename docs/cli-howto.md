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

## 2. Open the shell — in your project

`sf` works on the repository you launch it from, editing that working tree in place:

```console
$ cd ~/code/my-project
$ sf
Open Software Factory
  fireworks/accounts/fireworks/models/kimi-k2p7-code · /Users/you/code/my-project · /help for commands

›
```

The second line is your session: the engine, and the project being edited. `/project <path>` points
it somewhere else, `/help` lists the commands, Ctrl-D or `/quit` leaves.

If the directory isn't a git repository, `sf` offers to `git init` it — snapshots need git objects,
and without them there would be no way to undo a change.

Not sure the install is sound? `/smoke` drives the whole `objective → worker → PR → merge` pipeline
offline and reports `smoke: ok`.

## 3. Talking to the driver

Anything that isn't a `/command` goes to the driver agent, which decides what it is. Not everything
you type is a build request:

```console
› hi bot
  Hi there! I'm the driver agent of an autonomous software factory — I can help you build apps,
  scaffold repositories, plan features, and much more. What would you like to build today?

› make me a new repository called widgets, blank, no CI
  this looks like create-repo
  create-repo — Create and scaffold a new repository for the user.
? Repository name (widgets) ›
? Starting point
    1. Template with CI/CD  README, LICENSE, .gitignore, workflows
  › 2. Blank repository  README and .gitignore only
```

Three things can happen: the driver **answers** you, **starts a workflow** — carrying over what you
already said, so `widgets` and `blank` arrive as the defaults and you just press Enter — or
**plans the work**. It only ever proposes; nothing runs until you accept.

If the driver is unreachable, or names a workflow that doesn't exist, your message is planned as
work rather than dropped. Offline there is no judgement to apply, so everything is planned.

## 3.1 Asking for work

A request to build something gets planned. The driver shows the plan and does nothing until you
accept:

```console
› Create a landing page for my dog Pobrecita
  a few questions before I plan (Enter to skip any):
? What is the main purpose: playful profile, memorial, adoption page? › playful profile
? What sections must be included (bio, photos, stats, contact)? › bio and photos
? What tone should the page have (cute, elegant, humorous)? › playful
  planning…
  plan  Build a charming single-page landing site for Pobrecita the dog.
    1. Create a responsive landing page with a hero section, a short bio and a photo gallery
       area, using HTML and CSS.  → index.html, styles.css
? Run this? (Enter to accept, or say what to change) ›
  you-my-project: done
  you-my-project-1: merged (rounds=1)
```

After the run you see what changed and decide:

```console
  changed in /Users/you/code/my-project
    index.html | 27 +++++++++++++++++++++++++++
    styles.css |  8 ++++++++
    2 files changed, 35 insertions(+)
? Keep these changes? (Y/n) y
  kept — review them with git diff, commit when you're happy
```

Answering `n` restores every file the run touched — additions deleted, edits and deletions put
back — from a snapshot taken before it started. The snapshot is of your *working tree*, not of the
last commit, so uncommitted work you had in progress comes back exactly as it was.

One limitation worth knowing: **gitignored paths are outside the safety net.** Snapshots use
`git add -A`, which skips ignored files, so anything written under an ignored path (`secrets/`,
`node_modules/`, a local `.env`) is neither listed as changed nor undone by a revert.

**`sf` never commits for you, and never stages anything.** Snapshots are captured through a private
git index under `.git/`, so your staging area is exactly as you left it. The change lands in your
working tree as unstaged edits and untracked files; committing, branching and pushing stay yours.

**The driver asks before it plans.** Those questions are written by the driver agent for *your*
request — not a fixed form — and your answers go into the plan it proposes. Press Enter to skip any
of them; skip them all and it plans from the request alone. `/ask off` turns the interview off for
the session.

**You never have to write a merge gate.** The `→ files` on each step *are* the definition of done:
the step isn't accepted until those files exist, and it retries until they do. The driver proposes
them from your request; you just say whether the plan is right.

A step may also propose a **command** that proves it worked, shown before you accept:

```console
    1. Create fizzbuzz.py containing a fizzbuzz function…  → fizzbuzz.py
       ✓ runs: python -c "from fizzbuzz import fizzbuzz; assert fizzbuzz(15) == 'FizzBuzz'"
    2. Create tests/test_fizzbuzz.py with pytest cases…  → tests/test_fizzbuzz.py
       ✓ runs: python -m pytest -q tests/test_fizzbuzz.py
```

The step is not accepted until that command exits 0, and its output goes back to the worker as
feedback. This is what catches the failures a file-existence gate cannot — a module that imports but
does nothing, an entry point quietly turned into a library.

Accepting the plan is what authorises those commands to run, so read them. They are run **without a
shell** (no `&&`, `;` or redirection — those become literal arguments), with a 60-second timeout,
and with bytecode writing disabled so a check cannot litter your working tree.

**Feedback is plain language.** Anything that isn't Enter or `no` is treated as a change request,
and the driver re-plans with it:

```console
? Run this? (Enter to accept, or say what to change) › also add a photo gallery page
  planning…
  plan  Build a landing page and a separate photo gallery page for Pobrecita the dog.
    1. Create a responsive landing page (index.html) with a hero section, bio and a link to the
       gallery; style it with styles.css.  → index.html, styles.css
    2. Create a responsive photo gallery page with a grid of photos and navigation back to the
       landing page.  → gallery.html, gallery.css
? Run this? (Enter to accept, or say what to change) › y
```

Enter, `y`, `ok`, `run` accept; `n`, `no`, `cancel` abandon the request; anything else is feedback.

The driver and each worker are both told the working directory and what it already contains (git's
view, so ignored files like `.env` and `node_modules/` stay out) — the plan is made against the
repository you actually have, not an imagined one.

**Your project's own conventions are read too.** If the repository has an `AGENTS.md` (or a
`CLAUDE.md`), it is included in both the plan and the work, along with a global
`~/.config/osf/AGENTS.md` if you keep one. The project's file wins where they disagree, and when
there is too much to fit, broader files are dropped whole before yours is truncated.

Workers have five tools: `read_file`, `edit_file` (replace one exact string, leaving the rest
alone), `write_file` (create, or replace wholesale), and `find_files` / `search_files` for looking
beyond the listing. Three rules are enforced rather than requested:

- **an existing file cannot be overwritten until it has been read** in that session — a blind
  `write_file` over your work is refused with an explanation
- **an ambiguous edit is refused**: if the text to replace appears more than once, the worker is
  told to be more specific rather than guessing which one you meant
- **anything git ignores is out of bounds**, for reading as well as writing — `.env`, credentials,
  `node_modules/`, and `.git/` itself are refused, so a guessed filename cannot put your keys in a
  model request

Each step is reviewed against its own files. Locally the steps run in
order **in the same repository**, so a later step sees what the earlier ones wrote and can extend
it — ask for "a landing page, then a gallery page linked from it" and the link actually works. The
planner is told which mode it is in, because under `/forge memory` steps get separate workspaces
and must stand alone.

### About the repository name

Local work needs no name: the project you are in *is* the target. The other forges ask for one, and
detect the owner — `OSF_OWNER`, `GITHUB_OWNER`, `GH_OWNER` or `GITHUB_USER` if set, otherwise
whoever `gh auth login` signed in as (read from `~/.config/gh/hosts.yml`, never over the network),
and `local` if nothing is signed in. Typing `owner/name` at the name question works too.

Answers are checked as you give them: an unusable name is explained and asked again, in place. A
typo never costs you the request you just typed.

### The engine

`sf` picks up an engine at startup, so there is usually nothing to set: `OSF_MODEL` if you set one,
otherwise Fireworks whenever a `FIREWORKS_API_KEY` (or `FIREWORKS`) is in your environment or a
`.env` in the directory you launched from. The banner tells you which engine you got.

```console
$ sf
Open Software Factory
  fireworks/accounts/fireworks/models/kimi-k2p7-code · /Users/you/code/my-project · /help for commands
```

With no key — or without the `agent` extra installed — it says `offline (scripted worker)` instead.
Offline there is no driver to plan with: the planner echoes your request back as a single step with
no gate, and the scripted worker only ever writes `index.html`. That's enough to watch the loop
work, not to build anything. `/model provider/model` switches engines mid-session and `/model off`
returns to the scripted worker.

```console
› /model fireworks/accounts/fireworks/models/kimi-k2p7-code
  engine: fireworks/accounts/fireworks/models/kimi-k2p7-code
```

Planning is a plain completion against the same model the workers use — no tools, no workspace — so
it costs a fraction of a worker run. If a reply comes back unusable the driver is nudged once to
retry; if planning fails outright (no key, provider down) the shell says so and falls back to your
request as written rather than dropping it.

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
  objective: Create a new python repository 'widgets' with CI/CD: A widget library
  gates: README.md exists, LICENSE exists, .gitignore exists, .github/workflows/ci.yml exists
? Run it in ./widgets with fireworks/accounts/fireworks/models/kimi-k2p7-code? (Y/n) y
  create-repo-widgets: done
  create-repo-widgets-scaffold: merged (rounds=1)
  scaffolded into /Users/you/code/widgets
```

Locally this makes a **real new directory** next to the one you're in, `git init`s it, and scaffolds
there — it does not pour a README and workflows on top of the project you're currently in.

Questions in parentheses show a default — press Enter to take it. On a select, press Enter for the
`›` option or type its number. The owner is detected, not asked (see above).

**Starting point** is the choice that changes the work:

| Choice | The worker must produce | Skill |
|---|---|---|
| Template with CI/CD | `README.md`, `LICENSE`, `.gitignore`, `.github/workflows/ci.yml` (+ `release.yml`) | `new-repo-ci` |
| Blank repository | `README.md`, `.gitignore` | `new-repo-blank` |

Those file lists *are* the acceptance criteria: the step isn't accepted until they exist.

This flow needs a real engine — the offline scripted worker cannot scaffold a repository, and the
run will escalate. `/runs` lists the available workflows; `/run <name>` starts any of them.

### Finding what the worker wrote

Locally, the path is printed when the run finishes (`scaffolded into …`). Under `/forge memory` the
scaffold goes to a throwaway workspace instead:

```bash
ls -dt "${TMPDIR:-/tmp}"/osf-widgets-* | head -1     # newest workspace
```

Inspect it with `git -C <path> show --stat HEAD`. Those workspaces are never cleaned up, so delete
them when you're done.

## 5. Session commands

| Command | What it does |
|---|---|
| `/help` | list the commands |
| `/new-repo` | create and scaffold a new repository |
| `/runs`, `/run [name]` | list the prepackaged runs; start one |
| `/project [path]` | the repository `sf` edits (default: the one you launched from) |
| `/diff` | what has changed in the project (`git status --short`) |
| `/repo [name\|owner/name]` | target repository, for the non-local forges |
| `/model [provider/model\|off]` | set the engine workers run on |
| `/forge [local\|memory\|github\|github-org]` | where the work lands (default `local`: your repo, no PRs) |
| `/rounds [n]` | review rounds before a WorkItem escalates (default 3) |
| `/ask [on\|off]` | whether the driver asks clarifying questions before planning (default on) |
| `/status` | show all of the above |
| `/smoke` | offline pipeline self-check |
| `/quit` (`/exit`, Ctrl-D) | leave |

Called with no argument, the setting commands print the current value instead of changing it.

## 6. Working somewhere other than your repo

`/forge` chooses where the work lands:

| Mode | Where the work goes | PRs |
|---|---|---|
| `local` (default) | the repository you launched from, in place | none |
| `memory` | a throwaway git repo under `$TMPDIR`, one per step | in-process only |
| `github` / `github-org` | provisions a real repository | real, on GitHub |

`memory` is the dry run: steps get separate scratch workspaces, so they cannot build on each other
and the output lives in `$TMPDIR/osf-<name>-*`, which is never cleaned up. `local` is the mode that
behaves the way you'd expect a coding agent to.

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
| banner says `offline (scripted worker)` | no `FIREWORKS_API_KEY`/`FIREWORKS` found, or the `agent` extra isn't installed |
| `set FIREWORKS_API_KEY (or FIREWORKS) ...` | no key in the environment, and no `.env` at or above the directory you launched `sf` from |
| `ModuleNotFoundError: No module named 'openai'` | `/model` without the `agent` extra |
| `set GITHUB_TOKEN or GH_TOKEN for GitHubForge` | `/forge github` without a token |
| `no engine adapter for provider 'x'` | `/model` provider is not `fireworks` or `anthropic` |
| `'my repo!' isn't a valid repository name` | letters, numbers, dot, dash, underscore only — the question is asked again |
| `planner unavailable (...)` | no key or the provider is down; the shell falls back to your request, ungated |
| everything escalates | usually the offline scripted worker — set `/model` |

Errors are reported and the prompt returns; a failed run never drops you out of the shell, and a
rejected answer is re-asked rather than abandoning what you were doing.

## Known gaps

- No live GitHub loop until the git-remote isolation backend lands (see §6).
- Under `/forge memory` the outcome doesn't report the workspace path (locally it does).
- Free text is planned as an objective, not matched to a run: the shell won't infer "make me a
  repo" and open `/new-repo` for you. Prepackaged workflows are reached by command.
- With `/forge memory` or `github`, steps still get separate scratch workspaces and cannot build on
  each other. Locally they share the project, so they can.
- Nothing removes `$TMPDIR/osf-*` workspaces left by the non-local forges.
