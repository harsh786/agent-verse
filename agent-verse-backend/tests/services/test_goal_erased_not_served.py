"""P1e-5: an erased goal is not served from a replica's memory.

Live (AGK-GOVERNANCE): the data-subject erasure deleted a goal's row, and
``GET /goals/{id}`` still answered 200 from the API process's in-memory record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent.state import GoalStatus
from app.core.errors import NotFoundError
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-erase", plan=PlanTier.FREE, api_key_id="k")


def _service(monkeypatch: pytest.MonkeyPatch, *, queue: bool, dry_run: bool = False
             ) -> GoalService:
    svc = GoalService()
    svc._db = object()
    svc._task_queue = object() if queue else None
    svc._goals["g1"] = GoalRecord(
        goal_id="g1", goal_text="g", status=GoalStatus.PLANNING, tenant_id=CTX.tenant_id,
        priority="normal", dry_run=dry_run, created_at=datetime.now(UTC).isoformat(),
    )

    async def _no_row(goal_id: str, tenant_ctx: Any) -> None:
        return None

    monkeypatch.setattr(svc, "_db_get_goal_record", _no_row)
    return svc


async def test_an_erased_goal_is_not_found_and_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service(monkeypatch, queue=True)
    with pytest.raises(NotFoundError):
        await svc.get_goal("g1", CTX)
    assert "g1" not in svc._goals


@pytest.mark.parametrize("queue, dry_run", [(False, False), (True, True)])
async def test_a_goal_whose_row_may_not_be_written_yet_is_kept(
    monkeypatch: pytest.MonkeyPatch, queue: bool, dry_run: bool
) -> None:
    """Without a task queue (or for a dry run) the row is written in the background."""
    svc = _service(monkeypatch, queue=queue, dry_run=dry_run)
    record = await svc._refresh_goal_from_db_if_needed(svc._goals["g1"], CTX)
    assert record is svc._goals["g1"]
