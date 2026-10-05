"""a08-F182-01/02/03: civilization pause/resume/kill fail closed and actually act.

* pause()/resume() answered success after a failed (or no-op) status UPDATE and
  when no DB was wired; unknown civilization ids were not rejected.
* kill_agent always answered {killed}: no existence check, retire errors
  swallowed, and the ``civ_kill_agent`` Redis flag it set was read by nothing,
  so the agent's running goals kept going.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.civilization.governor import CivilizationControlError, Governor
from app.civilization.models import Constitution


class _Ctx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session:
    """Records statements; answers UPDATE rowcounts and SELECT rows by keyword."""

    def __init__(self, db: _DB) -> None:
        self._db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> _Ctx:
        return _Ctx()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        if "set_config" in sql:
            return SimpleNamespace(rowcount=1, fetchall=list, fetchone=lambda: None)
        if self._db.fail:
            raise RuntimeError("db down")
        self._db.statements.append((sql, params))
        if sql.lstrip().upper().startswith("UPDATE"):
            if "goals" in sql:
                return SimpleNamespace(rowcount=len(params.get("ids", [])))
            return SimpleNamespace(rowcount=self._db.update_rows)
        rows = self._db.goal_rows if "FROM goals" in sql else []
        self._db.goal_rows = []  # one page
        return iter([(r,) for r in rows])


class _DB:
    def __init__(self, update_rows: int = 1, fail: bool = False, goal_rows: Any = None) -> None:
        self.update_rows = update_rows
        self.fail = fail
        self.goal_rows = list(goal_rows or [])
        self.statements: list[tuple[str, Any]] = []

    def __call__(self) -> _Session:
        return _Session(self)


def _gov(db: Any = None, redis: Any = None) -> Governor:
    return Governor(
        constitution=Constitution(),
        civilization_id="civ-1",
        tenant_id="t1",
        db_session_factory=db,
        redis=redis,
    )


# ── F182-02: pause / resume ──────────────────────────────────────────────────


async def test_pause_without_a_db_is_an_error() -> None:
    with pytest.raises(CivilizationControlError):
        await _gov(db=None, redis=AsyncMock()).pause()


async def test_pause_failed_update_is_an_error() -> None:
    with pytest.raises(CivilizationControlError):
        await _gov(db=_DB(fail=True), redis=AsyncMock()).pause()


async def test_pause_unknown_civilization_is_lookup_error() -> None:
    with pytest.raises(LookupError):
        await _gov(db=_DB(update_rows=0), redis=AsyncMock()).pause()


async def test_pause_flag_has_no_ttl_and_a_failed_flag_write_is_an_error() -> None:
    redis = AsyncMock()
    await _gov(db=_DB(), redis=redis).pause()
    assert "ex" not in redis.set.await_args.kwargs  # a pause never lapses by itself

    redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
    with pytest.raises(CivilizationControlError):
        await _gov(db=_DB(), redis=redis).pause()


async def test_resume_failed_flag_delete_is_an_error() -> None:
    redis = AsyncMock()
    redis.delete = AsyncMock(side_effect=ConnectionError("redis down"))
    with pytest.raises(CivilizationControlError):
        await _gov(db=_DB(), redis=redis).resume()


# ── F182-01 / F182-03: kill ──────────────────────────────────────────────────


async def test_kill_unknown_member_is_lookup_error() -> None:
    with pytest.raises(LookupError):
        await _gov(db=_DB(update_rows=0), redis=AsyncMock()).kill_agent("ghost", None)


async def test_kill_retire_failure_raises() -> None:
    with pytest.raises(CivilizationControlError):
        await _gov(db=_DB(fail=True), redis=AsyncMock()).kill_agent("a1", None)


async def test_kill_cancels_the_members_running_goals() -> None:
    db = _DB(goal_rows=["g1", "g2"])
    redis = AsyncMock()
    signal = AsyncMock()
    with patch("app.reliability.goal_lifecycle.signal_cancel", signal):
        out = await _gov(db=db, redis=redis).kill_agent("a1", None)
    assert out == {"goals_cancelled": 2, "signal_failures": 0}
    assert [c.args[0] for c in signal.await_args_list] == ["g1", "g2"]
    select_sql, select_params = next(s for s in db.statements if "FROM goals" in s[0])
    # Scoped to this tenant, this agent and this civilization; non-terminal only.
    assert select_params["tid"] == "t1"
    assert select_params["aid"] == "a1"
    assert select_params["cid"] == "civ-1"
    assert "civilization_id" in select_sql
    assert "NOT IN ('complete', 'failed', 'cancelled')" in select_sql
