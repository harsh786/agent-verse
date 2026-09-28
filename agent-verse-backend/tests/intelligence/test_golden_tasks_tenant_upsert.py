"""add_golden_task must never overwrite another tenant's golden task.

``golden_tasks.id`` is a global primary key and the caller may supply it. The
upsert was ``ON CONFLICT (id) DO UPDATE SET goal = EXCLUDED.goal`` with no owner
check, so tenant B re-using tenant A's task id rewrote A's golden task. The
conflict branch is now restricted to the caller's own row and a collision with a
foreign row is reported instead of silently "succeeding".
"""

from __future__ import annotations

from typing import Any

import pytest

from app.intelligence.eval_suite import GoldenTask, add_golden_task


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Session:
    def __init__(self, returned_row: Any) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self._returned = returned_row

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.statements.append((str(stmt), params or {}))
        return _Result(self._returned)


def _upsert(session: _Session) -> tuple[str, dict[str, Any]]:
    return next(s for s in session.statements if "INSERT INTO golden_tasks" in s[0])


@pytest.mark.asyncio
async def test_upsert_only_updates_the_callers_own_row() -> None:
    session = _Session(returned_row=("gt-1",))
    tid = await add_golden_task(
        eval_suite_id="s1", task=GoldenTask(task_id="gt-1", goal="g"), tenant_id="t-a",
        db=lambda: session,
    )
    assert tid == "gt-1"
    sql, params = _upsert(session)
    assert "WHERE golden_tasks.tenant_id = EXCLUDED.tenant_id" in sql
    assert "RETURNING id" in sql
    assert params["tid"] == "t-a"
    # ...and it ran under the caller's tenant GUC.
    guc_sql, guc_params = session.statements[0]
    assert "set_config('app.tenant_id'" in guc_sql
    assert guc_params == {"tid": "t-a"}


@pytest.mark.asyncio
async def test_id_owned_by_another_tenant_is_rejected_not_overwritten() -> None:
    # The guarded ON CONFLICT branch updated nothing -> RETURNING yields no row.
    session = _Session(returned_row=None)
    with pytest.raises(ValueError, match="already in use"):
        await add_golden_task(
            eval_suite_id="s1", task=GoldenTask(task_id="gt-owned-by-a", goal="evil"),
            tenant_id="t-b", db=lambda: session,
        )
