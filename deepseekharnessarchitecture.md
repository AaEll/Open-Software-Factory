# Reading deepseek-harness: session start and model context

Notes from reading `deepseek-harness` (sibling checkout) with one question in mind: **what does a
model actually see at the start of a session, and how is that assembled?** Written for OSF, so
every section ends with what it means for us. Source paths are relative to that checkout.

Companion piece: [`opencodearchitecture.md`](opencodearchitecture.md) reads the same question
against opencode, and section 6 there compares the two.

Not a survey of the whole harness — it is large, and most of it (Cordis plugin composition,
profiles, bundles) is orthogonal to our problem. What follows is the context pipeline.

## 1. The system prompt is a registry, not a string

`packages/core/system-prompt` owns `ctx.systemPrompt`, and every prompt contributor registers a
named **section** with a centrally allocated order (`src/index.ts`, `SECTION_ORDERS`):

```
HARNESS_IDENTITY   -1000     PLAN_POLICY          500
HARNESS_SOURCE      -900     FILE_REFERENCE       900
WEB_SURFACE         -800     TOOL_BASH           1000
DEPLOYMENT_PERSONA     0     TOOL_READ           1100
                             TOOL_WRITE          1200
                             TOOL_EDIT           1300
                             TOOL_GLOB / GREP    1400 / 1500
                             ...
                             DELIVERABLE_FILE_REFERENCES  9000
                             STRUCTURED_OUTPUT            9900
```

Two things follow from this that a single prompt constant cannot do:

- **Each tool ships its own prompt section.** `TOOL_READ` is registered by `packages/fs/tool-fs`,
  `TOOL_BASH` by `packages/shell/tool-bash`. Mount the tool, get its instructions; unmount it, the
  guidance leaves with it. Instructions cannot drift from the tool they describe, and a prompt can
  never describe a tool the model does not have.
- **Order is allocated centrally, contributions are not.** Slots are named constants owned by the
  prompt package; a plugin asks for `getSectionOrder('TOOL_EDIT')` rather than inventing a number.
  Composition stays open, layout stays deliberate.

A section may also declare `complete: true`, meaning "I am the entire system prompt" — assembly
still runs so tools and variables resolve, then discards every other section. That is how a preset
gives an agent a wholly different identity without forking the loop.

> **For OSF.** Our `WORKER_SYSTEM` is one constant that describes `write_file`, `read_file` and
> `edit_file` in prose. It has already drifted once: the opening sentence still says "you implement
> the requested change by writing files with the write_file tool" — written when that was the only
> tool, and now contradicting the paragraph below it that says prefer `edit_file`. A section per
> tool, assembled from whatever tools the engine registers, makes that class of drift impossible.

## 2. Mutable facts are *not* in the system prompt

Alongside sections there is a second, deliberately separate registry: `PromptContext`, described as
"the cache-safe counterpart to `PromptSection`". Its contributions are materialized as a **durable
user-role snapshot placed after retained history**, not as prompt text, and re-emitted only when the
value changed.

Only three things use it (`CONTEXT_ORDERS`): `SANDBOX_POLICY`, `APPROVAL_POLICY`,
`SUBAGENT_DELEGATION`. The comment in `packages/interaction/user-approval/src/index.ts` gives the
reason outright:

> The complete current value travels after retained history, so switching policy does not rewrite
> the stable system-prompt cache prefix.

So the split is: **stable facts go in the system prompt; facts that change mid-session go in a late
user message.** It is a prompt-caching decision as much as a correctness one — mutating the prefix
invalidates the cache for the whole conversation.

> **For OSF.** We build the whole prompt per step from `worker_system(workspace)`, including the
> file listing, which changes the moment the agent writes anything. Every step therefore has a
> different prefix. We are small enough that this costs little today, but the shape is wrong, and
> it is the reason our listing can go stale mid-session without anything noticing.

## 3. What actually arrives at the first step

For a fresh session, in order:

