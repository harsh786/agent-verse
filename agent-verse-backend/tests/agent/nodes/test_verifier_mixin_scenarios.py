"""Scenario-driven tests for VerifierMixin._node_verify (app/agent/nodes/verifier_mixin.py).

VerifierMixin is the safety-critical gate that decides whether an executed step
actually succeeded and whether the agent should replan. These tests focus on the
ways verification can go WRONG rather than the happy path already covered by
tests/agent/test_graph_node_coverage.py and tests/agent/test_graph_comprehensive_coverage.py:

  - malformed / ambiguous verifier LLM output
  - verification with no step output at all
  - circuit-breaker / provider outage handling
  - LLM response cache hit & bypass semantics
  - model routing (model_router, execution-strategy fast model)
  - 3-way consensus verification + HITL escalation on disagreement
  - grounding gate: fail-open annotation vs fail-closed replan, and gate errors
  - verifier calibration recording (false-confirm rate tracking)
  - reflexion / rollback / episodic / procedural memory wiring on failure
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.governance.hitl import HITLGateway
from app.intelligence.eval_runner import EvalRunner
from app.memory.execution import ExecutionMemory
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
)
from app.providers.fake import FakeProvider
from app.rag.llm_response_cache import LLMResponseCache
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="verifier-scn-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _make_graph(verifier: FakeProvider | None = None, **kwargs) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["step output"]),
        verifier=verifier or FakeProvider(responses=['{"success": true, "reason": "done"}']),
        **kwargs,
    )


def _agent_state(goal: str = "test goal") -> AgentState:
    return AgentState(goal=goal, tenant_ctx=T)


def _completed_step(desc: str = "step 1", output: str = "did the thing") -> StepResult:
    return StepResult(description=desc, status=StepStatus.COMPLETE, output=output)


# ===========================================================================
# Malformed / ambiguous verifier output
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_garbled_non_json_response_does_not_crash_and_is_conservative() -> None:
    """A verifier LLM reply that is neither JSON nor a recognizable keyword must
    not crash verification, and per parse_verifier_verdict's text-fallback rules
    (no positive OR negative keyword present) defaults to failure — never a
    silent false-positive success.

    The response is prefixed with '{' so FakeProvider's structured-output
    auto-fill (which only kicks in for responses NOT starting with '{') does
    not mask the malformed content with a synthesized valid JSON object —
    exercising the real json.loads-failure -> text-fallback path."""
    verifier = FakeProvider(responses=["{asdkjqwe garbled unrelated text 12345"])
    graph = _make_graph(verifier=verifier)
    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    updated = result["agent_state"]
    assert updated.verification_success is False
    assert isinstance(updated.verification_feedback, str)


@pytest.mark.asyncio
async def test_verify_json_missing_success_key_falls_back_to_text_heuristics() -> None:
    """Ambiguous JSON (parses, but has no 'success' key) must not be treated as
    a JSON verdict — it should fall through to the text heuristic path rather
    than raising or defaulting to an unconditional success."""
    verifier = FakeProvider(responses=['{"note": "looks fine I guess"}'])
    graph = _make_graph(verifier=verifier)
    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    # No positive/negative keyword in the text fallback -> conservative False.
    assert result["agent_state"].verification_success is False


@pytest.mark.asyncio
async def test_verify_empty_verifier_response_handled_gracefully() -> None:
    """An empty string response (e.g. provider returned nothing) must not crash
    the verify node. supports_structured_output is disabled here so
    FakeProvider does not auto-fill the empty content with a synthesized
    JSON object, letting the real empty-response path run."""
    verifier = FakeProvider(responses=[""])
    verifier.supports_structured_output = lambda: False  # type: ignore[method-assign]
    graph = _make_graph(verifier=verifier)
    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    assert result["agent_state"].verification_success is False


# ===========================================================================
# No step output at all
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_with_zero_executed_steps() -> None:
    """Verifying a goal with no executed steps must not crash — the summary
    helper renders '(no steps executed)' and the verifier still runs."""
    verifier = FakeProvider(responses=['{"success": false, "reason": "nothing was done"}'])
    graph = _make_graph(verifier=verifier)
    agent_state = _agent_state()
    assert agent_state.steps == []

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    assert result["agent_state"].verification_success is False
    # The prompt sent to the verifier must reflect that no steps ran.
    sent = verifier.call_history[-1].messages[-1].content
    assert "(no steps executed)" in sent


# ===========================================================================
# Circuit breaker / provider outage
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_circuit_breaker_open_raises_permission_error() -> None:
    """When the circuit breaker is open (too many recent verifier failures),
    call_with_circuit_breaker raises RuntimeError, and _node_verify must convert
    this into a PermissionError rather than letting a bare RuntimeError escape —
    callers upstream route on exception type."""
    graph = _make_graph()
    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())

    with patch(
        "app.agent.nodes.verifier_mixin.call_with_circuit_breaker",
        AsyncMock(side_effect=RuntimeError("circuit open for verifier")),
    ):
        with pytest.raises(PermissionError, match="Verification unavailable"):
            await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})


@pytest.mark.asyncio
async def test_verify_timeout_is_not_swallowed() -> None:
    """A verification timeout (asyncio.wait_for inside call_with_circuit_breaker
    raises TimeoutError, not RuntimeError) is NOT caught by _node_verify's
    ``except RuntimeError`` clause and propagates to the caller. This documents
    real behavior: a slow verifier LLM call fails the whole node rather than
    being treated as an ambiguous/failed verdict."""
    graph = _make_graph()
    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())

    with patch(
        "app.agent.nodes.verifier_mixin.call_with_circuit_breaker",
        AsyncMock(side_effect=TimeoutError("verifier call timed out")),
    ):
        with pytest.raises(TimeoutError):
            await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})


# ===========================================================================
# LLM response cache
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_cache_hit_skips_provider_call_entirely() -> None:
    """A cached verifier response for the exact (system, user, model) key must
    be used instead of calling the verifier provider — the whole point of the
    cache is to avoid the LLM round-trip."""
    cache = LLMResponseCache()
    verifier = FakeProvider(responses=['{"success": false, "reason": "should not be used"}'])
    graph = _make_graph(verifier=verifier, llm_response_cache=cache)

    agent_state = _agent_state("cacheable goal")
    agent_state.steps.append(_completed_step())

    # Pre-seed the cache with the exact key _node_verify will look up: it
    # builds user = f"Goal: {goal}\nExecuted steps:\n{summary}" with an empty
    # model string (no model_router wired) and VERIFIER_SYSTEM as system.
    from app.agent.nodes._helpers import _build_verifier_summary
    from app.agent.prompts import VERIFIER_SYSTEM

    summary = _build_verifier_summary(agent_state.steps)
    user = f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}"
    await cache.set(
        system=VERIFIER_SYSTEM,
        user=user,
        model="",
        response='{"success": true, "reason": "cached verdict"}',
        tenant_id=T.tenant_id,
        task_type="verification",
    )

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    assert verifier.call_history == []  # provider never invoked
    assert result["agent_state"].verification_success is True
    assert result["agent_state"].verification_feedback == "cached verdict"


@pytest.mark.asyncio
async def test_verify_cache_bypassed_when_summary_has_tool_failure_marker() -> None:
    """should_skip_cache() must bypass the cache when the step summary contains
    a [TOOL FAILED] marker — caching a verdict for a failed-tool run would be
    unsafe/stale for a retried, potentially-different failure."""
    cache = LLMResponseCache()
    verifier = FakeProvider(responses=['{"success": false, "reason": "real call happened"}'])
    graph = _make_graph(verifier=verifier, llm_response_cache=cache)

    agent_state = _agent_state("goal with failing tool")
    failing_step = StepResult(
        description="call payment API",
        status=StepStatus.FAILED,
        output="error",
        tool_calls=[{"tool_name": "payment.charge", "success": False, "error": "card declined"}],
    )
    agent_state.steps.append(failing_step)

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    assert len(verifier.call_history) == 1  # cache was bypassed, provider called
    assert result["agent_state"].verification_feedback == "real call happened"


# ===========================================================================
# Model routing
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_execution_strategy_routes_faster_verifier_model_and_emits_event() -> None:
    """When agent_state.context['_execution_strategy'] nominates a
    verifier_model (Strategy C latency routing), it must override the
    model_router's choice and a verifier_model_routed event must be emitted."""
    events: list[dict] = []

    async def _cb(event: dict) -> None:
        events.append(event)

    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier)
    graph._event_callback = _cb

    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())

    class _Strategy:
        verifier_model = "fast-verifier-model"

    agent_state.context["_execution_strategy"] = _Strategy()

    await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    assert verifier.call_history[-1].model == "fast-verifier-model"
    assert any(
        e.get("type") == "verifier_model_routed" and e.get("model") == "fast-verifier-model"
        for e in events
    )


