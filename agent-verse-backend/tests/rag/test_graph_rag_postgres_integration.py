"""Integration tests: Graph RAG evidence queries against a REAL Postgres.

``tests/rag/test_graph_rag_pattern.py`` is the only other coverage for this
module and it drives a *fake* SQLAlchemy session that pattern-matches on SQL
comments. That cannot see any of the failure modes this codebase has repeatedly
shipped, because they are all invisible under mocks:

  * row-level security silently matching zero rows under a real least-privilege
    (non-BYPASSRLS) role -- ``knowledge_nodes`` / ``knowledge_edges`` carry an
    RLS policy from migration 0094;
  * SQL that does not actually compile/execute against the real schema (column
    names, ``JSON`` vs ``JSONB`` casts, ``ANY(:array)`` binding, the recursive
    CTE);
  * the depth bound and tenant predicates inside the recursive traversal, where
    a missing ``tenant_id`` join condition would walk into another tenant's
    subgraph.

These tests run the real ``query_graph_evidence`` against a migrated database,
as a role that is NOT the table owner and has NOBYPASSRLS, which is the
configuration production actually provisions.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/rag/test_graph_rag_postgres_integration.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.rag.agentic.patterns.graph import GraphEvidenceQuery, query_graph_evidence

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ROLE_PREFIX = "test_app_graphrag"
KG_TABLES = ("knowledge_nodes", "knowledge_edges")

TENANT_A = "tenant-graph-a"
TENANT_B = "tenant-graph-b"


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple]:
    """(owner_factory, app_factory).

    ``app_factory``'s role is NOSUPERUSER/NOBYPASSRLS and is *not* the owner of
    the knowledge-graph tables, so migration 0094's RLS policy genuinely applies
    to it -- the configuration under which an unscoped query returns nothing.
    """
    password = secrets.token_urlsafe(24)
    # The Postgres container is module-scoped while this fixture is per-test, so
    # the role name must be unique or the second test hits DuplicateObject.
    app_role = f"{_ROLE_PREFIX}_{secrets.token_hex(4)}"
    owner_engine = create_async_engine(postgres_url)
    async with owner_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {app_role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {app_role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {app_role}"))
        for tbl in KG_TABLES:
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {app_role}")
            )

    app_url = (
        make_url(postgres_url)
        .set(username=app_role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0, echo=False)
    yield (
        async_sessionmaker(owner_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await owner_engine.dispose()


async def _node(s, tenant, nid, ntype, label, content, source_id, conf, meta="{}") -> None:
    await s.execute(
        text(
            "INSERT INTO knowledge_nodes "
            "(id, tenant_id, node_type, label, content, source_id, confidence, "
            " extra_metadata, created_at, updated_at) "
            "VALUES (:id, :t, :nt, :l, :c, :sid, :conf, CAST(:m AS json), NOW(), NOW())"
        ),
        {"id": nid, "t": tenant, "nt": ntype, "l": label, "c": content,
         "sid": source_id, "conf": conf, "m": meta},
    )


async def _edge(s, tenant, eid, src, tgt, etype, evidence, conf, meta="{}") -> None:
    await s.execute(
        text(
            "INSERT INTO knowledge_edges "
            "(id, tenant_id, source_node_id, target_node_id, edge_type, evidence, "
            " provenance, confidence, extra_metadata, created_at) "
            "VALUES (:id, :t, :s, :g, :et, :ev, :prov, :conf, CAST(:m AS json), NOW())"
        ),
        {"id": eid, "t": tenant, "s": src, "g": tgt, "et": etype, "ev": evidence,
         "prov": "runbook", "conf": conf, "m": meta},
    )


@pytest_asyncio.fixture(scope="function")
async def seeded(factories: tuple) -> AsyncIterator[tuple]:
    """A small incident-response graph for tenant A, plus a decoy for tenant B.

    Tenant A chain:  redis --impacts--> checkout --mitigated_by--> eviction
                     --related--> postgres   (postgres is 3 hops from redis)
    Tenant B has a node with the SAME label and the SAME source_id, so a missing
    tenant predicate anywhere would surface it.
    """
    owner_factory, app_factory = factories
    async with owner_factory() as s:
        for tbl in ("knowledge_edges", "knowledge_nodes"):
            await s.execute(
                text(f"DELETE FROM {tbl} WHERE tenant_id = ANY(:t)"),
                {"t": [TENANT_A, TENANT_B]},
            )
        await _node(s, TENANT_A, "a-redis", "entity", "Redis",
                    "Redis cache cluster for checkout", "chunk-1", 0.90,
                    '{"tier": "gold"}')
        await _node(s, TENANT_A, "a-checkout", "entity", "Checkout Service",
                    "Handles payment checkout", "chunk-2", 0.80, '{"tier": "gold"}')
        await _node(s, TENANT_A, "a-eviction", "concept", "Cache Eviction",
                    "Eviction storm causes checkout latency", "chunk-3", 0.70,
                    '{"tier": "silver"}')
        await _node(s, TENANT_A, "a-postgres", "entity", "Postgres",
                    "Primary database", "chunk-4", 0.60, '{"tier": "silver"}')
        await _edge(s, TENANT_A, "a-e1", "a-redis", "a-checkout", "impacts",
                    "Redis outage impacts checkout", 0.90, '{"tier": "gold"}')
        await _edge(s, TENANT_A, "a-e2", "a-checkout", "a-eviction", "mitigated_by",
                    "Checkout latency mitigated by eviction tuning", 0.80,
                    '{"tier": "gold"}')
        await _edge(s, TENANT_A, "a-e3", "a-eviction", "a-postgres", "related",
                    "Eviction pressure correlates with database load", 0.50,
                    '{"tier": "silver"}')

        # Decoy: same label, same seed chunk id, different tenant.
        await _node(s, TENANT_B, "b-redis", "entity", "Redis",
                    "Tenant B private Redis notes", "chunk-1", 0.99)
        await _node(s, TENANT_B, "b-secret", "entity", "Tenant B Secret",
                    "should never appear for tenant A", "chunk-1", 0.99)
        await _edge(s, TENANT_B, "b-e1", "b-redis", "b-secret", "impacts",
                    "Tenant B private edge", 0.99)
        await s.commit()
    yield factories


def _q(tenant: str, **kw) -> GraphEvidenceQuery:
    return GraphEvidenceQuery(
        tenant_id=tenant,
        query=kw.pop("query", "Redis"),
        seed_chunk_ids=kw.pop("seed_chunk_ids", ("chunk-1",)),
        **kw,
    )


@pytest.mark.asyncio
async def test_returns_entity_path_and_community_evidence(seeded: tuple) -> None:
    """The real SQL compiles and returns all three evidence legs."""
    _owner, app_factory = seeded
    async with app_factory() as s, sqlalchemy_rls_context(s, TENANT_A):
        evidence = await query_graph_evidence(s, _q(TENANT_A))

    by_type: dict[str, list] = {}
    for item in evidence:
        by_type.setdefault(item.evidence_type, []).append(item)

    assert by_type.get("entity"), f"no entity evidence: {evidence}"
    assert by_type.get("path"), f"no path evidence: {evidence}"
    assert by_type.get("community"), f"no community evidence: {evidence}"

    assert "a-redis" in {e.evidence_id for e in by_type["entity"]}
    assert "a-e1" in {e.evidence_id for e in by_type["path"]}

    redis = next(e for e in by_type["entity"] if e.evidence_id == "a-redis")
    assert redis.provenance["label"] == "Redis"
    assert redis.provenance["source_id"] == "chunk-1"
    assert redis.provenance["metadata"] == {"tier": "gold"}


@pytest.mark.asyncio
async def test_tenant_isolation_under_nobypassrls_role(seeded: tuple) -> None:
    """Tenant A must never see tenant B's node sharing its label and seed chunk."""
    _owner, app_factory = seeded
    async with app_factory() as s, sqlalchemy_rls_context(s, TENANT_A):
        evidence = await query_graph_evidence(s, _q(TENANT_A))

    blob = repr(evidence)
    assert "b-redis" not in blob, blob
    assert "b-secret" not in blob, blob
    assert "Tenant B" not in blob, blob
    assert all(not e.evidence_id.startswith("b-") for e in evidence)


