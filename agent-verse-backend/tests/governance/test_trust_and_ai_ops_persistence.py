"""Integration tests: trust-governance approvals and AI-Ops state are DURABLE.

Both `app/api/trust_governance.py` and `app/api/ai_ops.py` are mounted, live
routers that kept their entire state in module-level dicts. trust_governance
even documented itself as "In-memory for demo; production uses DB" while being
the production path. Consequences in a multi-replica deployment:

  * an approval granted on one pod did not exist for the pod serving the next
    request, and every pending approval was lost on redeploy;
  * `required_approvers` was enforced by an application-level scan of an
    in-process list, so two concurrent approvals by the SAME person landing on
    different replicas could both count — defeating separation of duties;
  * an AI-Ops baseline set on one pod meant drift was computed against "no
    baseline" everywhere else.

These tests exercise the real stores against a migrated Postgres as a
NOSUPERUSER/NOBYPASSRLS role, and assert durability by reading back through a
*second, independent* store instance that shares no in-process state with the
one that wrote (the stand-in for another replica / a restart).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/governance/test_trust_and_ai_ops_persistence.py -q -m integration
"""

from __future__ import annotations

import asyncio
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

from app.evals.ai_ops_store import AIOpsStore
from app.governance.trust_approval_store import (
    ApprovalNotFoundError,
    DuplicateApproverError,
    TrustApprovalStore,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    "trust_approval_requests",
    "trust_approval_votes",
    "ai_ops_datasets",
    "ai_ops_eval_results",
    "ai_ops_judges",
    "ai_ops_baselines",
    "ai_ops_drift_alerts",
)
TENANT_A = "tenant-trust-a"
TENANT_B = "tenant-trust-b"


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
async def app_factory(postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    """A NOSUPERUSER/NOBYPASSRLS role — RLS genuinely applies."""
    password = secrets.token_urlsafe(24)
    role = f"test_app_trust_{secrets.token_hex(4)}"
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
        for tbl in TABLES:
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {role}")
            )
        for tbl in TABLES:
            await conn.execute(text(f"DELETE FROM {tbl}"))
    await admin_engine.dispose()

    url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    engine = create_async_engine(url, pool_size=6, max_overflow=0)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ── trust governance ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_approval_survives_a_restart(app_factory: async_sessionmaker) -> None:
    """A second, independent store must see the approval — the replica/restart case."""
    writer = TrustApprovalStore(app_factory)
    approval_id = secrets.token_hex(8)
    await writer.create(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        goal_id="g1",
        step_description="wire funds",
        tool_name="wire_transfer",
        risk_level="high",
        required_approvers=2,
    )
    await writer.add_vote(
        tenant_id=TENANT_A, approval_id=approval_id, approver_id="alice", note="ok"
    )

    reader = TrustApprovalStore(app_factory)  # shares no in-process state
    loaded = await reader.get(TENANT_A, approval_id)
    assert loaded is not None, "approval did not survive — it was never persisted"
    assert loaded["status"] == "pending"
    assert [a["approver_id"] for a in loaded["approvers"]] == ["alice"]


@pytest.mark.asyncio
async def test_distinct_approvers_complete_the_request(
    app_factory: async_sessionmaker,
) -> None:
    store = TrustApprovalStore(app_factory)
    approval_id = secrets.token_hex(8)
    await store.create(
        tenant_id=TENANT_A, approval_id=approval_id, goal_id=None,
        step_description="", tool_name="t", risk_level="high", required_approvers=2,
    )
    first = await store.add_vote(
        tenant_id=TENANT_A, approval_id=approval_id, approver_id="alice", note=""
    )
    assert first["status"] == "pending"
    second = await store.add_vote(
        tenant_id=TENANT_A, approval_id=approval_id, approver_id="bob", note=""
    )
    assert second["status"] == "approved", second


@pytest.mark.asyncio
async def test_the_same_approver_cannot_vote_twice(
    app_factory: async_sessionmaker,
) -> None:
    store = TrustApprovalStore(app_factory)
    approval_id = secrets.token_hex(8)
    await store.create(
        tenant_id=TENANT_A, approval_id=approval_id, goal_id=None,
        step_description="", tool_name="t", risk_level="high", required_approvers=3,
    )
    await store.add_vote(
        tenant_id=TENANT_A, approval_id=approval_id, approver_id="alice", note=""
    )
    with pytest.raises(DuplicateApproverError):
        await store.add_vote(
            tenant_id=TENANT_A, approval_id=approval_id, approver_id="alice", note=""
        )


