"""OI-1 on the worker path: approve once → the delete executes once → the goal completes.

``run_goal`` assembles the real AgentGraph (governance gates, checkpoints, the
worker's Redis-backed app state). Only the models, the approver and the MongoDB
connector are scripted: the verifier rejects the first round (forcing a replan
that plans the same delete again), and a second delivery of the same goal (a
redelivered message on another replica) runs it once more. The delete must hit
MongoDB exactly once and a human must be asked for it exactly once.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis.aioredis
import pytest

from app.agent.tool_context import ToolContext, ToolRef
from app.governance.cost import CostController
from app.governance.hitl import ApprovalStatus
from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider
from tests.scaling.test_worker_runtime_profile import worker  # noqa: F401

GOAL_ID = "g-oi1-worker"
TENANT_ID = "t-oi1-worker"
DELETE = {"collection": "orders", "filter": {"order_no": "ORD-OI1-DEL"}}
PLAN = '{"steps": ["Run the removal of order ORD-OI1-DEL", "Report the outcome"]}'


class _Human:
    def __init__(self) -> None:
        self.filed: list[str] = []
        self._redis: Any = None

    async def request_approval_async(self, *, action: str, **_kw: Any) -> str:
        self.filed.append(action)
        return f"req-{len(self.filed)}"

    async def wait_for_approval(self, request_id: str, **_kw: Any) -> ApprovalStatus:
        return ApprovalStatus.APPROVED

    def list_pending(self, **_kw: Any) -> list[Any]:
        return []


class _Mongo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        deleted = 1 if len(self.calls) == 1 else 0

        class Result:
            success = True
            output = {"deleted": deleted}
            error = ""

        return Result()


class _Verifier(FakeProvider):
    """Rejects the first verification of the whole test, accepts every later one."""

    calls = 0

    async def complete(self, request: Any) -> Any:
        type(self).calls += 1
        self.call_history.append(request)
        content = (
            '{"success": false, "reason": "cannot confirm the deletion", "retry": true}'
            if type(self).calls == 1
            else '{"success": true, "reason": "deleted: 1 recorded"}'
        )
        return CompletionResponse(content=content, model=request.model)


def _tool_context() -> ToolContext:
    return ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                server_id="builtin-mongodb:orders",
                server_name="orders-db",
                name="mongodb_delete_one",
                description="delete one document",
                input_schema={},
            )
        ],
    )


@pytest.fixture
def scripted(worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:  # noqa: F811
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    shared_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    human, mongo = _Human(), _Mongo()
    _Verifier.calls = 0
    seen: dict[str, Any] = {"human": human, "mongo": mongo, "states": [], "events": []}
    real_graph_cls = graph_mod.AgentGraph.__mro__[1]  # the fixture stubs run(); use the real one

    class _ScriptedGraph(real_graph_cls):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs.update(
                planner=FakeProvider(responses=[PLAN]),
                executor=FakeProvider(
                    responses=[
                        json.dumps({"tool": "mongodb_delete_one", "arguments": DELETE}),
                        "{'deleted': 1}",
                    ]
                ),
                verifier=_Verifier(),
                hitl_gateway=human,
                autonomy_mode="supervised",
                max_iterations=5,
                # No database / knowledge base in a unit test.
                knowledge_store=None,
                retrieval_gateway=None,
                semantic_cache=None,
                long_term_memory=None,
                # Grants are covered elsewhere; the live scenario granted the tools.
                grant_store=None,
                enforce_grants=False,
                # The worker's Redis cost counters are unreachable here.
                cost_controller=CostController(),
            )
            super().__init__(**kwargs)
            self._db_session_factory = None
            self._checkpoints_enabled = False

        async def run(self, **kwargs: Any) -> Any:
            user_cb = kwargs.get("event_callback")

            async def _cb(event: dict[str, Any]) -> None:
                seen["events"].append(event)
                if user_cb is not None:
                    await user_cb(event)

            kwargs["event_callback"] = _cb
            # No database in a unit test (the worker attaches its factory late).
            self._db_session_factory = None
            state = await super().run(**kwargs)
            seen["states"].append(state)
            return state

    class _Runner(tasks._WorkerMCPAgentRunner):  # type: ignore[misc]
        def __init__(self, runner: Any, _factory: Any, **kw: Any) -> None:
            async def _context() -> tuple[Any, Any, Any]:
                return None, mongo, _tool_context()

            super().__init__(runner, _context, **kw)

    # The tenant has no custom guardrail rules (an empty rule store, not "no DB").
    from app.guardrails_v2 import engine as engine_mod
    from app.guardrails_v2.engine import GuardrailsEngine

    class _NoRules:
        async def load(self, tenant_id: str) -> list[Any]:
            return []

        async def record_violations(self, *_a: Any, **_k: Any) -> None:
            return None

    fresh = GuardrailsEngine()
    fresh.bind_repository(_NoRules(), auto_persist=False)
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)

    # The tenant has no tool policies (an empty policy table, not "no DB").
    async def _no_policies(_db: Any, _tenant_id: str) -> Any:
        from app.governance.policies import PolicyEngine

        return PolicyEngine()

    monkeypatch.setattr(tasks, "_load_worker_policy_engine", _no_policies)
    monkeypatch.setattr(graph_mod, "AgentGraph", _ScriptedGraph)
    monkeypatch.setattr(tasks, "_WorkerMCPAgentRunner", _Runner)
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: shared_redis)
    worker["with_context"]({})
    return seen


def _run_goal() -> dict[str, Any]:
    from app.scaling import tasks

    result: dict[str, Any] = tasks.run_goal.run(
        GOAL_ID, TENANT_ID, "Remove the cancelled test order ORD-OI1-DEL", "normal", False
    )
    return result


@pytest.mark.usefixtures("readable_emergency_stop")
def test_worker_goal_approves_once_deletes_once_and_completes(
    scripted: dict[str, Any],
) -> None:
    result = _run_goal()

    state = scripted["states"][-1]
    assert result["status"] == "complete", state.error_message
    assert state.iterations >= 2  # the verifier forced a replan
    assert len(scripted["mongo"].calls) == 1
    assert scripted["human"].filed.count("mongodb_delete_one") == 1
    # The replanned (identical) removal step reused its approval.
    assert scripted["human"].filed.count("Run the removal of order ORD-OI1-DEL") == 1
    types = [e.get("type") for e in scripted["events"]]
    assert "approval_reused" in types
    # Not re-run: served from the run's dedup cache or the action ledger.
    assert "dedup_hit" in types or "tool_call_already_executed" in types

    # A redelivery of the same goal (another replica, fresh state and caches)
    # neither deletes again nor asks again: the ledger is in the shared Redis.
    filed_before = list(scripted["human"].filed)
    scripted["events"].clear()
    again = _run_goal()
    assert again["status"] == "complete", scripted["states"][-1].error_message
    assert len(scripted["mongo"].calls) == 1
    assert scripted["human"].filed == filed_before
    assert "tool_call_already_executed" in [e.get("type") for e in scripted["events"]]
