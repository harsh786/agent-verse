"""Unit: every chat/billing DB path runs tenant-scoped (GUC + predicate).

Companion to ``test_chat_billing_rls_integration.py`` (real Postgres,
NOBYPASSRLS role); these pin the statement shape without Docker.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tenancy.context import PlanTier, TenantContext
from tests.db._recording_session import RecordingSession, factory_for

TID = "tenant-unit-a"


def _ctx(tenant_id: str = TID) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _assert_scoped(session: RecordingSession, tenant_id: str = TID) -> None:
    assert session.began >= 1, "no explicit transaction"
    assert session.guc_calls and session.guc_calls[0] == tenant_id
    assert session.log[0] == f"guc:{tenant_id}", "data statement ran before the GUC"


# ── goal_templates (POST /chat/templates, POST /templates) ─────────────────────


@pytest.mark.asyncio
async def test_template_create_builds_row_in_scoped_tx_without_refresh() -> None:
    from app.api.templates import _TemplateStore

    session = RecordingSession()
    store = _TemplateStore(seed_builtins=False)
    store.set_db(factory_for(session))

    row = await store.create(TID, "P", "d", "You are helpful", "chat_persona", [])

    assert row["tenant_id"] == TID and row["version"] == 1
    assert session.began == 1 and session.guc_calls[0] == TID
    assert len(session.added) == 1
    # sqlalchemy_rls_context flushes the INSERT while the GUC is still set.
    assert session.flushed == 1


# ── budget_configs ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_upsert_is_tenant_scoped() -> None:
    from app.api.costs import UpdateBudgetRequest, update_budgets

    session = RecordingSession()
    tracker = SimpleNamespace(_db=factory_for(session))
    request = SimpleNamespace(
        state=SimpleNamespace(tenant=_ctx()),
        app=SimpleNamespace(state=SimpleNamespace(cost_tracker=tracker)),
    )
    await update_budgets(request, UpdateBudgetRequest(per_goal_usd=2.0))  # type: ignore[arg-type]

    _assert_scoped(session)
    [(sql, params)] = session.statements
    assert "INSERT INTO budget_configs" in sql and params["tid"] == TID


@pytest.mark.parametrize(
    "body",
    [
        {"per_goal_usd": 1e12},  # overflows NUMERIC(10,4)
        {"per_tenant_daily_usd": -1},
        {"alert_pct_thresholds": [0]},
        {"alert_pct_thresholds": [2**40]},  # overflows INTEGER[]
        {"per_agent_daily_usd": {"a": 1e9}},
    ],
)
def test_budget_out_of_range_is_422_not_500(body: dict) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.costs import router
    from app.tenancy.middleware import TenantMiddleware

    async def _resolve(key: str) -> TenantContext | None:
        # PUT /costs/budgets is admin-only; validation is what is under test.
        return (
            TenantContext(
                tenant_id=TID, plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=("admin",)
            )
            if key == "k"
            else None
        )

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.cost_tracker = SimpleNamespace(_db=None)
    resp = TestClient(app).put("/costs/budgets", json=body, headers={"X-API-Key": "k"})
    assert resp.status_code == 422, resp.text


# ── cost_ledger (analytics) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_by_model_groups_on_model_column() -> None:
    from app.analytics.aggregator import GoalAnalyticsAggregator

    session = RecordingSession(rows=[("gpt-4o", 1.5)])
    agg = GoalAnalyticsAggregator()
    agg._db = factory_for(session)
    out = await agg.cost_by_model_db(TID)
    [(sql, params)] = session.statements
    assert "COALESCE(model, 'unknown')" in sql and "tool_name" not in sql
    assert params["tid"] == TID and session.guc_calls[0] == TID
    assert out == {"gpt-4o": 1.5}


# ── POST /chat/templates validation ────────────────────────────────────────────


@pytest.fixture()
async def chat_client():
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "TplVal", "email": "tv@test.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        yield c


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        b'{"name": "a\\u0000b", "system_prompt": "x"}',
        b'{"name": "a", "system_prompt": "x\\ud800"}',
        b'{"name": "a", "description": "\\u0000", "system_prompt": "x"}',
    ],
)
async def test_chat_template_unstorable_text_is_422(chat_client, raw: bytes) -> None:
    r = await chat_client.post(
        "/chat/templates", content=raw, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_chat_template_description_is_bounded(chat_client) -> None:
    r = await chat_client.post(
        "/chat/templates",
        json={"name": "a", "description": "x" * 2001, "system_prompt": "x"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_chat_template_valid_unicode_is_created(chat_client) -> None:
    r = await chat_client.post(
        "/chat/templates",
        json={"name": "Persona 😀", "description": "désc", "system_prompt": "你好"},
    )
    assert r.status_code == 201, r.text
