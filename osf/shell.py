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
import importlib.util
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from osf.config import default_owner, detected_owner, parse_repo, valid_repo_name
from osf.driver import Driver, ObjectiveOutcome
from osf.forge import Forge
from osf.isolation import IsolationBackend
from osf.local.forge import InMemoryForge, NoForge
from osf.local.isolation import TempdirIsolation
from osf.local.project import ProjectIsolation, git_init, repo_root
from osf.planner import Answer, Exchange, Planner, ProposedPlan, StaticPlanner
from osf.prompts import STYLE, Cancelled, Choice, confirm, select, text
from osf.review import AcceptanceReviewer, PlanReviewer
from osf.runs import PrepackagedRun, all_runs, execute, get_run
from osf.runtime import AgentRuntime
from osf.types import ModelRef, RepoRef, Workspace

BANNER = "Open Software Factory"


@dataclass
class Session:
    """What the shell remembers between turns."""

    project: Path | None = None  # the repository `sf` edits; None until resolved from the cwd
    repo: RepoRef | None = None
    model: ModelRef | None = None  # None -> the offline scripted worker
    forge: str = "local"  # "local" | "memory" | "github" | "github-org"
    max_rounds: int = 3
    ask: bool = True  # let the driver ask clarifying questions before planning

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
        if self.forge == "local":
            return NoForge()
        if self.forge == "memory":
            return InMemoryForge()
        from osf.forges.github import GitHubForge

        return GitHubForge(org=self.forge == "github-org")

    def isolation(self) -> IsolationBackend:
        """Where the work happens: your repository by default, a throwaway copy otherwise."""
        if self.forge == "local" and self.project is not None:
            return ProjectIsolation(self.project)
        return TempdirIsolation()


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
        where = self.session.project or (repo_root() if self.session.forge == "local" else None)
        target = str(where) if where else f"forge {self.session.forge}"
        self.note(f"{self.session.engine} · {target} · /help for commands")
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
        repo = self._target()
        if repo is None:
            return
        plan = self.negotiate(goal)
        if plan is None:
            self.note("not run")
            return

        objective_id = f"{repo.owner}-{repo.name}"
        isolation = self.session.isolation()
        items = plan.work_items(objective_id)
        driver = Driver(
            runtime=self.session.runtime(),
            isolation=isolation,
            forge=self.session.make_forge(),
            reviewer=PlanReviewer(plan.criteria_by_item(objective_id)),
            decompose=lambda _objective: items,
            max_rounds=self.session.max_rounds,
        )
        before = self._snapshot(isolation)
        self._report(asyncio.run(driver.run(plan.objective(objective_id, repo))))
        self._settle(isolation, before)

    def _target(self) -> RepoRef | None:
        """What the work is aimed at: your project when local, a named repo otherwise."""
        if self.session.forge != "local":
            return self.session.repo or self._ask_repo()
        project = self._ensure_project()
        if project is None:
            return None
        return RepoRef(default_owner(), project.name)

    def _ensure_project(self) -> Path | None:
        """Resolve the repository `sf` edits, offering to create one if there isn't any."""
        if self.session.project is not None:
            return self.session.project
        root = repo_root()
        if root is None:
            here = Path.cwd()
            self.error(f"{here} is not a git repository")
            if not confirm(f"Run git init in {here}?", default=False):
                self.note("nothing to work in — cd into a repository, or use /forge memory")
                return None
            git_init(here)
            root = here
        self.session.project = root
        self.note(f"project: {root}")
        return root

    def _snapshot(self, isolation: IsolationBackend) -> str | None:
        """Capture the project before the agents touch it, so the change can be undone."""
        if not isinstance(isolation, ProjectIsolation):
            return None
        return isolation.snapshot(Workspace(str(isolation.root), str(isolation.root)))

    def _settle(self, isolation: IsolationBackend, before: str | None) -> None:
        """Show what changed in the project and let the user keep it or put it back."""
        if before is None or not isinstance(isolation, ProjectIsolation):
            return
        workspace = Workspace(str(isolation.root), str(isolation.root))
        changed = isolation.changed_since(workspace, before)
        if not changed:
            self.note("no files changed")
            return
        self.say(f"  {STYLE.bold('changed')} in {isolation.root}")
        for line in isolation.diff_since(workspace, before, stat=True).splitlines():
            self.say(f"    {line.strip()}")
        if confirm("Keep these changes?"):
            self.note("kept — review them with git diff, commit when you're happy")
            return
        restored = isolation.restore(workspace, before)
        self.note(f"reverted {len(restored)} file(s) to how they were")

    def negotiate(self, request: str) -> ProposedPlan | None:
        """Let the driver ask what it needs, then revise its plan until the user accepts."""
        answers = self.interview(request) if self.session.ask else []
        exchanges: list[Exchange] = []
        while True:
            plan = self.propose(request, exchanges, answers)
            self._show_plan(plan)
            answer = text(
                "Run this? (Enter to accept, or say what to change)", required=False
            )
            if not answer or answer.lower() in _ACCEPT:
                return plan
            if answer.lower() in _DECLINE:
                return None
            exchanges.append((plan, answer))

    def interview(self, request: str) -> list[Answer]:
        """Put the driver's own questions to the user. Blank answers are simply skipped."""
        try:
            questions = self.session.planner().clarify(request)
        except Exception as exc:
            self.error(f"driver unavailable ({type(exc).__name__}: {exc})")
            return []
        if not questions:
            return []
        self.note("a few questions before I plan (Enter to skip any):")
        answers = [(question, text(question, required=False)) for question in questions]
        return [(question, answer) for question, answer in answers if answer]

    def propose(
        self, request: str, exchanges: list[Exchange], answers: list[Answer]
    ) -> ProposedPlan:
        """Ask the planner for a plan, falling back to the literal request if it can't."""
        self.note("planning…")
        try:
            return self.session.planner().propose(
                request, exchanges, answers, shared_workspace=self.session.forge == "local"
            )
        except Exception as exc:
            self.error(f"planner unavailable ({type(exc).__name__}: {exc})")
            self.note("falling back to the request as written")
            return StaticPlanner().propose(request, exchanges, answers)

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
        name = plan.objective.repo.name
        if self.session.forge.startswith("github"):
            target = f"{plan.objective.repo.owner}/{name}"
            self.error(f"this creates {target} on GitHub for real")
        else:
            target = f"./{name}" if self.session.forge == "local" else name
        if not confirm(f"Run it in {target} with {self.session.engine}?"):
            self.note("not run")
            return

        try:
            isolation = self._run_isolation(plan.objective.repo.name)
        except FileExistsError as exc:
            self.error(str(exc))
            return
        outcome = asyncio.run(
            execute(
                plan,
                runtime=self.session.runtime(),
                isolation=isolation,
                forge=self.session.make_forge(),
                reviewer=AcceptanceReviewer(list(plan.objective.acceptance_criteria)),
                max_rounds=self.session.max_rounds,
            )
        )
        self._report(outcome)
        if isinstance(isolation, ProjectIsolation):
            self.note(f"scaffolded into {isolation.root}")

    def _run_isolation(self, name: str) -> IsolationBackend:
        """Where a prepackaged run builds.

        Locally, "create a repository" means a real new directory beside the one you are in —
        scaffolding a fresh project into an existing repo would dump a README and workflows on top
        of someone's work. The other forges keep using a throwaway workspace.
        """
        if self.session.forge != "local":
            return TempdirIsolation()
        target = Path.cwd() / name
        if target.exists() and any(target.iterdir()):
            raise FileExistsError(f"{target} already exists and is not empty")
        target.mkdir(parents=True, exist_ok=True)
        git_init(target)
        return ProjectIsolation(target)

    def _report(self, outcome: ObjectiveOutcome) -> None:
        colour = STYLE.green if outcome.state == "done" else STYLE.red
        self.say(f"  {outcome.objective_id}: {colour(outcome.state)}")
        for item in outcome.items:
            # PR#0 is the null forge's stand-in — local work has no pull request to point at.
            pr = f"PR#{item.pr.number}, " if item.pr and item.pr.number else ""
            self.note(f"{item.work_item_id}: {item.state} ({pr}rounds={item.rounds})")
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


