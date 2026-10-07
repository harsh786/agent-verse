"""a08-F200-02: a resumed goal can still undo what its crashed first attempt did.

The rollback stack was an in-memory RollbackEngine per graph build: after a
crash / requeue the resumed run started with an empty stack, so its later
permanent failure could not undo the first attempt's tool calls. The stack is
now rebuilt from the OI-1 action ledger (checkpoint mirror + Redis hash) on
resume — registration only; nothing is undone because of the crash itself.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.checkpoint_resume import (
    COMPLETED_STEPS_KEY,
    EXECUTABLE_PLAN_KEY,
    checkpoint_payload,
)
from app.agent.goal_action_ledger import GoalActionLedger, call_fingerprint
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.reliability.rollback import RollbackEngine
from app.reliability.tool_inverses import _INVERSE_REGISTRY, ROLLED_BACK, InverseResult
from app.tenancy.context import PlanTier, TenantContext

TOOL = "acme_create_ticket"
FAIL = '{"success": false, "retry": false, "reason": "the customer rejected it"}'
OK = '{"success": true, "reason": "ok"}'


@pytest.fixture
def undone(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _inverse(payload: dict[str, Any], client: Any) -> InverseResult:
        calls.append(payload)
        return InverseResult(ROLLED_BACK, "deleted")

    monkeypatch.setitem(_INVERSE_REGISTRY, TOOL, _inverse)
    return calls


async def _crashed_first_attempt_state(ctx: TenantContext, goal_id: str) -> AgentState:
    """State as checkpointed after step 1 (which created ticket T-1), then a crash."""
    state = AgentState(goal="handle the complaint", tenant_ctx=ctx)
    state.goal_id = goal_id
    state.plan = ["file a ticket", "notify the customer"]
    state.steps.append(
        StepResult(step_id="s1", description="file a ticket", status=StepStatus.COMPLETE,
                   output="ticket T-1 filed")
    )
    state.context[EXECUTABLE_PLAN_KEY] = ["file a ticket", "notify the customer"]
    state.context[COMPLETED_STEPS_KEY] = {"s1": "ticket T-1 filed"}
    args = {"title": "refund"}
    await GoalActionLedger(state.context, tenant_id=ctx.tenant_id, goal_id=goal_id).record_executed(
        call_fingerprint("srv", TOOL, args), step_id="s1", tool=TOOL, server_id="srv",
        arguments=args, output=json.dumps({"id": "T-1"}),
    )
    return state


def _resumed_graph(verdict: str) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["file a ticket", "notify the customer"]}']),
        executor=FakeProvider(responses=["customer notified"] * 3),
        verifier=FakeProvider(responses=[verdict] * 3),
        rollback_engine=RollbackEngine(),
        max_iterations=1,
    )


async def _resume(graph: AgentGraph, payload: dict[str, Any], ctx: TenantContext,
                  goal_id: str) -> Any:
    async def _load(gid: str, tenant_ctx: Any) -> dict[str, Any]:
        return payload

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    graph._load_checkpoint = _load  # type: ignore[method-assign]
    graph._write_checkpoint = _noop  # type: ignore[method-assign]
    return await graph.run(goal="handle the complaint", tenant_ctx=ctx, goal_id=goal_id)


async def test_resumed_goal_that_fails_undoes_the_first_attempts_call_once(
    undone: list[dict[str, Any]],
) -> None:
    ctx = TenantContext(tenant_id="t-rh", plan=PlanTier.PROFESSIONAL, api_key_id="k")
    state = await _crashed_first_attempt_state(ctx, "g-rh")
    payload = json.loads(json.dumps(checkpoint_payload(state, 0)))
    graph = _resumed_graph(FAIL)

    final = await _resume(graph, payload, ctx, "g-rh")

    assert final.status == GoalStatus.FAILED
    assert len(undone) == 1 and json.loads(undone[0]["result"]) == {"id": "T-1"}
    assert len(graph._rollback_engine) == 0  # popped: a later trigger undoes nothing


async def test_resumed_goal_that_succeeds_undoes_nothing(undone: list[dict[str, Any]]) -> None:
    ctx = TenantContext(tenant_id="t-rh2", plan=PlanTier.PROFESSIONAL, api_key_id="k")
    state = await _crashed_first_attempt_state(ctx, "g-rh2")
    graph = _resumed_graph(OK)

    final = await _resume(graph, json.loads(json.dumps(checkpoint_payload(state, 0))), ctx,
                          "g-rh2")

    assert final.status == GoalStatus.COMPLETE
    assert undone == []  # the crash alone never undoes anything
    assert len(graph._rollback_engine) == 1  # but the record is back for a later abort


@pytest.mark.integration
async def test_crash_resume_fail_on_postgres_runs_the_inverse_once(
    pg_url: str, undone: list[dict[str, Any]]
) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, goal_id = uuid.uuid4().hex, uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    async def _exec(sql: str, params: dict[str, Any]) -> None:
        async with factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            await s.execute(text(sql), params)

    try:
        await _exec("INSERT INTO tenants (id, name, email) VALUES (:t, 'rb', :e)",
                    {"t": tenant_id, "e": f"{tenant_id}@t.local"})
        await _exec("INSERT INTO goals (id, tenant_id, goal_text, status) "
                    "VALUES (:i, :t, 'handle the complaint', 'executing')",
                    {"i": goal_id, "t": tenant_id})

        # First attempt: step 1 ran the tool and its checkpoint (with the action
        # ledger) reached Postgres; then the worker died (its in-memory stack too).
        first = AgentGraph(planner=FakeProvider(), executor=FakeProvider(),
                           verifier=FakeProvider(), rollback_engine=RollbackEngine())
        first._db_session_factory = factory
        await first._write_checkpoint(
            goal_id, 0, await _crashed_first_attempt_state(ctx, goal_id), ctx
        )
        assert undone == []

        # Redelivered: a fresh graph resumes from Postgres, the goal then fails.
        resumed = _resumed_graph(FAIL)
        resumed._db_session_factory = factory
        final = await resumed.run(goal="handle the complaint", tenant_ctx=ctx, goal_id=goal_id)

        assert final.status == GoalStatus.FAILED
        assert len(undone) == 1  # the first attempt's ticket is deleted, exactly once
        assert json.loads(undone[0]["result"]) == {"id": "T-1"}
        assert undone[0]["tenant_id"] == tenant_id
    finally:
        await _exec("DELETE FROM goal_checkpoints WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM goals WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM tenants WHERE id = :t", {"t": tenant_id})
        await engine.dispose()
