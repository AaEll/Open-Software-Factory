"""Live integration test: a real Fireworks Kimi K2 worker builds the efactory.ai page.

This test hits the network and costs tokens, so it is opt-in: it runs only when a Fireworks key is
present and ``OSF_RUN_LIVE=1``. The normal offline suite (and CI) skips it. Run it with:

    pip install -e ".[agent]"
    OSF_RUN_LIVE=1 pytest tests/test_eval_efactory_live.py

Model defaults to Kimi K2 (``fireworks/accounts/fireworks/models/kimi-k2p6``); override with
``OSF_MODEL``.
"""

import asyncio
import os

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

KIMI_K2 = "fireworks/accounts/fireworks/models/kimi-k2p6"

_has_key = bool(os.environ.get("FIREWORKS_API_KEY") or os.environ.get("FIREWORKS"))
_opted_in = os.environ.get("OSF_RUN_LIVE") == "1"

pytestmark = pytest.mark.skipif(
    not (_has_key and _opted_in),
    reason="live Fireworks run — set OSF_RUN_LIVE=1 and FIREWORKS_API_KEY (or FIREWORKS)",
)


def test_efactory_webpage_live_fireworks_kimi_k2():
    os.environ.setdefault("OSF_MODEL", KIMI_K2)

    from evals import efactory_live

    model, result, _workspace, checks = asyncio.run(efactory_live.run())

    assert model.provider_id == "fireworks"
    assert result.outcome == "completed"
    failed = [c.criterion for c in checks if not c.passed]
    assert not failed, f"unmet acceptance criteria: {failed}"
