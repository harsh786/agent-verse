"""A replayed signed webhook refused by the guard is audited in trigger_events.

Live baseline finding: the guard answered a redelivery as a dedup no-op before
the dispatcher ran, so the trigger's event history showed nothing for it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.webhooks import replay


async def _always_delivered(*_a: Any, **_k: Any) -> bool:
    return True


async def test_refused_replay_calls_the_audit_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replay, "already_delivered", _always_delivered)
    audited: list[str] = []
    dispatched: list[str] = []

    async def _dispatch() -> Any:
        dispatched.append("x")

    async def _audit() -> None:
        audited.append("dedup")

    result = await replay.guarded_dispatch(
        object(), "t1", "trg", "signed-body:abc", _dispatch, on_refused=_audit
    )
    assert result.skip_reason == "dedup"
    assert audited == ["dedup"] and dispatched == []


async def test_audit_failure_never_dispatches_the_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replay, "already_delivered", _always_delivered)
    dispatched: list[str] = []

    async def _dispatch() -> Any:
        dispatched.append("x")

    async def _audit() -> None:
        raise RuntimeError("db down")

    result = await replay.guarded_dispatch(
        object(), "t1", "trg", "signed-body:abc", _dispatch, on_refused=_audit
    )
    assert result.skip_reason == "dedup" and dispatched == []


async def test_dispatcher_records_a_dedup_row_with_its_own_key() -> None:
    dispatcher = TriggerDispatcher.__new__(TriggerDispatcher)
    rows: list[Any] = []

    async def _persist(event: Any) -> None:
        rows.append(event)

    dispatcher._persist_event = _persist  # type: ignore[method-assign]

    class _Spec:
        trigger_id = "trg-1"
        trigger_type = "webhook"

    await dispatcher.record_refused_replay(_Spec(), {"a": 1}, "t1")
    await dispatcher.record_refused_replay(_Spec(), {"a": 1}, "t1")
    assert [r.skip_reason for r in rows] == ["dedup", "dedup"]
    assert rows[0].trigger_id == "trg-1" and rows[0].goal_id is None
    assert rows[0].idempotency_key != rows[1].idempotency_key
