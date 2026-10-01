"""The expired-evidence purge is scheduled, keeps up with write volume, fails loudly.

CORE-17 added the purge; CORE-36: one evidence row is written per strategy per
finished goal, but the daily purge deleted at most 100k rows and swallowed
failures, so at millions of goals the table grew forever. It now runs hourly,
loops until a batch comes back short (time-boxed; re-enqueues itself when the
time box ends first) and raises on failure so Celery retries and alerts.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.scaling.celery_app import celery_app

_TASK = "agentverse.maintenance.purge_expired_strategy_evidence"


def test_purge_task_is_registered_and_scheduled_hourly_on_maintenance() -> None:
    celery_app.loader.import_default_modules()
    assert _TASK in celery_app.tasks
    entries = [e for e in celery_app.conf.beat_schedule.values() if e["task"] == _TASK]
    assert len(entries) == 1
    assert entries[0]["options"]["queue"] == "maintenance"
    schedule = entries[0]["schedule"]
    assert len(schedule.hour) == 24  # every hour, not once a day


class _Store:
    """StrategyEvidenceStore stand-in whose batches shrink as rows run out."""

    def __init__(self, rows: int) -> None:
        self.rows = rows
        self.batches = 0

    async def _purge_batch(self, cutoff: Any, batch_size: int) -> int:
        self.batches += 1
        deleted = min(self.rows, batch_size)
        self.rows -= deleted
        return deleted


@pytest.mark.asyncio
async def test_purge_loops_until_a_short_batch() -> None:
    from app.orchestration.strategy_certification import StrategyEvidenceStore

    fake = _Store(rows=250_000)
    deleted, drained = await StrategyEvidenceStore.purge_until_drained(
        fake,  # type: ignore[arg-type]
        batch_size=5000,
        time_budget_s=60,
    )
    assert (deleted, drained) == (250_000, True)
    assert fake.rows == 0
    assert fake.batches == 51  # 50 full batches, then the short (empty) one


@pytest.mark.asyncio
async def test_purge_stops_at_the_time_box_and_reports_not_drained() -> None:
    from app.orchestration.strategy_certification import StrategyEvidenceStore

    ticks = iter(range(100))
    fake = _Store(rows=1_000_000)
    deleted, drained = await StrategyEvidenceStore.purge_until_drained(
        fake,  # type: ignore[arg-type]
        batch_size=1000,
        time_budget_s=3,
        clock=lambda: float(next(ticks)),
    )
    assert drained is False and 0 < deleted < 1_000_000


def test_purge_task_raises_on_failure(monkeypatch: Any) -> None:
    import app.orchestration.evidence_maintenance as mod

    async def _boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError("query would be affected by row-level security")

    monkeypatch.setattr(mod, "purge_expired_strategy_evidence_once", _boom)
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: object())
    with pytest.raises(RuntimeError):
        mod.purge_expired_strategy_evidence.run()


def test_purge_task_re_enqueues_itself_when_not_drained(monkeypatch: Any) -> None:
    import app.orchestration.evidence_maintenance as mod

    async def _partial(*a: Any, **k: Any) -> dict[str, Any]:
        return {"deleted": 900_000, "drained": False}

    queued: list[dict[str, Any]] = []
    monkeypatch.setattr(mod, "purge_expired_strategy_evidence_once", _partial)
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: object())
    monkeypatch.setattr(
        mod.purge_expired_strategy_evidence, "apply_async", lambda **kw: queued.append(kw)
    )
    out = mod.purge_expired_strategy_evidence.run()
    assert out == {"status": "ok", "deleted": 900_000, "drained": False}
    assert queued and queued[0]["queue"] == "maintenance"


def test_purge_task_returns_the_deleted_count(monkeypatch: Any) -> None:
    import app.orchestration.evidence_maintenance as mod

    async def _ok(*a: Any, **k: Any) -> dict[str, Any]:
        return {"deleted": 7, "drained": True}

    monkeypatch.setattr(mod, "purge_expired_strategy_evidence_once", _ok)
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: object())
    assert mod.purge_expired_strategy_evidence.run() == {
        "status": "ok", "deleted": 7, "drained": True,
    }
