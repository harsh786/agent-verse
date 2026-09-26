"""e2e_full: an enabled compliance bundle must survive, spread, and actually bind.

``POST /trust/compliance-bundles/{id}/enable`` answers with the
bundle active and an ``effective_max_autonomy`` of ``supervised`` for HIPAA. Two
things were wrong behind that answer:

1. **It was stored in one process's heap.** ``ComplianceBundleManager`` is a
   module-level singleton over a plain ``dict`` with no persistence, so the
   enablement lived only in the API replica that served the POST — invisible to
   every other replica and to every Celery worker (which is where agents
   actually run), and gone on restart.

2. **Nothing read it.** ``get_effective_max_autonomy`` and
   ``requires_hitl_for_tool`` had no call site anywhere outside the endpoint
   that returns them. A tenant could enable HIPAA, be told the ceiling was
   ``supervised``, and every agent would keep running fully/bounded-autonomous
   with no approval gate — the endpoint reported a compliance posture the
   platform never applied.

These assert the posture is persisted in Postgres (so a worker sees it) and that
it actually clamps the autonomy a submitted goal executes under.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_enabling_a_bundle_is_persisted_not_process_local(
    app: Any, tenant_client: Any
) -> None:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])

    resp = await tenant_client.post("/trust/compliance-bundles/hipaa/enable")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "hipaa" in body["active"]
    assert body["effective_max_autonomy"] == "supervised"

    # A fresh manager, as a different replica or a restarted worker would build,
    # must see the same posture — which is only possible if it was persisted.
    from app.governance.compliance_bundles import PostgresComplianceBundleStore

    fresh = PostgresComplianceBundleStore(app.state.db_session_factory)
    assert "hipaa" in await fresh.active_bundle_ids(tenant_id)
    assert await fresh.effective_max_autonomy(tenant_id) == "supervised"

    async with (
        app.state.db_session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        rows = (
            await session.execute(
                text(
                    "SELECT bundle_id FROM tenant_compliance_bundles "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            )
        ).fetchall()
    assert [r[0] for r in rows] == ["hipaa"]


async def test_bundle_enablement_is_tenant_scoped(app: Any, client: Any) -> None:
    from app.governance.compliance_bundles import PostgresComplianceBundleStore

    async def _signup() -> tuple[str, str]:
        email = f"cb-{uuid.uuid4().hex[:12]}@example.com"
        r = await client.post("/tenants/signup", json={"name": "CB", "email": email})
        assert r.status_code == 201, r.text
        return str(r.json()["api_key"]), str(r.json()["tenant_id"])

    from httpx import ASGITransport, AsyncClient

    key_a, tenant_a = await _signup()
    _key_b, tenant_b = await _signup()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": key_a},
    ) as ca:
        assert (
            await ca.post("/trust/compliance-bundles/pci_dss/enable")
        ).status_code == 200

    store = PostgresComplianceBundleStore(app.state.db_session_factory)
    assert "pci_dss" in await store.active_bundle_ids(tenant_a)
    assert await store.active_bundle_ids(tenant_b) == ()


async def test_compliance_ceiling_clamps_the_autonomy_a_goal_runs_under(
    app: Any, tenant_client: Any
) -> None:
    """HIPAA caps autonomy at ``supervised``; an agent asking for more is clamped."""
    from app.services.goal_service import resolve_effective_autonomy_mode

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])

    # Before enabling anything, the agent's own configuration stands.
    assert (
        await resolve_effective_autonomy_mode(
            app.state, tenant_id=tenant_id, requested="fully-autonomous"
        )
        == "fully-autonomous"
    )

    assert (
        await tenant_client.post("/trust/compliance-bundles/hipaa/enable")
    ).status_code == 200

    for requested in ("fully-autonomous", "bounded-autonomous", "supervised"):
        effective = await resolve_effective_autonomy_mode(
            app.state, tenant_id=tenant_id, requested=requested
        )
        assert effective == "supervised", (
            f"HIPAA is enabled but a goal requesting {requested!r} still runs as "
            f"{effective!r} — the compliance ceiling is not applied"
        )


async def test_a_submitted_goal_carries_the_ceiling_to_whichever_worker_runs_it(
    app: Any, tenant_client: Any
) -> None:
    """The ceiling is stamped on the goal, not just known by the submitting replica."""
    from app.services.goal_service import clamp_autonomy_mode

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    assert (
        await tenant_client.post("/trust/compliance-bundles/hipaa/enable")
    ).status_code == 200

    resp = await tenant_client.post(
        "/goals", json={"goal": "Summarise the on-call runbook.", "dry_run": True}
    )
    assert resp.status_code in (200, 201, 202), resp.text
    goal_id = resp.json()["goal_id"]

    record = app.state.goal_service._goals.get(goal_id)
    assert record is not None
    ceiling = record.execution_context.get("compliance_autonomy_ceiling")
    assert ceiling == "supervised", (
        "the goal does not carry the compliance ceiling, so a worker on another "
        f"replica would run it unclamped (execution_context={record.execution_context})"
    )
    # An agent configured more permissively than the ceiling is still clamped —
    # the case a ceiling stamped as a plain autonomy_mode would have missed.
    assert clamp_autonomy_mode("fully-autonomous", ceiling) == "supervised"
    assert clamp_autonomy_mode("supervised", "bounded-autonomous") == "supervised"


async def test_disable_restores_the_agent_configured_autonomy(
    app: Any, tenant_client: Any
) -> None:
    from app.services.goal_service import resolve_effective_autonomy_mode

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])

    assert (
        await tenant_client.post("/trust/compliance-bundles/hipaa/enable")
    ).status_code == 200
    assert (
        await resolve_effective_autonomy_mode(
            app.state, tenant_id=tenant_id, requested="fully-autonomous"
        )
        == "supervised"
    )

    resp = await tenant_client.delete("/trust/compliance-bundles/hipaa")
    assert resp.status_code == 200, resp.text
    assert "hipaa" not in resp.json()["active"]

    assert (
        await resolve_effective_autonomy_mode(
            app.state, tenant_id=tenant_id, requested="fully-autonomous"
        )
        == "fully-autonomous"
    )
