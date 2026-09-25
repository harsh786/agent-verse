"""e2e_full: trigger event / DLQ tenant isolation and DLQ retry semantics.

Three guarantees this proves behaviourally against real Postgres:

1. ``GET /triggers/{id}/events`` is tenant-scoped. The handler used to filter
   **only** on ``trigger_id`` with no ``tenant_id`` predicate and no RLS GUC, so
   any authenticated tenant that knew (or guessed) another tenant's trigger id
   read that tenant's firing history — including the raw webhook ``payload``.

2. ``write_to_dlq`` / ``GET /triggers/dlq`` round-trip under an RLS-enforcing
   session. Both sides ran without ever setting ``app.tenant_id``; under a
   least-privilege (non-BYPASSRLS) role the INSERT matched no policy and the
   SELECT matched zero rows, and both failures were swallowed by broad
   ``except Exception`` handlers, so the DLQ silently stayed empty forever.

3. ``POST /triggers/dlq/{id}/retry`` performs a real re-queue. It used to be a
   pure stub that returned ``{"status": "queued"}`` for any id at all — including
   ids belonging to another tenant and ids that do not exist.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _signup(client: Any) -> tuple[str, str]:
    """Create a fresh tenant; return ``(api_key, tenant_id)``."""
    email = f"trig-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Trig", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    body = resp.json()
    return str(body["api_key"]), str(body["tenant_id"])


async def _seed_trigger_event(
    app: Any, *, tenant_id: str, trigger_id: str, payload: dict[str, Any]
) -> str:
    """Insert one ``trigger_events`` row for ``tenant_id`` (bypassing the API)."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    event_id = str(uuid.uuid4())
    factory = app.state.db_session_factory
    async with (
        factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        await session.execute(
            text(
                "INSERT INTO trigger_events "
                "(id, tenant_id, trigger_id, trigger_type, idempotency_key, "
                " fired_at, payload, goal_created, goal_id) "
                "VALUES (:id, :tid, :trig, 'webhook', :idem, :fired, "
                "        CAST(:payload AS json), TRUE, :goal)"
            ),
            {
                "id": event_id,
                "tid": tenant_id,
                "trig": trigger_id,
                "idem": f"idem-{event_id}",
                "fired": datetime.now(UTC).replace(tzinfo=None),
                "payload": json.dumps(payload),
                "goal": str(uuid.uuid4()),
            },
        )
    return event_id


async def _client_for(app: Any, api_key: str) -> Any:
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": api_key},
    )


async def test_trigger_events_are_tenant_scoped(
    app: Any, client: Any, _reset_signup_rate_limit: None
) -> None:
    """Tenant B must not read tenant A's trigger firing history."""
    key_a, tenant_a = await _signup(client)
    key_b, tenant_b = await _signup(client)
    assert tenant_a != tenant_b

    trigger_id = str(uuid.uuid4())
    secret_payload = {"secret": "salamander-ledger-seal", "amount": 42}
    await _seed_trigger_event(
        app, tenant_id=tenant_a, trigger_id=trigger_id, payload=secret_payload
    )

    async with await _client_for(app, key_a) as ca:
        own = await ca.get(f"/triggers/{trigger_id}/events")
    assert own.status_code == 200
    assert len(own.json()) == 1, "tenant A must see its own trigger event"

    async with await _client_for(app, key_b) as cb:
        other = await cb.get(f"/triggers/{trigger_id}/events")
    assert other.status_code == 200
    leaked = other.json()
    assert leaked == [], f"cross-tenant trigger events leaked to tenant B: {leaked}"
    assert "salamander-ledger-seal" not in json.dumps(leaked)


async def test_dlq_write_is_visible_to_owner_only(
    app: Any, client: Any, _reset_signup_rate_limit: None
) -> None:
    """A DLQ write lands in Postgres and is readable by its owning tenant only."""
    from app.triggers.dlq import write_to_dlq

    key_a, tenant_a = await _signup(client)
    key_b, _tenant_b = await _signup(client)

    trigger_id = str(uuid.uuid4())
    factory = app.state.db_session_factory
    async with factory() as session:
        await write_to_dlq(
            session,
            tenant_id=tenant_a,
            trigger_id=trigger_id,
            failure_type="GOAL_ENQUEUE_FAILED",
            error_message="downstream unavailable",
            raw_payload={"marker": "dlq-nyctographic"},
        )

    async with await _client_for(app, key_a) as ca:
        mine = await ca.get("/triggers/dlq")
    assert mine.status_code == 200
    rows = mine.json()
    assert any(r["trigger_id"] == trigger_id for r in rows), (
        f"DLQ write never became readable for its own tenant: {rows}"
    )

    async with await _client_for(app, key_b) as cb:
        theirs = await cb.get("/triggers/dlq")
    assert theirs.status_code == 200
    assert all(r["trigger_id"] != trigger_id for r in theirs.json())


async def test_dlq_retry_requeues_and_rejects_foreign_ids(
    app: Any, client: Any, _reset_signup_rate_limit: None
) -> None:
    """Retry schedules a real re-attempt; unknown/foreign ids are 404, not 202."""
    from app.triggers.dlq import write_to_dlq

    key_a, tenant_a = await _signup(client)
    key_b, _tenant_b = await _signup(client)

    trigger_id = str(uuid.uuid4())
    factory = app.state.db_session_factory
    async with factory() as session:
        await write_to_dlq(
            session,
            tenant_id=tenant_a,
            trigger_id=trigger_id,
            failure_type="RATE_LIMITED",
            error_message="429 from downstream",
            raw_payload={"marker": "retry-me"},
        )

    async with await _client_for(app, key_a) as ca:
        rows = (await ca.get("/triggers/dlq")).json()
        entry = next(r for r in rows if r["trigger_id"] == trigger_id)
        dlq_id = entry["id"]
        assert entry["retry_count"] == 0

        accepted = await ca.post(f"/triggers/dlq/{dlq_id}/retry")
        assert accepted.status_code == 202, accepted.text

        after = (await ca.get("/triggers/dlq")).json()
        updated = next((r for r in after if r["id"] == dlq_id), None)
        assert updated is not None, "retried entry vanished from the DLQ"
        assert updated["retry_count"] == 1, (
            "retry did not record an attempt — the endpoint is a no-op stub"
        )
        assert updated["next_retry_at"] is not None, "retry scheduled no next attempt"

        missing = await ca.post(f"/triggers/dlq/{uuid.uuid4()}/retry")
        assert missing.status_code == 404, "unknown DLQ id was accepted"

    async with await _client_for(app, key_b) as cb:
        foreign = await cb.post(f"/triggers/dlq/{dlq_id}/retry")
    assert foreign.status_code == 404, "another tenant's DLQ entry was retryable"
