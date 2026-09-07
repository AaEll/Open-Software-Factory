# MVP scope

**A local agent that can take a request from plain language to deployed infrastructure.**

You run `sf` in a directory. You say what you want. It plans the work with you, edits the files in
that directory, proves the result runs, shows you the diff, and — when you ask — puts it on the
internet. One tool, one session, from empty folder to live URL.

That is the whole MVP. Everything below says what is already true, what is not yet, and how we will
know when it is finished.

## The path that defines "done"

From a cold machine, in an empty directory:

```console
$ mkdir mountain-brew && cd mountain-brew && git init
$ sf
Open Software Factory
  fireworks/…/kimi-k2p7-code · ~/mountain-brew · /help for commands

› a landing page for a specialty coffee shop in Denver called Mountain Brew
  a few questions before I plan (Enter to skip any):
? What should the page emphasise — beans, cafe visits, subscriptions? › cafe visits
  planning…
  plan  Build a single-page site for Mountain Brew with hero, hours and location.
    1. Create index.html and styles.css with the hero, hours, and a map link.  → index.html, styles.css
       ✓ runs: python -c "assert 'Mountain Brew' in open('index.html').read()"
? Approve this plan? (Enter to accept, or say what to change) ›
  working: Create index.html and styles.css…
  mountain-brew: done  8.2k tokens
  changed in ~/mountain-brew
    index.html | 84 ++++++++++
    styles.css | 61 +++++++
? Keep these changes? › 1
  kept — review them with git diff, commit when you're happy

› /publish
  publishing 2 files…
  preview: https://mountain-brew-a1b2.workers.dev
? Check the domain mountainbrew.coffee? (y/N) › y
  available · $12.00/yr · pay through PromptPay to claim it
```

When that runs end to end without a developer editing a Python file, the MVP is done.

## In scope

| | |
|---|---|
| **Local editing** | The agent works in the repository you launched it from, in place, with snapshots as the undo |
| **Planning with a human** | It proposes; you accept, redirect in plain language, or decline. Nothing runs unapproved |
| **Proving the work** | Steps carry a command that must exit 0, not just files that must exist |
| **Deployment** | Publishing a preview to cloud hosting, checking a domain, and paying for it through a broker |
| **Unattended use** | `sf "…" --yes` for scripts and CI, with a meaningful exit code |

## Out of scope for the MVP

Named here so nobody rebuilds them by accident:

- **The autonomous forge loop.** No driver agent reviewing GitHub PRs, no merge-on-green, no
  multi-agent coordination through the forge. `GitHubForge` exists and works, but the MVP does not
  depend on it. This is the long-term product; it is not this milestone.
- **Durable, resumable sessions.** A run lives and dies in one process. Event sourcing is in
  `docs/ARCHITECTURE.md` and stays there for now.
- **Concurrency.** WorkItems run in order. `depends_on` exists in the model and is ignored.
- **Multi-agent delegation.** One driver, one worker per step.
- **Cloud execution.** The agent runs on your machine. Only the *deployment* touches the cloud.

## Where we are

### Working, and verified by running it

- **The shell.** Bare `sf` opens a session; the driver routes each message — answer, start a
  workflow, or plan the work. `sf "request"` does one thing and exits.
- **Planning.** The driver asks up to three clarifying questions, proposes a plan of 1–3 steps with
  the files each produces and a command that proves it, and revises on plain-language feedback.
- **Editing.** Five sandboxed tools (`read_file`, `edit_file`, `write_file`, `find_files`,
  `search_files`) with policy enforced rather than requested: read before overwrite, no ambiguous
  edits, nothing git ignores, nothing outside the workspace.
- **Context.** The working directory, its file listing, and the project's own `AGENTS.md` /
  `CLAUDE.md` reach both the driver and the worker.
- **Checks.** A step's command runs with no shell, a 60s timeout, and bytecode writing off; failure
  output goes back to the worker as feedback.
- **Review.** Keep everything, revert everything, or walk the diff file by file.
- **Cost and progress.** Each step is announced; usage is reported in dollars, or tokens when the
  provider publishes no price.
- **Offline tests.** 218 tests, including engine adapters replayed from recorded API responses, so
  CI needs no key and spends nothing.

### Built, but not reachable from the product

`osf/promptpay/` speaks to the PromptPay broker — health, authorize, domain preview, publish
preview — and `publish_objective_site()` turns a workspace into a live preview. It works, and
`python -m evals.mvp_promptpay` demonstrates it. **But it is only reachable from that eval script.**
The shell has no `/publish`, and the eval publishes from a throwaway `TempdirIsolation` workspace
rather than from the project you are actually working in.

That gap is the MVP.

## What is missing

Ordered by what blocks the demo above.

1. **`/publish` in the shell, and `--publish` headless.** Publishing must be something a user asks
   for in the session that produced the files, not a separate Python module.
2. **Publish from the local project.** `publish_objective_site` takes a `Workspace`; the local path
   is a `ProjectIsolation` over the user's repo. Wiring these is small, but until it is done the
   thing you deploy is not the thing you edited.
3. **Scope what gets published.** `collect_site_files` walks the whole tree for known text suffixes.
   In a throwaway workspace that is every generated file; in a real repository it is also every
   `README.md`, every `package.json`, and everything under `node_modules/`. It must respect
   `.gitignore` and publish a site directory, not a repository.
4. **Deployment state.** A second `/publish` should update the same preview, not orphan the first.
   We keep no record of what is deployed where.
5. **The driver should know deployment exists.** "Build me a landing page and put it online" is one
   request; today the plan can only cover the building half.
6. **A cold-start path.** The demo above requires PromptPay running locally with an encryption key,
   an allowlist and a budget. One documented command, or it is not an MVP anyone else can run.

Two smaller items that are not blockers but are close:

- **The driver plans without being able to look inside files.** It sees a listing; the worker has
  search tools and the driver does not. Cheap improvements are possible before the full tool loop.
- **Speed.** A broad request becomes a 2–3 step plan at up to 12 model round trips per step. One
  observed run took nine minutes. Worth measuring before adding driver tools on top.

## How we will know it is done

- [ ] `sf` in an empty git repo produces a working site from one sentence, with the diff kept
- [ ] `/publish` returns a live preview URL for the files in *that* directory
- [ ] A domain can be checked and claimed through the broker in the same session
- [ ] `sf "…" --yes` does the same headlessly and exits non-zero when it fails
- [ ] Nothing outside the project is published, and no credential ever reaches the model
- [ ] The whole path is documented as a sequence a stranger can follow on a clean machine
- [ ] Tests cover the publish path offline, with no broker and no spend

## Relationship to the long-term product

`docs/ARCHITECTURE.md` describes an autonomous, forge-coordinated, event-sourced factory —
objectives in, merged PRs out, minimal human input. That is still the destination. The MVP is
deliberately the *local* half of it, with a human in the loop at two points (accept the plan, keep
the result) and a deployment path bolted on so the product does something end to end.

The pieces built for the MVP are not throwaway: the `AgentRuntime` / `IsolationBackend` / `Forge`
seams, the planner, the tool policy, and the recorded-response tests all carry forward. What the
MVP does *not* do is pretend the autonomous loop already works.
