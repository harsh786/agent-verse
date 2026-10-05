"""GRD-1: structured tool results are grounding evidence.

On a high-risk goal the verifier's final-answer NLI gate judged "The order was
deleted." against the raw tool output ``{'acknowledged': True, 'deleted_count':
1}`` by word overlap — "deleted" is not a word of "deleted_count", and a Python
``None`` / ``False`` in the dict read as a negation — so a true answer was
"unsupported" or even "contradicted" and the goal replanned, asking a human
again each time (MCP-MONGO-HITL).

Structured results (JSON / dict reprs, counts, booleans, status codes, ids) are
normalised into checkable facts (``deleted_count=1`` → "1 deleted", status 204
→ "succeeded"); a claim one of them supports is grounded, and a claim one of
them refutes (a wrong count, success over an error, an invented id) is
contradicted.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.nodes._helpers import collect_grounding_sources
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.structured_evidence import (
    StructuredFactNLI,
    extract_structured_facts,
    facts_from_tool_calls,
)
from app.intelligence.grounding_verification import verify_grounding
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="grd-1-tenant", plan=PlanTier.ENTERPRISE, api_key_id="k1")

DELETE_GOAL = "Delete the cancelled order ord-1042 from the production orders collection"
UPDATE_GOAL = "Update order ord-1042 to status shipped in the production orders collection"
INSERT_GOAL = "Insert a new order for customer acme into the production orders collection"
ROWS_GOAL = "Delete the stale rows from the production sessions table"
OBJ_ID = "66f1a2b3c4d5e6f708192a3b"
# What a tool record keeps of a long result: ``str(output)[:300]`` (unparseable).
TRUNCATED = (
    "{'acknowledged': True, 'deleted_count': 1, 'raw_result': {'n': 1, "
    "'electionId': ObjectId('7fffffff0000000000000003'), 'opTime': {'ts': Timestamp(17"
)
MCP_WRAPPED = {"content": [{"type": "text", "text": '{"deleted_count": 1}'}]}


def _tc(output: Any, *, success: bool = True, error: str = "") -> dict[str, Any]:
    return {
        "tool_name": "db_tool",
        "server_id": "srv",
        "success": success,
        "error": error,
        "output": str(output) if output not in ("", None) else "",
    }


# (goal, tool call, true claim)
TRUE_CLAIMS: list[tuple[str, dict[str, Any], str]] = [
    (DELETE_GOAL, _tc({"deleted": 1}), "The order was deleted."),
    (DELETE_GOAL, _tc({"acknowledged": True, "deleted_count": 1}), "The order was deleted."),
    (DELETE_GOAL, _tc({"deleted": 1, "error": None}), "The order ord-1042 was deleted."),
    (
        DELETE_GOAL,
        _tc({"acknowledged": True, "deleted_count": 1, "dry_run": False}),
        "The order was deleted successfully.",
    ),
    (DELETE_GOAL, _tc({"deletedCount": 1}), "1 order was removed."),
    (DELETE_GOAL, _tc({"status_code": 204, "body": ""}), "The delete request succeeded."),
    (
        DELETE_GOAL,
        _tc({"status_code": 200, "ok": True, "body": {"deleted": True}}),
        "The order was deleted.",
    ),
    (DELETE_GOAL, _tc(MCP_WRAPPED), "The order was deleted."),
    (DELETE_GOAL, _tc(TRUNCATED), "The order was deleted."),
    (DELETE_GOAL, _tc("HTTP/1.1 204 No Content"), "The order was deleted."),
    (UPDATE_GOAL, _tc({"matchedCount": 1, "modifiedCount": 1}), "The order was updated."),
    (INSERT_GOAL, _tc({"inserted_id": OBJ_ID}), f"The order was created with id {OBJ_ID}."),
    (ROWS_GOAL, _tc({"rowcount": 3}), "3 stale rows were deleted."),
    (
        DELETE_GOAL,
        _tc("", success=False, error="permission denied"),
        "The order was not deleted: the delete failed.",
    ),
    (DELETE_GOAL, _tc({"deleted": 0}), "No order was deleted."),
]

# (goal, tool call, fabricated claim)
FALSE_CLAIMS: list[tuple[str, dict[str, Any], str]] = [
    (DELETE_GOAL, _tc({"deleted": 1}), "5 orders were deleted."),
    (DELETE_GOAL, _tc({"acknowledged": True, "deleted_count": 1}), "Deleted 12 orders."),
    (DELETE_GOAL, _tc({"deleted": 0}), "The order was deleted."),
    (
        DELETE_GOAL,
        _tc({"error": "order not found", "deleted": 0}),
        "The order was deleted successfully.",
    ),
    (DELETE_GOAL, _tc({"status_code": 500, "error": "internal"}), "The delete request succeeded."),
    (DELETE_GOAL, _tc({"status_code": 404, "body": {"detail": "missing"}}), "The order was deleted."),
    (DELETE_GOAL, _tc("", success=False, error="permission denied"), "The order was deleted."),
    (DELETE_GOAL, _tc({"deleted": 1}), "The order was not deleted."),
    (UPDATE_GOAL, _tc({"matchedCount": 0, "modifiedCount": 0}), "The order was updated."),
    (
        INSERT_GOAL,
        _tc({"inserted_id": OBJ_ID}),
        "The order was created with id 77aa00bb11cc22dd33ee44ff.",
    ),
]


def _graph() -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["out"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "done"}']),
    )


async def _verify(goal: str, tc: dict[str, Any], answer: str) -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=T)
    state.cited_answer = answer
    step = StepResult(description="run the tool", status=StepStatus.COMPLETE, output=answer)
    step.tool_calls.append(tc)
    state.steps.append(step)
    out = await _graph()._node_verify({"agent_state": state, "tenant_ctx": T})
    result: AgentState = out["agent_state"]
    return result


# ── the verifier's final-answer gates (high-risk goals) ─────────────────────


@pytest.mark.parametrize(("goal", "tc", "claim"), TRUE_CLAIMS)
async def test_true_claim_over_a_structured_result_is_grounded(
    goal: str, tc: dict[str, Any], claim: str
) -> None:
    s = await _verify(goal, tc, claim)
    assert s.context.get("claim_grounding_safe") is True, s.ungrounded_claims
    assert s.context.get("final_answer_grounded") is True, s.ungrounded_claims
    assert s.verification_success is True, s.verification_feedback


@pytest.mark.parametrize(("goal", "tc", "claim"), FALSE_CLAIMS)
async def test_fabricated_claim_over_a_structured_result_still_replans(
    goal: str, tc: dict[str, Any], claim: str
) -> None:
    s = await _verify(goal, tc, claim)
    assert s.context.get("claim_grounding_safe") is False
    assert s.verification_success is False
    assert "grounding" in (s.verification_feedback or "")


async def test_an_errored_tool_call_without_output_is_evidence_of_failure() -> None:
    """A failed call used to contribute no evidence at all, which skipped the gate."""
    s = await _verify(
        DELETE_GOAL, _tc("", success=False, error="permission denied"), "The order was deleted."
    )
    assert s.context.get("claim_grounding_safe") is False
    assert s.verification_success is False


# ── facts ───────────────────────────────────────────────────────────────────


def test_counts_status_codes_and_ids_become_checkable_facts() -> None:
    facts = extract_structured_facts("{'acknowledged': True, 'deleted_count': 1}")
    assert facts.counts["deleted"] == [1]
    assert facts.successes >= 1
    rendered = facts.render()
    assert "1 deleted" in rendered
    assert "succeeded" in rendered

    http = extract_structured_facts('{"status_code": 204, "body": ""}')
    assert http.status_codes == [204]
    assert "succeeded" in http.render()

    ins = extract_structured_facts(f"{{'inserted_id': '{OBJ_ID}'}}")
    assert ins.ids == [OBJ_ID]
    assert ins.counts["inserted"] == [1]


def test_null_error_and_false_flags_are_not_failures() -> None:
    facts = extract_structured_facts("{'deleted': 1, 'error': None, 'dry_run': False}")
    assert facts.failures == []
    assert facts.counts["deleted"] == [1]


def test_error_values_and_failed_calls_are_failures() -> None:
    assert extract_structured_facts("{'error': 'order not found', 'deleted': 0}").failures
    assert extract_structured_facts('{"status_code": 503}').failures
    assert facts_from_tool_calls([_tc("", success=False, error="boom")]).failures


def test_nested_and_truncated_results_are_read() -> None:
    assert extract_structured_facts(str(MCP_WRAPPED)).counts["deleted"] == [1]
    assert extract_structured_facts(TRUNCATED).counts["deleted"] == [1]


def test_plain_prose_yields_no_facts() -> None:
    assert extract_structured_facts("The weather in Paris is mild today.").empty


def test_structured_facts_reuse_the_goal_fact_evidence_path() -> None:
    """P5-6's collect_grounding_sources also carries the normalised facts."""

    class _Step:
        tool_calls = [_tc({"acknowledged": True, "deleted_count": 1})]

    sources = collect_grounding_sources([_Step()], "", goal=DELETE_GOAL)
    assert DELETE_GOAL in sources
    assert any("1 deleted" in s for s in sources)


async def test_fact_nli_plugs_into_verify_grounding() -> None:
    evidence = ["{'acknowledged': True, 'deleted_count': 1}", DELETE_GOAL]
    facts = facts_from_tool_calls([_tc({"acknowledged": True, "deleted_count": 1})])
    verdict = await verify_grounding(
        "The order was deleted.", evidence, nli=StructuredFactNLI(facts, " ".join(evidence))
    )
    assert verdict.safe_to_emit, verdict.reasons
    wrong = await verify_grounding(
        "7 orders were deleted.", evidence, nli=StructuredFactNLI(facts, " ".join(evidence))
    )
    assert wrong.contradicted_claims == ["7 orders were deleted."]


async def test_claims_without_an_operation_fall_back_to_the_heuristic() -> None:
    evidence = ["{'deleted_count': 1}", DELETE_GOAL]
    facts = facts_from_tool_calls([_tc({"deleted_count": 1})])
    verdict = await verify_grounding(
        "The customer has a premium loyalty account in Berlin.",
        evidence,
        nli=StructuredFactNLI(facts, " ".join(evidence)),
    )
    assert not verdict.safe_to_emit