@pytest.mark.asyncio
async def test_concurrent_duplicate_approvals_cannot_both_count(
    app_factory: async_sessionmaker,
) -> None:
    """Separation of duties must hold as a DATABASE invariant, not a Python check.

    Two approvals by the same person racing on different replicas would both
    pass an application-level "has alice approved?" scan. UNIQUE (request_id,
    approver_id) makes that impossible regardless of timing.
    """
    store = TrustApprovalStore(app_factory)
    approval_id = secrets.token_hex(8)
    await store.create(
        tenant_id=TENANT_A, approval_id=approval_id, goal_id=None,
        step_description="", tool_name="t", risk_level="high", required_approvers=3,
    )

    async def vote() -> object:
        try:
            return await store.add_vote(
                tenant_id=TENANT_A, approval_id=approval_id,
                approver_id="alice", note="",
            )
        except Exception as exc:  # DuplicateApproverError or a serialization error
            return exc

    outcomes = await asyncio.gather(*(vote() for _ in range(4)))
    succeeded = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(succeeded) == 1, f"more than one concurrent vote counted: {outcomes}"

    loaded = await store.get(TENANT_A, approval_id)
    assert loaded is not None
    assert len(loaded["approvers"]) == 1, loaded["approvers"]
    assert loaded["status"] == "pending", loaded


@pytest.mark.asyncio
async def test_approvals_are_tenant_isolated(app_factory: async_sessionmaker) -> None:
    store = TrustApprovalStore(app_factory)
    approval_id = secrets.token_hex(8)
    await store.create(
        tenant_id=TENANT_A, approval_id=approval_id, goal_id=None,
        step_description="secret", tool_name="t", risk_level="high",
        required_approvers=1,
    )
    assert await store.get(TENANT_B, approval_id) is None
    assert await store.list(TENANT_B) == []
    with pytest.raises(ApprovalNotFoundError):
        await store.reject(
            tenant_id=TENANT_B, approval_id=approval_id, reason="x", rejected_by="mallory"
        )


# ── ai ops ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_ops_state_survives_a_restart(app_factory: async_sessionmaker) -> None:
    writer = AIOpsStore(app_factory)
    dataset_id = secrets.token_hex(8)
    await writer.create_dataset(
        tenant_id=TENANT_A, dataset_id=dataset_id, name="golden",
        description="d", golden_tasks=[{"expected_output": "x"}],
    )
    await writer.set_baseline(tenant_id=TENANT_A, metric_name="latency_ms", value=250.0)
    await writer.add_alert(
        tenant_id=TENANT_A,
        alert={"alert_id": secrets.token_hex(8), "severity": "critical",
               "metric_name": "latency_ms", "message": "spike"},
    )

    reader = AIOpsStore(app_factory)
    ds = await reader.get_dataset(TENANT_A, dataset_id)
    assert ds is not None and ds["task_count"] == 1, ds
    assert await reader.get_baseline(TENANT_A, "latency_ms") == 250.0
    assert len(await reader.list_alerts(TENANT_A)) == 1
    assert (await reader.alert_severity_counts(TENANT_A)).get("critical") == 1


@pytest.mark.asyncio
async def test_a_zero_baseline_is_a_real_baseline(
    app_factory: async_sessionmaker,
) -> None:
    """0.0 is falsy; the old code treated it as "no baseline set".

    That made every eval run look like a first run — the regression check was
    skipped and the baseline silently re-set each time.
    """
    store = AIOpsStore(app_factory)
    await store.set_baseline(tenant_id=TENANT_A, metric_name="error_rate", value=0.0)
    assert await store.get_baseline(TENANT_A, "error_rate") == 0.0

    # An auto-baseline must not clobber the operator's value.
    await store.set_baseline_if_absent(
        tenant_id=TENANT_A, metric_name="error_rate", value=0.9
    )
    assert await store.get_baseline(TENANT_A, "error_rate") == 0.0


@pytest.mark.asyncio
async def test_ai_ops_state_is_tenant_isolated(app_factory: async_sessionmaker) -> None:
    store = AIOpsStore(app_factory)
    dataset_id = secrets.token_hex(8)
    await store.create_dataset(
        tenant_id=TENANT_A, dataset_id=dataset_id, name="private",
        description="", golden_tasks=[],
    )
    await store.set_baseline(tenant_id=TENANT_A, metric_name="m", value=1.0)
    await store.add_alert(
        tenant_id=TENANT_A,
        alert={"alert_id": secrets.token_hex(8), "severity": "critical",
               "metric_name": "m", "message": "x"},
    )

    assert await store.get_dataset(TENANT_B, dataset_id) is None
    assert await store.list_datasets(TENANT_B) == []
    assert await store.get_baseline(TENANT_B, "m") is None
    assert await store.list_alerts(TENANT_B) == []
    assert await store.count_baselines(TENANT_B) == 0
