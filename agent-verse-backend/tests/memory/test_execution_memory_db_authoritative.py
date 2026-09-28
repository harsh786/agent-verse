"""Execution-memory recall must not pass this replica's cache off as the DB's answer.

Regression: recall_async / recall_failures_async caught any DB error and
silently returned the process-local lists — a different (partial,
replica-local) view presented as the tenant's history.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.memory.execution import ExecutionMemory, RecallResult


class _BrokenDB:
    def __call__(self) -> _BrokenDB:
        return self

    async def __aenter__(self) -> Any:
        raise RuntimeError("DB connection failed")

    async def __aexit__(self, *a: Any) -> None:
        return None


def _mem() -> ExecutionMemory:
    mem = ExecutionMemory()
    mem._plans["t1"] = [{"goal": "Deploy service", "plan": ["build", "push"], "success": True}]
    mem._failures["t1"] = [{"goal": "Deploy service", "error": "denied"}]
    return mem


@pytest.mark.asyncio
async def test_recall_async_db_error_returns_empty_degraded() -> None:
    res = await _mem().recall_async("Deploy service", tenant_id="t1", db=_BrokenDB())
    assert isinstance(res, RecallResult)
    assert list(res) == []
    assert res.degraded is True


@pytest.mark.asyncio
async def test_recall_failures_async_db_error_returns_empty_degraded() -> None:
    res = await _mem().recall_failures_async("Deploy service", tenant_id="t1", db=_BrokenDB())
    assert list(res) == []
    assert res.degraded is True


@pytest.mark.asyncio
async def test_no_db_configured_still_uses_in_memory_store() -> None:
    res = await _mem().recall_async("Deploy service", tenant_id="t1", db=None)
    assert res.degraded is False
    assert res[0]["plan"] == ["build", "push"]
    fails = await _mem().recall_failures_async("Deploy service", tenant_id="t1", db=None)
    assert fails.degraded is False
    assert fails[0]["error"] == "denied"
