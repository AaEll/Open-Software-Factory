"""The `sf` shell — an interactive dialog with the driver.

`sf` opens a prompt. Type a goal in plain language and it becomes an objective the driver
reconciles; type a `/command` to reach the structured flows. Prepackaged runs are walked question
by question from the `RunParam` schema they declare (`/run create-repo` asks for the name, what it
should do, and whether to start from a CI/CD template or a blank repo), so adding a question to a
run needs no change here.

Session state — target repo, engine, forge, review rounds — is set with slash commands and reused
by everything you type afterwards, so the common case is one short answer per turn rather than a
line of flags. Nothing here is required to be a TTY: the loop reads lines, so a piped script of
commands drives it identically (which is how the tests run).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass

from osf.config import default_owner, detected_owner, parse_repo, valid_repo_name
from osf.driver import Driver, ObjectiveOutcome
from osf.forge import Forge
from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.planner import Exchange, Planner, ProposedPlan, StaticPlanner
from osf.prompts import STYLE, Cancelled, Choice, confirm, select, text
from osf.review import AcceptanceReviewer, PlanReviewer
from osf.runs import PrepackagedRun, all_runs, execute, get_run
from osf.runtime import AgentRuntime
from osf.types import ModelRef, RepoRef

BANNER = "Open Software Factory"


@dataclass
class Session:
    """What the shell remembers between turns."""

    repo: RepoRef | None = None
    model: ModelRef | None = None  # None -> the offline scripted worker
    forge: str = "memory"  # "memory" | "github" | "github-org"
    max_rounds: int = 3

    @property
    def engine(self) -> str:
        return str(self.model) if self.model else "offline (scripted worker)"

    def runtime(self) -> AgentRuntime:
        if self.model is None:
            from osf.local.runtime import StaticSiteRuntime

            return StaticSiteRuntime()
        from osf.engines import resolve_runtime

        return resolve_runtime(self.model)

    def planner(self) -> Planner:
        if self.model is None:
            return StaticPlanner()
        from osf.engines import resolve_planner

        return resolve_planner(self.model)

    def make_forge(self) -> Forge:
        if self.forge == "memory":
            return InMemoryForge()
        from osf.forges.github import GitHubForge

        return GitHubForge(org=self.forge == "github-org")


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    args: str
    help: str
    handler: Callable[[Shell, str], None]


class Shell:
    """The read-dispatch-print loop behind `sf`."""

    def __init__(self, session: Session | None = None) -> None:
        self.session = session or Session()
        self.running = True
        self.commands: dict[str, Command] = {}
        for command in _COMMANDS:
            self.commands[command.name] = command
        for alias, target in _ALIASES.items():
            self.commands[alias] = self.commands[target]

    # --- output ---------------------------------------------------------------------------

    def say(self, message: str = "") -> None:
        print(message)

    def note(self, message: str) -> None:
        print(STYLE.dim(f"  {message}"))

    def error(self, message: str) -> None:
        print(STYLE.red(f"  {message}"))

    # --- loop -----------------------------------------------------------------------------

    def run(self) -> int:
        self.say(STYLE.bold(BANNER))
        self.note(f"{self.session.engine} · forge {self.session.forge} · /help for commands")
        self.say()
        while self.running:
            try:
                line = input(f"{STYLE.cyan('›')} ").strip()
            except (EOFError, KeyboardInterrupt):
                self.say()
                break
            if not line:
                continue
            try:
                self.dispatch(line)
            except Cancelled:
                self.note("cancelled")
            except ValueError as exc:  # bad input: the message is already written for a human
                self.error(str(exc))
            except Exception as exc:  # a bad run must not take the shell down with it
                self.error(f"{type(exc).__name__}: {exc}")
            self.say()
        return 0

    def dispatch(self, line: str) -> None:
        if not line.startswith("/"):
            self.objective(line)
            return
        name, _, rest = line[1:].partition(" ")
        command = self.commands.get(name)
        if command is None:
            self.error(f"unknown command /{name} — try /help")
            return
        command.handler(self, rest.strip())

    # --- free-text objectives -------------------------------------------------------------

    def objective(self, goal: str) -> None:
        """Plan the request with the user, then reconcile the plan they accepted."""
        repo = self.session.repo or self._ask_repo()
        plan = self.negotiate(goal)
        if plan is None:
            self.note("not run")
            return

        objective_id = f"{repo.owner}-{repo.name}"
        objective = plan.objective(objective_id, repo)
        items = plan.work_items(objective_id)
        driver = Driver(
            runtime=self.session.runtime(),
            isolation=TempdirIsolation(),
            forge=self.session.make_forge(),
            reviewer=PlanReviewer(plan.criteria_by_item(objective_id)),
            decompose=lambda _objective: items,
            max_rounds=self.session.max_rounds,
        )
        self._report(asyncio.run(driver.run(objective)))

    def negotiate(self, request: str) -> ProposedPlan | None:
        """Show the driver's plan and revise it until the user accepts — or declines."""
        exchanges: list[Exchange] = []
        while True:
            plan = self.propose(request, exchanges)
            self._show_plan(plan)
            answer = text(
                "Run this? (Enter to accept, or say what to change)", required=False
            )
            if not answer or answer.lower() in _ACCEPT:
                return plan
            if answer.lower() in _DECLINE:
                return None
            exchanges.append((plan, answer))

    def propose(self, request: str, exchanges: list[Exchange]) -> ProposedPlan:
        """Ask the planner for a plan, falling back to the literal request if it can't."""
        self.note("planning…")
        try:
            return self.session.planner().propose(request, exchanges)
        except Exception as exc:
            self.error(f"planner unavailable ({type(exc).__name__}: {exc})")
            self.note("falling back to the request as written")
            return StaticPlanner().propose(request, exchanges)

    def _show_plan(self, plan: ProposedPlan) -> None:
        self.say(f"  {STYLE.bold('plan')}  {plan.goal}")
        for index, step in enumerate(plan.steps, start=1):
            gate = STYLE.dim(f"  → {', '.join(step.files)}") if step.files else ""
            self.say(f"    {index}. {step.spec}{gate}")
        if not plan.files:
            self.say(f"    {STYLE.dim('no file gate — the first green PR merges')}")

    def _ask_repo(self) -> RepoRef:
        """Ask only for a name. The owner is detected, never demanded.

        Work starts as a local `git init` repo, so an account is beside the point until you point
        the shell at a forge — at which point we use whatever `gh` is signed in as.
        """
        name = text("Repository name", validate=valid_repo_name)
        repo = parse_repo(name) if "/" in name else RepoRef(default_owner(), name)
        self.session.repo = repo
        self.note(f"repo: {_repo_str(repo)}")
        return repo

    # --- prepackaged runs -------------------------------------------------------------------

    def start_run(self, run: PrepackagedRun) -> None:
        """Walk a run's declared parameters, then execute the plan it builds."""
        self.say(STYLE.bold(f"  {run.name}") + STYLE.dim(f" — {run.description}"))
        params = {param.name: ask_param(param) for param in run.params}
        plan = run.build(params)

        self.note(f"objective: {plan.objective.goal}")
        self.note(f"gates: {', '.join(plan.objective.acceptance_criteria) or 'none'}")
        target = f"{plan.objective.repo.owner}/{plan.objective.repo.name}"
        if self.session.forge != "memory":
            self.error(f"this creates {target} on GitHub for real")
        if not confirm(f"Run it on {target} with {self.session.engine}?"):
            self.note("not run")
            return

        outcome = asyncio.run(
            execute(
                plan,
                runtime=self.session.runtime(),
                isolation=TempdirIsolation(),
                forge=self.session.make_forge(),
                reviewer=AcceptanceReviewer(list(plan.objective.acceptance_criteria)),
                max_rounds=self.session.max_rounds,
            )
        )
        self._report(outcome)

    def _report(self, outcome: ObjectiveOutcome) -> None:
        colour = STYLE.green if outcome.state == "done" else STYLE.red
        self.say(f"  {outcome.objective_id}: {colour(outcome.state)}")
        for item in outcome.items:
            pr = f"PR#{item.pr.number}" if item.pr else "no PR"
            self.note(f"{item.work_item_id}: {item.state} ({pr}, rounds={item.rounds})")
        if outcome.state != "done":
            self.note("try /rounds to allow more attempts, /model to use a real engine")


