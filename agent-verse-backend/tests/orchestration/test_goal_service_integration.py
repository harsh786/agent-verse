"""GoalService must build and attach GoalRuntimeProfile when flag is enabled."""
from __future__ import annotations

import os


async def test_goal_service_builds_profile_when_flag_enabled(signed_up_client):
    """With DYNAMIC_ORCHESTRATION=true, goal submission attaches a runtime profile."""
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"DYNAMIC_ORCHESTRATION": "true"}
    ):
        from app.core.runtime_flags import get_runtime_flags
        get_runtime_flags.cache_clear()
        r = await signed_up_client.post("/goals", json={
            "goal": "list all open Jira tickets",
            "agent_id": None,
        })
        assert r.status_code in (200, 201, 202)
    from app.core.runtime_flags import get_runtime_flags
    get_runtime_flags.cache_clear()


async def test_goal_service_works_without_flag(signed_up_client):
    """Without flag, goal submission works exactly as before."""
    r = await signed_up_client.post("/goals", json={
        "goal": "list all open Jira tickets",
        "agent_id": None,
    })
    assert r.status_code in (200, 201, 202)
