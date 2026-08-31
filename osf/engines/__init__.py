"""Real agent-engine adapters implementing `osf.runtime.AgentRuntime`.

Unlike `osf.local` (offline scripted stand-ins), these call live agent backends. The provider in a
`ModelRef` selects the adapter — `fireworks` (OpenAI-compatible, the factory default) or
`anthropic`. Requires the `agent` extra (`pip install -e ".[agent]"`) and the provider's API key.
"""

from __future__ import annotations

from osf.planner import Planner
from osf.runtime import AgentRuntime
from osf.types import ModelRef


def resolve_runtime(model: ModelRef) -> AgentRuntime:
    """Pick the engine adapter for a model's provider (opencode-style provider resolution)."""
    if model.provider_id == "fireworks":
        from osf.engines.fireworks import FireworksRuntime

        return FireworksRuntime(model)
    if model.provider_id == "anthropic":
        from osf.engines.claude import ClaudeRuntime

        return ClaudeRuntime(model)
    raise ValueError(f"no engine adapter for provider {model.provider_id!r}")


def resolve_planner(model: ModelRef) -> Planner:
    """Pick the planning adapter for a model's provider, mirroring `resolve_runtime`."""
    if model.provider_id == "fireworks":
        from osf.engines.fireworks import FireworksPlanner

        return FireworksPlanner(model)
    if model.provider_id == "anthropic":
        from osf.engines.claude import ClaudePlanner

        return ClaudePlanner(model)
    raise ValueError(f"no planning adapter for provider {model.provider_id!r}")