1. **Identity and source** — who the harness is; `HARNESS_SOURCE` names the DSH checkout path and
   then explicitly warns against inferring anything from it: *"The checkout location and current
   working directory are separate values and may differ; never infer the working directory from
   this path. Use pwd to determine the current working directory."*
2. **Deployment persona** — `packages/preset/persona`, shadowable per agent by a preset.
3. **Policy sections** — plan mode, team policy, and the per-tool sections for whatever is mounted.
4. **Workspace instructions** — `packages/context/agent-instructions` composes the `AGENTS.md`
   chain (user-global file plus the project chain) into the first request as a durable baseline.
5. **Runtime context** — sandbox mode and approval policy, as the user-role snapshot from §2.

Then the loop runs: `turn/start` → claim input → assemble sections and tool schemas →
`agent/pre-step` → `step/start` → request → tool calls → `step/end` (`docs/architecture.md`).

Two properties of that baseline are worth stealing:

- **It is a session event, not a hidden injection.** The rule is stated as *"Model-visible means
  logged"* — anything reaching a model request must be reconstructable from the session log, and a
  runtime invariant asserts it. Injected instructions are ordinary `user/message` events, so they
  replay, compact and resume like everything else.
- **It has a byte budget, spent from the outside in.** "Broader files are omitted before the most
  specific file is truncated." When the budget binds, the global `AGENTS.md` is dropped whole before
  the directory-local one loses a line.

## 4. Nobody hands the model a file tree

This is the finding I did not expect. **No prompt section contains a project listing.** The only
directory enumeration in the repository is `packages/context/file-reference-local`, which powers
`@file` autocomplete *in the UI*, and its README is emphatic:

> Selecting a candidate never reads or attaches file contents; the model must call a filesystem
> tool to inspect a file.

The model finds out what exists by using `glob`, `grep`, `read` and `bash` — hence "use pwd to
determine the current working directory" rather than asserting it. Discovery is a tool loop, not a
prompt preamble.

`agent-instructions` follows the same principle for *refresh*: newly relevant nested `AGENTS.md`
files appear when a successful `read`, `write` or `edit` touches their directory. There is no file
watcher, and the README says why shell activity is not used for discovery — "each local shell call
starts a fresh process and parsing arbitrary shell syntax is not a reliable filesystem seam."

> **For OSF.** We inject a `git ls-files` listing because our worker has no way to look: no glob, no
> grep, no ls, no bash. That was the right call for the tools we have — and it fixed a real bug,
> where the planner routed work into a `cli.py` that did not exist. But it is a workaround for a
> missing capability, and it has two costs their design does not pay: the listing is capped at 40
> entries (useless in a large repo), and it is a snapshot that goes stale as the agent writes.
> A `glob`/`grep` pair would replace it with something that scales and cannot be stale.
>
> It also explains an odd result from probing our worker: shown a listing and asked about secrets,
> it read `.gitignore`, saw `.env` named there, and concluded *"there is no .env file"*. It treats
> the listing as the complete world. A model with search tools would have looked.

## 5. Policy is enforced under the tools, not asked for in the prompt

Three layers, none of which is prompt text:

| Layer | Package | What it does |
|---|---|---|
| Read-before-edit | `packages/fs/fs-observation-policy` | "an unseen file can only be created, an observed file can only be replaced at the version last seen, and editing requires a prior read" |
| Sandbox modes | `packages/fs/fs-sandbox` | `read-only` / `workspace-write` (target must sit under the session workspace) / `danger-full-access`; a denial is a structured `FS_SANDBOX_DENIED` |
| Approval | `packages/interaction/user-approval` | per-mutation approval, with the current policy stated to the model as runtime context |

The observation policy "participates through the `fs/*` events only, so it registers no service and
has no public methods; removing it leaves the bare provider's unconditional mutation behavior
instead of breaking the tools." Policy is a *removable plugin over a seam*, not a branch inside the
tool.

