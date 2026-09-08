"""WorkflowRunner._execute_inline must scope its failure update to the tenant.

When an inline (test / no-Celery) run raises, the failure is recorded via
``run_store.update_status`` — which is keyword-only ``tenant_id``-required (RLS).
The handler previously omitted ``tenant_id``, so a failing inline run raised
``TypeError`` inside the except block (HTTP 500) instead of marking the run
FAILED. This proves the tenant id is threaded through.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.workflow.dsl import WorkflowDefinition
from app.workflow.runner import WorkflowRunner
from app.workflow.state import WorkflowRunStatus

pytestmark = pytest.mark.asyncio


class _RaisingCompiled:
    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> None:
        raise RuntimeError("boom")


class _Compiler:
    def compile(self, definition: Any) -> _RaisingCompiled:
        return _RaisingCompiled()


async def test_execute_inline_records_failure_scoped_to_tenant() -> None:
    run_store = AsyncMock()
    runner = WorkflowRunner(compiler=_Compiler(), run_store=run_store)
    state = {"tenant_id": "tenant-xyz", "run_id": "run-1"}

    # Must not raise (the failure is caught and recorded, not propagated).
    await runner._execute_inline("run-1", WorkflowDefinition(name="x"), state)  # type: ignore[arg-type]

    run_store.update_status.assert_awaited_once()
    call = run_store.update_status.await_args
    assert call.args[0] == "run-1"
    assert call.args[1] == WorkflowRunStatus.FAILED
    assert call.kwargs["tenant_id"] == "tenant-xyz"
