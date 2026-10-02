"""MEM-45: tool blacklists expire or are cleared (admin, audited), and the
reliability verdict uses decayed counters so a recovered tool becomes reliable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.memory.tool_reliability import BLACKLIST_TTL, ToolReliabilityStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

T = "t-tool-rel"
T0 = datetime(2026, 9, 1, tzinfo=UTC)


async def test_blacklist_expires() -> None:
    store = ToolReliabilityStore()
    await store.blacklist(tenant_id=T, tool_name="jira.search", reason="flaky", now=T0)
    live = await store.get_reliability(tenant_id=T, tool_name="jira.search", now=T0)
    assert live["blacklisted"] and live["unreliable"]
    assert live["blacklist_expires_at"] == (T0 + BLACKLIST_TTL).isoformat()
    later = T0 + BLACKLIST_TTL + timedelta(minutes=1)
    lapsed = await store.get_reliability(tenant_id=T, tool_name="jira.search", now=later)
    assert not lapsed["blacklisted"] and not lapsed["unreliable"]


async def test_clear_blacklist() -> None:
    store = ToolReliabilityStore()
    assert await store.clear_blacklist(tenant_id=T, tool_name="x") is False
    await store.blacklist(tenant_id=T, tool_name="x", reason="flaky", now=T0)
    assert await store.clear_blacklist(tenant_id=T, tool_name="x") is True
    assert not (await store.get_reliability(tenant_id=T, tool_name="x", now=T0))["unreliable"]


async def test_recovered_tool_becomes_reliable_again() -> None:
    store = ToolReliabilityStore()
    for _ in range(10):
        await store.record(tenant_id=T, tool_name="gh", success=False, now=T0)
    assert (await store.get_reliability(tenant_id=T, tool_name="gh", now=T0))["unreliable"]
    month_later = T0 + timedelta(days=30)
    for _ in range(5):
        await store.record(tenant_id=T, tool_name="gh", success=True, now=month_later)
    stats = await store.get_reliability(tenant_id=T, tool_name="gh", now=month_later)
    assert stats["success_rate"] < 0.4  # lifetime: 5/15
    assert stats["recent_success_rate"] > 0.9 and not stats["unreliable"]


# ── DELETE /memory/tool-reliability/{tool}/blacklist ─────────────────────────


def _app(store: ToolReliabilityStore, roles: tuple[str, ...], audit: Any) -> FastAPI:
    from app.api.memory import router

    ctx = TenantContext(tenant_id=T, plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=roles)

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == "k1" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.tool_reliability_store = store
    app.state.audit_log = audit
    app.state.db_session_factory = None
    return app


async def _delete(app: FastAPI, tool: str) -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete(f"/memory/tool-reliability/{tool}/blacklist", headers={"X-API-Key": "k1"})
    return r.status_code


async def test_clear_route_is_admin_only_and_audited() -> None:
    store = ToolReliabilityStore()
    await store.blacklist(tenant_id=T, tool_name="jira.search", reason="flaky")
    audit = AsyncMock()
    assert await _delete(_app(store, ("viewer",), audit), "jira.search") == 403
    assert (await store.get_reliability(tenant_id=T, tool_name="jira.search"))["blacklisted"]
    audit.record_async.assert_not_awaited()

    assert await _delete(_app(store, ("admin",), audit), "jira.search") == 204
    assert not (await store.get_reliability(tenant_id=T, tool_name="jira.search"))["blacklisted"]
    (event,) = [c.args[0] for c in audit.record_async.await_args_list]
    assert event.tool_name == "jira.search" and event.outcome == "blacklist_cleared"

    assert await _delete(_app(store, ("admin",), audit), "jira.search") == 404


async def test_clear_route_fails_closed_without_a_durable_audit() -> None:
    store = ToolReliabilityStore()
    await store.blacklist(tenant_id=T, tool_name="x", reason="flaky")
    audit = AsyncMock()
    audit.record_async.side_effect = RuntimeError("audit db down")
    assert await _delete(_app(store, ("admin",), audit), "x") == 503
    assert (await store.get_reliability(tenant_id=T, tool_name="x"))["blacklisted"]
