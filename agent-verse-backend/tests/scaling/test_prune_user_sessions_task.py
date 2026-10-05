"""SAML-01: expired user sessions are pruned by a scheduled maintenance task.

The pruning SQL runs on real Postgres in
tests/integration/test_user_sessions_pg.py; this pins the Celery wiring.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch


def test_prune_user_sessions_is_scheduled_on_the_maintenance_queue() -> None:
    from app.scaling.celery_app import celery_app

    entries = [
        e
        for e in celery_app.conf.beat_schedule.values()
        if e["task"] == "agentverse.maintenance.prune_user_sessions"
    ]
    assert len(entries) == 1
    assert entries[0]["options"]["queue"] == "maintenance"
    assert entries[0]["schedule"] <= 3600


def test_task_prunes_on_the_maintenance_factory_and_raises_on_failure() -> None:
    from app.scaling import tasks

    factory = object()
    prune = AsyncMock(return_value=12)
    with (
        patch("app.db.session.get_system_session_factory", return_value=factory),
        patch("app.auth.user_sessions.prune_user_sessions", prune),
    ):
        result: dict[str, Any] = tasks.prune_user_sessions()
    assert result["pruned_count"] == 12
    assert prune.await_args.args == (factory,)

    failing = AsyncMock(side_effect=RuntimeError("db down"))
    with (
        patch("app.db.session.get_system_session_factory", return_value=factory),
        patch("app.auth.user_sessions.prune_user_sessions", failing),
    ):
        try:
            tasks.prune_user_sessions()
        except RuntimeError:
            pass
        else:  # pragma: no cover - the assertion below explains the failure
            raise AssertionError("a failed prune must raise, never report 0 rows")
