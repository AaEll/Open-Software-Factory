# Reading opencode: how the context is architected

Companion to [`deepseekharnessarchitecture.md`](deepseekharnessarchitecture.md), same question — what
reaches the model, and how is it put together — read against the `opencode` sibling checkout. Paths
below are relative to that checkout.

The two harnesses agree on more than they differ, and where they differ it is usually a different
answer to the same pressure. The disagreements are the interesting part.

## 1. The system prompt is chosen per model

`packages/opencode/src/session/system.ts` dispatches on the model id before anything else:

```ts
if (model.api.id.includes("gpt-4") || …("o1") || …("o3"))  return [PROMPT_BEAST]
if (model.api.id.includes("gpt"))    return [ …("codex") ? PROMPT_CODEX : PROMPT_GPT ]
if (model.api.id.includes("gemini-")) return [PROMPT_GEMINI]
if (model.api.id.includes("claude"))  return [PROMPT_ANTHROPIC]
if (model.api.id.toLowerCase().includes("kimi") || …) return [PROMPT_KIMI]
return [PROMPT_DEFAULT]
```

Those are whole prompt files, not variations on a theme: `packages/opencode/src/session/prompt/`
holds ten, from 95 to 155 lines each (`kimi.txt` 95, `anthropic.txt` 105, `gpt.txt` 107,
`gemini.txt` 155). They differ in register and in what they insist on. `kimi.txt` opens by telling
the model when *not* to act — *"For simple questions/greetings that do not involve any information
in the working directory or on the internet, you may simply reply directly. For anything else,
default to taking action with tools"* — and pushes parallel tool calls hard (*"you are HIGHLY
RECOMMENDED to make them in parallel"*). An agent definition may replace the whole thing
(`input.agent.prompt`).

> **For OSF.** We send Kimi a six-line `WORKER_SYSTEM` written for no model in particular, while
> opencode ships a 95-line prompt for exactly the model we run. `ModelRef` already carries
> `provider_id` and `model_id`, so we have the dispatch key; we have simply never used it. The
> greeting rule in `kimi.txt` is also the same problem we solved in the shell with a separate
> routing call — they solved it inside the prompt for one model.

## 2. What the environment block contains — and what it doesn't

`SystemPrompt.environment()` builds this:

```
You are powered by the model named {id}. The exact model ID is {provider}/{id}
Here is some useful information about the environment you are running in:
<env>
  Working directory: {ctx.directory}
  Workspace root folder: {ctx.worktree}
  Is directory a git repo: {yes|no}
  Platform: {process.platform}
  Today's date: {…}
</env>
```

Then optional `<available_references>` — extra directories the project declared, each with a name,
path and description.

**No file listing.** Same as deepseek-harness, arrived at independently: tell the model where it is,
not what is there. The model finds files with `glob`, `grep`, `read` and `bash`.

> **For OSF.** Two harnesses, same conclusion, and we do the opposite for the same reason we did it:
> our worker has no way to look. Worth noting what else lands in their `<env>` that we omit —
> whether the directory is a git repo, the platform, the date, and the model's own identity.

## 3. Assembly order, and why it collapses to two blocks

`packages/opencode/src/session/prompt.ts` builds the array:

```ts
const system = [ ...env, ...instructions, ...(mcpInstructions ?? []), ...(skills ?? []) ]
```

and `packages/opencode/src/session/llm/request.ts` prepends the model prompt, then **flattens
everything after the header into a single second block**:

```ts
if (system.length > 2 && system[0] === header) {
  const rest = system.slice(1)
  system.length = 0
  system.push(header, rest.join("\n"))
}
```

That looks arbitrary until you find `applyCaching` in `packages/opencode/src/provider/transform.ts`:

```ts
const system = msgs.filter((m) => m.role === "system").slice(0, 2)
const final  = msgs.filter((m) => m.role !== "system").slice(-2)
// …mark each with cacheControl: { type: "ephemeral" } (per-provider spelling)
```

Providers allow a handful of cache breakpoints. opencode spends them deliberately: **two on the
system prompt** (stable model prompt, then everything else) and **two at the tail of the
conversation**. The two-block shape exists so the breakpoints land on a stable boundary.

This is the same pressure deepseek-harness names in `user-approval` — *"switching policy does not
rewrite the stable system-prompt cache prefix"* — solved from the other end. deepseek keeps volatile
facts **out** of the system prompt entirely, as a late user message. opencode leaves them in but
**segregates** them behind the header so only the second block is invalidated.

> **For OSF.** Both designs treat the prompt prefix as a resource to protect. We rebuild
> `worker_system(workspace)` from scratch each step, file listing included, so our prefix changes
> the moment the agent writes a file. Cheap at our size, wrong in shape, and free to fix: the
> constant part is already a constant.

## 4. `AGENTS.md` is baseline context in both harnesses — and absent in ours

