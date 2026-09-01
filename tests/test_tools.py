"""The worker's tools: sandboxed reads and writes.

These are the only way an agent touches a project, so the sandbox is the boundary that matters —
a path that escapes the workspace escapes into the user's machine.
"""

from pathlib import Path

import pytest

from osf.engines._tools import (
    Toolbox,
    apply_edit,
    apply_glob,
    apply_grep,
    apply_read,
    apply_write,
)
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


# --- edit_file: exact replacement, ambiguity refused --------------------------------------------


def test_edit_replaces_one_exact_string_and_leaves_the_rest(workspace, tmp_path: Path):
    (tmp_path / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    message, is_error = apply_edit(workspace, "app.py", "b = 2", "b = 22")
    assert not is_error
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "a = 1\nb = 22\nc = 3\n"


def test_edit_refuses_an_ambiguous_match(workspace, tmp_path: Path):
    """Two matches could mean either place; guessing is how a file gets quietly corrupted."""
    (tmp_path / "app.py").write_text("x = 1\ny = 0\nz = 1\n", encoding="utf-8")
    message, is_error = apply_edit(workspace, "app.py", "= 1", "= 9")
    assert is_error
    assert "appears 2 times" in message
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "x = 1\ny = 0\nz = 1\n"  # untouched


def test_replace_all_accepts_the_ambiguity_deliberately(workspace, tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1\nz = 1\n", encoding="utf-8")
    message, is_error = apply_edit(workspace, "app.py", "= 1", "= 9", replace_all=True)
    assert not is_error
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "x = 9\nz = 9\n"


def test_edit_refuses_an_empty_old_string(workspace, tmp_path: Path):
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    message, is_error = apply_edit(workspace, "app.py", "", "everything")
    assert is_error
    assert "Use write_file" in message  # an empty match is a rewrite wearing an edit's clothes


def test_edit_explains_a_near_miss(workspace, tmp_path: Path):
    (tmp_path / "app.py").write_text("    indented = True\n", encoding="utf-8")
    message, is_error = apply_edit(workspace, "app.py", "indented = False", "x")
    assert is_error
    assert "must match exactly" in message


def test_edit_on_a_missing_file_says_so(workspace):
    message, is_error = apply_edit(workspace, "nope.py", "a", "b")
    assert is_error
    assert "does not exist" in message


# --- read-before-overwrite ----------------------------------------------------------------------


def test_write_may_create_anything(workspace, tmp_path: Path):
    box = Toolbox(workspace)
    message, is_error, kind = box.dispatch("write_file", {"path": "new.py", "content": "x = 1"})
    assert not is_error and kind == "file.write"
    assert (tmp_path / "new.py").is_file()


def test_write_over_an_unread_file_is_refused(workspace, tmp_path: Path):
    """The failure this policy exists for: a blind overwrite that discards the user's content."""
    (tmp_path / "index.html").write_text("<p>A very good dog.</p>", encoding="utf-8")
    box = Toolbox(workspace)

    message, is_error, kind = box.dispatch(
        "write_file", {"path": "index.html", "content": "<p>x</p>"}
    )
    assert is_error
    assert kind == "file.refused"
    assert "read_file it first" in message
    assert "A very good dog." in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_write_is_allowed_once_the_file_has_been_read(workspace, tmp_path: Path):
    (tmp_path / "index.html").write_text("<p>A very good dog.</p>", encoding="utf-8")
    box = Toolbox(workspace)

    box.dispatch("read_file", {"path": "index.html"})
    message, is_error, _kind = box.dispatch(
        "write_file", {"path": "index.html", "content": "<p>rewritten deliberately</p>"}
    )
    assert not is_error
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<p>rewritten deliberately</p>"


def test_editing_counts_as_having_seen_the_file(workspace, tmp_path: Path):
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    box = Toolbox(workspace)

    box.dispatch("edit_file", {"path": "app.py", "old_string": "a = 1", "new_string": "a = 2"})
    _message, is_error, _kind = box.dispatch("write_file", {"path": "app.py", "content": "a = 3\n"})
    assert not is_error


def test_the_policy_is_per_session(workspace, tmp_path: Path):
    """One worker reading a file must not license another worker's blind overwrite."""
    (tmp_path / "app.py").write_text("original\n", encoding="utf-8")
    Toolbox(workspace).dispatch("read_file", {"path": "app.py"})

    _message, is_error, _kind = Toolbox(workspace).dispatch(
        "write_file", {"path": "app.py", "content": "clobbered"}
    )
    assert is_error


def test_an_unknown_tool_is_reported_not_raised(workspace):
    message, is_error, _kind = Toolbox(workspace).dispatch("rm_rf", {"path": "/"})
    assert is_error and "Unknown tool" in message


# --- project conventions and model guidance -----------------------------------------------------


def test_the_project_instruction_file_reaches_the_worker(workspace, tmp_path: Path):
    """A repo's AGENTS.md states its house rules; an agent that never sees them will break them."""
    from osf.engines._tools import worker_system

    (tmp_path / "AGENTS.md").write_text("Every module starts with `# house style`.\n", "utf-8")
    prompt = worker_system(workspace)
    assert "house style" in prompt
    assert "AGENTS.md" in prompt


def test_kimi_gets_its_own_guidance(workspace):
    from osf.engines._tools import worker_system

    generic = worker_system(workspace)
    kimi = worker_system(workspace, model_id="accounts/fireworks/models/kimi-k2p7-code")
    assert "in parallel" in kimi
    assert "in parallel" not in generic  # the overlay is per model family, not for everyone


def test_the_worker_is_told_it_cannot_ask(workspace):
    """Our worker runs unattended — guidance written for an interactive session would mislead it."""
    from osf.engines._tools import worker_system

    prompt = worker_system(workspace, model_id="kimi-k2")
    assert "cannot ask anyone" in prompt


# --- ignored paths are out of bounds ------------------------------------------------------------


def _repo(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)


def test_reading_a_gitignored_file_is_refused(workspace, tmp_path: Path):
    """`.env` is exactly the file an agent must not be able to read into a model request."""
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("FIREWORKS_API_KEY=hunter2", encoding="utf-8")

    message, is_error, kind = Toolbox(workspace).dispatch("read_file", {"path": ".env"})
    assert is_error and kind == "file.refused"
    assert "hunter2" not in message
    assert "git ignores it" in message


def test_the_git_directory_is_refused_even_when_not_ignored(workspace, tmp_path: Path):
    _repo(tmp_path)
    _message, is_error, _kind = Toolbox(workspace).dispatch("read_file", {"path": ".git/config"})
    assert is_error


def test_writing_to_an_ignored_path_is_refused(workspace, tmp_path: Path):
    """Otherwise an agent can hide output where neither the diff nor the revert will find it."""
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text("secrets/\n", encoding="utf-8")

    _message, is_error, _kind = Toolbox(workspace).dispatch(
        "write_file", {"path": "secrets/leak.txt", "content": "x"}
    )
    assert is_error
    assert not (tmp_path / "secrets" / "leak.txt").exists()


def test_tracked_files_are_unaffected(workspace, tmp_path: Path):
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    message, is_error, _kind = Toolbox(workspace).dispatch("read_file", {"path": "app.py"})
    assert not is_error and message == "x = 1\n"


def test_a_caller_that_means_it_can_opt_out(workspace, tmp_path: Path):
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=value", encoding="utf-8")

    box = Toolbox(workspace, allow_ignored=True)
    message, is_error, _kind = box.dispatch("read_file", {"path": ".env"})
    assert not is_error and message == "KEY=value"


def test_the_prompt_puts_the_volatile_listing_last(workspace, tmp_path: Path):
    """Stable text first: the listing changes the moment the agent writes anything."""
    from osf.engines._tools import WORKER_SYSTEM, worker_system

    (tmp_path / "AGENTS.md").write_text("house rule\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    prompt = worker_system(workspace, model_id="kimi-k2")

    assert prompt.startswith(WORKER_SYSTEM)
    assert prompt.index("house rule") < prompt.index("Your working directory is")


# --- searching the project ----------------------------------------------------------------------


def test_find_files_matches_a_glob(workspace, tmp_path: Path):
    _repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("hi\n", encoding="utf-8")

    matches, is_error = apply_glob(workspace, "*.py")
    assert not is_error
    assert "src/app.py" in matches
    assert "notes.md" not in matches


def test_find_files_reports_no_match_without_erroring(workspace, tmp_path: Path):
    _repo(tmp_path)
    matches, is_error = apply_glob(workspace, "*.rs")
    assert not is_error  # "nothing here" is an answer, not a failure
    assert "No files match" in matches


def test_search_files_returns_paths_and_line_numbers(workspace, tmp_path: Path):
    _repo(tmp_path)
    (tmp_path / "app.py").write_text("import os\n\ndef handler():\n    return 1\n", "utf-8")

    matches, is_error = apply_grep(workspace, r"def \w+")
    assert not is_error
    assert "app.py:3: def handler():" in matches


def test_search_can_be_limited_by_glob(workspace, tmp_path: Path):
    _repo(tmp_path)
    (tmp_path / "app.py").write_text("TARGET\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("TARGET\n", encoding="utf-8")

    matches, _is_error = apply_grep(workspace, "TARGET", "*.md")
    assert "notes.md" in matches
    assert "app.py" not in matches


def test_search_ignores_what_git_ignores(workspace, tmp_path: Path):
    """The same boundary as read_file: search must not surface secrets either."""
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("FIREWORKS_API_KEY=hunter2\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("KEY = 'public'\n", encoding="utf-8")

    matches, _is_error = apply_grep(workspace, "KEY")
    assert "app.py" in matches
    assert "hunter2" not in matches
    assert ".env" not in apply_glob(workspace, "*")[0]


def test_a_bad_regular_expression_is_reported(workspace, tmp_path: Path):
    _repo(tmp_path)
    matches, is_error = apply_grep(workspace, "([unclosed")
    assert is_error and "not a valid regular expression" in matches


def test_search_results_are_bounded(workspace, tmp_path: Path):
    """A broad question must return a usable answer, not the whole repository."""
    from osf.engines._tools import GREP_LIMIT

    _repo(tmp_path)
    (tmp_path / "big.py").write_text("match\n" * (GREP_LIMIT + 50), encoding="utf-8")

    matches, _is_error = apply_grep(workspace, "match")
    assert matches.count("big.py:") == GREP_LIMIT
    assert "stopped at" in matches


def test_the_search_tools_are_reachable_through_the_toolbox(workspace, tmp_path: Path):
    _repo(tmp_path)
    (tmp_path / "app.py").write_text("hello\n", encoding="utf-8")
    box = Toolbox(workspace)

    found, is_error, kind = box.dispatch("find_files", {"pattern": "*.py"})
    assert not is_error and kind == "file.search" and "app.py" in found

    hits, is_error, _kind = box.dispatch("search_files", {"pattern": "hello"})
    assert not is_error and "app.py:1" in hits
