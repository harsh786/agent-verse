"""ORG-38 on real Postgres + Redis: the beat entrypoint expires a crashed target's
handoff and resumes the parent; a resume that failed after the committed transition
is re-run; a session paused again after the handoff ended is left alone.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/coordination/test_handoff_sweeper_integration.py -m integration --no-cov
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.coordination.contracts import AuthorizationContext
from app.coordination.handoffs.models import HandoffRecord, HandoffState
from app.coordination.handoffs.repository import PostgresHandoffRepository
from app.coordination.handoffs.resumption import HandoffParentResumer
from app.coordination.handoffs.service import HandoffService
from app.coordination.outbox_tasks import sweep_handoffs_once
from app.coordination.service import CoordinationService, SessionAdmission
from app.coordination.store import CoordinationStore
from app.coordination.transcript.repository import PostgresTranscriptRepository
from app.coordination.transcript.service import TranscriptService
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration]


class _Members:
    async def active_member(self, *_a: str) -> bool:
        return True

    async def connector_allowlist(self, *_a: str) -> frozenset[str]:
        return frozenset()


@pytest_asyncio.fixture
async def env(test_backends: tuple[str, str]) -> AsyncIterator[dict[str, Any]]:
    pg_url, _redis = test_backends
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"hs{uuid.uuid4().hex[:12]}"
    async with factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, 'Sweep', :email, 'free', true)"
            ),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )
    coordination = CoordinationService(CoordinationStore(factory))
    transcript = TranscriptService(PostgresTranscriptRepository(factory))
    resumer = HandoffParentResumer(
        coordination_service=lambda: coordination,
        transcript_service=lambda: transcript,
        live_bus=lambda: None,
    )
    repo = PostgresHandoffRepository(factory, system_session_factory=factory)
    service = HandoffService(
        repo,
        membership=_Members(),
        emit_event=resumer.emit_event,
        pause_parent=resumer.pause_parent,
        resume_parent=resumer.resume_parent,
    )
    yield {
        "tenant": TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k"),
        "coordination": coordination,
        "transcript": transcript,
        "repo": repo,
        "service": service,
    }
    await engine.dispose()


async def _paused_handoff(env: dict[str, Any], *, deadline: datetime) -> tuple[str, str]:
    tenant: TenantContext = env["tenant"]
    coordination: CoordinationService = env["coordination"]
    created = await coordination.create_session(
        tenant,
        SessionAdmission(
            civilization_id="civ",
            goal_id="goal",
            policy_snapshot={},
            budget_snapshot={},
            authorization=AuthorizationContext(
                actor_id="k", permissions=frozenset({"coordination:create"})
            ),
        ),
    )
    await coordination.start_session(
        tenant, created.session_id, expected_version=1, idempotency_key="start"
    )
    now = datetime.now(UTC)
    record = HandoffRecord(
        handoff_id=uuid.uuid4().hex,
        tenant_id=tenant.tenant_id,
        session_id=created.session_id,
        civilization_id="civ",
        source_agent_id="source",
        target_agent_id="target",
        task_summary="Draft the report",
        remaining_budget_usd=1.0,
        deadline=deadline,
        acceptance_token_digest=hashlib.sha256(b"token").hexdigest(),
        idempotency_key="request",
        created_at=min(now, deadline - timedelta(hours=1)),
        updated_at=now,
    )
    await env["repo"].create(record)
    await env["service"].transition(
        tenant.tenant_id, record.handoff_id, target=HandoffState.ACCEPTED,
        expected_version=1, idempotency_key="accept",
    )
    state = (await coordination.get_session(tenant, created.session_id)).state
    assert state == "paused"
    return created.session_id, record.handoff_id


async def test_beat_sweep_expires_a_crashed_targets_handoff_and_resumes_parent(
    env: dict[str, Any],
) -> None:
    tenant: TenantContext = env["tenant"]
    session_id, handoff_id = await _paused_handoff(
        env, deadline=datetime.now(UTC) + timedelta(seconds=1)
    )
    # Not overdue yet: untouched.
    assert (await sweep_handoffs_once())["expired"] == 0

    past_deadline = datetime.now(UTC) - timedelta(minutes=5)
    async with env["repo"]._sessions() as s, s.begin():
        await s.execute(
            text("UPDATE handoffs SET deadline = :d WHERE id = :i"),
            {"d": past_deadline, "i": handoff_id},
        )

    stats = await sweep_handoffs_once()

    assert stats["expired"] >= 1
    record = await env["repo"].get(tenant.tenant_id, handoff_id)
    assert record.state == HandoffState.EXPIRED
    assert (await env["coordination"].get_session(tenant, session_id)).state == "active"
    messages = await env["transcript"].page(tenant.tenant_id, session_id)
    assert [m.message_type for m in messages].count("decision") == 1

    # Idempotent: the next tick does nothing more.
    await sweep_handoffs_once()
    messages = await env["transcript"].page(tenant.tenant_id, session_id)
    assert [m.message_type for m in messages].count("decision") == 1


async def test_failed_resume_is_rerun_but_a_repaused_session_is_left_alone(
    env: dict[str, Any],
) -> None:
    tenant: TenantContext = env["tenant"]
    future = datetime.now(UTC) + timedelta(hours=1)
    stuck_session, stuck = await _paused_handoff(env, deadline=future)
    repaused_session, repaused = await _paused_handoff(env, deadline=future)

    # The handoffs end but their resumes never ran (crash after the commit).
    for hid in (stuck, repaused):
        await env["repo"].transition(
            tenant.tenant_id, hid, target=HandoffState.CANCELLED,
            expected_version=2, idempotency_key="cancel",
        )
    # An operator resumes and pauses the second session again afterwards.
    coordination: CoordinationService = env["coordination"]
    s = await coordination.get_session(tenant, repaused_session)
    s = await coordination.resume_session(
        tenant, repaused_session, expected_version=s.version, idempotency_key="op-resume"
    )
    await coordination.pause_session(
        tenant, repaused_session, expected_version=s.version, idempotency_key="op-pause"
    )

    stats = await sweep_handoffs_once()

    assert stats["resumed"] >= 1
    assert (await coordination.get_session(tenant, stuck_session)).state == "active"
    assert (await coordination.get_session(tenant, repaused_session)).state == "paused"