`packages/opencode/src/session/instruction.ts` reads a global layer
(`~/.config/opencode/AGENTS.md`, plus `~/.claude/CLAUDE.md` unless disabled) and a project layer
(`AGENTS.md`, `CLAUDE.md`, deprecated `CONTEXT.md`), and folds them into `system` on every request.

Nested files are discovered the same way deepseek does it — from filesystem activity, not a watcher:

```ts
if (part.type === "tool" && part.tool === "read" && part.state.status === "completed")
  for (const p of part.state.metadata?.loaded ?? []) paths.add(p)
```

Read a file, and the instruction files relevant to its directory become visible on the next request.
deepseek's `agent-instructions` does the same thing through `read`/`write`/`edit` touches.

> **For OSF.** We read **no** instruction file at all. Point `sf` at a repository with an `AGENTS.md`
> — including our own, which carries the conventions in this project — and it will not see it. Both
> reference implementations treat that file as the baseline the model is owed. This is the cheapest
> real gap on the list: read the chain from the project root, prepend it, done.

## 5. Skills are advertised, not loaded

`SystemPrompt.skills()` puts a *catalogue* in the prompt — names and descriptions — with the
instruction *"Use the skill tool to load a skill when a task matches its description."* The comment
in the source is a nice piece of empiricism:

> the agents seem to ingest the information about skills a bit better if we present a more verbose
> version of them here and a less verbose version in tool description, rather than vice versa

MCP servers get the same treatment: their instructions are injected per server, filtered to those
whose tools survive the agent's permission ruleset.

> **For OSF.** `osf/skills.py` renders a skill's *entire* instructions into the worker prompt via
> `apply_skills`. With two skills (`new-repo-ci`, `new-repo-blank`) that is fine; it stops being
> fine at ten, and there is no mechanism for a worker to pull one in mid-task. Their split —
> description in the prompt, body behind a tool call — is the version that scales.

## 6. Where the two harnesses actually disagree

| Concern | opencode | deepseek-harness |
|---|---|---|
| Prompt shape | one text per model, chosen by id | a registry of named sections, ordered centrally |
| Tool guidance | inside the per-model prompt | each tool registers its own section |
| Volatile facts | kept in system prompt, segregated behind a cache breakpoint | moved out, delivered as a late user-role snapshot |
| Cache strategy | explicit breakpoints (2 system + 2 tail) | stable prefix by construction |
| Editing | `edit` (oldString/newString/replaceAll), `write` separate | `str_replace_editor` (`view`/`create`/`str_replace`/`insert`) |
| Read-before-write | `writeIfUnchanged` + `StaleContentError` at the mutation layer | `fs-observation-policy` plugin over `fs/*` events |
| Instruction files | `AGENTS.md`/`CLAUDE.md`, global + project, refreshed from `read` results | `AGENTS.md` chain with a byte budget, refreshed from `read`/`write`/`edit` touches |

Where they agree is more instructive than where they differ:

- **Nobody preloads a file tree.** Give the model its location and search tools.
- **Instruction files are baseline context**, refreshed by watching what the agent touches.
- **Editing is exact-string replacement**; whole-file writes are a separate, lesser tool.
- **The prompt prefix is a resource** worth designing around.
- **Progressive disclosure for anything large** — skills, MCP instructions, nested rules.

## 7. What I would take from opencode specifically

Ordered by value against what we have now:

1. **Read the `AGENTS.md` / `CLAUDE.md` chain** into worker and driver context. We ignore project
   conventions entirely today; both harnesses treat this as table stakes.
2. **A Kimi-specific worker prompt.** We run one model and prompt it generically. `kimi.txt` is 95
   lines of exactly that, and `ModelRef` already gives us the dispatch key.
3. **Enrich `<env>`**: git-repo yes/no, platform, date, and the model's own identity — all cheap,
   all things our worker currently cannot know.
4. **Two-block system prompt** (stable header, volatile rest) ahead of any caching work.
5. **Skills as catalogue + loader tool**, before the registry grows past a handful.

Items 1–3 are small and independent. Item 4 is structural but tiny today. Item 5 can wait until
there are more skills than fit comfortably in a prompt.

## Where to look

| Question | Path in `opencode` |
|---|---|
| Per-model prompt choice | `packages/opencode/src/session/system.ts` (`provider()`) |
| The prompt texts | `packages/opencode/src/session/prompt/*.txt` |
| `<env>` block, skills, MCP | `packages/opencode/src/session/system.ts` (`environment`, `skills`, `mcp`) |
| Assembly order | `packages/opencode/src/session/prompt.ts` (`const system = [...]`) |
| Header/rest collapse | `packages/opencode/src/session/llm/request.ts` (`prepare`) |
| Cache breakpoints | `packages/opencode/src/provider/transform.ts` (`applyCaching`) |
| `AGENTS.md` loading and refresh | `packages/opencode/src/session/instruction.ts` |
| Exact-string editing | `packages/core/src/tool/edit.ts` |
| Snapshots and restore | `packages/core/src/snapshot.ts` |
