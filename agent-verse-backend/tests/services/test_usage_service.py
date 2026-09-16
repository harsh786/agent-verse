"""Tests for Phase 1e — usage metering service."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.services.usage_service import UsageService


class _FakeUsageSession:
    """Simulates the usage_records rollup SELECT ... GROUP BY metric."""

    def __init__(self, rows: list[tuple[str, float, float]]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "_FakeUsageSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> "_FakeUsageSession":
        return self

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        class _Result:
            def __init__(self, rows: list[tuple[str, float, float]]) -> None:
                self._rows = rows

            def all(self) -> list[tuple[str, float, float]]:
                return self._rows

        return _Result(self._rows)


@asynccontextmanager
async def _noop_rls(session: Any, tenant_id: str) -> AsyncIterator[Any]:
    yield session


class TestUsageService:
    @pytest.mark.asyncio
    async def test_record_adds_to_buffer(self):
        svc = UsageService()
        await svc.record(tenant_id="t1", metric="goals", quantity=1.0)
        assert len(svc._buffer) == 1
        assert svc._buffer[0]["metric"] == "goals"

    @pytest.mark.asyncio
    async def test_record_goal_completion(self):
        svc = UsageService()
        await svc.record_goal_completion(
            tenant_id="t1",
            goal_id="g1",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=0.015,
        )
        assert any(r["metric"] == "goals" for r in svc._buffer)
        assert any(r["metric"] == "llm_tokens" for r in svc._buffer)

    @pytest.mark.asyncio
    async def test_get_usage_summary(self):
        svc = UsageService()
        await svc.record(tenant_id="t1", metric="goals", quantity=5.0)
        await svc.record(tenant_id="t1", metric="tool_calls", quantity=10.0, unit_cost_usd=0.001)
        await svc.record(tenant_id="t2", metric="goals", quantity=3.0)  # other tenant

        summary = await svc.get_usage_summary("t1")
        assert summary["usage"]["goals"] == 5.0
        assert summary["usage"]["tool_calls"] == 10.0
        assert summary["usage"].get("goals") == 5.0  # t2 goals not mixed in
        assert "t2" not in str(summary.get("tenant_id", ""))  # summary is for t1

    @pytest.mark.asyncio
    async def test_get_usage_summary_rolls_up_db_and_merges_buffer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.db import rls as rls_module

        monkeypatch.setattr(rls_module, "sqlalchemy_rls_context", _noop_rls)

        # DB holds already-flushed rows; the buffer holds un-flushed records.
        session = _FakeUsageSession([("goals", 40.0, 0.60), ("tool_calls", 100.0, 0.10)])
        svc = UsageService(db_factory=lambda: session)
        await svc.record(tenant_id="t1", metric="goals", quantity=2.0, unit_cost_usd=0.01)
        await svc.record(tenant_id="t2", metric="goals", quantity=9.0)  # other tenant

        summary = await svc.get_usage_summary("t1")

        assert summary["usage"]["goals"] == 42.0  # 40 (DB) + 2 (buffer)
        assert summary["usage"]["tool_calls"] == 100.0
        assert summary["costs"]["goals"] == pytest.approx(0.62)
        assert summary["total_cost_usd"] == pytest.approx(0.72)

    @pytest.mark.asyncio
    async def test_get_usage_summary_no_db_uses_buffer_only(self) -> None:
        svc = UsageService()  # no DB factory
        await svc.record(tenant_id="t1", metric="goals", quantity=3.0)
        summary = await svc.get_usage_summary("t1")
        assert summary["usage"]["goals"] == 3.0

    @pytest.mark.asyncio
    async def test_record_tool_call(self):
        svc = UsageService()
        await svc.record_tool_call(
            tenant_id="t1", tool_name="jira_search", server_id="jira", goal_id="g1"
        )
        assert any(r["metric"] == "tool_calls" for r in svc._buffer)

    def test_billing_router_importable(self):
        from app.api.billing import router

        assert router is not None

    def test_billing_has_usage_endpoint(self):
        from app.api.billing import router

        paths = [r.path for r in router.routes]
        assert any("/usage" in p for p in paths)

    def test_billing_has_plans_endpoint(self):
        from app.api.billing import router

        paths = [r.path for r in router.routes]
        assert any("/plans" in p for p in paths)

    def test_usage_service_module_singleton(self):
        from app.services.usage_service import _usage_service

        assert _usage_service is not None
