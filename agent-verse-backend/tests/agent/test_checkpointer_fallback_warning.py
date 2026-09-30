"""WF-15: AgentGraph warns (log + metric) when it swaps an unusable checkpointer
for the in-memory MemorySaver, and the worker's SIGTERM log names the
checkpointer really in use instead of claiming a durable checkpoint."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import AgentGraph
from app.providers.fake import FakeProvider


class _SyncOnlySaver:
    """Like the sync RedisSaver: aget_tuple exists but is not a coroutine."""

    def aget_tuple(self, config: Any) -> None:
        return None


def _metric(reason: str) -> float:
    from app.observability.metrics import CHECKPOINTER_FALLBACK_TOTAL

    return float(CHECKPOINTER_FALLBACK_TOTAL.labels(reason=reason)._value.get())


def test_sync_only_checkpointer_swap_logs_a_warning_and_counts_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _metric("sync_only")
    p = FakeProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, checkpointer=_SyncOnlySaver())

    assert isinstance(graph._checkpointer, MemorySaver)
    out = capsys.readouterr().out
    assert "agent_checkpointer_fallback_to_memory" in out
    assert "_SyncOnlySaver" in out and "sync_only" in out
    assert _metric("sync_only") == before + 1


def test_async_checkpointer_is_kept_without_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    saver = MemorySaver()
    p = FakeProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, checkpointer=saver)
    assert graph._checkpointer is saver
    assert "agent_checkpointer_fallback_to_memory" not in capsys.readouterr().out


def test_sigterm_log_names_the_real_checkpointer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import signal

    from app.scaling import tasks

    handlers: dict[int, Any] = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    tasks._setup_sigterm()
    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit):
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    message = caplog.records[-1].getMessage()
    assert "checkpointer=MemorySaver" in message
    assert "not durable" in message
    assert "checkpoint written" not in message
