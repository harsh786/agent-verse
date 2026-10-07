"""a03-F061-N1: the worker's multi_agent workflow gate carries grants + permission matrix.

``_worker_tool_gate`` built the GovernedToolGate from a namespace with no
``grant_store`` and no ``permission_matrix``. With grant enforcement on (the
default) every tool call of a queued multi_agent workflow goal was denied
``grant_store_unavailable``, and the default-deny matrix never applied. The gate
now gets the Postgres grant store and the default-deny matrix, and still fails
closed when there is no DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.governance import compliance_bundles, policy_rules
from app.governance.grants.models import Grant
from app.governance.grants.store import InMemoryGrantStore
from app.scaling import tasks
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-wf-gate", plan=PlanTier.ENTERPRISE, api_key_id="k")


def _grant(tenant: str, agent: str, scopes: tuple[str, ...]) -> Grant:
    now = datetime.now(UTC)
    return Grant(
        grant_id=f"g-{uuid.uuid4().hex[:10]}",
        tenant_id=tenant,
        grantor="admin",
        grantee_agent_id=agent,
        scopes=scopes,
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )


@pytest.fixture
def no_db_side_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Policy rules, bundles and per-agent rules are covered elsewhere."""

    async def _none(*_a: Any, **_k: Any) -> Any:
        return []

    async def _no_bundles(self: Any, tenant_id: str) -> tuple[str, ...]:
        return ()

    from app.governance import agent_permissions

    monkeypatch.setattr(policy_rules, "load_active_policy_rules", _none)
    monkeypatch.setattr(agent_permissions, "load_agent_permissions", _none)
    compliance_bundles.invalidate_active_bundles()
    monkeypatch.setattr(
        compliance_bundles.PostgresComplianceBundleStore, "active_bundle_ids", _no_bundles
    )
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)


async def _decide(gate: Any, tool: str) -> Any:
    return await gate.authorize(tool_name=tool, arguments={}, tenant_ctx=T, goal_id="goal-1")


async def test_granted_tool_runs_and_ungranted_is_denied(
    monkeypatch: pytest.MonkeyPatch, no_db_side_checks: None
) -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant(T.tenant_id, "agent-1", ("read_*", "drop_table")))
    import app.db.session as db_session
    import app.governance.grants.postgres_store as pg_store

    monkeypatch.setattr(db_session, "get_session_factory", lambda: object())
    monkeypatch.setattr(pg_store, "PostgresGrantStore", lambda _factory: store)

    gate = tasks._worker_tool_gate(None, None, None, "agent-1")

    assert (await _decide(gate, "read_file")).allowed
    denied = await _decide(gate, "send_email")
    assert not denied.allowed and "tool_out_of_scope" in denied.reason
    # Granted, but the platform default-deny matrix still blocks it.
    dropped = await _decide(gate, "drop_table")
    assert not dropped.allowed and "permission matrix" in dropped.reason


async def test_no_db_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch, no_db_side_checks: None
) -> None:
    import app.db.session as db_session

    def _no_db() -> Any:
        raise RuntimeError("no DATABASE_URL")

    monkeypatch.setattr(db_session, "get_session_factory", _no_db)
    gate = tasks._worker_tool_gate(None, None, None, "agent-1")
    decision = await _decide(gate, "read_file")
    assert not decision.allowed and "grant_store_unavailable" in decision.reason


@pytest.mark.integration
async def test_worker_gate_reads_real_postgres_grants(
    test_backends: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _none(*_a: Any, **_k: Any) -> Any:
        return []

    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)
    monkeypatch.setattr(policy_rules, "load_active_policy_rules", _none)
    compliance_bundles.invalidate_active_bundles()
    from app.db.session import get_session_factory
    from app.governance.grants.postgres_store import PostgresGrantStore

    tenant = f"t-wfg-{uuid.uuid4().hex[:8]}"
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k")
    await PostgresGrantStore(get_session_factory()).issue(
        _grant(tenant, "agent-pg", ("read_*",))
    )

    gate = tasks._worker_tool_gate(None, None, None, "agent-pg")
    ok = await gate.authorize(tool_name="read_file", arguments={}, tenant_ctx=ctx, goal_id="g")
    assert ok.allowed, ok.reason
    no = await gate.authorize(tool_name="send_email", arguments={}, tenant_ctx=ctx, goal_id="g")
    assert not no.allowed and "tool_out_of_scope" in no.reason