def _cmd_project(shell: Shell, rest: str) -> None:
    if rest:
        path = Path(rest).expanduser().resolve()
        root = repo_root(path) if path.is_dir() else None
        if root is None:
            shell.error(f"{path} is not a git repository")
            return
        shell.session.project = root
        shell.session.repo = None  # the target follows the project
    shell.note(f"project: {shell.session.project or 'the directory you launched from'}")


def _cmd_diff(shell: Shell, _rest: str) -> None:
    project = shell.session.project or repo_root()
    if project is None:
        shell.error("no project — cd into a git repository")
        return
    isolation = ProjectIsolation(project)
    status = isolation.exec_sync(["git", "status", "--short"])
    shell.say(status.rstrip() or STYLE.dim("  working tree clean"))


def _cmd_forge(shell: Shell, rest: str) -> None:
    if rest:
        if rest not in ("local", "memory", "github", "github-org"):
            shell.error("forge must be local, memory, github, or github-org")
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


def _cmd_ask(shell: Shell, rest: str) -> None:
    if rest:
        if rest not in ("on", "off"):
            shell.error("ask must be on or off")
            return
        shell.session.ask = rest == "on"
    shell.note(f"clarifying questions: {'on' if shell.session.ask else 'off'}")


def _cmd_status(shell: Shell, _rest: str) -> None:
    session = shell.session
    shell.note(f"project: {session.project or 'the directory you launched from'}")
    shell.note(f"repo:   {_repo_str(session.repo)}")
    shell.note(f"engine: {session.engine}")
    shell.note(f"forge:  {session.forge}")
    shell.note(f"rounds: {session.max_rounds}")
    shell.note(f"ask:    {'on' if session.ask else 'off'}")


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
    Command("project", "[path]", "the repository sf edits", _cmd_project),
    Command("diff", "", "show what has changed in the project", _cmd_diff),
    Command("repo", "[owner/name]", "set the target repository (non-local forges)", _cmd_repo),
    Command("model", "[provider/model|off]", "set the engine workers run on", _cmd_model),
    Command("forge", "[local|memory|github]", "where the work lands", _cmd_forge),
    Command("rounds", "[n]", "review rounds before escalating", _cmd_rounds),
    Command("ask", "[on|off]", "let the driver ask before planning", _cmd_ask),
    Command("status", "", "show the session settings", _cmd_status),
    Command("smoke", "", "run the offline pipeline self-check", _cmd_smoke),
    Command("quit", "", "leave the shell", _cmd_quit),
)

