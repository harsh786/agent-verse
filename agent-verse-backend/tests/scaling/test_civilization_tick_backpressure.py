"""Civilization discovery must not grow the maintenance queue without bound.

Seen on the local stack: discover_and_tick_civilizations ran every 30 s and enqueued a
tick for each of 2,699 active civilizations (~90/s) with no expiry and no check for a
tick already queued, so the Celery 'maintenance' list reached 3.17M messages (4 GB),
Redis was OOM-killed repeatedly and every other maintenance task waited behind it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.kv.pop(k, None) is not None)


def _factory(rows: list[tuple[str, str]]) -> Any:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=rows)))
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _discover(fake: _FakeRedis, rows: list[tuple[str, str]]) -> tuple[dict, MagicMock]:
    from app.scaling.tasks import discover_and_tick_civilizations

    with patch("app.db.session.get_system_session_factory", return_value=_factory(rows)), \
         patch("app.scaling.tasks._get_sync_redis", return_value=fake), \
         patch("app.scaling.tasks.civilization_tick.apply_async") as enqueue:
        return discover_and_tick_civilizations(), enqueue


def test_a_civilization_with_a_queued_tick_is_not_enqueued_again() -> None:
    fake = _FakeRedis()
    rows = [("c1", "t1"), ("c2", "t2")]
    first, enq1 = _discover(fake, rows)
    assert first["civilizations_ticked"] == 2
    assert enq1.call_count == 2
    second, enq2 = _discover(fake, rows)
    assert second["civilizations_ticked"] == 0
    assert second["skipped_pending"] == 2
    assert enq2.call_count == 0


def test_ticks_expire_so_a_backlog_is_dropped_not_run() -> None:
    _, enq = _discover(_FakeRedis(), [("c1", "t1")])
    kwargs = enq.call_args.kwargs
    assert kwargs["args"] == ["c1", "t1"]
    assert 0 < kwargs["expires"] <= 60


def test_a_finished_tick_clears_its_marker() -> None:
    from app.scaling import tasks

    fake = _FakeRedis()
    _discover(fake, [("c1", "t1")])
    assert fake.kv, "discovery should mark the tick as pending"
    with patch("app.scaling.tasks._get_sync_redis", return_value=fake), \
         patch("app.scaling.tasks._run_async", side_effect=RuntimeError("tick body failed")):
        try:
            tasks.civilization_tick("c1", "t1")
        except RuntimeError:
            pass
    assert not fake.kv, "the marker must clear even when the tick fails"
    again, enq = _discover(fake, [("c1", "t1")])
    assert again["civilizations_ticked"] == 1
    assert enq.call_count == 1
