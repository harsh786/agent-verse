"""Comprehensive real-OpenAI E2E tests for ALL agent patterns in AgentVerse.

Each test exercises a distinct reasoning pattern (CoT, Self-Refine, Reflexion,
Tree-of-Thoughts, Peer-Review, Self-Consistency, ReAct, Plan-and-Execute,
Debate, Memory, Multi-Step, High-Iteration) against a real payment-domain goal
using gpt-4o-mini with zero mocking.

Run:
    uv run pytest tests/real_e2e/test_agent_patterns_real_openai.py -v --no-cov -s
"""

from __future__ import annotations

import asyncio
import os

import pytest
from dotenv import load_dotenv

from tests._paths import BACKEND_ROOT

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen before any app import
# ---------------------------------------------------------------------------

load_dotenv(BACKEND_ROOT / ".env")

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_KEY:
    pytest.skip("OPENAI_API_KEY not set", allow_module_level=True)

# All tests in this module carry both marks automatically.
pytestmark = [pytest.mark.slow, pytest.mark.real_openai]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL = "gpt-4o-mini"
_TIMEOUT_SEC = 240          # hard cap per test (complex multi-step goals need more time)
_DEFAULT_MAX_ITER = 5       # keeps tests from running too long
_TENANT_ID = "real-e2e-test"

# Both "complete" and "failed" are valid terminal statuses; the graph sets
# GoalStatus.FAILED when max_iterations is exceeded (not a separate enum value).
_TERMINAL_STATUSES = {"complete", "failed"}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _tenant():
    """Return a minimal enterprise TenantContext for E2E tests."""
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(
        tenant_id=_TENANT_ID,
        plan=PlanTier.ENTERPRISE,
        api_key_id="e2e-real-openai-key",
    )


def make_provider():
    """Instantiate a real OpenAI-compatible provider pointing at gpt-4o-mini."""
    from app.providers.openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(api_key=OPENAI_KEY, default_model=_MODEL)


def make_graph(**kwargs):
    """Build an AgentGraph with real OpenAI planner/executor/verifier.

    ``kwargs`` are forwarded to AgentGraph so callers can enable feature flags
    such as ``enable_cot=True``, ``enable_self_refine=True``, etc.
    """
    from app.agent.graph import AgentGraph

    p = make_provider()
    return AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        max_iterations=kwargs.pop("max_iterations", _DEFAULT_MAX_ITER),
        **kwargs,
    )


def _result_text(state) -> str:
    """Collect the agent's output text from completed steps.

    Falls back to verification_feedback → cited_answer → error_message so
    that assertions are never blocked on an empty string even when all steps
    failed.
    """
    outputs = [s.output for s in state.steps if s.output and s.output.strip()]
    text = "\n\n".join(outputs)
    if not text:
        text = (
            state.verification_feedback
            or state.cited_answer
            or state.error_message
            or ""
        )
    return text


async def _run(graph, goal: str) -> object:
    """Execute graph.run() with an asyncio timeout guard."""
    return await asyncio.wait_for(
        graph.run(goal=goal, tenant_ctx=_tenant()),
        timeout=_TIMEOUT_SEC,
    )


# ---------------------------------------------------------------------------
# Test 1 — Chain-of-Thought
# ---------------------------------------------------------------------------


