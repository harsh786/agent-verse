"""Integration (QA-14): domain_context / domain_metadata round-trip through Postgres.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_agent_domain_context_integration.py -m integration
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_domain_fields_round_trip_post_get_put_in_postgres(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4().hex
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :e, 'professional', true)"
            ),
            {"id": tenant_id, "e": f"{secrets.token_hex(4)}@example.test"},
        )
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    def _app() -> FastAPI:
        app = FastAPI()

        @app.middleware("http")
        async def _tenant(request: Any, call_next: Any) -> Any:
            request.state.tenant = ctx
            return await call_next(request)

        app.include_router(agents_router)
        # A fresh store per app: GET must come from Postgres, not a warm cache.
        app.state.agent_store = AgentStore(db_session_factory=factory)
        return app

    legal = {"bar_number": "CA12345", "jurisdiction": "CA"}
    try:
        async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
            resp = await c.post(
                "/agents",
                json={"name": "lawyer", "domain_context": "legal", "domain_metadata": legal},
            )
            assert resp.status_code == 201, resp.text
            agent_id = resp.json()["agent_id"]

        async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
            got = (await c.get(f"/agents/{agent_id}")).json()
            assert got["domain_context"] == "legal"
            assert got["domain_metadata"] == legal
            listed = {a["agent_id"]: a for a in (await c.get("/agents")).json()}
            assert listed[agent_id]["domain_context"] == "legal"

            upd = await c.put(
                f"/agents/{agent_id}",
                json={"domain_context": "healthcare", "domain_metadata": {"npi": "1234567890"}},
            )
            assert upd.status_code == 200, upd.text

        async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
            got = (await c.get(f"/agents/{agent_id}")).json()
            assert got["domain_context"] == "healthcare"
            assert got["domain_metadata"] == {"npi": "1234567890"}

        # The column the agent identity service reads (app/auth/agent_identity.py).
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT domain_context, domain_metadata FROM agents "
                        "WHERE id = :id AND tenant_id = :t"
                    ),
                    {"id": agent_id, "t": tenant_id},
                )
            ).one()
        assert row[0] == "healthcare"
        assert row[1] == {"npi": "1234567890"}
    finally:
        await engine.dispose()