def ask_param(param) -> str:
    """Ask one `RunParam` using the widget its schema implies."""
    default = param.resolve_default()
    if param.choices:
        return select(param.prompt, param.choices, default=default)
    return text(
        param.prompt, default=default, required=param.required, validate=param.validate
    )


# --- command handlers -----------------------------------------------------------------------


def _cmd_help(shell: Shell, _rest: str) -> None:
    shell.say(STYLE.bold("  commands"))
    for command in _COMMANDS:
        usage = f"/{command.name} {command.args}".strip()
        shell.say(f"  {usage:<36}{STYLE.dim(command.help)}")
    shell.say()
    shell.note("anything else you type becomes an objective the driver reconciles")


def _cmd_runs(shell: Shell, _rest: str) -> None:
    for run in sorted(all_runs(), key=lambda r: r.name):
        shell.say(f"  {run.name:<16}{STYLE.dim(run.description)}")


def _cmd_run(shell: Shell, rest: str) -> None:
    if not rest:
        choices = [Choice(r.name, r.name, r.description) for r in sorted(all_runs(), key=str)]
        rest = select("Which run?", choices)
    try:
        run = get_run(rest)
    except KeyError:
        shell.error(f"unknown run {rest!r} — /runs lists them")
        return
    shell.start_run(run)


