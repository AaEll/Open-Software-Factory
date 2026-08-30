"""The ``osf`` command line — start the factory without writing Python.

Subcommands:

* ``osf runs`` — list the prepackaged runs
* ``osf run <name> -p k=v`` — execute a prepackaged run (e.g. ``create-repo``)
* ``osf objective <goal> --repo owner/name`` — reconcile an ad-hoc objective
* ``osf smoke`` — the offline pass-through smoke test

Every command assembles the same four swappable pieces the driver needs — runtime, isolation,
forge, reviewer — from flags: ``--model`` picks the engine (omit it for the offline scripted
runtime), ``--forge`` picks memory vs. real GitHub, and the reviewer is
`osf.review.AcceptanceReviewer` built from the objective's acceptance criteria.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from osf.driver import Driver, ObjectiveOutcome, Reviewer
from osf.forge import Forge
from osf.isolation import IsolationBackend
from osf.local.forge import InMemoryForge
from osf.local.isolation import TempdirIsolation
from osf.model import Objective
from osf.review import AcceptanceReviewer
from osf.runs import all_runs, execute, get_run
from osf.runtime import AgentRuntime
from osf.types import ModelRef, RepoRef


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osf", description="Objectives in, merged PRs out.")
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("runs", help="list the prepackaged runs")
    subs.add_parser("smoke", help="run the offline end-to-end smoke test")

    run = subs.add_parser("run", help="execute a prepackaged run by name")
    run.add_argument("name", help="run name (see `osf runs`)")
    run.add_argument(
        "-p",
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="parameter for the run; repeatable",
    )
    _add_loop_flags(run)

    objective = subs.add_parser("objective", help="reconcile an ad-hoc objective")
    objective.add_argument("goal", help="what the factory should achieve")
    objective.add_argument("--repo", required=True, metavar="OWNER/NAME", help="target repository")
    objective.add_argument(
        "--criterion",
        action="append",
        default=[],
        metavar="TEXT",
        help="acceptance criterion; repeatable. Criteria naming files gate the merge.",
    )
    objective.add_argument("--id", default=None, help="objective id (default: derived from repo)")
    _add_loop_flags(objective)

    return parser


def _add_loop_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        metavar="PROVIDER/MODEL",
        help="engine to run workers with (default: the offline scripted runtime)",
    )
    parser.add_argument(
        "--forge",
        choices=("memory", "github"),
        default="memory",
        help="where PRs are opened (default: memory, a dry run)",
    )
    parser.add_argument(
        "--org", action="store_true", help="with --forge github: create repos under an org"
    )
    parser.add_argument("--max-rounds", type=int, default=3, help="review rounds per WorkItem")
    parser.add_argument("--json", action="store_true", help="print the outcome as JSON")


def _parse_params(pairs: Sequence[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"osf: bad --param {pair!r}; expected KEY=VALUE")
        params[key] = value
    return params


def _parse_repo(ref: str) -> RepoRef:
    owner, sep, name = ref.partition("/")
    if not sep or not owner or not name:
        raise SystemExit(f"osf: bad --repo {ref!r}; expected OWNER/NAME")
    return RepoRef(owner=owner, name=name)


def _make_runtime(model: str | None) -> AgentRuntime:
    if model is None:
        from osf.local.runtime import StaticSiteRuntime

        return StaticSiteRuntime()
    try:  # keys live in .env for real engines; python-dotenv ships with the `agent` extra
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    from osf.engines import resolve_runtime

    return resolve_runtime(ModelRef.parse(model))


def _make_forge(choice: str, *, org: bool) -> Forge:
    if choice == "memory":
        return InMemoryForge()
    from osf.forges.github import GitHubForge

    return GitHubForge(org=org)


def _make_isolation() -> IsolationBackend:
    return TempdirIsolation()


def _report(outcome: ObjectiveOutcome, *, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "objective_id": outcome.objective_id,
                    "state": outcome.state,
                    "items": [
                        {
                            "work_item_id": item.work_item_id,
                            "state": item.state,
                            "pr": item.pr.number if item.pr else None,
                            "rounds": item.rounds,
                        }
                        for item in outcome.items
                    ],
                },
                indent=2,
            )
        )
    else:
        print(f"objective {outcome.objective_id}: {outcome.state}")
        for item in outcome.items:
            pr = f"PR#{item.pr.number}" if item.pr else "no PR"
            print(f"  {item.work_item_id}: {item.state} ({pr}, rounds={item.rounds})")
    return 0 if outcome.state == "done" else 1


def _cmd_runs() -> int:
    for run in sorted(all_runs(), key=lambda r: r.name):
        print(f"{run.name}\t{run.description}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        run = get_run(args.name)
    except KeyError:
        raise SystemExit(f"osf: unknown run {args.name!r}; see `osf runs`") from None
    try:
        plan = run.build(_parse_params(args.param))
    except KeyError as exc:
        raise SystemExit(f"osf: run {run.name!r} needs --param {exc.args[0]}=...") from None

    outcome = asyncio.run(
        execute(
            plan,
            runtime=_make_runtime(args.model),
            isolation=_make_isolation(),
            forge=_make_forge(args.forge, org=args.org),
            reviewer=_reviewer_for(plan.objective),
            max_rounds=args.max_rounds,
        )
    )
    return _report(outcome, as_json=args.json)


def _cmd_objective(args: argparse.Namespace) -> int:
    repo = _parse_repo(args.repo)
    objective = Objective(
        id=args.id or f"{repo.owner}-{repo.name}",
        repo=repo,
        goal=args.goal,
        acceptance_criteria=list(args.criterion),
    )
    driver = Driver(
        runtime=_make_runtime(args.model),
        isolation=_make_isolation(),
        forge=_make_forge(args.forge, org=args.org),
        reviewer=_reviewer_for(objective),
        max_rounds=args.max_rounds,
    )
    return _report(asyncio.run(driver.run(objective)), as_json=args.json)


def _reviewer_for(objective: Objective) -> Reviewer:
    return AcceptanceReviewer(list(objective.acceptance_criteria))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "runs":
        return _cmd_runs()
    if args.command == "smoke":
        from osf.smoke import main as smoke_main

        smoke_main()  # raises SystemExit with the smoke's own status
        return 0
    if args.command == "run":
        return _cmd_run(args)
    return _cmd_objective(args)


if __name__ == "__main__":
    sys.exit(main())
