"""ORG-25: every coordination pattern has a driver that populates its read model."""

from __future__ import annotations

from typing import Any

import pytest

from app.coordination.pattern_runs.service import (
    PatternRuntimeUnavailableError,
    PatternSessionError,
    UnknownPatternError,
)
from tests.coordination.pattern_run_support import (
    TENANT,
    ScriptedProvider,
    active_session,
    pattern_state,
    service,
)


async def _run(state: Any, session_id: str, pattern: str, key: str = "run-1", **kw: Any) -> Any:
    return await service(state).run(
        TENANT,
        session_id,
        pattern,
        objective=kw.pop("objective", "Write a short market report"),
        participants=kw.pop("participants", ()),
        max_rounds=kw.pop("max_rounds", 6),
        options=kw.pop("options", {}),
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_magentic_run_writes_the_progress_ledger() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "magentic")
    assert result["phase"] == "completed"
    assert result["safe_output"] == "FINAL REPORT"
    ledger = await state.progress_ledger_repository.current("tenant", session_id)
    assert ledger is not None
    assert set(ledger.completed_work) == {"research", "draft"}
    assert ledger.satisfied_criteria == ("report written",)
    # Work spread across participants (least-loaded selection).
    assert len(set(ledger.assignment_history)) > 1
    revisions = await state.progress_ledger_repository.revisions("tenant", session_id)
    assert len(revisions) >= 3


@pytest.mark.asyncio
async def test_magentic_reset_exhaustion_issues_human_review_and_approval_resumes() -> None:
    provider = ScriptedProvider(magentic_completes=False)
    state = pattern_state(provider)
    session_id = await active_session(state)
    waiting = await _run(state, session_id, "magentic")
    assert waiting["phase"] == "awaiting_human"
    review = waiting["human_review"]
    assert review["reason"] == "reset_exhausted"
    decision = await state.magentic_human_review.submit(
        "tenant", session_id, token=review["token"], approved=True, safe_note="add data"
    )
    assert decision.approved
    provider.magentic_completes = True
    resumed = await service(state).apply_magentic_review(TENANT, session_id, approved=True)
    assert resumed is not None
    assert resumed["phase"] == "completed"
    assert resumed["safe_output"] == "FINAL REPORT"
    with pytest.raises(PermissionError):  # the token was one-time
        await state.magentic_human_review.submit(
            "tenant", session_id, token=review["token"], approved=True, safe_note=""
        )


@pytest.mark.asyncio
async def test_magentic_rejection_closes_the_run() -> None:
    state = pattern_state(ScriptedProvider(magentic_completes=False))
    session_id = await active_session(state)
    await _run(state, session_id, "magentic")
    closed = await service(state).apply_magentic_review(TENANT, session_id, approved=False)
    assert closed is not None
    assert closed["phase"] == "failed"
    assert closed["terminal_reason"] == "human_rejected"


@pytest.mark.asyncio
async def test_moa_run_persists_layers_proposals_and_aggregate() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "mixture_of_agents")
    assert result["phase"] == "completed"
    assert result["safe_output"] == "AGGREGATED ANSWER"
    layers = await state.moa_repository.layers("tenant", session_id)
    assert [layer.layer_index for layer in layers] == [0, 1]
    proposals = await state.moa_repository.proposals("tenant", layers[0].strategy_execution_id, 0)
    assert len(proposals) == 3
    assert all(item.valid for item in proposals)
    assert len({layer.layer_id for layer in layers}) == 2


@pytest.mark.asyncio
async def test_camel_run_records_dialogue_and_state() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "camel")
    assert result["phase"] == "completed"
    assert result["safe_output"] == "CAMEL SOLUTION"
    records = await state.camel_repository.list_session("tenant", session_id)
    assert len(records) == 1
    assert records[0].state["checkpoint"]["turn_count"] == 2
    transcript = await state.transcript_service.page("tenant", session_id)
    assert [m.sender_agent_id for m in transcript][:2] == ["ai_user", "ai_assistant"]


@pytest.mark.asyncio
async def test_generative_run_observes_reflects_and_completes() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "generative_agents")
    assert result["phase"] == "completed"
    assert result["safe_output"] == "GENERATIVE SUMMARY"
    view = result["view"]
    assert len(view["observations"]) == 3
    assert view["reflections"][0]["safe_conclusion"] == "Markets reward preparation."


@pytest.mark.asyncio
async def test_swarm_run_produces_claims_and_gossip_topology() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "decentralized_swarm")
    assert result["phase"] == "completed"
    assert result["safe_output"] == "SWARM ANSWER"
    view = result["view"]
    assert {item["state"] for item in view["work_items"]} == {"completed"}
    assert all(item["fencing_token"] >= 1 for item in view["work_items"])
    kinds = {edge["message_type"] for edge in view["edges"]}
    assert {"advertisement", "claim", "result"} <= kinds
    assert all(edge["source"] != edge["target"] for edge in view["edges"])


@pytest.mark.asyncio
async def test_auction_run_seals_unseals_allocates_and_settles() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "market_auction")
    assert result["phase"] == "completed"
    assert result["safe_output"] == "DELIVERED WORK"
    view = result["view"]
    assert view["allocation"]["winner_id"] == "bidder-2"
    assert view["allocation"]["state"] == "settled"
    assert [bid["bidder_id"] for bid in view["bids"]][0] == "bidder-2"
    assert await state.auction_bid_inbox.count("tenant", session_id) == 3


@pytest.mark.asyncio
async def test_pattern_read_models_do_not_leak_across_patterns_or_tenants() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    await _run(state, session_id, "camel")
    assert await state.swarm_repository.list_session("tenant", session_id) == ()
    assert await state.camel_repository.list_session("other-tenant", session_id) == ()
    runs = await service(state).list_runs("tenant", session_id, "camel")
    assert [run["phase"] for run in runs] == ["completed"]


@pytest.mark.asyncio
async def test_same_idempotency_key_replays_without_new_llm_calls() -> None:
    provider = ScriptedProvider()
    state = pattern_state(provider)
    session_id = await active_session(state)
    first = await _run(state, session_id, "camel")
    calls = len(provider.prompts)
    again = await _run(state, session_id, "camel")
    assert again["replayed"] is True
    assert again["execution_id"] == first["execution_id"]
    assert len(provider.prompts) == calls
    with pytest.raises(PatternSessionError, match="different"):
        await _run(state, session_id, "camel", objective="something else")


@pytest.mark.asyncio
async def test_admission_fails_closed() -> None:
    state = pattern_state(None)
    session_id = await active_session(state)
    with pytest.raises(PatternRuntimeUnavailableError, match="LLM provider"):
        await _run(state, session_id, "camel")
    state.llm_provider = ScriptedProvider()
    pending = await active_session(state, start=False)
    with pytest.raises(PatternSessionError, match="pending"):
        await _run(state, pending, "camel")
    with pytest.raises(KeyError):
        await _run(state, "missing-session", "camel")
    with pytest.raises(UnknownPatternError):
        await _run(state, session_id, "nonsense")