# ===========================================================================
# 3-way consensus verification + HITL
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_consensus_not_invoked_when_primary_verifier_succeeds() -> None:
    """Consensus is only run to double-check a FAILURE (cost-saving design) —
    it must never be invoked when the primary verifier already said success."""
    consensus = AsyncMock()
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier, consensus_verifier=consensus)

    agent_state = _agent_state("deploy to production")
    step = StepResult(
        description="deploy",
        status=StepStatus.COMPLETE,
        output="deployed",
        tool_calls=[{"tool_name": "deploy.run", "risk_level": "destructive"}],
    )
    agent_state.steps.append(step)

    await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    consensus.verify.assert_not_called()


@pytest.mark.asyncio
async def test_verify_consensus_disagreement_escalates_to_hitl_and_can_flip_verdict() -> None:
    """High-risk (destructive tool call) goal whose primary verifier says
    FAILURE gets a consensus re-check; when the consensus result flips to
    success but disagreement exists, the HITL gateway must receive an
    approval request rather than silently trusting the consensus majority."""
    hitl = HITLGateway()
    consensus_result = MagicMock()
    consensus_result.success = True
    consensus_result.majority_reason = "2 of 3 verifiers say it worked"
    consensus_result.requires_hitl = True

    consensus = MagicMock()
    consensus.verify = AsyncMock(return_value=consensus_result)

    verifier = FakeProvider(responses=['{"success": false, "reason": "looked broken", "retry": true}'])
    graph = _make_graph(verifier=verifier, consensus_verifier=consensus, hitl_gateway=hitl)

    agent_state = _agent_state("delete production database backups")
    step = StepResult(
        description="delete backups",
        status=StepStatus.COMPLETE,
        output="deleted",
        tool_calls=[{"tool_name": "db.delete", "risk_level": "destructive"}],
    )
    agent_state.steps.append(step)

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    consensus.verify.assert_called_once()
    assert result["agent_state"].verification_success is True
    assert result["agent_state"].verification_feedback == "2 of 3 verifiers say it worked"
    assert len(hitl._requests) == 1  # an approval request was actually filed