@pytest.mark.asyncio
async def test_tenant_b_sees_only_its_own_subgraph(seeded: tuple) -> None:
    _owner, app_factory = seeded
    async with app_factory() as s, sqlalchemy_rls_context(s, TENANT_B):
        evidence = await query_graph_evidence(s, _q(TENANT_B))

    ids = {e.evidence_id for e in evidence}
    assert ids, "tenant B got no evidence at all"
    assert all(i.startswith("b-") for i in ids), ids


@pytest.mark.asyncio
async def test_recursive_traversal_stops_at_depth_bound(seeded: tuple) -> None:
    """The community CTE bounds traversal at depth < 2.

    From the seed `a-redis`: checkout is 1 hop, eviction 2 hops, postgres 3 hops.
    `a-postgres` must therefore NOT be a community member.
    """
    _owner, app_factory = seeded
    async with app_factory() as s, sqlalchemy_rls_context(s, TENANT_A):
        evidence = await query_graph_evidence(s, _q(TENANT_A))

    communities = [e for e in evidence if e.evidence_type == "community"]
    assert communities
    rooted = next(
        (c for c in communities if c.provenance["community_id"] == "a-redis"), None
    )
    assert rooted is not None, [c.provenance for c in communities]
    members = set(rooted.provenance["member_node_ids"])
    assert {"a-redis", "a-checkout", "a-eviction"} <= members, members
    assert "a-postgres" not in members, members


