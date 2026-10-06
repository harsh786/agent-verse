"""Policy create → list → reload → delete against real Postgres under the app role.

Covers the SQL of QA-8 (the list reads the time window from the latest live
version snapshot), QA-9 (reload selects and orders by priority) and QA-13 (the
DB list is authoritative: empty after a delete, whatever this replica holds).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.governance import router
from app.governance.policies import PolicyEngine, PolicyResult
from tests.governance._router_app import make_app, tenant
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_policy_round_trip_under_the_app_role(pg_url: str) -> None:
    tid = f"t-qa-{uuid.uuid4().hex[:8]}"
    engine = await app_role_engine(
        pg_url, ["governance_policies", "policy_versions", "tenant_settings"]
    )
    try:
        db = sessionmaker_for(engine)
        app = make_app(router, ctx=tenant(tid))
        app.state.db_session_factory = db
        app.state.policy_engine = PolicyEngine()
        now = datetime.now(UTC).hour
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            deny = await c.post(
                "/governance/policies",
                json={"name": "deny", "tools_pattern": "deploy*", "action": "block"},
            )
            ask = await c.post(
                "/governance/policies",
                json={
                    "name": "ask-now",
                    "tools_pattern": "deploy*",
                    "action": "require_approval",
                    "priority": 10,
                    "allowed_hours_utc": [(now + 12) % 24, now],
                    "allowed_weekdays": list(range(7)),
                },
            )
            assert deny.status_code == 201, deny.text
            assert ask.status_code == 201, ask.text
            # a03-F060-03: a window in the policy's own timezone round-trips.
            kiri = datetime.now(ZoneInfo("Pacific/Kiritimati")).hour
            local = await c.post(
                "/governance/policies",
                json={
                    "name": "local-tz",
                    "tools_pattern": "drop*",
                    "action": "deny",
                    "priority": 5,
                    "allowed_hours_utc": [kiri],
                    "timezone": "Pacific/Kiritimati",
                },
            )
            assert local.status_code == 201, local.text

            listed = (await c.get("/governance/policies")).json()
            by_name = {p["name"]: p for p in listed}
            assert [p["name"] for p in listed] == ["ask-now", "local-tz", "deny"]  # priority DESC
            assert by_name["local-tz"]["timezone"] == "Pacific/Kiritimati"
            assert by_name["deny"]["timezone"] == "UTC"
            assert by_name["ask-now"]["allowed_hours_utc"] == sorted([now, (now + 12) % 24])
            assert by_name["ask-now"]["allowed_weekdays"] == list(range(7))
            assert by_name["deny"]["action"] == "deny"
            assert by_name["deny"]["allowed_hours_utc"] is None

            # Another replica (fresh engine) reloads the same decision from the DB.
            other = PolicyEngine()
            await other.reload_from_db(db, tenant_id=tid, strict=True)
            reloaded = {p.name: p for p in other._policies if p.tenant_id == tid}
            assert reloaded["ask-now"].priority == 10
            assert reloaded["ask-now"].allowed_hours_utc == frozenset({now, (now + 12) % 24})
            assert reloaded["local-tz"].timezone == "Pacific/Kiritimati"
            assert other.evaluate("drop_table", tenant_ctx=tenant(tid)) == PolicyResult.DENY
            assert other.evaluate("deploy_prod", tenant_ctx=tenant(tid)) == (
                PolicyResult.REQUIRE_APPROVAL
            )

            for p in listed:
                r = await c.delete(f"/governance/policies/{p['policy_id']}")
                assert r.status_code == 204, r.text
            # Authoritative empty list, even with this replica's registry populated.
            app.state._policy_registry = {tid: {"x": {"policy_id": "x", "name": "stale"}}}
            r = await c.get("/governance/policies")
            assert r.status_code == 200 and r.json() == []
    finally:
        await engine.dispose()
