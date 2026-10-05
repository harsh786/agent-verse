"""P4-2: the audit writer refuses an overflowing id loudly; it never truncates."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.governance.audit import (
    AuditEvent,
    AuditFieldTooLongError,
    AuditLog,
    check_audit_widths,
)
from app.governance.permissions import ActionLevel
from app.tenancy.context import PlanTier, TenantContext


def _event(**kw: Any) -> AuditEvent:
    base: dict[str, Any] = {
        "goal_id": "g",
        "tool_name": "t",
        "action_level": ActionLevel.ALLOW_LOG,
        "outcome": "success",
    }
    base.update(kw)
    return AuditEvent(**base)


class _ExplodingDb:
    calls = 0

    def __call__(self) -> Any:
        type(self).calls += 1
        raise AssertionError("an overflowing row must never reach the database")


def test_dashed_uuid_tenant_and_ids_fit() -> None:
    check_audit_widths(
        {"tenant_id": str(uuid.uuid4()), "goal_id": str(uuid.uuid4()), "id": uuid.uuid4().hex}
    )


@pytest.mark.parametrize("field", ["tenant_id", "goal_id", "step_id", "api_key_id"])
def test_overflow_names_the_column(field: str) -> None:
    with pytest.raises(AuditFieldTooLongError, match=f"audit_log.{field} is 65 chars"):
        check_audit_widths({field: "x" * 65})


@pytest.mark.asyncio
async def test_record_async_refuses_without_retrying_the_db() -> None:
    db = _ExplodingDb()
    log = AuditLog(db_session_factory=db, retry_base_delay=0)
    ctx = TenantContext(tenant_id="t" * 65, plan=PlanTier.FREE, api_key_id="k")
    with pytest.raises(AuditFieldTooLongError):
        await log.record_async(_event(), tenant_ctx=ctx)
    assert _ExplodingDb.calls == 0


@pytest.mark.asyncio
async def test_record_never_truncates_a_long_workflow_step_id() -> None:
    """The sync writer counts the overflow as a lost write (logged as an error);
    the stored step id is never a silently truncated prefix."""
    log = AuditLog(db_session_factory=_ExplodingDb(), retry_base_delay=0)
    ctx = TenantContext(tenant_id=uuid.uuid4().hex, plan=PlanTier.FREE, api_key_id="k")
    log.record(_event(step_id="s" * 80), tenant_ctx=ctx)
    assert await log.flush(timeout=5) == 1