_ALIASES = {"h": "help", "exit": "quit", "q": "quit"}

# Answers to "Run this?" that mean yes or no rather than "change it to…".
_ACCEPT = frozenset({"y", "yes", "ok", "run", "go", "accept"})
_DECLINE = frozenset({"n", "no", "cancel", "stop", "abort"})


def default_session() -> Session:
    """Seed the session from the environment.

    `OSF_MODEL` wins; failing that, a Fireworks key in the environment (or `.env`) means the user
    has an engine, so use it rather than silently falling back to the scripted worker — planning
    with no model produces a plan that only echoes the request.
    """
    try:
        from dotenv import find_dotenv, load_dotenv

        # usecwd: search up from where the user launched `sf`, not from this file — an installed
        # package would otherwise look beside itself in site-packages and find nothing.
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    return Session(model=default_model())


def default_model() -> ModelRef | None:
    """`OSF_MODEL`, else Fireworks when its key and SDK are both available, else offline."""
    configured = os.environ.get("OSF_MODEL", "").strip()
    if configured:
        return ModelRef.parse(configured)
    if importlib.util.find_spec("openai") is None:
        return None  # key or not, without the `agent` extra there is no engine to run
    from osf.engines.fireworks import DEFAULT_MODEL, api_key

    return DEFAULT_MODEL if api_key() else None
