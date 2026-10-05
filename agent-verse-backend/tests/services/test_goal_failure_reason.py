"""NF-14: GET /goals/{id} shows why a goal failed — sanitized.

The reason lived only in goals.error_message (never even loaded from the DB)
and in worker_failed / goal_failed events; the goal resource had no field for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.state import GoalStatus
from app.services.failure_reason import public_failure_reason, terminal_reason_code
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-nf14", plan=PlanTier.PROFESSIONAL, api_key_id="k")


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("OperationalError: connect to postgresql+asyncpg://app:s3cret@db.internal:5432/av",
         ["s3cret", "db.internal", "5432"]),
        ("ConnectionRefusedError: [Errno 61] Connect call failed ('10.0.3.7', 6379)",
         ["10.0.3.7", "6379"]),
        ("ConnectionError: Error 111 connecting to redis-master:6379. Connection refused.",
         ["redis-master"]),
        ("ServerSelectionTimeoutError: mongo-0.mongo.prod.svc.cluster.local:27017 timed out",
         ["mongo-0", "cluster.local", "27017"]),
        ("HTTP 401 from https://api.vendor.test/v1/x?api_key=abcdef0123456789",
         ["api.vendor.test", "abcdef0123456789"]),
        ("RuntimeError: Authorization: Bearer sk_" "live_ABCDEFGHIJKLMNOPQRSTUVWX failed",
         ["sk_" "live_ABCDEFGHIJKLMNOPQRSTUVWX"]),
        ("upstream fe80::1ff:fe23:4567:890a refused", ["fe80::1ff:fe23:4567:890a"]),
        ("login as admin:hunter2@10.1.1.1 refused", ["hunter2"]),
    ],
)
def test_reason_never_carries_secrets_or_hosts(raw: str, leaked: list[str]) -> None:
    out = public_failure_reason(raw)
    assert out is not None
    for fragment in leaked:
        assert fragment not in out, out


def test_reason_keeps_the_human_explanation_and_is_bounded() -> None:
    text = (
        "approval expired: approval request abc123 was not decided before it expired "
        "while the goal waited for a human (supervised mode). Resubmit the goal to retry."
    )
    assert public_failure_reason(text) == text
    assert public_failure_reason("") is None
    assert public_failure_reason(None) is None
    long = public_failure_reason("x " * 2000)
    assert long is not None and len(long) <= 500


@pytest.mark.parametrize(
    ("status", "message", "code"),
    [
        ("failed", "approval expired: approval request r1 ...", "approval_expired"),
        ("failed", "Goal runner lost: no heartbeat for over 120s", "runner_lost"),
        ("failed", "Dead lettered: max_retries_exceeded", "dead_lettered"),
        ("failed", "Goal timed out after 3600s", "timeout"),
        ("failed", "AttributeError: 'NoneType' object has no attribute 'x'", "error"),
        ("cancelled", "Blocked by emergency stop: tenant stop", "emergency_stop"),
        ("cancelled", "cancelled by operator", "cancelled"),
        ("complete", "", None),
        ("executing", "anything", None),
    ],
)
def test_terminal_reason_codes(status: str, message: str, code: str | None) -> None:
    assert terminal_reason_code(status, message) == code


async def test_get_goal_exposes_sanitized_failure_reason() -> None:
    svc = GoalService()
    svc._goals["g-failed"] = GoalRecord(
        goal_id="g-failed",
        goal_text="Sync the orders",
        status=GoalStatus.FAILED,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-10-05T00:00:00+00:00",
        error_message="OperationalError: password=hunter2 host db.prod.internal:5432 down",
    )
    svc._goals["g-ok"] = GoalRecord(
        goal_id="g-ok",
        goal_text="Fine",
        status=GoalStatus.COMPLETE,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-10-05T00:00:00+00:00",
    )

    failed = await svc.get_goal("g-failed", tenant_ctx=CTX)
    assert failed["terminal_reason"] == "error"
    reason = failed["failure_reason"]
    assert reason.startswith("OperationalError")
    assert "hunter2" not in reason and "db.prod.internal" not in reason

    ok = await svc.get_goal("g-ok", tenant_ctx=CTX)
    assert ok["failure_reason"] is None
    assert ok["terminal_reason"] is None


async def test_failure_reason_survives_a_db_load_on_another_replica() -> None:
    row = SimpleNamespace(
        id="g-db",
        tenant_id=CTX.tenant_id,
        goal_text="Durable goal",
        status="failed",
        priority="normal",
        dry_run=False,
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        completed_at=datetime(2026, 10, 5, 1, tzinfo=UTC),
        agent_id=None,
        workflow_mode="single_agent",
        execution_context={},
        error_message="approval expired: approval request r9 was not decided in time",
    )

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return row

    class _Begin:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *a: object) -> None:
            return None

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        def begin(self) -> _Begin:
            return _Begin()

        async def execute(self, *a: Any, **k: Any) -> _Result:
            return _Result()

    svc = GoalService(db_session_factory=lambda: _Session())
    fetched = await svc.get_goal("g-db", tenant_ctx=CTX)

    assert fetched["status"] == "failed"
    assert fetched["failure_reason"].startswith("approval expired")
    assert fetched["terminal_reason"] == "approval_expired"
