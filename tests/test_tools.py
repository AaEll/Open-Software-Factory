"""The worker's tools: sandboxed reads and writes.

These are the only way an agent touches a project, so the sandbox is the boundary that matters —
a path that escapes the workspace escapes into the user's machine.
"""

from pathlib import Path

import pytest

from osf.engines._tools import apply_read, apply_write
from osf.types import Workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(path=str(tmp_path), handle=str(tmp_path))


def test_write_then_read_round_trips(workspace, tmp_path: Path):
    apply_write(workspace, "src/app.py", "print('hi')\n")
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert apply_read(workspace, "src/app.py") == ("print('hi')\n", False)


@pytest.mark.parametrize("escape", ["../outside.txt", "../../etc/passwd", "src/../../oops.txt"])
def test_neither_tool_can_leave_the_workspace(escape, workspace, tmp_path: Path):
    """The agent is given a directory; it must not be able to read or write outside it."""
    (tmp_path.parent / "outside.txt").write_text("secret", encoding="utf-8")

    message, is_error = apply_write(workspace, escape, "owned")
    assert is_error and "Refused" in message
    message, is_error = apply_read(workspace, escape)
    assert is_error and "Refused" in message
    assert (tmp_path.parent / "outside.txt").read_text(encoding="utf-8") == "secret"


def test_reading_a_missing_file_explains_itself(workspace):
    message, is_error = apply_read(workspace, "nope.txt")
    assert is_error
    assert "does not exist" in message


def test_reading_a_binary_file_does_not_raise(workspace, tmp_path: Path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    message, is_error = apply_read(workspace, "logo.png")
    assert is_error
    assert "not a text file" in message


def test_a_read_before_a_rewrite_is_what_preserves_content(workspace, tmp_path: Path):
    """The workflow the tools exist to support: read, extend, write back.

    Without read_file the agent could only guess at what a file contained, and rewriting it
    silently destroyed whatever it did not think to reproduce.
    """
    (tmp_path / "index.html").write_text("<h1>Pobrecita</h1><p>A very good dog.</p>", "utf-8")

    existing, _ = apply_read(workspace, "index.html")
    apply_write(workspace, "index.html", existing + "<footer>© 2026</footer>")

    result = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "A very good dog." in result
    assert "<footer>" in result
