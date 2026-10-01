"""e2e_full (GOAL-LOOKUP-503): a goal-store outage answers 503, a missing goal 404.

``GoalService._db_get_goal_record`` swallowed every DB error and returned
``None``, so while Postgres was unreachable ``GET /goals/{id}`` answered 404
"Goal not found" for goals that exist. Against the real booted app this
submits a real goal, evicts it from the replica's memory (as another replica /
a restart would), then points the goal service at an unreachable database.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_existing_goal_is_503_not_404_during_outage(
    app: Any, tenant_client: Any
) -> None:
    created = await tenant_client.post(
        "/goals", json={"goal": "Summarise the quarterly report", "dry_run": True}
    )
    assert created.status_code in (200, 201, 202), created.text
    goal_id = created.json()["goal_id"]
    ok = await tenant_client.get(f"/goals/{goal_id}")
    assert ok.status_code == 200, ok.text

    missing = await tenant_client.get(f"/goals/{uuid.uuid4().hex}")
    assert missing.status_code == 404, missing.text

    svc = app.state.goal_service
    svc._goals.pop(goal_id, None)  # as on another replica / after a restart
    previous = svc._db
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        connect_args={"timeout": 2},
    )
    svc._db = async_sessionmaker(engine, expire_on_commit=False)
    try:
        outage = await tenant_client.get(f"/goals/{goal_id}")
        selection = await tenant_client.get(f"/goals/{goal_id}/pattern-selection")
    finally:
        svc._db = previous
        await engine.dispose()

    assert outage.status_code == 503, outage.text
    assert outage.json()["error"]["code"] == "GOAL_STORE_UNAVAILABLE"
    assert selection.status_code == 503, selection.text

    # Back online: the same goal is readable again (it was never "not found").
    again = await tenant_client.get(f"/goals/{goal_id}")
    assert again.status_code == 200, again.text