Note also `fs-sandbox` keys `workspace-write` to **the session's immutable cwd**
(`packages/sandbox/sandbox-policy`): the workspace boundary is fixed when the session starts and
cannot be moved by anything the agent does.

> **For OSF.** We adopted read-before-overwrite in `Toolbox`, and it works — ordered to overwrite
> without reading, the model was refused and recovered in one step. Two gaps remain against this
> design. First, ours is a policy branch *inside* `dispatch`, not a seam, so it cannot be swapped or
> removed per deployment. Second, we have no read policy at all: our sandbox only stops path escape,
> so `read_file(".env")` returns `SECRET=hunter2` and `.git/config` reads fine. Their answer is
> mode-based enforcement under the provider; ours is that the model did not think to try, which is
> not a control.

## 6. Loop hygiene is advice, not a block

`packages/guard/repeat-tool-reminder` watches for identical tool calls — same tool, same arguments —
and at 3, 5 and 8 repeats delivers a reminder to analyze the last result and change approach or
stop. Enabled by default. The README is careful about why it is advisory: *"a legitimate repeated
call is delayed by nothing, and the decision to continue, change approach, or stop stays with the
model."* It tracks each agent separately and a new user message clears the count.

> **For OSF.** Our equivalent is `max_rounds`, which is a hard cap on review iterations and says
> nothing to the worker about *why* it is going in circles. We saw `rounds=2` and `rounds=3` runs
> that were plainly repetition. A nudge carrying the observation ("you have written this file twice
> with the same content") is cheaper than a retry and more useful than a cap.

## 7. Context has a maintenance story

Long sessions are handled by three cooperating packages: `compaction` (the seam), `compaction-basic`
(condense oldest history into a summary as token pressure builds; also after a context-overflow
error, then retry), and `compaction-tool-result-pruner` (trim oversized tool results to head +
`middle pruned` marker + tail *before* summarizing, since that often relieves pressure with no model
call at all). The full original stays in the session log for replay — only the derived history
shrinks. And the honest limitation is documented: compaction "cannot shrink the system prompt,
tools, or session prefix."

> **For OSF.** We have no compaction and no need for it yet — our workers are single-shot, capped at
> `_MAX_STEPS = 12`, and never see a long history. Worth knowing the shape before we build the
> durable event-sourced sessions the architecture doc promises, because "the log is the source of
> the context" only works if something can compact the derived view.

## What I would take, in order

1. **A search tool pair (`glob`, `grep`).** Removes the listing workaround, scales past 40 files,
   cannot go stale, and fixes the "the listing is the whole world" failure. Biggest gap.
2. **Per-tool prompt sections.** Kills the drift already visible in `WORKER_SYSTEM`, and makes the
   prompt correct by construction when the tool set changes.
3. **A read policy.** Refuse gitignored paths and `.git/` by default, escalate deliberately. Today
   any repo we run in exposes its `.env` to the model.
4. **Stable prefix, late context.** Move anything that changes mid-session out of the system prompt.
   Cheap now, structural later.
5. **A repeat nudge** to replace silent round-burning.

## Where to look

| Question | Path in `deepseek-harness` |
|---|---|
| Prompt assembly, slot order | `packages/core/system-prompt/src/index.ts` |
| Section vs context contract | `docs/subsystems/system-prompt.md` |
| Turn/step lifecycle | `docs/architecture.md` ("Turn flow") |
| `AGENTS.md` loading and refresh | `packages/context/agent-instructions/README.md` |
| Read-before-edit | `packages/fs/fs-observation-policy/README.md` |
| Sandbox modes and workspace root | `packages/fs/fs-sandbox`, `packages/sandbox/sandbox-policy` |
| Exact-string editing | `packages/fs/tool-str-replace-editor/src/index.ts` |
| Loop hygiene | `packages/guard/repeat-tool-reminder/README.md` |
| History compaction | `packages/compaction/*/README.md` |