@pytest.mark.asyncio
async def test_verify_consensus_verifier_exception_does_not_crash_verification() -> None:
    """A broken/erroring consensus verifier must not crash the whole verify
    node — the primary verdict (failure) should simply stand."""
    consensus = MagicMock()
    consensus.verify = AsyncMock(side_effect=RuntimeError("consensus provider down"))

    verifier = FakeProvider(responses=['{"success": false, "reason": "primary says no", "retry": true}'])
    graph = _make_graph(verifier=verifier, consensus_verifier=consensus)

    agent_state = _agent_state("wipe the staging environment")
    step = StepResult(
        description="wipe",
        status=StepStatus.COMPLETE,
        output="done",
        tool_calls=[{"tool_name": "infra.wipe", "risk_level": "destructive"}],
    )
    agent_state.steps.append(step)

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    assert result["agent_state"].verification_success is False
    assert result["agent_state"].verification_feedback == "primary says no"


@pytest.mark.asyncio
async def test_verify_consensus_risk_lookup_handles_dict_tool_calls() -> None:
    """tool_calls can be plain dicts (e.g. deserialized from a checkpoint)
    rather than dataclasses; the risk_level lookup must handle both without
    raising AttributeError (regression guard for the _risk_of helper)."""
    consensus = AsyncMock()
    verifier = FakeProvider(responses=['{"success": false, "reason": "no", "retry": true}'])
    graph = _make_graph(verifier=verifier, consensus_verifier=consensus)

    agent_state = _agent_state("normal goal, not high risk")
    step = StepResult(
        description="step",
        status=StepStatus.COMPLETE,
        output="done",
        tool_calls=[{"tool_name": "safe.read"}],  # no risk_level key at all
    )
    agent_state.steps.append(step)

    # Must not raise despite missing risk_level.
    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
    assert result["agent_state"].verification_success is False


