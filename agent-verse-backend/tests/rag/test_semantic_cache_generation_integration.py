"""KB-51 on real pgvector under a NOBYPASSRLS role: durable writes survive a
knowledge-generation bump and real 36-char tenant ids.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/rag/test_semantic_cache_generation_integration.py -m integration
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.semantic_cache import SemanticCache
from app.rag.vector_cache_backend import PgVectorCacheBackend
from app.tenancy.context import PlanTier, TenantContext
from tests.memory._pg import app_role_engine

pytestmark = pytest.mark.integration

_EMB = [0.3, 0.4, 0.5, 0.6]


async def _count(admin_url: str, tid: str) -> list[tuple[int, int]]:
    engine = create_async_engine(admin_url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT generation, count(*) FROM semantic_cache_entries "
                    "WHERE tenant_id = :t GROUP BY generation ORDER BY generation"
                ),
                {"t": tid},
            )
            return [(int(r[0]), int(r[1])) for r in rows]
    finally:
        await engine.dispose()


async def test_generation_bump_keeps_durable_writes_and_isolates_generations(
    pg_url: str,
) -> None:
    engine = await app_role_engine(pg_url, ["semantic_cache_entries"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tid, other = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        backend = PgVectorCacheBackend(factory, ttl_seconds=3600)
        writer = SemanticCache(backend=backend)
        await writer.store_async(_EMB, "q", "old answer", tid)
        await writer.invalidate_tenant(tid)  # knowledge changed: generation 1
        await writer.store_async(_EMB, "q", "new answer", tid)

        assert await _count(pg_url, tid) == [(0, 1), (1, 1)]

        reader = SemanticCache(backend=backend)  # another replica, empty L1
        reader._generation[tid] = 1
        hit = await reader.get_similar(_EMB, tid)
        assert hit is not None and hit.response == "new answer"
        assert await backend.get_similar(_EMB, tid, 0.5, generation=0) is not None
        assert await backend.get_similar(_EMB, tid, 0.5, generation=2) is None
        # RLS: another tenant never sees these rows.
        assert await backend.get_similar(_EMB, other, 0.5, generation=1) is None

        # Tenant erase removes every generation.
        await writer.clear_async(tenant_ctx=TenantContext(tid, PlanTier.FREE, "k"))
        assert await _count(pg_url, tid) == []
    finally:
        await engine.dispose()


async def test_purge_drops_superseded_generations(pg_url: str) -> None:
    engine = await app_role_engine(pg_url, ["semantic_cache_entries"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tid = str(uuid.uuid4())
    try:
        backend = PgVectorCacheBackend(factory, ttl_seconds=3600)
        backend._PURGE_EVERY = 2
        await backend.store("q", _EMB, "g0", tid, generation=0)
        await backend.store("q", _EMB, "g3", tid, generation=3)  # 2nd write purges
        assert await _count(pg_url, tid) == [(3, 1)]
    finally:
        await engine.dispose()
