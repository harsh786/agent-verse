"""Cache read/write symmetry — a poisoned entry is never stored OR served.

Regression for the false-completion bug: a 'requires approval (non-supervised
mode)' placeholder (or any error/empty/reasoning output) must be rejected on
every cache path so it can't be served as a fake success and satisfy the
verifier.
"""

from __future__ import annotations

import pytest

from app.agent.nodes.executor_mixin import _is_uncacheable_output


@pytest.mark.parametrize(
    "bad",
    [
        "High-risk tool 'telegram_send_message' requires approval (non-supervised mode).",
        '{"error": "boom"}',
        "error: something failed",
        "model_not_found",
        "rate_limit_exceeded",
        "tool not available",
        "argument validation failed for foo",
        "circuit open",
        '{"issues": [], "total": 0}',
        '{"projects": []}',
        "[]",
        "{}",
        "",
        "   ",
        "short",  # < 10 chars
        "I'll call the telegram_send_message tool now",
        "Let me search for the answer",
        "I will use the tool to send",
    ],
)
def test_uncacheable_outputs_are_rejected(bad: str) -> None:
    assert _is_uncacheable_output(bad) is True


@pytest.mark.parametrize(
    "good",
    [
        "{'ok': True, 'message_id': 70, 'chat_id': 1397083658}",
        '{"result": "The top 5 stocks are AAPL, MSFT, NVDA, AMZN, GOOG with details..."}',
        "The analysis completed successfully with 3 matching records returned.",
    ],
)
def test_real_results_are_cacheable(good: str) -> None:
    assert _is_uncacheable_output(good) is False


def test_none_is_uncacheable() -> None:
    assert _is_uncacheable_output(None) is True
