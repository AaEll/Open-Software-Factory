"""Open Software Factory (OSF).

Autonomous multi-agent system that turns objectives into merged PRs.

Phase 0: this package defines the core contracts (`AgentRuntime`, `IsolationBackend`,
`Forge`) and the event-sourced data model. No product logic yet. See docs/ARCHITECTURE.md.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("open-software-factory")
except PackageNotFoundError:  # not installed (e.g. running from a source tree without build)
    __version__ = "0.0.0"
