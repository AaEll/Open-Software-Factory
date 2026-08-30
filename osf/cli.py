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

from osf.shell import BANNER, Shell, default_session


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="sf",
        description=f"{BANNER} — objectives in, merged PRs out.",
        epilog=(
            "Run `sf` with no arguments to open the shell. Inside it, /help lists the commands "
            "and anything else you type becomes an objective. `sf-smoke` runs the offline "
            "pipeline self-check without a TTY."
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return Shell(default_session()).run()


if __name__ == "__main__":
    sys.exit(main())
