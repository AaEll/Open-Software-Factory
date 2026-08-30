"""Environment-derived defaults and input validation shared by the shell and the runs.

Defaults here are *suggestions* offered as a prompt default — never applied silently. The
validators raise `ValueError` with a message written for the person at the keyboard, so a prompt
can show it and ask again instead of abandoning what they were doing.
"""

from __future__ import annotations

import getpass
import os
import re

from osf.types import RepoRef

# GitHub allows alphanumerics and hyphens in a user/org; repos also allow dot and underscore.
OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+")


def default_owner() -> str:
    """Best guess at the user's forge account: an explicit env var, else the local username."""
    for var in ("OSF_OWNER", "GITHUB_OWNER", "GH_OWNER", "GITHUB_USER"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        return getpass.getuser()
    except Exception:  # no password database entry (some containers)
        return "me"


def valid_owner(value: str) -> str:
    """A forge account name: letters, numbers, and hyphens."""
    value = value.strip()
    if not OWNER_RE.fullmatch(value):
        raise ValueError(f"{value!r} isn't a valid owner — letters, numbers and dashes only")
    return value


def valid_repo_name(value: str) -> str:
    """A repository name, or a full `owner/name` for anyone who prefers to type it that way."""
    value = value.strip()
    if "/" in value:
        parse_repo(value)  # raises with its own explanation
        return value
    if not REPO_RE.fullmatch(value):
        raise ValueError(
            f"{value!r} isn't a valid repository name — letters, numbers, dot, dash, underscore"
        )
    return value


def parse_repo(ref: str) -> RepoRef:
    """Parse `owner/name`, explaining what was wrong when it isn't one."""
    ref = ref.strip()
    owner, sep, name = ref.partition("/")
    if not sep:
        raise ValueError(f"{ref!r} is missing an owner — write it as owner/{ref or 'name'}")
    if not owner or not name or "/" in name:
        raise ValueError(f"{ref!r} isn't an owner/name pair")
    return RepoRef(owner=valid_owner(owner), name=valid_repo_name(name))