# ===========================================================================
# Grounding gate
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_grounding_gate_annotates_cited_answer_on_normal_goal() -> None:
    """Fail-open path: an ungrounded cited_answer on a normal-risk goal is
    annotated with grounding caveats rather than blocked, and verification
    stays successful."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "looks right"}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _agent_state("write a summary report")
    agent_state.cited_answer = "The revenue was exactly 918273 dollars and grew 445566 percent."
    step = StepResult(
        description="summarize",
        status=StepStatus.COMPLETE,
        output="summary drafted",
        tool_calls=[{"output": "the report mentions overall growth"}],
    )
    agent_state.steps.append(step)

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    assert result["agent_state"].verification_success is True
    # annotate_ungrounded() must have modified the cited answer with a caveat.
    assert result["agent_state"].cited_answer != (
        "The revenue was exactly 918273 dollars and grew 445566 percent."
    )


@pytest.mark.asyncio
async def test_verify_grounding_gate_error_is_contained_and_never_crashes() -> None:
    """The grounding gate is explicitly documented as 'must never crash
    verification' — if check_grounding raises, the verdict must still be
    returned safely."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _agent_state("normal task")
    agent_state.cited_answer = "Some answer with claim 12345."
    step = StepResult(
        description="step",
        status=StepStatus.COMPLETE,
        output="output",
        tool_calls=[{"output": "evidence text"}],
    )
    agent_state.steps.append(step)

    with patch(
        "app.agent.grounding.check_grounding",
        side_effect=RuntimeError("grounding check exploded"),
    ):
        result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    # Verification must complete despite the grounding-gate blowing up.
    assert result["agent_state"].verification_success is True


# ===========================================================================
# Verifier calibration (false-confirm rate tracking)
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_records_calibration_verdict_for_false_confirm_tracking() -> None:
    """Every verdict must be recorded to the calibration store so the
    false-confirm rate (verifier said success, actual outcome was failure)
    can be measured later — this is the core Phase-3-Track-E safety metric."""
    calibration_store = MagicMock()
    calibration_store.record_verdict = AsyncMock(return_value="rec-1")

    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])
    graph = _make_graph(verifier=verifier, calibration_store=calibration_store)

    agent_state = _agent_state("calibration goal")
    agent_state.steps.append(_completed_step())

    await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
    # Background task scheduling — allow it to run.
    await asyncio.sleep(0)

    calibration_store.record_verdict.assert_called_once()
    _, kwargs = calibration_store.record_verdict.call_args
    assert kwargs["verifier_verdict"] is True
    assert kwargs["goal_id"] == agent_state.goal_id
    assert kwargs["tenant_id"] == T.tenant_id