@pytest.mark.asyncio
async def test_metadata_filter_excludes_non_matching_nodes(seeded: tuple) -> None:
    """The jsonb containment filter really applies against the JSON column."""
    _owner, app_factory = seeded
    async with app_factory() as s, sqlalchemy_rls_context(s, TENANT_A):
        evidence = await query_graph_evidence(
            s, _q(TENANT_A, query="Redis Postgres Cache", filters={"tier": "gold"})
        )

    entity_ids = {e.evidence_id for e in evidence if e.evidence_type == "entity"}
    assert "a-redis" in entity_ids, entity_ids
    # silver-tier nodes must be filtered out
    assert "a-eviction" not in entity_ids, entity_ids
    assert "a-postgres" not in entity_ids, entity_ids


@pytest.mark.asyncio
async def test_unscoped_session_returns_nothing_under_rls(seeded: tuple) -> None:
    """Without the app.tenant_id GUC the policy must match zero rows.

    This is the regression guard for the failure mode that has recurred across
    this codebase: a code path that forgets `sqlalchemy_rls_context` still
    "works" on a superuser dev connection and silently returns nothing in
    production.
    """
    _owner, app_factory = seeded
    async with app_factory() as s:
        evidence = await query_graph_evidence(s, _q(TENANT_A))
    assert evidence == [], evidence


# ── RLS must be FORCEd, not merely ENABLEd ───────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def owning_role_factory(seeded: tuple) -> AsyncIterator[async_sessionmaker]:
    """A session as a NON-SUPERUSER role that OWNS the knowledge-graph tables.

    Postgres applies an ENABLE-only RLS policy to every role *except the table
    owner*. An application that owns its own schema — the common case when the
    app runs its own migrations — therefore has RLS silently inert. Only
    ``FORCE ROW LEVEL SECURITY`` constrains the owner too, which is why every
    other sensitive table in this codebase (audit_events, policy_evaluations,
    compliance_requests, marketplace_installs, ...) sets both.
    """
    owner_factory, _app_factory = seeded
    password = secrets.token_urlsafe(24)
    role = f"kg_owner_{secrets.token_hex(4)}"

    async with owner_factory() as s:
        quoted = (
            await s.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await s.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await s.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await s.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        for tbl in KG_TABLES:
            await s.execute(text(f"ALTER TABLE {tbl} OWNER TO {role}"))
        await s.commit()

    url = (
        make_url(str(owner_factory.kw["bind"].url))
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_rls_is_forced_so_the_table_owner_cannot_bypass_tenant_isolation(
    owning_role_factory: async_sessionmaker,
) -> None:
    """Migration 0094 ENABLEs RLS on the knowledge graph but never FORCEs it.

    `query_graph_evidence` carries its own `WHERE tenant_id = :tenant_id`
    predicate, so this is not a live leak through that function — but it means
    the knowledge graph has no RLS backstop at all for the owning role. Any
    other query against these tables that forgets the predicate (or a future
    one) leaks across tenants with nothing to stop it, unlike every comparable
    table here, which sets ENABLE *and* FORCE.
    """
    async with owning_role_factory() as s, sqlalchemy_rls_context(s, TENANT_A):
        rows = (await s.execute(text("SELECT id FROM knowledge_nodes"))).fetchall()

    ids = {r[0] for r in rows}
    assert ids, "owner saw no rows at all — fixture problem, not an RLS result"
    leaked = {i for i in ids if i.startswith("b-")}
    assert not leaked, (
        f"table owner read tenant B's nodes {leaked} while scoped to {TENANT_A}: "
        "RLS is ENABLEd but not FORCEd, so it does not apply to the owner"
    )


@pytest.mark.asyncio
async def test_knowledge_graph_tables_declare_force_row_level_security(
    factories: tuple,
) -> None:
    owner_factory, _app = factories
    async with owner_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:t)"
                ),
                {"t": list(KG_TABLES)},
            )
        ).fetchall()

    status = {r[0]: (r[1], r[2]) for r in rows}
    for table in KG_TABLES:
        enabled, forced = status[table]
        assert enabled, f"{table}: RLS not enabled"
        assert forced, f"{table}: RLS enabled but not FORCEd (owner bypasses it)"
