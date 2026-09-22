"""D-4/D-5 wiring: the verifier's final-answer gate must run the composed
NLI claim + attribution check (``verify_grounding``), not only the heuristic
keyword grounding.

``app.intelligence.grounding_verification.verify_grounding`` composes the three
previously-dead components — ``ClaimDecomposer`` (D-4), ``NLIChecker``, and
``AttributionVerifier`` (D-5). It was orphaned (only its own test imported it).
These tests prove (a) the composed pipeline produces the right verdict
deterministically and (b) the verifier module actually references it so the
disconnect cannot silently return.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.intelligence.grounding_verification import verify_grounding


@pytest.mark.asyncio
async def test_verify_grounding_passes_grounded_answer() -> None:
    verdict = await verify_grounding(
        "The revenue rose by twelve percent.",
        ["Quarterly revenue rose by twelve percent across all regions."],
    )
    assert verdict.safe_to_emit is True
    assert verdict.contradicted_claims == []
    assert verdict.claim_score >= 0.7


@pytest.mark.asyncio
async def test_verify_grounding_flags_fabricated_answer() -> None:
    verdict = await verify_grounding(
        "The CEO is named Zxqwub Fakename and revenue fell 90 percent.",
        ["Quarterly revenue rose by twelve percent across all regions."],
    )
    assert verdict.safe_to_emit is False
    assert verdict.unsupported_claims


def test_verifier_mixin_wires_verify_grounding() -> None:
    """The verifier node must call verify_grounding on the live path (D-4/D-5)."""
    from app.agent.nodes.verifier_mixin import VerifierMixin

    source = inspect.getsource(VerifierMixin._node_verify)
    assert "verify_grounding" in source, (
        "verify_grounding is not called from _node_verify — the NLI claim + "
        "attribution grounding check (D-4/D-5) is disconnected from the live path"
    )


# ===========================================================================
# Live wiring through the real _node_verify path.
#
# The tests above only prove verify_grounding works in isolation and that its
# name appears in the source. They never actually drive it through
# VerifierMixin._node_verify, so the fail-closed/fail-open branching around it
# (lines ~313-355 of verifier_mixin.py) was previously untested end-to-end.
# These tests build a real answer/evidence pair that the keyword-based
# check_grounding gate (gate 1: no numeric/id/date/url/quoted claims present)
# trivially passes, so any failure below can only come from the NLI gate.
# ===========================================================================

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_T = TenantContext(tenant_id="verifier-grounding-wiring-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")

# A reworded contradiction: shares enough content words with the evidence to
# be recognized as "about the same thing", but asserts the opposite polarity
# (negation mismatch) — exactly the class of fabrication the keyword gate
# (which only looks for numbers/ids/dates/urls/quotes) cannot see, but the
# NLI heuristic (token-overlap + negation-mismatch) catches deterministically.
_CONTRADICTING_ANSWER = "The deployment was successful and encountered no issues."
_CONTRADICTING_EVIDENCE = "The deployment failed and encountered multiple issues."


def _grounding_graph() -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["step output"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "looks done"}']),
    )


def _state_with_contradiction(goal: str) -> AgentState:
    agent_state = AgentState(goal=goal, tenant_ctx=_T)
    agent_state.cited_answer = _CONTRADICTING_ANSWER
    step = StepResult(
        description="deploy",
        status=StepStatus.COMPLETE,
        output="deployed",
        tool_calls=[{"output": _CONTRADICTING_EVIDENCE}],
    )
    agent_state.steps.append(step)
    return agent_state


@pytest.mark.asyncio
async def test_nli_grounding_failure_fails_closed_on_high_risk_goal_mid_verification() -> None:
    """A high-risk goal (contains 'deploy') whose cited answer semantically
    contradicts its evidence — with no numeric/id claim for the keyword gate
    to catch — must still be caught by the NLI verify_grounding gate and flip
    success -> False, retry -> True: a grounding failure discovered mid
    verification must fail closed, not silently pass through."""
    graph = _grounding_graph()
    agent_state = _state_with_contradiction("deploy the release to production")

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": _T})

    updated = result["agent_state"]
    assert updated.context["claim_grounding_safe"] is False
    assert updated.verification_success is False
    assert updated.context["verification_retry"] is True
    assert "NLI claim/attribution" in updated.verification_feedback


@pytest.mark.asyncio
async def test_nli_grounding_failure_fails_open_on_normal_risk_goal() -> None:
    """The identical contradiction on a NORMAL-risk goal must be recorded
    (claim_grounding_safe False, a claim_grounding_warning emitted) but must
    NOT flip the verdict — fail-open is the documented behavior off the
    high-risk path, contrasting with the high-risk case above."""
    graph = _grounding_graph()
    agent_state = _state_with_contradiction("write an internal status update")

    result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": _T})

    updated = result["agent_state"]
    assert updated.context["claim_grounding_safe"] is False
    assert updated.verification_success is True


@pytest.mark.asyncio
async def test_transient_grounding_check_error_fails_open_and_skips_retry() -> None:
    """A transient error raised BY verify_grounding itself (e.g. a dependency
    hiccup in the underlying NLI checker) is swallowed by the gate's own
    try/except — documented as 'must never crash verification' — so the
    verifier is left as-is rather than being driven into a retry. This is the
    deliberate contrast with a genuine (permanent) unsafe verdict, which DOES
    flip success and set retry=True (see the fail-closed test above): an
    infra error must never masquerade as a grounding failure."""
    graph = _grounding_graph()
    agent_state = _state_with_contradiction("deploy the release to production")

    with patch(
        "app.intelligence.grounding_verification.verify_grounding",
        AsyncMock(side_effect=ConnectionError("transient NLI backend hiccup")),
    ):
        result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": _T})

    updated = result["agent_state"]
    assert updated.verification_success is True  # unaffected by the transient error
    assert "claim_grounding_safe" not in updated.context  # gate never completed


@pytest.mark.asyncio
async def test_grounding_check_timeout_during_verification_does_not_crash() -> None:
    """A hard timeout inside verify_grounding (the NLI backend hangs) must not
    crash the whole verification node — TimeoutError is caught by the same
    broad 'grounding gate must never crash' contract as any other exception,
    so a slow/hung grounding dependency degrades gracefully instead of
    breaking verification for the whole goal."""
    graph = _grounding_graph()
    agent_state = _state_with_contradiction("deploy the release to production")

    with patch(
        "app.intelligence.grounding_verification.verify_grounding",
        AsyncMock(side_effect=TimeoutError("NLI backend timed out")),
    ):
        result = await graph._node_verify({"agent_state": agent_state, "tenant_ctx": _T})

    assert result["agent_state"].verification_success is True
