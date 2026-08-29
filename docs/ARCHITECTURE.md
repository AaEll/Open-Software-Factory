# Open Software Factory — Architecture

> Status: **Draft (Phase 0)**. This document defines the target architecture and the contracts we
> build against. It is direction-setting; component internals are specified per phase.

## 1. What this is

Open Software Factory (OSF) is an autonomous, multi-agent system that turns high-level **objectives**
into **merged pull requests** with minimal human input. A human sets an objective (and optional
approval gates); from there, agents plan, implement, review, and merge in continuous loops. OSF runs
the same way locally and in the cloud.

OSF is a **net-new product**. It is not a fork or wrapper of any existing agent engine. It *borrows
proven patterns* from [`anomalyco/opencode`](https://github.com/anomalyco/opencode) — an open agent
API, durable event-sourced sessions, subagent delegation, and location-scoped isolation — but shares
no source with it.

## 2. Principles

- **Minimal human input.** The unit of intent is an objective; everything downstream is autonomous.
- **The forge is the coordination substrate.** PRs, review comments, checks, and merges are the
  durable, human-observable medium. No bespoke message bus for agent coordination.
- **Everything is resumable.** State is event-sourced; a crashed loop resumes from the log.
- **Swappable at the seams.** Agent engine, isolation backend, and forge are each one interface with
  multiple adapters.
- **Safety is explicit.** Per-role permissions, budget/iteration caps, and optional human gates.

## 3. Component overview

```
   human ──▶ OBJECTIVE (goal · repo · acceptance criteria · caps · gates)
                     │
             DRIVER AGENT (per objective) ── autonomous reconcile loop
             spec → dispatch → review → comment → merge → re-plan
                 │                         │
                 │ dispatch                │ review/merge via forge
          WORKER AGENT(s)  ───────────▶  FORGE (GitHub)
          one WorkItem → PR               PRs · comments · checks · merges
                 │ runs inside
          ISOLATION BACKEND  (worktree local · container cloud)
                 │ executes via
          AGENT RUNTIME  (open agent API: create · prompt · stream · interrupt)
                          engines: opencode-server · Claude Agent SDK · …

   Cross-cutting: Orchestration/State engine (event-sourced) · Scheduler
                  (concurrency + budgets) · Permission policy · Observability/control plane
```

### 3.1 Objective intake
The only required human input. A durable record: goal text, target repo, acceptance criteria,
budget/iteration caps, and optional approval gates.

### 3.2 Driver agent (autonomous control loop)
One long-lived driver per objective, modeled as a **reconciliation loop**: compare desired state
(objective + acceptance criteria) against actual state (repo + open/merged PRs), then act. It
decomposes the objective into WorkItems, dispatches workers, reviews PRs via the forge, leaves
feedback, requests changes, merges when checks pass, re-plans, and decides *done* or escalates to a
human. Each loop tick is a **durable, resumable step** driven by the orchestration engine — not an
in-memory `while` loop.

### 3.3 Worker agent (ephemeral)
Takes one WorkItem, runs in an isolated workspace, implements the change, runs tests, and opens/
updates a PR. On later ticks it addresses review feedback. May fan out to its own subagents
(explore/implement/test).

### 3.4 Agent Runtime — the open agent API
Engine-agnostic interface (§4.1). First adapter: opencode's headless server (self-hostable, already
speaks a session/event API). The boundary keeps us free to add other engines.

### 3.5 Isolation backend (pluggable, tiered)
One interface (§4.2). **Git worktrees** locally (fast, cheap, shared host); **containers** in the
cloud (strong isolation, scale, untrusted-safe). Callers never know which is in use.

### 3.6 Forge integration
GitHub first (GitLab later). Creates branches/PRs, posts review comments, reads check/CI status, and
merges (§4.3). This is both the coordination substrate and the human observation surface.

### 3.7 Orchestration / state engine
Durable, **event-sourced** store of the data model (§5) plus the scheduler, retries, resumability,
and driver tick execution. Records intent before acting so crashed loops resume cleanly.