def _cmd_new_repo(shell: Shell, _rest: str) -> None:
    shell.start_run(get_run("create-repo"))


def _cmd_repo(shell: Shell, rest: str) -> None:
    if rest:
        # `/repo site` is as good as `/repo me/site` — the owner falls back to your account.
        shell.session.repo = (
            parse_repo(rest) if "/" in rest else RepoRef(default_owner(), valid_repo_name(rest))
        )
    shell.note(f"repo: {_repo_str(shell.session.repo)}")


def _cmd_model(shell: Shell, rest: str) -> None:
    if rest in ("off", "offline"):
        shell.session.model = None
    elif rest:
        shell.session.model = ModelRef.parse(rest)
    shell.note(f"engine: {shell.session.engine}")


def _cmd_forge(shell: Shell, rest: str) -> None:
    if rest:
        if rest not in ("memory", "github", "github-org"):
            shell.error("forge must be memory, github, or github-org")
            return
        shell.session.forge = rest
        if rest != "memory" and detected_owner() is None:
            shell.error("no GitHub account detected — run `gh auth login` or set OSF_OWNER")
    shell.note(f"forge: {shell.session.forge}")


def _cmd_rounds(shell: Shell, rest: str) -> None:
    if rest:
        if not rest.isdigit() or int(rest) < 1:
            shell.error("rounds must be a positive integer")
            return
        shell.session.max_rounds = int(rest)
    shell.note(f"rounds: {shell.session.max_rounds}")


def _cmd_status(shell: Shell, _rest: str) -> None:
    session = shell.session
    shell.note(f"repo:   {_repo_str(session.repo)}")
    shell.note(f"engine: {session.engine}")
    shell.note(f"forge:  {session.forge}")
    shell.note(f"rounds: {session.max_rounds}")


def _cmd_smoke(shell: Shell, _rest: str) -> None:
    from osf.smoke import run_smoke

    ok = asyncio.run(run_smoke())
    shell.say(f"  smoke: {STYLE.green('ok') if ok else STYLE.red('FAILED')}")


def _cmd_quit(shell: Shell, _rest: str) -> None:
    shell.running = False


def _repo_str(repo: RepoRef | None) -> str:
    return f"{repo.owner}/{repo.name}" if repo else "unset"


_COMMANDS = (
    Command("help", "", "show this list", _cmd_help),
    Command("new-repo", "", "create and scaffold a new repository", _cmd_new_repo),
    Command("runs", "", "list the prepackaged runs", _cmd_runs),
    Command("run", "[name]", "start a prepackaged run", _cmd_run),
    Command("repo", "[owner/name]", "set the target repository", _cmd_repo),
    Command("model", "[provider/model|off]", "set the engine workers run on", _cmd_model),
    Command("forge", "[memory|github|github-org]", "where PRs are opened", _cmd_forge),
    Command("rounds", "[n]", "review rounds before escalating", _cmd_rounds),
    Command("status", "", "show the session settings", _cmd_status),
    Command("smoke", "", "run the offline pipeline self-check", _cmd_smoke),
    Command("quit", "", "leave the shell", _cmd_quit),
)

_ALIASES = {"h": "help", "exit": "quit", "q": "quit"}

# Answers to "Run this?" that mean yes or no rather than "change it to…".
_ACCEPT = frozenset({"y", "yes", "ok", "run", "go", "accept"})
_DECLINE = frozenset({"n", "no", "cancel", "stop", "abort"})


def default_session() -> Session:
    """Seed the session from the environment: `.env` keys and an optional `OSF_MODEL`."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    model = os.environ.get("OSF_MODEL")
    return Session(model=ModelRef.parse(model) if model else None)
