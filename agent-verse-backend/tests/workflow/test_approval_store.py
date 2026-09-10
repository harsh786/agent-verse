"""Real-Postgres coverage for PostgresWorkflowApprovalStore (gap #2).

Proves the durable, RLS-scoped workflow-HITL approval store round-trips a
``WorkflowHITLRequest`` and enforces tenant isolation at the DB layer, exercised
through a non-superuser, non-owner role so RLS is genuinely enforced (mirrors
``tests/workflow/test_run_store.py``). This is the store that makes a pending
approval created by an out-of-process Celery worker visible to the API process.

Run with:
    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/workflow/test_approval_store.py -q
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.workflow.approval_store import PostgresWorkflowApprovalStore
from app.workflow.hitl_extension import WorkflowHITLRequest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "approval_app"


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


def _app_url(admin_url: str, password: str) -> str:
    return (
        make_url(admin_url)
        .set(username=APP_ROLE, password=password)
        .render_as_string(hide_password=False)
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def store(postgres_url: str) -> AsyncIterator[PostgresWorkflowApprovalStore]:
    """An RLS-enforcing (NOBYPASSRLS, non-owner) app-role-backed store."""
    password = secrets.token_urlsafe(24)
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_approvals TO {APP_ROLE}")
        )

    app_engine = create_async_engine(_app_url(postgres_url, password), pool_size=4, max_overflow=0)
    yield PostgresWorkflowApprovalStore(async_sessionmaker(app_engine, expire_on_commit=False))
    await app_engine.dispose()
    await admin_engine.dispose()


def _req(tenant_id: str, **overrides: object) -> WorkflowHITLRequest:
    base: dict[str, object] = {
        "tenant_id": tenant_id,
        "run_id": str(uuid.uuid4()),
        "workflow_id": str(uuid.uuid4()),
        "step_id": "gate",
        "step_name": "Approval gate",
        "assigned_to": "reviewer-1",
        "priority": "high",
    }
    base.update(overrides)
    return WorkflowHITLRequest(**base)  # type: ignore[arg-type]


async def test_save_get_round_trip(store: PostgresWorkflowApprovalStore) -> None:
    tenant = str(uuid.uuid4())
    req = _req(tenant, note="please review")
    await store.save(req)

    got = await store.get(req.request_id, tenant)
    assert got is not None
    assert got.request_id == req.request_id
    assert got.run_id == req.run_id
    assert got.step_id == "gate"
    assert got.tenant_id == tenant
    assert got.priority == "high"
    assert got.assigned_to == "reviewer-1"
    assert got.status == "pending"
    assert got.note == "please review"


async def test_get_without_tenant_returns_none(store: PostgresWorkflowApprovalStore) -> None:
    # Legacy signature (no tenant) cannot authorize an RLS read -> None, so the
    # gateway falls back to its in-memory mirror.
    req = _req(str(uuid.uuid4()))
    await store.save(req)
    assert await store.get(req.request_id) is None


async def test_rls_cross_tenant_isolation(store: PostgresWorkflowApprovalStore) -> None:
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    req = _req(tenant_a)
    await store.save(req)

    # Tenant B cannot read tenant A's approval by id, nor see it when listing.
    assert await store.get(req.request_id, tenant_b) is None
    items_b, total_b = await store.list_pending(tenant_b)
    assert all(r.tenant_id == tenant_b for r in items_b)
    assert req.request_id not in {r.request_id for r in items_b}
    _ = total_b


async def test_list_pending_filters(store: PostgresWorkflowApprovalStore) -> None:
    tenant = str(uuid.uuid4())
    mine = _req(tenant, assigned_to="alice", priority="critical")
    theirs = _req(tenant, assigned_to="bob", priority="low")
    await store.save(mine)
    await store.save(theirs)

    alice_items, _ = await store.list_pending(tenant, assigned_to="alice")
    assert {r.request_id for r in alice_items} == {mine.request_id}

    crit_items, _ = await store.list_pending(tenant, priority="critical")
    assert mine.request_id in {r.request_id for r in crit_items}
    assert theirs.request_id not in {r.request_id for r in crit_items}


async def test_decide_upsert_and_stats(store: PostgresWorkflowApprovalStore) -> None:
    tenant = str(uuid.uuid4())
    req = _req(tenant, assigned_to="carol")
    await store.save(req)

    pending_items, _ = await store.list_pending(tenant, assigned_to="carol")
    assert req.request_id in {r.request_id for r in pending_items}

    # Decide: same request_id upserts to a terminal status.
    from datetime import UTC, datetime

    req.status = "decided"
    req.action_taken = "approve"
    req.reviewed_by = "carol"
    req.reviewed_at = datetime.now(UTC).isoformat()
    await store.save(req)

    got = await store.get(req.request_id, tenant)
    assert got is not None and got.status == "decided" and got.action_taken == "approve"

    # No longer pending.
    still_pending, _ = await store.list_pending(tenant, assigned_to="carol")
    assert req.request_id not in {r.request_id for r in still_pending}

    stats = await store.get_stats(tenant)
    assert stats["total_requests"] >= 1
    assert stats["pending_count"] == 0


async def test_list_by_run(store: PostgresWorkflowApprovalStore) -> None:
    tenant = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    a = _req(tenant, run_id=run_id, step_id="gate1")
    b = _req(tenant, run_id=run_id, step_id="gate2")
    other = _req(tenant)
    for r in (a, b, other):
        await store.save(r)

    for_run = await store.list_by_run(tenant, run_id)
    assert {r.request_id for r in for_run} == {a.request_id, b.request_id}
