"""a10-F232-01 on real Postgres: GET /goals/{id}/replay is bounded and pages.

goal_events / goal_steps / decision_traces were read with no LIMIT, so a long
goal's replay loaded its whole history in one response. Each list is now capped
at ``limit``; events page with ``after_sequence`` / ``next_after_sequence``.
Runs the real ``replay_goal`` as the least-privilege (NOBYPASSRLS) app role.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.replay import replay_goal
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration


def _request(tenant_id: str, db: object) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(tenant=SimpleNamespace(tenant_id=tenant_id)),
        app=SimpleNamespace(state=SimpleNamespace(db_session_factory=db)),
    )


async def test_replay_pages_events_and_caps_steps_and_traces(pg_url: str) -> None:
    tenant = f"replay-page-{uuid.uuid4().hex[:8]}"
    goal_id = uuid.uuid4().hex
    admin = create_async_engine(pg_url)
    async with admin.begin() as c:
        await c.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
            {"t": tenant, "e": f"{tenant}@example.test"},
        )
        await c.execute(
            text("INSERT INTO goals (id, tenant_id, goal_text, status) VALUES (:g, :t, 'x', 'complete')"),
            {"g": goal_id, "t": tenant},
        )
        for seq in range(1, 8):
            await c.execute(
                text(
                    "INSERT INTO goal_events (id, tenant_id, goal_id, sequence, event_type, payload) "
                    "VALUES (:id, :t, :g, :s, 'step_started', '{}')"
                ),
                {"id": uuid.uuid4().hex, "t": tenant, "g": goal_id, "s": seq},
            )
        for idx in range(4):
            await c.execute(
                text(
                    "INSERT INTO goal_steps (id, tenant_id, goal_id, step_index, description, status) "
                    "VALUES (:id, :t, :g, :i, 'step', 'complete')"
                ),
                {"id": uuid.uuid4().hex, "t": tenant, "g": goal_id, "i": idx},
            )
            await c.execute(
                text(
                    "INSERT INTO decision_traces (id, tenant_id, goal_id, action, reasoning, "
                    "confidence) VALUES (:id, :t, :g, 'act', 'why', 0.5)"
                ),
                {"id": uuid.uuid4().hex, "t": tenant, "g": goal_id},
            )
    app_eng = await app_role_engine(
        pg_url, ["goals", "goal_events", "goal_steps", "decision_traces", "evaluations"]
    )
    req = _request(tenant, sessionmaker_for(app_eng))
    try:
        first = await replay_goal(req, goal_id, limit=3)  # type: ignore[arg-type]
        assert [e["sequence"] for e in first["timeline"][1:]] == [1, 2, 3]
        assert first["timeline"][0]["type"] == "goal_created"
        assert first["events_truncated"] is True and first["next_after_sequence"] == 3
        assert first["step_count"] == 3 and first["steps_truncated"] is True
        assert len(first["decision_traces"]) == 3 and first["decision_traces_truncated"]

        seqs: list[int] = []
        cursor = first["next_after_sequence"]
        while cursor is not None:
            page = await replay_goal(req, goal_id, limit=3, after_sequence=cursor)  # type: ignore[arg-type]
            assert all(e["type"] != "goal_created" for e in page["timeline"])
            seqs += [e["sequence"] for e in page["timeline"]]
            cursor = page["next_after_sequence"]
        assert seqs == [4, 5, 6, 7]

        whole = await replay_goal(req, goal_id)  # type: ignore[arg-type]
        assert whole["event_count"] == 7 and whole["events_truncated"] is False
        assert whole["next_after_sequence"] is None and whole["step_count"] == 4
    finally:
        await app_eng.dispose()
        await admin.dispose()
