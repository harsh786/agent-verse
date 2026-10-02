"""RATE-01: the plan's knowledge-collection limit is enforced on create.

``check_knowledge_collection_limit`` existed but no create path called it, so
any plan could create unlimited collections. ``KnowledgeStore`` (the single
create path: POST /knowledge/collections and the gateway inbox auto-create)
now counts the tenant's collections and refuses the N+1th with
PlanLimitExceededError (429). On Postgres the count and the INSERT run in one
transaction under a per-tenant advisory lock, so concurrent creates on any
number of replicas cannot overshoot.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PLAN_LIMITS, PlanTier, TenantContext
from app.tenancy.limits import PlanLimitExceededError


def _ctx(tid: str, plan: PlanTier) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=plan, api_key_id="k")


async def test_in_memory_store_refuses_the_collection_over_the_plan_limit() -> None:
    store = KnowledgeStore()
    free = _ctx("t-free", PlanTier.FREE)
    limit = PLAN_LIMITS[PlanTier.FREE].max_knowledge_collections
    for i in range(limit):
        await store.create_collection_async(KnowledgeCollection(name=f"c{i}"), tenant_ctx=free)
    with pytest.raises(PlanLimitExceededError) as exc:
        await store.create_collection_async(KnowledgeCollection(name="over"), tenant_ctx=free)
    assert exc.value.http_status == 429
    # Another tenant's quota is independent.
    await store.create_collection_async(
        KnowledgeCollection(name="c0"), tenant_ctx=_ctx("t-other", PlanTier.FREE)
    )


def test_api_create_over_the_limit_is_429_not_503() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import knowledge as knowledge_api
    from app.core.errors import PlatformError

    app = FastAPI()
    app.include_router(knowledge_api.router)

    @app.exception_handler(PlatformError)
    async def _h(_: Any, exc: PlatformError) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.http_status, content={"code": exc.code})

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = _ctx("t-api", PlanTier.FREE)
        return await call_next(request)

    app.state.knowledge_store = KnowledgeStore()
    client = TestClient(app)
    prefix = knowledge_api.router.prefix
    limit = PLAN_LIMITS[PlanTier.FREE].max_knowledge_collections
    for i in range(limit):
        assert client.post(f"{prefix}/collections", json={"name": f"c{i}"}).status_code == 201
    over = client.post(f"{prefix}/collections", json={"name": "over"})
    assert over.status_code == 429
    assert over.json()["code"] == "PLAN_LIMIT_EXCEEDED"


@pytest.mark.integration
async def test_postgres_limit_holds_under_concurrent_creates(pg_url: str) -> None:
    engine = create_async_engine(pg_url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tid = uuid.uuid4().hex
    try:
        async with factory() as s, s.begin():
            await s.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
                {"id": tid, "e": f"{tid}@example.test"},
            )
        starter = _ctx(tid, PlanTier.STARTER)
        limit = PLAN_LIMITS[PlanTier.STARTER].max_knowledge_collections
        replicas = [KnowledgeStore(factory) for _ in range(4)]
        results = await asyncio.gather(
            *[
                replicas[i % 4].create_collection_async(
                    KnowledgeCollection(name=f"c{i}"), tenant_ctx=starter
                )
                for i in range(limit + 6)
            ],
            return_exceptions=True,
        )
        created = [r for r in results if isinstance(r, str)]
        refused = [r for r in results if isinstance(r, PlanLimitExceededError)]
        assert len(created) == limit, results
        assert len(refused) == 6
        async with factory() as s, s.begin():
            count = (
                await s.execute(
                    text("SELECT count(*) FROM knowledge_collections WHERE tenant_id = :t"),
                    {"t": tid},
                )
            ).scalar_one()
        assert count == limit
    finally:
        await engine.dispose()