# ===========================================================================
# Failure-path wiring: rollback, reflexion
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_permanent_failure_rolls_back_and_stores_reflexion_lesson() -> None:
    """On a permanent failure (retry=False) with pending rollback actions, the
    rollback engine must run its compensating actions AND a reflexion lesson
    must be scheduled — losing either would mean unrecovered side effects or
    the agent repeating the same mistake next time."""
    rollback = MagicMock(spec=RollbackEngine)
    rollback.__len__ = MagicMock(return_value=1)
    rollback.rollback_all_async = AsyncMock(return_value=1)

    verifier = FakeProvider(
        responses=['{"success": false, "reason": "unrecoverable", "retry": false}']
    )
    graph = _make_graph(verifier=verifier, rollback_engine=rollback)

    agent_state = _agent_state("irreversible action")
    agent_state.steps.append(_completed_step())

    reflexion_wirer = MagicMock()
    reflexion_wirer.maybe_store_async = AsyncMock()
    with patch(
        "app.agent.reflexion_wirer.get_reflexion_wirer",
        return_value=reflexion_wirer,
    ):
        result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
        await asyncio.sleep(0)

    rollback.rollback_all_async.assert_called_once()
    assert result["agent_state"].verification_success is False
    reflexion_wirer.maybe_store_async.assert_called_once()


@pytest.mark.asyncio
async def test_verify_retryable_failure_does_not_roll_back() -> None:
    """A retryable failure (retry=True) must leave rollback actions intact —
    the agent may still complete the goal on the next iteration, and rolling
    back prematurely would destroy legitimate in-progress state."""
    rollback = MagicMock(spec=RollbackEngine)
    rollback.__len__ = MagicMock(return_value=3)
    rollback.rollback_all_async = AsyncMock()

    verifier = FakeProvider(
        responses=['{"success": false, "reason": "missing field", "retry": true}']
    )
    graph = _make_graph(verifier=verifier, rollback_engine=rollback)

    agent_state = _agent_state("retryable task")
    agent_state.steps.append(_completed_step())

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    rollback.rollback_all_async.assert_not_called()
    assert result["agent_state"].context["verification_retry"] is True
    assert result["agent_state"].verification_success is False


# ===========================================================================
# Episodic / procedural memory wiring
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_records_episodic_memory_on_both_success_and_failure() -> None:
    """Episodic memory must record the goal's experience regardless of
    success/failure — both outcomes are useful training signal."""
    episodic = MagicMock()
    episodic.record = AsyncMock()

    for verdict in ('{"success": true, "reason": "ok"}', '{"success": false, "reason": "no", "retry": true}'):
        verifier = FakeProvider(responses=[verdict])
        graph = _make_graph(verifier=verifier, episodic_memory=episodic)
        agent_state = _agent_state()
        agent_state.steps.append(_completed_step())

        await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
        await asyncio.sleep(0)

    assert episodic.record.await_count == 2


@pytest.mark.asyncio
async def test_verify_procedural_memory_learns_only_on_success() -> None:
    """Procedural skill learning must only trigger on a successful goal —
    learning a 'procedure' from a failed run would reinforce bad behavior."""
    procedural = MagicMock()
    procedural.learn = AsyncMock()

    # Failure case: learn() must not be scheduled.
    verifier_fail = FakeProvider(responses=['{"success": false, "reason": "no", "retry": true}'])
    graph_fail = _make_graph(verifier=verifier_fail, procedural_memory=procedural)
    agent_state_fail = _agent_state()
    agent_state_fail.steps.append(_completed_step())
    await graph_fail._node_verify({"agent_state": agent_state_fail, "tenant_ctx": T})
    await asyncio.sleep(0)
    procedural.learn.assert_not_called()

    # Success case: learn() must be scheduled.
    verifier_ok = FakeProvider(responses=['{"success": true, "reason": "done"}'])
    graph_ok = _make_graph(verifier=verifier_ok, procedural_memory=procedural)
    agent_state_ok = _agent_state()
    agent_state_ok.steps.append(_completed_step())
    await graph_ok._node_verify({"agent_state": agent_state_ok, "tenant_ctx": T})
    await asyncio.sleep(0)
    procedural.learn.assert_called_once()


