"""Regressions: two agent-loop paths that were wired but unreachable.

* ``rag_remediate`` required ``context["allow_rag_remediation"]``, which nothing
  ever set — a "no information about X" verdict always replanned against the
  same insufficient context instead of re-retrieving.
* ``stuck_loop_detected`` was queued in ``context["_pending_events"]`` by the
  (sync) router and never drained, so it never reached the event stream.
"""

from __future__ import annotations

from typing import Any

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _graph() -> AgentGraph:
    p = FakeProvider(responses=["x"])
    return AgentGraph(planner=p, executor=p, verifier=p)


def _failed_state(feedback: str) -> dict[str, Any]:
    st = AgentState(goal="what is the refund window", tenant_ctx=CTX)
    st.verification_feedback = feedback
    st.verification_success = False
    return {"agent_state": st, "tenant_ctx": CTX, "iteration": 1}


def test_context_gap_routes_to_remediation_for_agents_with_knowledge() -> None:
    g = _graph()
    g._agent_collection_ids = ["kb-1"]
    g._retrieval_gateway = object()
    assert g._route(_failed_state("No information about the refund window.")) == "rag_remediate"


def test_remediation_stays_off_without_bound_knowledge_or_when_disabled() -> None:
    g = _graph()
    g._retrieval_gateway = object()
    assert g._route(_failed_state("No information about the refund window.")) != "rag_remediate"

    g._agent_collection_ids = ["kb-1"]
    state = _failed_state("No information about the refund window.")
    state["agent_state"].context["allow_rag_remediation"] = False
    assert g._route(state) != "rag_remediate"


async def test_stuck_loop_event_reaches_the_event_stream() -> None:
    g = _graph()
    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    g._event_callback = _cb
    state = _failed_state("step failed")
    st: AgentState = state["agent_state"]
    st.steps = [StepResult(description=f"s{i}", status=StepStatus.FAILED) for i in range(3)]
    assert g._route(state) == "replan"

    await g._node_plan(state)
    assert any(e.get("type") == "stuck_loop_detected" for e in events)
    assert "_pending_events" not in st.context