### 3.8 Permission / safety policy
Per-role allow/deny/ask rules, budget caps (tokens/$/time), iteration caps, and optional human
approval gates (e.g., merges to `main` require human ack).

### 3.9 Observability & control plane
API + dashboard to watch runs, transcripts, PR status, and spend, and to pause/resume/kill an
objective or worker. Cost governance is first-class.

## 4. Core contracts

These are illustrative signatures (Python), not final APIs. They pin down the seams.

### 4.1 `AgentRuntime`
```python
class AgentRuntime(Protocol):
    async def create_session(self, workspace: Workspace, role: str) -> SessionId: ...
    async def prompt(self, session: SessionId, text: str) -> None: ...
    def stream_events(self, session: SessionId) -> AsyncIterator[AgentEvent]: ...
    async def interrupt(self, session: SessionId) -> None: ...
    async def result(self, session: SessionId) -> AgentResult: ...   # transcript, cost, outcome
```

### 4.2 `IsolationBackend`
```python
class IsolationBackend(Protocol):
    async def prepare(self, repo: RepoRef, branch: str) -> Workspace: ...
    async def exec(self, ws: Workspace, cmd: list[str]) -> ExecResult: ...
    async def cleanup(self, ws: Workspace) -> None: ...
```

### 4.3 `Forge`
```python
class Forge(Protocol):
    async def open_pr(self, repo: RepoRef, branch: str, title: str, body: str) -> PrRef: ...
    async def comment(self, pr: PrRef, body: str, *, review: bool = False) -> None: ...
    async def checks(self, pr: PrRef) -> ChecksStatus: ...
    async def merge(self, pr: PrRef) -> None: ...
```

## 5. Data model (event-sourced)

`Objective (1) → (N) WorkItem → (N) PullRequest → (N) AgentRun`

- **Objective** — human intent + acceptance criteria + caps/gates.
- **WorkItem** — a unit of work; WorkItems form a dependency **DAG**.
- **PullRequest** — forge PR produced for a WorkItem; carries review/feedback rounds.
- **AgentRun** — one agent execution: role, engine, workspace, transcript, cost, outcome.

Every entity is rebuilt from an append-only event log, making the whole factory replay-safe and
auditable.

## 6. Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Isolation | Pluggable, tiered (worktree local / container cloud) | Only option that satisfies "local and cloud". |
| Orchestrator language | Python *(open — see §8)* | Matches repo; strong async/GitHub/agent-SDK ecosystem; engine reached over HTTP so not constraining. |
| First agent engine | opencode headless server adapter | Self-hostable; already speaks a session/event API. |
| Coordination substrate | The forge (PRs/comments/merges) | Free durability, auditability, human-in-the-loop UX. |
| Driver model | Reconciler with durable ticks | Safe for long unattended runs. |

## 7. Roadmap

- **Phase 0 — Foundations & contracts.** This doc; interface + data-model stubs; stack; scaffolding/CI.
- **Phase 1 — Single worker slice.** Human triggers one WorkItem → worker in a worktree → opens a real PR. No driver.
- **Phase 2 — Driver control loop.** Autonomous single-objective loop; durable/resumable ticks.
- **Phase 3 — Concurrency & containers.** DAG scheduling, container backend, budget/iteration caps.
- **Phase 4 — Safety, multi-engine, observability.** Permission policy + gates; 2nd engine adapter; control plane.
- **Phase 5 — Scale & resilience.** Distributed workers, clustered state, crash recovery, GitLab adapter.

## 8. Open questions (resolve in Phase 0)

- Orchestrator language final call (Python vs a TS mono-stack).
- Forge auth model (GitHub App vs PAT) and how the driver's identity appears on PRs/comments.
- "Definition of done" mechanism (acceptance-criteria checks vs LLM judgment vs required CI).
- Budget/cost accounting granularity (per-AgentRun token/$ metering).
