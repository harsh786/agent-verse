"""run_goal never re-runs a finished goal (terminal check + atomic claim).

Regression: the worker marked the goal ``executing`` without looking at its
current status. With ``acks_late`` and the Redis broker's visibility timeout
(1h by default, far shorter than a 24h enterprise goal), Celery redelivers a
long-running goal's message; once the first run finished and released its lock,
the redelivered copy ran the goal a second time.

Now the worker atomically claims the goal row
(``UPDATE goals SET status='executing' WHERE status NOT IN terminal RETURNING``)
before building anything; a goal that is already terminal is skipped, a
running goal still defers to the per-goal lock, and a claim that cannot be
verified fails closed (retry, then fail) instead of running unguarded.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.scaling.tasks import _claim_goal_for_execution as _real_claim_fn


@pytest.fixture(autouse=True)
def _real_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the suite-wide claim stub (tests/scaling/conftest.py)."""
    from app.scaling import tasks

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _real_claim_fn)


class _Lock:
    released: list[str] = []

    def __init__(self, redis_client: Any, value: str) -> None:
        pass

    def acquire(self, goal_id: str, ttl_ms: int = 0) -> bool:
        return True

    def release(self, goal_id: str) -> None:
        _Lock.released.append(goal_id)


class _RetryCalledError(Exception):
    pass


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from app.scaling import tasks

    seen: dict[str, Any] = {"provider_resolved": 0, "decrements": 0, "statuses": []}
    _Lock.released = []

    def _provider(tenant_id: str) -> Any:
        seen["provider_resolved"] += 1
        raise AssertionError("a terminal goal must not be executed again")

    async def _dec(tenant_id: str, redis_url: str) -> None:
        seen["decrements"] += 1

    async def _ensure(*a: Any, **k: Any) -> None:
        return None

    async def _update(self: Any, goal_id: str, tenant_id: str, status: str, **k: Any) -> bool:
        seen["statuses"].append(status)
        return True

    from app.services.goal_service import GoalService

    monkeypatch.setattr(GoalService, "_db_ensure_goal_row", _ensure)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", _update)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: MagicMock())
    monkeypatch.setattr(tasks, "_SyncGoalLock", _Lock)
    monkeypatch.setattr(tasks, "_get_llm_provider", _provider)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _dec)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "redis://localhost:6399/0")
    monkeypatch.setattr("redis.from_url", lambda *a, **k: object())
    return seen


def test_redelivered_terminal_goal_is_skipped(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    async def _claim(goal_id: str, tenant_id: str) -> str:
        return "complete"  # the row is already terminal: the claim matched nothing

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claim)

    result = tasks.run_goal.run("g-done", "tenant-c", "goal", "normal", False)

    assert result["status"] == "skipped"
    assert result["reason"] == "already_terminal"
    assert worker["provider_resolved"] == 0
    assert "executing" not in worker["statuses"]
    # The finished goal already released its concurrency slot.
    assert worker["decrements"] == 0
    assert _Lock.released == ["g-done"]


def test_claim_error_retries_instead_of_running(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    async def _claim(goal_id: str, tenant_id: str) -> str:
        raise ConnectionError("postgres unreachable")

    def _retry(*a: Any, **k: Any) -> Any:
        raise _RetryCalledError()

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claim)
    monkeypatch.setattr(tasks.run_goal, "retry", _retry)

    with pytest.raises(_RetryCalledError):
        tasks.run_goal.run("g-claim-err", "tenant-c", "goal", "normal", False)
    assert worker["provider_resolved"] == 0
    assert _Lock.released == ["g-claim-err"]


def test_claim_error_after_last_retry_fails_the_goal(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    async def _claim(goal_id: str, tenant_id: str) -> str:
        raise ConnectionError("postgres unreachable")

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claim)
    tasks.run_goal.push_request(retries=tasks.run_goal.max_retries)
    try:
        result = tasks.run_goal.run("g-claim-last", "tenant-c", "goal", "normal", False)
    finally:
        tasks.run_goal.pop_request()

    assert result["status"] == "failed"
    assert result["reason"] == "goal_claim_unavailable"
    assert worker["provider_resolved"] == 0
    assert worker["decrements"] == 1


def test_claimed_goal_runs(worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    async def _claim(goal_id: str, tenant_id: str) -> str:
        return "claimed"

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claim)
    # dry_run exits right after the claim, without building a provider.
    result = tasks.run_goal.run("g-live", "tenant-c", "goal", "normal", True)
    assert result["status"] == "complete"


# ── the claim SQL ─────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _Session:
    def __init__(self, claimed: Any, existing: Any) -> None:
        self.claimed = claimed
        self.existing = existing
        self.sql: list[str] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        text = str(stmt)
        self.sql.append(text)
        if text.lstrip().upper().startswith("UPDATE"):
            return _Result(self.claimed)
        if "set_config" in text:
            return _Result(None)
        return _Result(self.existing)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claimed", "existing", "expected"),
    [
        ("g", None, "claimed"),
        (None, "complete", "complete"),
        (None, "cancelled", "cancelled"),
    ],
)
async def test_claim_is_one_conditional_update(
    monkeypatch: pytest.MonkeyPatch, claimed: Any, existing: Any, expected: str
) -> None:
    from app.scaling import tasks

    session = _Session(claimed, existing)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)

    assert await tasks._claim_goal_for_execution("g", "t") == expected
    update = next(s for s in session.sql if s.lstrip().upper().startswith("UPDATE"))
    assert "NOT IN" in update.upper()
    assert "RETURNING" in update.upper()


@pytest.mark.asyncio
async def test_claim_of_a_missing_row_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    session = _Session(None, None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)

    with pytest.raises(LookupError):
        await tasks._claim_goal_for_execution("g", "t")


def test_broker_visibility_timeout_outlasts_the_longest_goal() -> None:
    """acks_late + a visibility timeout shorter than a goal = redelivery mid-run."""
    from app.scaling.celery_app import celery_app
    from app.tenancy.context import PLAN_LIMITS

    longest = max(limits.goal_timeout_seconds for limits in PLAN_LIMITS.values())
    opts = celery_app.conf.broker_transport_options or {}
    assert opts.get("visibility_timeout", 3600) > longest
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