# ===========================================================================
# Verifier context injection (N9 ContextPipeline)
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_prepends_verifier_context_when_present() -> None:
    """A sufficiently long '_verifier_context' entry (from the ContextPipeline)
    must be prepended to the verifier prompt so the LLM sees it."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())
    agent_state.context["_verifier_context"] = (
        "Prior related executions failed for the same reason repeatedly. " * 3
    )

    await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    sent = verifier.call_history[-1].messages[-1].content
    assert "[Context for verification]" in sent


def _make_runtime_profile() -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="rp-goal",
        tenant_id=T.tenant_id,
        properties=GoalProperties(raw_goal="scored goal"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


@pytest.mark.asyncio
async def test_verify_success_with_runtime_profile_computes_and_stores_scorecard() -> None:
    """When dynamic orchestration is on (the default) and a _runtime_profile is
    present in context, a real RuntimeScorecard must be computed on success and
    persisted into agent_state.context['scorecard'] — this is what feeds the
    self-improvement / regression-gate loop that watches for silently
    degrading agents."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _agent_state("scored goal")
    agent_state.steps.append(_completed_step())
    agent_state.context["_runtime_profile"] = _make_runtime_profile()

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
    await asyncio.sleep(0)

    scorecard = result["agent_state"].context.get("scorecard")
    assert scorecard is not None
    assert "overall_score" in scorecard
    assert 0.0 <= scorecard["overall_score"] <= 1.0


@pytest.mark.asyncio
async def test_verify_guardrail_blocks_leaked_secret_in_final_output() -> None:
    """A cited answer that leaks a credential-shaped secret (AWS access key
    pattern) must be redacted by the Guardrails 2.0 FINAL_OUTPUT gate rather
    than shipped to the caller — this is a hard safety requirement, not a
    soft warning."""
    from app.guardrails_v2.engine import guardrails_engine

    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _agent_state("fetch AWS credentials for the automation user")
    agent_state.cited_answer = "Sure, the access key is AKIAABCDEFGHIJKLMNOP"
    step = StepResult(
        description="fetch",
        status=StepStatus.COMPLETE,
        output="fetched",
        tool_calls=[{"output": "AKIAABCDEFGHIJKLMNOP retrieved from vault"}],
    )
    agent_state.steps.append(step)

    tenant = TenantContext(tenant_id="guardrail-secret-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
    # Reset any stale rule state for this fresh tenant id so the test is
    # deterministic regardless of test execution order.
    guardrails_engine._rules.pop(tenant.tenant_id, None)

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": tenant})

    assert result["agent_state"].cited_answer == "[Output redacted by guardrail policy]"


@pytest.mark.asyncio
async def test_verify_answer_synthesizer_overwrites_cited_answer_with_citations() -> None:
    """On success, the answer synthesizer must run and its citation-carrying
    answer must become the agent's cited_answer/provenance — losing this
    wiring would silently ship un-cited (harder to audit) answers."""
    from app.agent.synthesis import CitedAnswer, Citation

    synthesizer = MagicMock()
    synthesizer.synthesize = AsyncMock(
        return_value=CitedAnswer(
            answer="The deploy finished successfully [1].",
            citations=[Citation(text="deploy ok", source="step_0", step_index=0)],
        )
    )

    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier, answer_synthesizer=synthesizer)

    agent_state = _agent_state("deploy the app")
    agent_state.steps.append(_completed_step("deploy", "deploy ok"))

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    synthesizer.synthesize.assert_called_once()
    assert result["agent_state"].cited_answer == "The deploy finished successfully [1]."
    assert result["agent_state"].provenance == [
        {"text": "deploy ok", "source": "step_0", "step": 0}
    ]


