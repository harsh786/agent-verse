"""ORG-25 integration: pattern runs persist RLS-scoped, pattern-scoped read models.

Runs the migrations (including the ``strategy_checkpoints.pattern`` revision) in a
throwaway Postgres and drives real pattern runs through the Postgres repositories
as a NOBYPASSRLS role.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/coordination/test_pattern_runs_postgres.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.coordination.auction.repository import PostgresAuctionRepository, PostgresSealedBidInbox
from app.coordination.camel.repository import PostgresCamelRepository
from app.coordination.contracts import AuthorizationContext
from app.coordination.generative.repository import PostgresGenerativeRepository
from app.coordination.ledger.repository import PostgresProgressLedgerRepository
from app.coordination.live_bus import CoordinationLiveBus
from app.coordination.magentic.human_review import MagenticHumanReviewService
from app.coordination.magentic.repository import PostgresMagenticRunRepository
from app.coordination.moa.repository import PostgresMoARepository, PostgresMoARunRepository
from app.coordination.pattern_runs.service import PatternRunService
from app.coordination.service import CoordinationService, SessionAdmission
from app.coordination.store import CoordinationStore
from app.coordination.swarm.repository import PostgresSwarmRepository
from app.coordination.transcript.repository import PostgresTranscriptRepository
from app.coordination.transcript.service import TranscriptService
from app.tenancy.context import PlanTier, TenantContext
from tests.coordination.pattern_run_support import ScriptedProvider

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANTS = ("tenant-pattern-a", "tenant-pattern-b")


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


@pytest_asyncio.fixture
async def app_factory(postgres_url: str) -> AsyncIterator[Any]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_pattern_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        )
        for tenant in TENANTS:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant, "email": f"{tenant}@example.test"},
            )
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield async_sessionmaker(app_engine, expire_on_commit=False)
    await app_engine.dispose()
    await admin_engine.dispose()


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=("operator",)
    )


def _state(factory: Any) -> SimpleNamespace:
    review = MagenticHumanReviewService()
    review.set_db(factory)
    return SimpleNamespace(
        llm_provider=ScriptedProvider(),
        coordination_service=CoordinationService(CoordinationStore(factory)),
        transcript_service=TranscriptService(PostgresTranscriptRepository(factory)),
        coordination_live_bus=CoordinationLiveBus(),
        progress_ledger_repository=PostgresProgressLedgerRepository(factory),
        magentic_run_repository=PostgresMagenticRunRepository(factory),
        magentic_human_review=review,
        moa_repository=PostgresMoARepository(factory),
        moa_run_repository=PostgresMoARunRepository(factory),
        camel_repository=PostgresCamelRepository(factory),
        generative_repository=PostgresGenerativeRepository(factory),
        swarm_repository=PostgresSwarmRepository(factory),
        auction_repository=PostgresAuctionRepository(factory),
        auction_bid_inbox=PostgresSealedBidInbox(factory),
    )


async def _session(state: SimpleNamespace, tenant: str) -> str:
    ctx = _ctx(tenant)
    created = await state.coordination_service.create_session(
        ctx,
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
    await state.coordination_service.start_session(
        ctx, created.session_id, expected_version=1, idempotency_key="start"
    )
    return str(created.session_id)


async def _run(state: SimpleNamespace, tenant: str, session_id: str, pattern: str) -> Any:
    return await PatternRunService(state).run(
        _ctx(tenant),
        session_id,
        pattern,
        objective="Write a short market report",
        participants=(),
        max_rounds=6,
        options={},
        idempotency_key=f"{pattern}-key",
    )


@pytest.mark.asyncio
async def test_every_pattern_persists_its_read_model_under_rls(app_factory: Any) -> None:
    state = _state(app_factory)
    tenant_a, tenant_b = TENANTS
    session_id = await _session(state, tenant_a)
    for pattern in (
        "camel",
        "decentralized_swarm",
        "generative_agents",
        "market_auction",
        "mixture_of_agents",
        "magentic",
    ):
        result = await _run(state, tenant_a, session_id, pattern)
        assert result["phase"] == "completed", (pattern, result["terminal_reason"])

    camel = await state.camel_repository.list_session(tenant_a, session_id)
    swarm = await state.swarm_repository.list_session(tenant_a, session_id)
    # Pattern-scoped: each read model lists only its own pattern's run.
    assert len(camel) == 1 and camel[0].state["config"]["pattern"] == "camel"
    assert len(swarm) == 1 and swarm[0].state["view"]["edges"]
    assert await state.auction_bid_inbox.count(tenant_a, session_id) == 3
    assert len(await state.moa_repository.layers(tenant_a, session_id)) == 2
    ledger = await state.progress_ledger_repository.current(tenant_a, session_id)
    assert ledger is not None and set(ledger.completed_work) == {"research", "draft"}
    # Tenant-scoped under RLS: another tenant sees nothing for the same session id.
    assert await state.camel_repository.list_session(tenant_b, session_id) == ()
    assert await state.swarm_repository.list_session(tenant_b, session_id) == ()
    # A retried command on "another replica" replays the stored outcome.
    replay = await _run(_state(app_factory), tenant_a, session_id, "camel")
    assert replay["replayed"] is True and replay["phase"] == "completed"


@pytest.mark.asyncio
async def test_magentic_review_round_trip_through_postgres(app_factory: Any) -> None:
    state = _state(app_factory)
    state.llm_provider = ScriptedProvider(magentic_completes=False)
    tenant = TENANTS[0]
    session_id = await _session(state, tenant)
    waiting = await _run(state, tenant, session_id, "magentic")
    assert waiting["phase"] == "awaiting_human"
    await state.magentic_human_review.submit(
        tenant, session_id, token=waiting["human_review"]["token"], approved=True, safe_note=""
    )
    other_replica = _state(app_factory)
    other_replica.llm_provider = ScriptedProvider(magentic_completes=True)
    resumed = await PatternRunService(other_replica).apply_magentic_review(
        _ctx(tenant), session_id, approved=True
    )
    assert resumed is not None and resumed["phase"] == "completed"


@pytest.mark.asyncio
async def test_api_admits_and_a_worker_executes_the_run(app_factory: Any) -> None:
    """ORG-39: the API replica only admits (no LLM call); the worker task, with its
    own state over the same database, executes from the persisted document."""
    from app.coordination.pattern_runs.tasks import run_pattern_once, tenant_payload

    api = _state(app_factory)
    tenant = TENANTS[1]
    session_id = await _session(api, tenant)
    document, pending = await PatternRunService(api).admit(
        _ctx(tenant),
        session_id,
        "camel",
        objective="Write a short market report",
        participants=(),
        max_rounds=6,
        options={},
        idempotency_key="camel-async",
    )
    assert pending is True
    queued = await api.camel_repository.list_session(tenant, session_id)
    assert [r.execution_id for r in queued] == [document.execution_id]
    assert (queued[0].state.get("view") or {}).get("phase") != "completed"

    worker = _state(app_factory)
    done = await run_pattern_once(
        tenant_payload(_ctx(tenant)), session_id, "camel", document.execution_id, state=worker
    )
    assert done["phase"] == "completed"
    stored = await api.camel_repository.list_session(tenant, session_id)
    assert stored[0].state["view"]["phase"] == "completed"
    # A redelivered task returns the stored outcome; a re-admit needs no execution.
    again = await run_pattern_once(
        tenant_payload(_ctx(tenant)), session_id, "camel", document.execution_id, state=worker
    )
    assert again["replayed"] is True
    _doc, still_pending = await PatternRunService(api).admit(
        _ctx(tenant),
        session_id,
        "camel",
        objective="Write a short market report",
        participants=(),
        max_rounds=6,
        options={},
        idempotency_key="camel-async",
    )
    assert still_pending is False
