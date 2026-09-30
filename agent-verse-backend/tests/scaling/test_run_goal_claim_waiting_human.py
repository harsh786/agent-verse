"""WF-18: a redelivered run_goal message never un-pauses a goal waiting for a human.

The claim set ``status='executing'`` on any non-terminal row, waiting_human
included, so an acks_late redelivery (or a stale duplicate message) silently
resumed a goal a human had not approved. The claim now excludes waiting_human:
such a message is acked and skipped, the row untouched. Resuming is the job of
resume_goal, which moves the row out of waiting_human BEFORE it re-enqueues
run_goal (tests/services/test_resume_goal_order.py), so the legitimate relaunch
is still claimable.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.scaling.tasks import _claim_goal_for_execution as _real_claim_fn


@pytest.fixture(autouse=True)
def _real_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _real_claim_fn)


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _Session:
    def __init__(self, claimed: Any, existing: Any) -> None:
        self.claimed, self.existing = claimed, existing
        self.updates: list[str] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        text = str(stmt)
        if text.lstrip().upper().startswith("UPDATE"):
            from sqlalchemy.dialects import postgresql

            self.updates.append(
                str(stmt.compile(dialect=postgresql.dialect(),
                                 compile_kwargs={"literal_binds": True}))
            )
            return _Result(self.claimed)
        if "set_config" in text:
            return _Result(None)
        return _Result(self.existing)


@pytest.mark.asyncio
async def test_claim_never_matches_a_goal_waiting_for_a_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    session = _Session(claimed=None, existing="waiting_human")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)

    assert await tasks._claim_goal_for_execution("g", "t") == "waiting_human"
    (update,) = session.updates
    assert "'waiting_human'" in update and "NOT IN" in update.upper()


def test_redelivered_message_for_a_waiting_goal_is_acked_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    seen: dict[str, Any] = {"provider": 0, "decrements": 0, "statuses": [], "released": []}

    class _Lock:
        def __init__(self, *a: Any) -> None:
            pass

        def acquire(self, goal_id: str, ttl_ms: int = 0) -> bool:
            return True

        def release(self, goal_id: str) -> None:
            seen["released"].append(goal_id)

    def _provider(tenant_id: str) -> Any:
        seen["provider"] += 1
        raise AssertionError("a goal waiting for a human must not run")

    async def _dec(tenant_id: str, redis_url: str) -> None:
        seen["decrements"] += 1

    async def _ensure(*a: Any, **k: Any) -> None:
        return None

    async def _update(self: Any, goal_id: str, tenant_id: str, status: str, **k: Any) -> bool:
        seen["statuses"].append(status)
        return True

    async def _claim(goal_id: str, tenant_id: str) -> str:
        return "waiting_human"

    monkeypatch.setattr(GoalService, "_db_ensure_goal_row", _ensure)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", _update)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: MagicMock())
    monkeypatch.setattr(tasks, "_SyncGoalLock", _Lock)
    monkeypatch.setattr(tasks, "_get_llm_provider", _provider)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _dec)
    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claim)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "redis://localhost:6399/0")
    monkeypatch.setattr("redis.from_url", lambda *a, **k: object())

    result = tasks.run_goal.run("g-paused", "tenant-w", "goal", "normal", False)

    assert result["status"] == "skipped"
    assert result["reason"] == "waiting_for_human"
    assert seen["provider"] == 0
    assert "executing" not in seen["statuses"]
    assert seen["decrements"] == 0
    assert seen["released"] == ["g-paused"]
