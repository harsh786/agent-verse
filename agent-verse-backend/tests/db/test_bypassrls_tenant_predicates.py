"""RLS-AUDIT: tenant-scoped stores isolate tenants without relying on RLS.

On a SUPERUSER / BYPASSRLS connection (the local compose stack used one) Postgres
RLS filters nothing, so a query whose only tenant scoping is the
``app.tenant_id`` GUC returns or changes every tenant's rows. These tests run
the stores that the RLS audit found relying solely on RLS against the
testcontainer SUPERUSER and assert tenant B can neither see nor change tenant
A's rows. (The list endpoints are swept end-to-end by
``tests/e2e_full/test_cross_tenant_list_sweep_e2e.py``.)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def superuser(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        bypass = (
            await s.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).scalar_one()
    assert bypass, "this test must run on a role that bypasses RLS"
    yield factory
    await engine.dispose()


async def _tenant(factory: Any) -> str:
    tid = uuid.uuid4().hex
    async with factory() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tid, "e": f"{tid}@example.test"},
        )
    return tid


async def test_policy_versions_never_cross_tenants(superuser: Any) -> None:
    from app.governance.policies import PolicyVersionManager

    mgr = PolicyVersionManager()
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    async with superuser() as db:
        created = await mgr.create_policy(
            db, tenant_id=a, name="pa", rules=[], description="", change_summary="v1"
        )
    pid = created["policy_id"] if isinstance(created, dict) else created.policy_id

    async with superuser() as db:
        assert await mgr.get_version_history(db, b, pid) == []
    async with superuser() as db:
        with pytest.raises(ValueError):
            await mgr.update_policy(db, b, pid, {"name": "hijack"}, change_summary="x")
    async with superuser() as db:
        with pytest.raises(ValueError):
            await mgr.rollback(db, b, pid, 1, reason="x")
    async with superuser() as db:
        history = await mgr.get_version_history(db, a, pid)
    assert [(v.version_number, v.name, v.is_active, v.tenant_id) for v in history] == [
        (1, "pa", True, a)
    ]


async def test_memory_feedback_and_lifecycle_never_cross_tenants(superuser: Any) -> None:
    from app.memory.contracts import MemoryFeedback, MemoryWriteRequest
    from app.memory.postgres_repository import PostgresMemoryRepository

    class _Cipher:
        def encrypt(self, plaintext: str) -> str:
            return plaintext.encode().hex()

        def decrypt(self, ciphertext: str) -> str:
            return bytes.fromhex(ciphertext).decode()

    a, b = await _tenant(superuser), await _tenant(superuser)
    repo = PostgresMemoryRepository(superuser, cipher=_Cipher())
    rec = await repo.write(
        MemoryWriteRequest(
            tenant_id=a,
            memory_kind="reflexion",
            content="retry the deploy slowly",
            source_goal_id=uuid.uuid4().hex,
            source_execution_id="exec",
            evidence_refs=("goal://evidence",),
            classification="internal",
            confidence=8000,
            idempotency_key=uuid.uuid4().hex,
            retention_policy_id="reflexion-standard",
        )
    )
    with pytest.raises(KeyError):
        await repo.feedback(
            MemoryFeedback(
                memory_id=rec.memory_id,
                tenant_id=b,
                execution_id="g",
                was_used=True,
                was_helpful=False,
                was_harmful=True,
                outcome_score=-5000,
                feedback_reason="hijack",
                recorded_at=datetime.now(UTC),
            )
        )
    with pytest.raises(KeyError):
        await repo.update_lifecycle(b, rec.memory_id, state="deleted", expected_version=rec.version)
    async with superuser() as s:
        row = (
            await s.execute(
                text(
                    "SELECT lifecycle_state, harmful_count, version FROM memory_records "
                    "WHERE id = :id"
                ),
                {"id": rec.memory_id},
            )
        ).one()
    assert row.lifecycle_state == rec.lifecycle_state
    assert row.harmful_count == 0 and row.version == rec.version


async def test_routing_decisions_never_cross_tenants(superuser: Any) -> None:
    from app.routing_runtime.contracts import (
        OptimizationOutcome,
        RoutingCandidate,
        RoutingDecision,
        RoutingSignalSet,
    )
    from app.routing_runtime.decision_store import PostgresDecisionStore

    a, b = await _tenant(superuser), await _tenant(superuser)
    cand = RoutingCandidate(
        candidate_id="m",
        candidate_version="v1",
        provider="p",
        capabilities=frozenset({"structured"}),
        readiness="ready",
        trust_score=9000,
        quality_score=9000,
        estimated_cost_usd=0.1,
        estimated_latency_ms=100,
        saturation=0,
        policy_allowed=True,
        rejection_reasons=(),
    )
    signals = RoutingSignalSet(
        task_shape="analysis",
        complexity="medium",
        risk="low",
        freshness="current",
        requires_code=False,
        requires_collaboration=False,
        required_capabilities=frozenset({"structured"}),
        data_classes=frozenset({"internal"}),
        deadline_ms=1000,
        max_cost_usd=1,
        max_tokens=1000,
        tenant_plan="professional",
        profile_version=1,
    )

    def _decision(tenant: str, decision_id: str) -> RoutingDecision:
        return RoutingDecision(
            decision_id=decision_id,
            tenant_id=tenant,
            goal_id="goal",
            execution_id="exec",
            category="model",
            signals=signals,
            candidates=(cand,),
            selected_candidate_id="m",
            fallback_chain=("m",),
            safe_rationale=f"rationale-{tenant}",
            policy_trace={},
            created_at=datetime.now(UTC),
        )

    store = PostgresDecisionStore(superuser)
    did = uuid.uuid4().hex
    await store.save_decision(_decision(a, did))
    assert await store.get_decision(b, did) is None
    # Re-saving A's decision id under B must not hand B tenant A's payload.
    with pytest.raises(ValueError):
        await store.save_decision(_decision(b, did))

    outcome = OptimizationOutcome(
        outcome_id=uuid.uuid4().hex,
        tenant_id=a,
        decision_id=did,
        attempt=1,
        evaluator_version="v1",
        success=True,
        quality_score=9000,
        actual_cost_usd=0.1,
        actual_latency_ms=10,
        prompt_tokens=1,
        completion_tokens=1,
        fallback_used=False,
        recorded_at=datetime.now(UTC),
    )
    await store.save_outcome(outcome)
    with pytest.raises(ValueError):
        await store.save_outcome(outcome.model_copy(update={"tenant_id": b}))
