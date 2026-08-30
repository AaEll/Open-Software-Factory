"""A dependency-free prompt toolkit for the `sf` shell.

Structured questions — text, single-select, confirm — rendered on a plain TTY, plus the styling
helpers the shell prints with. The shape is borrowed from the wizards agent CLIs use (a question,
an optional default, a cancel path); nothing here talks to a model or the network.

Input is read with `input()`, so the shell also works when stdin is a pipe — piping a script of
commands is how the tests drive it. Ctrl-C / EOF raise `Cancelled`, which the caller turns into
"back to the prompt" rather than a traceback.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass


class Cancelled(Exception):
    """The user aborted the current question (Ctrl-C, Ctrl-D, or a closed stdin)."""


@dataclass(frozen=True, slots=True)
class Choice:
    value: str
    label: str
    hint: str = ""


class Style:
    """ANSI codes, blanked out when stdout is not a terminal (pipes, CI logs, tests)."""

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = sys.stdout.isatty() if enabled is None else enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("90", text)

    def cyan(self, text: str) -> str:
        return self._wrap("96", text)

    def green(self, text: str) -> str:
        return self._wrap("92", text)

    def red(self, text: str) -> str:
        return self._wrap("91", text)


STYLE = Style()


def _ask(prompt: str) -> str:
    """Read one line, mapping every abort path onto `Cancelled`."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        print()
        raise Cancelled from exc


def text(
    message: str,
    *,
    default: str = "",
    required: bool = True,
    validate: Callable[[str], str] | None = None,
) -> str:
    """Ask a free-text question. Empty input takes the default; re-asks if required and unset.

    `validate` returns the cleaned-up answer or raises `ValueError` with an explanation. A rejected
    answer is re-asked in place — the question is never abandoned over a typo, so whatever the user
    was in the middle of survives.
    """
    suffix = STYLE.dim(f" ({default})") if default else ""
    while True:
        answer = _ask(f"{STYLE.cyan('?')} {message}{suffix} {STYLE.dim('›')} ") or default
        if not answer and not required:
            return answer
        if not answer:
            print(STYLE.red("  a value is required"))
            continue
        if validate is None:
            return answer
        try:
            return validate(answer)
        except ValueError as exc:
            print(STYLE.red(f"  {exc}"))


def select(message: str, options: Sequence[Choice], *, default: str = "") -> str:
    """Ask a single-select question. Accepts the item number or the value itself."""
    if not options:
        raise ValueError("select needs at least one option")
    default_value = default or options[0].value
    print(f"{STYLE.cyan('?')} {message}")
    for index, option in enumerate(options, start=1):
        marker = "›" if option.value == default_value else " "
        hint = STYLE.dim(f"  {option.hint}") if option.hint else ""
        print(f"  {STYLE.cyan(marker)} {index}. {option.label}{hint}")

    by_value = {option.value: option for option in options}
    while True:
        answer = _ask(f"  {STYLE.dim('›')} ")
        if not answer:
            return default_value
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1].value
        if answer in by_value:
            return answer
        print(STYLE.red(f"  pick 1-{len(options)} or a name"))


def confirm(message: str, *, default: bool = True) -> bool:
    """Ask a yes/no question."""
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = _ask(f"{STYLE.cyan('?')} {message} {STYLE.dim(f'({suffix})')} ").lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print(STYLE.red("  answer y or n"))
