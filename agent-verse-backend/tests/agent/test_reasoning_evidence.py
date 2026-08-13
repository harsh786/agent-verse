from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agent.graph import AgentGraph
from app.agent.patterns.peer_review import PeerReviewPattern
from app.agent.patterns.self_consistency import SelfConsistencyPattern
from app.agent.reasoning_evidence import ReasoningEvidence, opaque_evidence_id
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="tenant-1",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="key-1",
)


def test_reasoning_evidence_rejects_private_or_free_form_fields() -> None:
    evidence = ReasoningEvidence(
        strategy_id="self_consistency",
        status="completed",
        call_count=3,
        quorum=2,
    )
    serialized = evidence.model_dump_json()
    for forbidden in ("thoughts", "chain_of_thought", "cot_reasoning", "raw_critique"):
        assert forbidden not in serialized
        with pytest.raises(ValidationError):
            ReasoningEvidence.model_validate(
                {**evidence.model_dump(), forbidden: "private"}
            )


async def test_self_consistency_enforces_call_limit_and_emits_aggregate_evidence() -> None:
    provider = FakeProvider(responses=["answer", "answer", "unused"])
    execution = await SelfConsistencyPattern(n_samples=3).execute_with_evidence(
        prompt="solve",
        provider=provider,
        call_limit=2,
    )

    assert execution.result == "answer"
    assert execution.evidence.call_count == 2
    assert execution.evidence.quorum == 2
    assert execution.evidence.status == "exhausted"
    assert execution.evidence.limit_reason == "call_limit"


async def test_peer_review_fails_closed_without_independent_identity() -> None:
    execution = await PeerReviewPattern().execute_with_evidence(
        output="result",
        goal="goal",
        provider=FakeProvider(responses=[]),
        producer_identity="provider/model-a",
        reviewer_identity="provider/model-a",
    )

    assert execution.evidence.status == "rejected"
    assert execution.evidence.call_count == 0
    assert execution.evidence.limit_reason == "reviewer_not_independent"


def test_private_candidates_are_replaced_by_stable_opaque_ids() -> None:
    identifier = opaque_evidence_id("secret private candidate")

    assert identifier.startswith("sha256:")
    assert "secret" not in identifier
    assert identifier == opaque_evidence_id("secret private candidate")


async def test_chain_of_thought_node_discards_private_provider_content() -> None:
    private = "PRIVATE THOUGHT: api_key=top-secret reasoning"
    planner = FakeProvider(responses=[private])
    graph = AgentGraph(
        planner=planner,
        executor=FakeProvider(),
        verifier=FakeProvider(),
        enable_cot=True,
    )
    state = AgentState(goal="goal", tenant_ctx=TENANT)

    result = await graph._node_think({"agent_state": state})
    serialized = json.dumps(result, default=str)

    assert "cot_reasoning" not in result
    assert private not in serialized
    assert "top-secret" not in serialized
    assert result["reasoning_evidence"]["strategy_id"] == "chain_of_thought"
