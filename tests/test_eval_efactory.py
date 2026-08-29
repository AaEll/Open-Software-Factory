"""End-to-end eval: efactory.ai landing page runs through the pipeline and merges."""

import asyncio

from evals import efactory_webpage


def test_efactory_webpage_end_to_end():
    result, checks = asyncio.run(efactory_webpage.run())

    assert result.merged, "PR should merge once checks pass"
    assert result.agent.outcome == "completed"

    failed = [c.criterion for c in checks if not c.passed]
    assert not failed, f"unmet acceptance criteria: {failed}"
