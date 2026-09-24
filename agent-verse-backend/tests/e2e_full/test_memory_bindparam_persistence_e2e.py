"""e2e_full: episodic_memories / long_term_memory writes against real Postgres.

Regression coverage for two classes of bug found by reading the live
``e2e_full`` run's captured logs:

1. ``:param::jsonb`` — a bind parameter immediately followed by Postgres's
   ``::`` cast operator — is not valid inside a SQLAlchemy ``text()`` query:
   its bind-parameter regex refuses to treat ``:name`` as a parameter when
   immediately followed by another ``:``, so the literal text
   ``:embedding::jsonb`` / ``:tools_used::jsonb`` was left unsubstituted and
   asyncpg received invalid SQL (``PostgresSyntaxError: syntax error at or
   near ":"``). Fixed in ``app/memory/episodic.py`` (and the same pattern in
   ``app/memory/procedural.py`` and several other files — see the session's
   codebase-wide grep) by switching to ``CAST(:param AS jsonb)``. Both writes
   swallow their own exceptions (log-and-continue), so the only way to prove
   the fix is to check the row actually landed in Postgres.

2. ``long_term_memory.embedding`` is a FIXED-width ``vector(2048)`` column
   (migration 0122 — not per-row/per-collection like RAG's
   ``knowledge_chunks_*`` tables). ``recall_async``'s ANN query must cast to
   the same width the column actually has (``_LTM_EMBEDDING_DIM`` in
   ``app/memory/long_term.py``) or every recall fails with a pgvector
   ``DataError`` ("expected 2048 dimensions, not N"). Proven here with a real
   embed → store → recall round trip against the actual migrated column.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.agent.state import AgentState, GoalStatus, StepResult
from app.memory.long_term import _LTM_EMBEDDING_DIM, LongTermMemory
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

# `_reset_signup_rate_limit` is a fixture from tests/e2e_full/conftest.py — pytest
# auto-discovers it for every test module under tests/e2e_full/, no import needed.

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def _seeded_tenant_ctx(client: Any, _reset_signup_rate_limit: None) -> Any:
    """A TenantContext for a real signed-up tenant (FK target for both tables).

    Goes through the real ``/tenants/signup`` endpoint rather than hand-rolling
    an INSERT into ``tenants`` — that table has columns/constraints owned by
    the tenancy service, not this test.
    """
    email = f"bindparam-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "BindParam", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    body = resp.json()
    return TenantContext(
        tenant_id=body["tenant_id"],
        plan=PlanTier.FREE,
        api_key_id=body["api_key_id"],
    )


async def test_episodic_memory_record_persists_to_real_postgres(
    app: Any, _seeded_tenant_ctx: Any
) -> None:
    """EpisodicMemoryStore.record() actually inserts a row (Bug 2 target).

    Before the fix, ``:embedding::jsonb``/``:tools_used::jsonb`` made every
    INSERT raise ``PostgresSyntaxError``, caught and logged as
    ``episodic_memory_persist_failed`` — the goal kept running, but
    ``episodic_memories`` silently never gained a row. Assert the row exists,
    not just that ``record()`` didn't raise (it never raises either way).
    """
    tenant_ctx = _seeded_tenant_ctx
    db_factory = app.state.db_session_factory

    episodic = app.state.episodic_memory
    assert episodic is not None, "app.state.episodic_memory is not wired"

    goal_id = uuid.uuid4().hex
    state = AgentState(
        goal="Audit the Zephyrine ledger for quarterly compliance",
        tenant_ctx=tenant_ctx,
        goal_id=goal_id,
        status=GoalStatus.COMPLETE,
        steps=[
            StepResult(
                description="Consult the compliance ledger",
                tool_calls=[{"tool_name": "search_knowledge"}],
            )
        ],
        verification_feedback="Answered correctly from the ledger",
    )

    await episodic.record(state=state, tenant_ctx=tenant_ctx, quality_score=0.9)

    async with db_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT goal_id, outcome, tools_used, quality_score "
                    "FROM episodic_memories WHERE tenant_id = :tid AND goal_id = :gid"
                ),
                {"tid": tenant_ctx.tenant_id, "gid": goal_id},
            )
        ).fetchone()

    assert row is not None, (
        "no row in episodic_memories after record() — the INSERT is still "
        "silently failing (check for a resurrected :param::jsonb bug)"
    )
    assert row[0] == goal_id
    assert row[1] == "success"
    assert row[3] == pytest.approx(0.9)


async def test_long_term_memory_recall_finds_stored_memory_via_pgvector(
    app: Any, _seeded_tenant_ctx: Any
) -> None:
    """A real embed -> store_async -> recall_async round trip (Bug 1 target).

    Before the fix, ``recall_async``'s hardcoded ``halfvec(2048)`` cast (or,
    with a mismatched fixture, the embedder's actual output dimension) didn't
    match ``long_term_memory.embedding``'s real column width, and every
    recall failed with ``pgvector_recall_failed`` (asyncpg DataError) and fell
    back to keyword search — never actually exercising pgvector. Use an
    embedder sized to the column's real width and assert the stored memory
    comes back through the ANN query itself (not the keyword fallback), by
    using content that shares no keywords with the query.
    """
    tenant_ctx = _seeded_tenant_ctx
    db_factory = app.state.db_session_factory

    long_term = app.state.long_term_memory
    assert long_term is not None, "app.state.long_term_memory is not wired"

    # Sized to the column's actual fixed width (see app/memory/long_term.py),
    # not an arbitrary literal — see that module's _LTM_EMBEDDING_DIM docstring
    # for why this must NOT be settings.embedding_dim.
    embedder = FakeProvider(embed_dim=_LTM_EMBEDDING_DIM)

    memory = LongTermMemory(
        content="The quarterly compliance audit requires zephyrine axolotl records.",
        source_goal_id=uuid.uuid4().hex,
        memory_type="domain_fact",
    )
    await long_term.store_async(
        memory=memory, tenant_ctx=tenant_ctx, db=db_factory, embedder=embedder
    )

    async with db_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT embedding IS NOT NULL FROM long_term_memory "
                    "WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": memory.memory_id, "tid": tenant_ctx.tenant_id},
            )
        ).fetchone()
    assert row is not None and row[0] is True, (
        "long_term_memory row missing its embedding after store_async() — the "
        "INSERT with CAST(:emb AS vector) is failing (check for a dimension "
        "mismatch against the real vector(2048) column)"
    )

    # recall_async silently falls back to in-memory keyword scoring on ANY
    # pgvector error (including a dimension mismatch) and would still return
    # this single memory even via that fallback, masking the bug. Capture
    # structlog output around the call so the test fails loudly if the ANN
    # query didn't actually succeed, instead of passing for the wrong reason.
    import structlog.testing

    with structlog.testing.capture_logs() as logs:
        recalled = await long_term.recall_async(
            "unrelated wombat inquiry",
            tenant_ctx,
            top_k=5,
            db=db_factory,
            embedder=embedder,
        )

    failures = [entry for entry in logs if entry.get("event") == "pgvector_recall_failed"]
    assert not failures, f"recall_async fell back after a pgvector error: {failures!r}"
    assert any(m.memory_id == memory.memory_id for m in recalled), (
        "stored memory was not recalled via pgvector — recall_async's "
        f"halfvec cast likely doesn't match the real column width: {recalled!r}"
    )
