"""ENT-28: a priced marketplace template never installs without a completed purchase.

Authors could set ``price_usd`` but ``install`` never looked at it, so every
tenant deployed paid templates free. Install now answers ``payment_required``
(HTTP 402) unless the buyer holds a ``completed`` purchase row, checked inside
the install transaction under the buyer's RLS context. The unfinished
monetization API (set-price / onboard-author / purchase) is removed.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.enterprise.marketplace_v2 import MarketplaceV2
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


def test_monetization_api_is_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("app.api.marketplace_monetization") is None
    from app.bootstrap.routers import _GUARDED_ROUTERS

    assert not any("monetization" in r[1] for r in _GUARDED_ROUTERS)


@pytest.mark.parametrize("route", ["/marketplace/templates/t1/deploy", "/marketplace/t1/deploy"])
def test_deploy_routes_map_payment_required_to_402(route: str) -> None:
    from app.api.enterprise import marketplace_router
    from app.tenancy.middleware import TenantMiddleware

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _ctx("buyer") if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(marketplace_router)
    svc = AsyncMock()
    svc.install = AsyncMock(
        return_value={
            "success": False,
            "payment_required": True,
            "price_usd": 9.99,
            "error": "Template t1 is priced; purchase it before installing.",
            "template_id": "t1",
        }
    )
    app.state.marketplace_v2 = svc
    resp = TestClient(app).post(route, json={"params": {}}, headers={"X-API-Key": "k"})
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["error"] == "PAYMENT_REQUIRED"


# ── real Postgres, NOBYPASSRLS app role ──────────────────────────────────────


@pytest_asyncio.fixture
async def dbs(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    from tests._auth_pg import app_role_url, session_factory

    owner_engine, owner = session_factory(pg_url)
    app_engine, app_factory = session_factory(await app_role_url(pg_url))
    yield {"owner": owner, "app": app_factory}
    await app_engine.dispose()
    await owner_engine.dispose()


async def _seed_tenant(owner: Any, tid: str) -> None:
    async with owner() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tid, "e": f"{tid}@example.test"},
        )
        await s.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_priced_template_needs_completed_purchase(dbs: dict[str, Any]) -> None:
    owner, app_factory = dbs["owner"], dbs["app"]
    author, buyer = f"author-{secrets.token_hex(4)}", f"buyer-{secrets.token_hex(4)}"
    await _seed_tenant(owner, author)
    await _seed_tenant(owner, buyer)

    svc = MarketplaceV2(db_factory=app_factory)
    record = await svc.publish_template(
        data={
            "name": "Paid Triage",
            "slug": f"paid-{secrets.token_hex(4)}",
            "description": "triage",
            "visibility": "public",
            "template_config": {"goal_template": "triage"},
        },
        tenant_ctx=_ctx(author),
        run_security_review=False,
    )
    template_id = str(record["id"])
    async with owner() as s:
        await s.execute(
            text(
                "UPDATE marketplace_templates SET price_usd = 9.99, "
                "visibility = 'public', review_status = 'approved' WHERE id = :id"
            ),
            {"id": template_id},
        )
        await s.commit()

    denied = await svc.install(template_id=template_id, params={}, tenant_ctx=_ctx(buyer))
    assert denied["success"] is False
    assert denied["payment_required"] is True
    async with owner() as s:
        n_agents = (
            await s.execute(text("SELECT count(*) FROM agents WHERE tenant_id = :t"), {"t": buyer})
        ).scalar_one()
    assert n_agents == 0

    # A pending (unpaid) purchase is not enough; another tenant's completed one neither.
    async with owner() as s:
        for status, who in (("pending", buyer), ("completed", author)):
            await s.execute(
                text(
                    "INSERT INTO marketplace_purchases "
                    "(id, template_id, buyer_tenant_id, amount_usd, status) "
                    "VALUES (:id, :tpl, :b, 9.99, :st)"
                ),
                {"id": uuid.uuid4().hex, "tpl": template_id, "b": who, "st": status},
            )
        await s.commit()
    still = await svc.install(template_id=template_id, params={}, tenant_ctx=_ctx(buyer))
    assert still.get("payment_required") is True

    async with owner() as s:
        await s.execute(
            text(
                "INSERT INTO marketplace_purchases "
                "(id, template_id, buyer_tenant_id, amount_usd, status) "
                "VALUES (:id, :tpl, :b, 9.99, 'completed')"
            ),
            {"id": uuid.uuid4().hex, "tpl": template_id, "b": buyer},
        )
        await s.commit()
    ok = await svc.install(template_id=template_id, params={}, tenant_ctx=_ctx(buyer))
    assert ok["success"] is True, ok

    # The author installing their own priced template does not need to buy it.
    own = await svc.install(template_id=template_id, params={}, tenant_ctx=_ctx(author))
    assert own["success"] is True, own