@pytest.mark.asyncio
async def test_verify_low_score_triggers_self_optimization() -> None:
    """A goal that completes but scores below the 0.5 excellence threshold
    must trigger self-optimization — this is how the system learns from
    genuinely weak (not just failed) runs."""
    mock_eval = MagicMock(spec=EvalRunner)
    mock_scorecard = MagicMock()
    mock_scorecard.average_score.return_value = 0.2
    mock_eval.score_and_persist = AsyncMock(return_value=mock_scorecard)

    verifier = FakeProvider(responses=['{"success": true, "reason": "weak but done"}'])
    graph = _make_graph(verifier=verifier, eval_runner=mock_eval)
    graph._self_optimizer = MagicMock()

    with patch.object(
        graph, "_trigger_self_optimization", AsyncMock()
    ) as trigger:
        agent_state = _agent_state("borderline task")
        agent_state.steps.append(_completed_step())
        await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
        await asyncio.sleep(0)

    trigger.assert_called_once()


@pytest.mark.asyncio
async def test_verify_high_score_does_not_trigger_self_optimization() -> None:
    """A goal scoring at/above the 0.5 threshold must NOT trigger
    self-optimization — avoids optimization churn on genuinely good runs."""
    mock_eval = MagicMock(spec=EvalRunner)
    mock_scorecard = MagicMock()
    mock_scorecard.average_score.return_value = 0.9
    mock_eval.score_and_persist = AsyncMock(return_value=mock_scorecard)

    verifier = FakeProvider(responses=['{"success": true, "reason": "great"}'])
    graph = _make_graph(verifier=verifier, eval_runner=mock_eval)
    graph._self_optimizer = MagicMock()

    with patch.object(
        graph, "_trigger_self_optimization", AsyncMock()
    ) as trigger:
        agent_state = _agent_state("great task")
        agent_state.steps.append(_completed_step())
        await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
        await asyncio.sleep(0)

    trigger.assert_not_called()


@pytest.mark.asyncio
async def test_verify_success_persists_memory_async_and_computes_latency() -> None:
    """When a DB session factory is wired (production mode), successful-goal
    memory persistence must also schedule the async DB-backed variants
    (record_async / extract_from_goal_async) — not just the in-memory sync
    update — and latency must be computed from the goal start timestamp for
    the runtime scorecard."""
    exec_mem = MagicMock(spec=ExecutionMemory)
    exec_mem.record = MagicMock()
    exec_mem.record_async = AsyncMock()

    ltm = MagicMock()
    ltm.extract_from_goal = MagicMock()
    ltm.extract_from_goal_async = AsyncMock()

    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier, exec_memory=exec_mem, long_term_memory=ltm)
    graph._db_session_factory = MagicMock()  # any truthy sentinel

    agent_state = _agent_state("persisted goal")
    agent_state.plan = ["step 1"]
    agent_state.steps.append(_completed_step())
    agent_state.context["_goal_start_ms"] = 1.0  # far in the past -> positive latency

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})
    await asyncio.sleep(0)

    exec_mem.record_async.assert_called_once()
    ltm.extract_from_goal_async.assert_called_once()
    assert result["agent_state"].context["_latency_ms"] > 0


@pytest.mark.asyncio
async def test_verify_ignores_short_verifier_context() -> None:
    """A short (<=50 char) _verifier_context must NOT be prepended — it exists
    to avoid polluting the prompt with near-empty/noise context."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _agent_state()
    agent_state.steps.append(_completed_step())
    agent_state.context["_verifier_context"] = "too short"

    await graph._node_verify({"agent_state": agent_state, "tenant_ctx": T})

    sent = verifier.call_history[-1].messages[-1].content
    assert "[Context for verification]" not in sent
