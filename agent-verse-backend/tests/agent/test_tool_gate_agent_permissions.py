"""PERM-01 / PERM-02: workflow tool calls honour the agent's own permission rules.

``GovernedToolGate`` (workflow runs, API and worker) applied guardrails, the
permission matrix, policies, grants, tool risk and cost — but never the
per-agent ``agent_permissions`` rules, so an agent's DENY rule and its
per_goal_limit / daily_limit did not bind workflow tool calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import pytest

from app.agent.tool_gate import GovernedToolGate, gate_from_app_state
from app.governance import agent_permissions
from app.governance.agent_permissions import AgentPermissionRule
from app.governance.permissions import ActionLevel
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-perm-gate", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.fixture
def rules(monkeypatch: pytest.MonkeyPatch) -> list[AgentPermissionRule]:
    current: list[AgentPermissionRule] = []

    async def _load(db: Any, tenant_id: str, agent_id: str) -> tuple[AgentPermissionRule, ...]:
        assert agent_id == "agent-7"
        return tuple(current)

    monkeypatch.setattr(agent_permissions, "load_agent_permissions", _load)
    return current


def _gate(redis: Any = None) -> GovernedToolGate:
    return GovernedToolGate(
        agent_id="agent-7", db_session_factory=object(), redis=redis, guardrails=None
    )


async def _authorize(gate: GovernedToolGate, tool: str, goal: str = "g1") -> Any:
    return await gate.authorize(
        tool_name=tool, arguments={}, tenant_ctx=T, goal_id=goal, step_description="read it"
    )


async def test_agent_deny_rule_blocks_a_workflow_tool_call(rules: list[Any]) -> None:
    rules.append(AgentPermissionRule(tool_name="search_*", level=ActionLevel.DENY))
    decision = await _authorize(_gate(), "search_docs")
    assert not decision.allowed
    assert "agent permission" in decision.reason
    assert (await _authorize(_gate(), "read_file")).allowed  # no rule → allowed


async def test_per_goal_limit_binds(rules: list[Any]) -> None:
    rules.append(
        AgentPermissionRule(tool_name="read_file", level=ActionLevel.ALLOW, per_goal_limit=2)
    )
    gate = _gate()
    assert (await _authorize(gate, "read_file")).allowed
    assert (await _authorize(gate, "read_file")).allowed
    assert not (await _authorize(gate, "read_file")).allowed
    assert (await _authorize(gate, "read_file", goal="g2")).allowed  # per goal


async def test_daily_limit_is_shared_across_gates_via_redis(rules: list[Any]) -> None:
    rules.append(AgentPermissionRule(tool_name="read_file", level=ActionLevel.ALLOW, daily_limit=1))
    redis = fakeredis.aioredis.FakeRedis()
    assert (await _authorize(_gate(redis), "read_file")).allowed
    # A second process / replica: same shared counter.
    assert not (await _authorize(_gate(redis), "read_file")).allowed


async def test_approval_rule_requires_hitl(rules: list[Any]) -> None:
    rules.append(AgentPermissionRule(tool_name="read_file", level=ActionLevel.APPROVAL))
    decision = await _authorize(_gate(), "read_file")  # no HITL gateway wired
    assert not decision.allowed
    assert "approval" in decision.reason


def test_app_state_gate_carries_the_db_and_redis() -> None:
    state = SimpleNamespace(db_session_factory="db", _redis="r")
    gate = gate_from_app_state(state, agent_id="a")
    assert gate._db == "db" and gate._redis == "r"


def test_worker_gate_carries_the_db_and_shared_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: "worker-redis")
    gate = tasks._worker_tool_gate(None, None, None, "agent-7")
    assert gate._redis == "worker-redis"
    assert gate._db is not None
