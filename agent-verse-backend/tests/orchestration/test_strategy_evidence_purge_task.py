"""CORE-17: the expired-evidence purge is a registered, scheduled maintenance task."""

from __future__ import annotations

from typing import Any

from app.scaling.celery_app import celery_app

_TASK = "agentverse.maintenance.purge_expired_strategy_evidence"


def test_purge_task_is_registered_and_scheduled_on_maintenance() -> None:
    celery_app.loader.import_default_modules()
    assert _TASK in celery_app.tasks
    entries = [e for e in celery_app.conf.beat_schedule.values() if e["task"] == _TASK]
    assert len(entries) == 1
    assert entries[0]["options"]["queue"] == "maintenance"


def test_purge_task_reports_errors_instead_of_raising(monkeypatch: Any) -> None:
    import app.orchestration.evidence_maintenance as mod

    async def _boom(*a: Any, **k: Any) -> int:
        raise RuntimeError("query would be affected by row-level security")

    monkeypatch.setattr(mod, "purge_expired_strategy_evidence_once", _boom)
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: object())
    assert mod.purge_expired_strategy_evidence.run() == {
        "status": "error",
        "error": "RuntimeError",
    }


def test_purge_task_returns_the_deleted_count(monkeypatch: Any) -> None:
    import app.orchestration.evidence_maintenance as mod

    async def _ok(*a: Any, **k: Any) -> int:
        return 7

    monkeypatch.setattr(mod, "purge_expired_strategy_evidence_once", _ok)
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: object())
    assert mod.purge_expired_strategy_evidence.run() == {"status": "ok", "deleted": 7}
