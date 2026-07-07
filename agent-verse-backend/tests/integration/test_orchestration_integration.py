# tests/integration/test_orchestration_integration.py
"""Integration tests requiring real Postgres + Redis (testcontainers).

Run with: DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock \
          TESTCONTAINERS_RYUK_DISABLED=true \
          uv run pytest tests/integration/test_orchestration_integration.py -m integration
"""
from __future__ import annotations
import os
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
async def test_goal_runtime_profile_persisted_to_execution_context():
    """GoalRuntimeProfile JSON must be serializable to execution_context format."""
    os.environ["DYNAMIC_ORCHESTRATION"] = "true"
    from app.core.runtime_flags import get_runtime_flags
    get_runtime_flags.cache_clear()

    from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
    from app.orchestration.strategy_registry import build_default_registry
    builder = RuntimeProfileBuilder(registry=build_default_registry())
    profile, trace = await builder.build_with_trace(
        "list all open Jira tickets",
        tenant_id="test_tenant",
        goal_id="integration_test_goal",
    )
    assert profile.goal_id == "integration_test_goal"
    assert profile.tenant_id == "test_tenant"
    profile_dict = profile.to_dict()
    import json
    json_str = json.dumps(profile_dict)
    assert len(json_str) > 50
    assert "goal_id" in profile_dict

    os.environ.pop("DYNAMIC_ORCHESTRATION", None)
    get_runtime_flags.cache_clear()


@pytest.mark.integration
async def test_semantic_cache_bridge_tenant_isolation_with_real_cache():
    """SemanticCacheBridge must not leak cache entries between tenants."""
    from app.state_runtime.cache_bridge import SemanticCacheBridge
    bridge = SemanticCacheBridge()

    await bridge.maybe_store(
        step_text="list open tickets",
        step_output="Found 5 tickets",
        tenant_id="tenant_alpha_isolated",
        is_error=False,
        is_nondeterministic=False,
    )
    result = await bridge.lookup(
        step_text="list open tickets",
        tenant_id="tenant_beta_isolated",
    )
    assert result is None


@pytest.mark.integration
async def test_full_goal_submission_with_dynamic_orchestration(signed_up_client):
    """Full flow: submit goal with DYNAMIC_ORCHESTRATION=true → goal accepted, no crash."""
    os.environ["DYNAMIC_ORCHESTRATION"] = "true"
    from app.core.runtime_flags import get_runtime_flags
    get_runtime_flags.cache_clear()

    r = await signed_up_client.post("/goals", json={
        "goal": "analyse code quality of the codebase",
        "agent_id": None,
    })
    assert r.status_code in (200, 201, 202)

    os.environ.pop("DYNAMIC_ORCHESTRATION", None)
    get_runtime_flags.cache_clear()
