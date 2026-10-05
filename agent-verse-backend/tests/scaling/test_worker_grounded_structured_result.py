"""GRD-1 on the worker path: a structured delete result grounds the final answer.

Live (MCP-MONGO-HITL): the approved ``mongodb_delete_one`` returned
``{'acknowledged': True, 'deleted_count': 1}`` and the agent answered "The order
was deleted." The high-risk final-answer grounding gate judged that sentence
against the raw dict by word overlap — "deleted" is not a word of
"deleted_count" — called it unsupported and replanned; every replan asked a
human again. A structured tool result is evidence: ``deleted_count: 1`` is the
fact "1 deleted".

``run_goal`` assembles the real AgentGraph (governance gates, the verifier's
final-answer gates, the worker's Redis-backed app state). Only the models, the
approver and the MongoDB connector are scripted.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis.aioredis
import pytest

from app.agent.tool_context import ToolContext, ToolRef
from app.governance.cost import CostController
from app.governance.hitl import ApprovalStatus
from app.providers.fake import FakeProvider
from tests.scaling.test_worker_runtime_profile import worker  # noqa: F401

GOAL_ID = "g-grd1-worker"
TENANT_ID = "t-grd1-worker"
GOAL = "Delete the cancelled order ORD-GRD-DEL from the production orders collection"
DELETE = {"collection": "orders", "filter": {"order_no": "ORD-GRD-DEL"}}
PLAN = (
    '{"steps": ["Delete order ORD-GRD-DEL from the orders collection", '
    '"Report the outcome to the user"]}'
)
ANSWER = "The order ORD-GRD-DEL was deleted."


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
    def __init__(self, output: dict[str, Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._output = output

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        out = self._output

        class Result:
            success = True
            output = out
            error = ""

        return Result()


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


def _scripted(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    mongo: _Mongo,
) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    shared_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    human = _Human()
    seen: dict[str, Any] = {"human": human, "mongo": mongo, "states": [], "events": []}
    real_graph_cls = graph_mod.AgentGraph.__mro__[1]  # the fixture stubs run(); use the real one

    class _ScriptedGraph(real_graph_cls):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs.update(
                planner=FakeProvider(responses=[PLAN]),
                executor=FakeProvider(
                    responses=[
                        json.dumps({"tool": "mongodb_delete_one", "arguments": DELETE}),
                        ANSWER,
                    ]
                ),
                # The model verifier is satisfied: only the grounding gates decide.
                verifier=FakeProvider(
                    responses=['{"success": true, "reason": "the delete ran"}']
                ),
                hitl_gateway=human,
                autonomy_mode="supervised",
                max_iterations=5,
                knowledge_store=None,
                retrieval_gateway=None,
                semantic_cache=None,
                long_term_memory=None,
                grant_store=None,
                enforce_grants=False,
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
            self._db_session_factory = None
            state = await super().run(**kwargs)
            seen["states"].append(state)
            return state

    class _Runner(tasks._WorkerMCPAgentRunner):  # type: ignore[misc]
        def __init__(self, runner: Any, _factory: Any, **kw: Any) -> None:
            async def _context() -> tuple[Any, Any, Any]:
                return None, mongo, _tool_context()

            super().__init__(runner, _context, **kw)

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

    result: dict[str, Any] = tasks.run_goal.run(GOAL_ID, TENANT_ID, GOAL, "normal", False)
    return result


@pytest.mark.usefixtures("readable_emergency_stop")
def test_high_risk_delete_completes_grounded_without_a_replan_loop(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _scripted(
        worker, monkeypatch, _Mongo({"acknowledged": True, "deleted_count": 1})
    )
    result = _run_goal()

    state = seen["states"][-1]
    assert result["status"] == "complete", (state.error_message, state.verification_feedback)
    # Grounded on the first verification: no replan, so nobody is asked twice.
    assert state.iterations == 1, state.verification_feedback
    assert state.context.get("final_answer_grounded") is True
    assert state.context.get("claim_grounding_safe") is True, state.ungrounded_claims
    assert len(seen["mongo"].calls) == 1
    assert seen["human"].filed.count("mongodb_delete_one") == 1
    types = [e.get("type") for e in seen["events"]]
    assert "claim_grounding_warning" not in types
    assert "grounding_warning" not in types


@pytest.mark.usefixtures("readable_emergency_stop")
def test_high_risk_delete_claim_is_not_grounded_by_an_errored_delete(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: the same answer against a delete that removed nothing."""
    seen = _scripted(
        worker,
        monkeypatch,
        _Mongo({"acknowledged": True, "deleted_count": 0, "error": "no matching order"}),
    )
    result = _run_goal()

    state = seen["states"][-1]
    # "The order ... was deleted." is refuted by deleted_count 0 + an error: the
    # goal does not complete on it (and the delete is still never re-run).
    assert result["status"] != "complete"
    assert state.context.get("claim_grounding_safe") is False
    assert len(seen["mongo"].calls) == 1
    assert "claim_grounding_warning" in [e.get("type") for e in seen["events"]]
