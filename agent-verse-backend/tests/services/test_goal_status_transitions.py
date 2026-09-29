"""Cancel / pause never overwrite a finished goal, and never report a lost write.

Regressions:

* ``cancel_goal`` / ``pause_goal`` decided from this replica's (possibly stale)
  memory copy of the goal and then wrote the new status UNCONDITIONALLY, so a
  goal a worker had already completed was overwritten to ``cancelled`` /
  ``waiting_human``.
* A failed status write was swallowed (logged) while the API reported success,
  so the next DB refresh silently undid the cancel/pause.

Now the write is conditional (``WHERE status NOT IN terminal``) and a DB failure
surfaces as ``ServiceUnavailableError`` (HTTP 503).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import GoalStatus
from app.core.errors import ServiceUnavailableError
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

CTX = TenantContext(tenant_id="t-st", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_TERMINAL = {"complete", "failed", "cancelled"}


class _DbGoalService(GoalService):
    """GoalService over a fake goals table that honours conditional updates."""

    def __init__(self, *, fail_writes: bool = False) -> None:
        super().__init__(db_session_factory=object())
        self.rows: dict[str, str] = {}
        self.fail_writes = fail_writes
        self.events: list[dict[str, Any]] = []

    async def _db_update_goal_status(  # type: ignore[override]
        self,
        goal_id: str,
        tenant_id: str,
        status: str,
        error_message: str = "",
        iterations: int = 0,
        only_if_active: bool = False,
        raise_on_error: bool = False,
    ) -> bool:
        if self.fail_writes:
            if raise_on_error:
                raise ServiceUnavailableError("db down")
            return False
        current = self.rows.get(goal_id)
        if current is None:
            return False
        if only_if_active and current in _TERMINAL:
            return False
        self.rows[goal_id] = status
        return True

    async def _db_get_goal_record(  # type: ignore[override]
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> GoalRecord | None:
        status = self.rows.get(goal_id)
        if status is None:
            return None
        rec = self._goals.get(goal_id)
        if rec is not None:
            rec.status = GoalStatus(status)
            return rec
        return None

    async def _dispatch_event(  # type: ignore[override]
        self, goal_id: str, event: dict[str, Any], *a: Any, **k: Any
    ) -> None:
        self.events.append(event)


def _seed(
    svc: _DbGoalService,
    goal_id: str,
    *,
    memory: GoalStatus,
    db: str,
    finishes_meanwhile: bool = False,
) -> None:
    svc._goals[goal_id] = GoalRecord(
        goal_id=goal_id,
        goal_text="g",
        status=memory,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    svc.rows[goal_id] = db
    # The goal runs elsewhere (a worker); the Redis signal succeeds. With
    # ``finishes_meanwhile`` the worker completes the goal between this
    # replica's read and its status write (the race the write must survive).

    async def _signal(*_a: Any, **_k: Any) -> None:
        if finishes_meanwhile:
            svc.rows[goal_id] = "complete"

    svc._signal_runner = AsyncMock(side_effect=_signal)  # type: ignore[method-assign]


async def test_cancel_does_not_overwrite_a_goal_that_already_completed() -> None:
    svc = _DbGoalService()
    _seed(svc, "g1", memory=GoalStatus.EXECUTING, db="executing", finishes_meanwhile=True)

    result = await svc.cancel_goal("g1", CTX)

    assert svc.rows["g1"] == "complete"
    assert result["status"] == "complete"
    assert {"type": "goal_cancelled"} not in svc.events


async def test_cancel_of_a_running_goal_still_cancels() -> None:
    svc = _DbGoalService()
    _seed(svc, "g2", memory=GoalStatus.EXECUTING, db="executing")

    result = await svc.cancel_goal("g2", CTX)

    assert svc.rows["g2"] == "cancelled"
    assert result["status"] == "cancelled"
    assert {"type": "goal_cancelled"} in svc.events


async def test_cancel_surfaces_a_failed_status_write() -> None:
    svc = _DbGoalService(fail_writes=True)
    _seed(svc, "g3", memory=GoalStatus.EXECUTING, db="executing")

    with pytest.raises(ServiceUnavailableError):
        await svc.cancel_goal("g3", CTX)
    assert {"type": "goal_cancelled"} not in svc.events


async def test_pause_does_not_overwrite_a_goal_that_already_completed() -> None:
    svc = _DbGoalService()
    _seed(svc, "g4", memory=GoalStatus.EXECUTING, db="executing", finishes_meanwhile=True)

    with pytest.raises(ValueError, match="not running"):
        await svc.pause_goal("g4", CTX)
    assert svc.rows["g4"] == "complete"
    assert {"type": "goal_paused"} not in svc.events


async def test_pause_surfaces_a_failed_status_write() -> None:
    svc = _DbGoalService(fail_writes=True)
    _seed(svc, "g5", memory=GoalStatus.EXECUTING, db="executing")

    with pytest.raises(ServiceUnavailableError):
        await svc.pause_goal("g5", CTX)
    assert {"type": "goal_paused"} not in svc.events


# ── the SQL helper itself ─────────────────────────────────────────────────────


class _Session:
    def __init__(self, *, rowcount: int = 1, error: Exception | None = None) -> None:
        self.rowcount = rowcount
        self.error = error
        self.statements: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, *a: Any, **k: Any) -> Any:
        if self.error is not None and "UPDATE goals" in str(stmt):
            raise self.error
        self.statements.append(stmt)
        return MagicMock(rowcount=self.rowcount)


def _svc_with(session: _Session) -> GoalService:
    return GoalService(db_session_factory=lambda: session)


async def test_conditional_update_excludes_terminal_rows_and_reports_no_match() -> None:
    session = _Session(rowcount=0)
    svc = _svc_with(session)

    changed = await svc._db_update_goal_status(
        "g", CTX.tenant_id, "cancelled", only_if_active=True, raise_on_error=True
    )

    assert changed is False
    sql = " ".join(str(s) for s in session.statements if "UPDATE goals" in str(s))
    assert "NOT IN" in sql.upper()


async def test_update_raises_service_unavailable_when_asked() -> None:
    session = _Session(error=RuntimeError("connection reset"))
    svc = _svc_with(session)

    with pytest.raises(ServiceUnavailableError):
        await svc._db_update_goal_status(
            "g", CTX.tenant_id, "cancelled", only_if_active=True, raise_on_error=True
        )
    # Default (background writers) keeps logging instead of raising.
    assert (
        await svc._db_update_goal_status("g", CTX.tenant_id, "cancelled") is False
    )
