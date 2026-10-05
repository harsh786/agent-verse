"""OI-1: the goal action ledger (executed calls + approval decisions)."""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis

from app.agent.checkpoint_resume import checkpoint_payload, restore_from_checkpoint
from app.agent.goal_action_ledger import (
    LEDGER_CONTEXT_KEY,
    GoalActionLedger,
    action_approval_key,
    call_approval_key,
    call_fingerprint,
    executed_calls_planner_block,
    step_approval_key,
)
from app.agent.state import AgentState, StepResult, StepStatus
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="oi1-ledger", plan=PlanTier.ENTERPRISE, api_key_id="k")


def test_fingerprint_is_canonical_and_scoped() -> None:
    a = call_fingerprint("s1", "mongodb_delete_one", {"filter": {"x": 1}, "collection": "o"})
    b = call_fingerprint("s1", "mongodb_delete_one", {"collection": "o", "filter": {"x": 1}})
    assert a == b
    assert a != call_fingerprint(
        "s2", "mongodb_delete_one", {"collection": "o", "filter": {"x": 1}}
    )
    assert a != call_fingerprint(
        "s1", "mongodb_delete_one", {"collection": "o", "filter": {"x": 2}}
    )
    assert call_approval_key(a) != step_approval_key(a)
    assert action_approval_key("t: s", "t", {"a": 1}) != action_approval_key("t: s", "t", {"a": 2})


async def test_redis_entries_are_shared_and_expire() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    one = GoalActionLedger({}, tenant_id="t1", goal_id="g1", redis=redis, ttl_seconds=60)
    await one.record_executed(
        "fp", step_id="s-1", tool="x", server_id="srv", arguments={"a": 1}, output="ok"
    )
    await one.record_approval("call:fp", request_id="r1", action="x")

    other_replica = GoalActionLedger({}, tenant_id="t1", goal_id="g1", redis=redis)
    assert (await other_replica.executed("fp") or {})["output"] == "ok"
    assert (await other_replica.approval("call:fp") or {})["request_id"] == "r1"
    other_goal = GoalActionLedger({}, tenant_id="t1", goal_id="g2", redis=redis)
    assert await other_goal.executed("fp") is None
    other_tenant = GoalActionLedger({}, tenant_id="t2", goal_id="g1", redis=redis)
    assert await other_tenant.approval("call:fp") is None
    ttl = await redis.ttl("agentverse:goal_action_ledger:t1:g1")
    assert 0 < ttl <= 60


async def test_garbage_in_redis_is_not_trusted() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.hset("agentverse:goal_action_ledger:t1:g1", "e:fp", "not json")
    await redis.hset("agentverse:goal_action_ledger:t1:g1", "a:k", '{"status": "rejected"}')
    ledger = GoalActionLedger({}, tenant_id="t1", goal_id="g1", redis=redis)
    assert await ledger.executed("fp") is None
    assert await ledger.approval("k") is None


async def test_ledger_survives_a_checkpoint_resume() -> None:
    state = AgentState(goal="g", tenant_ctx=T)
    state.goal_id = "g-ckpt"
    state.steps.append(StepResult(description="s", status=StepStatus.COMPLETE, output="o"))
    state.context["_executable_plan"] = ["s"]
    state.context["_ckpt_done"] = {"step-1": "o"}
    ledger = GoalActionLedger(state.context, tenant_id=T.tenant_id, goal_id=state.goal_id)
    await ledger.record_executed(
        "fp",
        step_id="step-1",
        tool="mongodb_delete_one",
        server_id="m",
        arguments={"filter": {"order_no": "A"}},
        output="{'deleted': 1}",
    )
    await ledger.record_approval("call:fp", request_id="r1", action="mongodb_delete_one")

    payload = checkpoint_payload(state, 0)
    restored = restore_from_checkpoint(payload, None, goal="g", tenant_ctx=T, goal_id="g-ckpt")

    assert restored is not None
    again = GoalActionLedger(restored.context, tenant_id=T.tenant_id, goal_id="g-ckpt")
    assert (await again.executed("fp") or {})["step_id"] == "step-1"
    assert await again.approval("call:fp") is not None


def test_planner_block_lists_executed_calls() -> None:
    ctx: dict[str, Any] = {
        LEDGER_CONTEXT_KEY: {
            "executed": {
                "fp": {
                    "tool": "mongodb_delete_one",
                    "arguments": '{"filter":{"order_no":"A"}}',
                    "output": "{'deleted': 1}",
                }
            }
        }
    }
    block = executed_calls_planner_block(ctx)
    assert "ALREADY EXECUTED" in block
    assert "mongodb_delete_one" in block and "'deleted': 1" in block
    assert executed_calls_planner_block({}) == ""


def test_planner_context_carries_the_block() -> None:
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    graph = AgentGraph(planner=FakeProvider(), executor=FakeProvider(), verifier=FakeProvider())
    state = AgentState(goal="g", tenant_ctx=T)
    state.context[LEDGER_CONTEXT_KEY] = {
        "executed": {"fp": {"tool": "mongodb_delete_one", "arguments": "{}", "output": "ok"}}
    }
    parts = graph._pattern_result_parts(state)
    assert any("ALREADY EXECUTED" in p for p in parts)