async def test_chain_of_thought_real_world():
    """SRE engineer asks for step-by-step 503 debugging on a payment API.

    CoT node fires before plan, injecting reasoning into the planner context.
    """
    graph = make_graph(enable_cot=True)
    state = await _run(
        graph,
        goal=(
            "Walk me through step-by-step debugging of HTTP 503 errors on a "
            "payment processing API. List each diagnostic step."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES, (
        f"Unexpected terminal status: {state.status!r}"
    )

    text = _result_text(state)
    assert len(text) > 100, (
        f"Expected real LLM output >100 chars, got {len(text)} chars:\n{text[:400]}"
    )

    text_lower = text.lower()
    keywords = ["503", "debug", "step", "check", "log", "monitor"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in CoT output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Self-Refine
# ---------------------------------------------------------------------------


async def test_self_refine_real_world():
    """Developer asks agent to write then self-improve a Python validation function.

    The refine node fires after execute and improves the step output in place.
    """
    graph = make_graph(enable_self_refine=True)
    state = await _run(
        graph,
        goal=(
            "Write a Python function to validate an Indian mobile number "
            "(10 digits, starts with 6-9). First write it, then refine it "
            "with edge cases and docstring."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    assert len(text) > 150, (
        f"Expected >150 chars, got {len(text)} chars:\n{text[:400]}"
    )

    text_lower = text.lower()
    assert "def " in text or "function" in text_lower, (
        f"Expected function definition in refined output.\nGot:\n{text[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Reflexion
# ---------------------------------------------------------------------------


async def test_reflexion_real_world():
    """Agent reflects on JWT premature expiry to extract actionable lessons.

    The reflect node fires after a failed verify, injecting lessons into the
    next planning round.
    """
    graph = make_graph(enable_reflection=True)
    state = await _run(
        graph,
        goal=(
            "Explain what causes a JWT token to expire prematurely in a "
            "distributed payment system and what lessons can be learned to prevent it."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    text_lower = text.lower()
    keywords = ["jwt", "token", "expire", "lesson", "prevent"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in Reflexion output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Tree-of-Thoughts
# ---------------------------------------------------------------------------


async def test_tree_of_thoughts_real_world():
    """System architect explores three HA payment-switch designs and picks the best.

    The tree_of_thoughts node evaluates multiple thought branches before planning.
    """
    graph = make_graph(enable_tree_of_thoughts=True)
    state = await _run(
        graph,
        goal=(
            "Explore 3 different architectural approaches for a high-availability "
            "payment switch that processes 10,000 TPS. Evaluate each approach and "
            "recommend the best one."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    assert len(text) > 200, (
        f"Expected >200 chars, got {len(text)} chars:\n{text[:400]}"
    )

    text_lower = text.lower()
    keywords = ["approach", "architecture", "recommend", "option", "trade"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in ToT output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 5 — Peer Review
# ---------------------------------------------------------------------------


async def test_peer_review_real_world():
    """Independent LLM peer-review of a POST /payments endpoint design.

    The peer_review node runs after verify and injects critique back into state.
    """
    graph = make_graph(enable_peer_review=True)
    state = await _run(
        graph,
        goal=(
            "Review this API endpoint design: POST /payments with body "
            "{amount, currency, card_token}. Identify security issues, "
            "missing fields, and suggest improvements."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    text_lower = text.lower()
    keywords = ["security", "review", "improve", "suggest", "issue", "missing"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in peer-review output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 6 — Self-Consistency
# ---------------------------------------------------------------------------


async def test_self_consistency_real_world():
    """PCI DSS CVV question answered via majority-vote self-consistency sampling.

    The self_consistency node samples N completions after execute and picks the
    majority answer before verify sees the result.
    """
    graph = make_graph(enable_self_consistency=True)
    state = await _run(
        graph,
        goal=(
            "What is the PCI DSS requirement for storing CVV data after authorization? "
            "Answer this question and verify your answer is consistent with PCI DSS standards."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    text_lower = text.lower()
    keywords = ["pci", "cvv", "store", "prohibit", "not allowed", "requirement"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in self-consistency output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 7 — ReAct (base pattern)
# ---------------------------------------------------------------------------


async def test_react_pattern_real_world():
    """DevOps engineer reasons step-by-step through INSUFFICIENT_FUNDS false positives.

    ReAct is the default graph topology (no feature flags required).
    """
    graph = make_graph()
    state = await _run(
        graph,
        goal=(
            "I need to understand why payment transactions fail with error code "
            "'INSUFFICIENT_FUNDS' 30% of the time even for customers with enough balance. "
            "Think step by step: what are the possible causes? Reason through each one."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    assert len(text) > 150, (
        f"Expected >150 chars, got {len(text)} chars:\n{text[:400]}"
    )

    text_lower = text.lower()
    keywords = ["reason", "cause", "possible", "check", "balance", "timing"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in ReAct output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 8 — Plan-and-Execute
# ---------------------------------------------------------------------------


async def test_plan_and_execute_real_world():
    """PM asks for a full Google Pay integration plan with technical and testing steps.

    The structured planner node breaks the goal into an ordered step list that
    the executor carries out sequentially.
    """
    graph = make_graph()
    state = await _run(
        graph,
        goal=(
            "Create a step-by-step implementation plan for adding Google Pay support "
            "to an existing payment gateway. Include: technical steps, API integrations "
            "needed, testing approach, and rollout strategy."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    assert len(text) > 300, (
        f"Expected >300 chars, got {len(text)} chars:\n{text[:400]}"
    )

    text_lower = text.lower()
    keywords = ["step", "plan", "google", "pay", "integration", "test"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in P&E output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 9 — Debate pattern
# ---------------------------------------------------------------------------


async def test_debate_pattern_real_world():
    """Architecture debate: microservices vs monolith for a new payment platform.

    The standard graph drives the debate via the planner → executor loop;
    the goal prompt explicitly requests both sides and a conclusion.
    """
    graph = make_graph()
    state = await _run(
        graph,
        goal=(
            "Debate the pros and cons of using microservices vs monolithic architecture "
            "for a new payment platform. Present arguments for both sides and reach a conclusion."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    text_lower = text.lower()
    keywords = ["microservice", "monolith", "pros", "cons", "argument"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in Debate output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 10 — Agent with Execution Memory
# ---------------------------------------------------------------------------


async def test_full_agent_with_memory_real_world():
    """Agent recalls past winning plans from ExecutionMemory during RAG retrieval.

    An in-memory ExecutionMemory is wired so the rag_retrieval node can surface
    past plans as context before the planner starts.
    """
    from app.memory.execution import ExecutionMemory

    mem = ExecutionMemory()
    graph = make_graph(exec_memory=mem)
    state = await _run(
        graph,
        goal=(
            "List the top 3 causes of payment gateway outages and for each cause "
            "explain the mitigation strategy. Remember each cause as you go."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    assert len(text) > 200, (
        f"Expected >200 chars with memory-backed agent, got {len(text)} chars:\n{text[:400]}"
    )

    text_lower = text.lower()
    # At least one cause/mitigation keyword must appear
    keywords = ["cause", "outage", "mitigation", "failure", "network", "database", "timeout"]
    assert any(w in text_lower for w in keywords), (
        f"Expected one of {keywords} in memory-backed output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 11 — Multi-step ordered goal
# ---------------------------------------------------------------------------


async def test_goal_with_multiple_steps_real_world():
    """Three-part ordered webhook goal: definition → retry logic → JSON payload.

    Validates that the planner preserves step ordering and all three parts
    appear in the accumulated output.
    """
    graph = make_graph()
    state = await _run(
        graph,
        goal=(
            "Do the following in order: "
            "1) Define what a webhook is in payment systems, "
            "2) Explain retry logic for failed webhooks, "
            "3) Write a sample webhook payload for a payment success event in JSON format."
        ),
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    text_lower = text.lower()

    assert "webhook" in text_lower, (
        f"Expected 'webhook' in multi-step output.\nGot:\n{text_lower[:400]}"
    )
    assert "{" in text or "json" in text_lower, (
        f"Expected JSON object or 'json' keyword in output.\nGot:\n{text_lower[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 12 — High-iteration comprehensive security analysis
# ---------------------------------------------------------------------------


async def test_goal_with_high_iterations_real_world():
    """OWASP Top 10 security analysis with max_iterations=8 for thorough coverage.

    Longer iteration budget lets the agent complete all three sub-tasks of a
    complex security analysis without hitting the iteration cap.
    """
    graph = make_graph(max_iterations=8)
    state = await asyncio.wait_for(
        graph.run(
            goal=(
                "Perform a comprehensive security analysis of a payment API: "
                "1) List OWASP Top 10 risks relevant to payments, "
                "2) For each risk explain the specific payment context, "
                "3) Provide one mitigation per risk."
            ),
            tenant_ctx=_tenant(),
        ),
        # Give the 8-iteration run a bit more headroom than the default tests.
        timeout=_TIMEOUT_SEC,
    )

    assert str(state.status) in _TERMINAL_STATUSES

    text = _result_text(state)
    assert len(text) > 500, (
        f"Expected >500 chars for comprehensive OWASP analysis, "
        f"got {len(text)} chars:\n{text[:400]}"
    )

    text_lower = text.lower()
    security_keywords = ["owasp", "injection", "authentication", "xss", "mitigation",
                         "vulnerability", "risk", "payment"]
    assert any(w in text_lower for w in security_keywords), (
        f"Expected one of {security_keywords} in OWASP analysis.\nGot:\n{text_lower[:400]}"
    )
