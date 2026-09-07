"""The `sf` entry point.

`sf` takes no subcommands: it opens the shell (`osf.shell`), an interactive dialog with the driver
where plain language becomes an objective and `/commands` reach the structured flows. The
pass-through smoke test keeps its own console script, `sf-smoke`, so CI and the container image
have a non-interactive gate that needs no TTY.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from osf.shell import BANNER, Shell, default_session, run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sf",
        description=f"{BANNER} — say what you want built; it plans, edits, and shows you the diff.",
        epilog=(
            "Run `sf` with no arguments to open the shell. Inside it, /help lists the commands "
            "and anything else you type becomes a request. `sf-smoke` runs the offline pipeline "
            "self-check."
        ),
    )
    parser.add_argument(
        "request",
        nargs="?",
        help="do this and exit, without the shell — for scripts and CI",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="with a request: accept the plan and keep the result without asking",
    )
    parser.add_argument(
        "--project",
        metavar="PATH",
        help="the repository to work in (default: the one you launched from)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = default_session()
    if args.project:
        session.project = Path(args.project).expanduser().resolve()
    if args.request:
        return run_once(session, args.request, assume_yes=args.yes)
    return Shell(session).run()


if __name__ == "__main__":
    sys.exit(main())
