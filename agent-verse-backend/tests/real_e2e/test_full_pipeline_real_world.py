"""Real-world E2E pipeline tests using live OpenAI (gpt-4o-mini).

12 domain-specific goal scenarios that exercise the full AgentGraph pipeline:
initialize → rag_retrieval → plan → execute → [refine] → verify → complete/replan.

NO mocking — every LLM call hits the real OpenAI API.

Run with:
    uv run pytest tests/real_e2e/test_full_pipeline_real_world.py -v --no-cov -s
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from tests._paths import BACKEND_ROOT

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen before any app imports
# ---------------------------------------------------------------------------

load_dotenv(BACKEND_ROOT / ".env")

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_KEY:
    pytest.skip("OPENAI_API_KEY not set", allow_module_level=True)

pytestmark = [pytest.mark.slow, pytest.mark.real_openai]

# ---------------------------------------------------------------------------
# Shared tenant context
# ---------------------------------------------------------------------------

from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="e2e-pipeline-real-world",
    plan=PlanTier.ENTERPRISE,
    api_key_id="real-e2e-key-1",
)

# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_provider():
    """Return a real OpenAI provider using gpt-4o-mini."""
    from app.providers.openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(api_key=OPENAI_KEY, default_model="gpt-4o-mini")


def make_graph(**kwargs):
    """Return an AgentGraph wired with real OpenAI for planner / executor / verifier."""
    from app.agent.graph import AgentGraph

    p = make_provider()
    return AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        max_iterations=kwargs.pop("max_iterations", 6),
        **kwargs,
    )


def get_output(state) -> str:
    """Return the combined text output from a completed AgentState.

    Priority:
      1. ``cited_answer`` — synthesized answer when an AnswerSynthesizer is wired.
      2. Concatenated ``steps[*].output`` — raw execution outputs (always present).
    """
    if state.cited_answer and state.cited_answer.strip():
        return state.cited_answer
    return " ".join(s.output for s in state.steps if s.output)


def is_done(state) -> bool:
    """True when the goal reached a terminal state (complete OR failed)."""
    from app.agent.state import GoalStatus

    return state.status in (GoalStatus.COMPLETE, GoalStatus.FAILED)


# ---------------------------------------------------------------------------
# 1. DevOps / SRE — incident root-cause analysis
# ---------------------------------------------------------------------------


async def test_devops_incident_analysis():
    """Agent performs systematic root-cause analysis of a payment-service timeout."""
    goal = (
        "A payment service is returning HTTP 504 timeouts. The error started 20 minutes ago. "
        "Perform a systematic root cause analysis: check possible causes (database, upstream "
        "API, network, code), rank them by likelihood, and provide a remediation checklist."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 300, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["timeout", "database", "check", "remediation", "cause"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 2. Compliance / Legal — payment gateway regulatory requirements
# ---------------------------------------------------------------------------


async def test_payment_compliance_advisor():
    """Agent lists RBI, NPCI, PCI DSS, and data-localisation requirements for India."""
    goal = (
        "I am building a payment gateway for Indian merchants. List all the regulatory "
        "requirements I must comply with: RBI guidelines, NPCI mandates, PCI DSS "
        "requirements, and data localisation rules. For each requirement state: what it is, "
        "who it applies to, and the penalty for non-compliance."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 400, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["rbi", "pci", "npci", "compliance", "requirement"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 3. Software Engineering — code generation + review
# ---------------------------------------------------------------------------


async def test_code_generation_and_review():
    """Agent generates a typed Python validation function with docstring and tests."""
    goal = (
        "Write a Python function called `validate_payment_request` that validates: "
        "1) amount is positive float, 2) currency is 3-letter ISO code, "
        "3) card_token is non-empty string, 4) merchant_id is non-empty string. "
        "Include type hints, docstring, and unit test examples."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    # The agent describes or writes the function — check for the name in any form
    assert "validate_payment_request" in output, (
        f"Function name not found in output. Output: {output[:400]}"
    )
    assert "amount" in output, (
        f"'amount' not found in output. Output: {output[:400]}"
    )


# ---------------------------------------------------------------------------
# 4. API Design — refund management REST API
# ---------------------------------------------------------------------------


async def test_api_design_goal():
    """Agent designs a RESTful refund-management API with endpoints, schemas, error codes."""
    goal = (
        "Design a RESTful API for a refund management system. Include: endpoints, "
        "HTTP methods, request/response schemas, error codes, and rate limiting strategy. "
        "The API must handle partial refunds, bulk refunds, and refund status tracking."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    keywords = ["POST", "GET", "endpoint", "refund", "schema", "200", "400"]
    assert any(kw in output for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:400]}"
    )


# ---------------------------------------------------------------------------
# 5. Data Analytics — payment method analysis
# ---------------------------------------------------------------------------


async def test_data_analysis_goal():
    """Agent analyses a payment-data summary and produces actionable recommendations."""
    goal = (
        "Analyze this payment data summary: "
        "Credit cards: 1.2M transactions, 87% success rate, avg ticket Rs 4500. "
        "UPI: 3.4M transactions, 94% success rate, avg ticket Rs 850. "
        "Debit cards: 900K transactions, 82% success rate, avg ticket Rs 2100. "
        "Provide: key insights, which method to prioritize, "
        "recommendations to improve the lowest performer."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 200, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["upi", "credit", "debit", "recommend", "insight", "success"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 6. Security — payment-flow audit
# ---------------------------------------------------------------------------


async def test_security_audit_goal():
    """Agent identifies OWASP risks in a naive payment flow and proposes a secure alternative."""
    goal = (
        "Perform a security audit of this payment flow: "
        "User enters card details → sent to frontend → JavaScript sends to backend "
        "→ backend calls payment gateway. "
        "Identify all security vulnerabilities, OWASP risks applicable, "
        "and provide a secure implementation alternative."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    lower = output.lower()
    keywords = ["vulnerability", "owasp", "secure", "risk", "ssl", "pci", "encrypt"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 7. Developer Experience — webhook integration docs
# ---------------------------------------------------------------------------


async def test_technical_documentation_goal():
    """Agent writes complete webhook integration documentation including HMAC verification."""
    goal = (
        "Write technical documentation for a webhook integration. Include: "
        "what webhooks are, how to verify webhook signatures using HMAC-SHA256, "
        "sample code in Python for signature verification, "
        "common webhook events for a payment system, and troubleshooting guide."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 400, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["webhook", "hmac", "signature", "python", "verify"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 8. Knowledge Retrieval — RAG-powered goal with seeded KnowledgeStore
# ---------------------------------------------------------------------------


async def test_rag_powered_goal():
    """Agent uses seeded KnowledgeStore docs to answer refund-timeline / chargeback queries."""
    from app.rag.models import Chunk, KnowledgeCollection
    from app.rag.store import KnowledgeStore

    # Seed in-memory knowledge store with synthetic payment docs
    store = KnowledgeStore()
    collection = KnowledgeCollection(
        name="payment-docs",
        description="Internal payment system documentation",
    )
    store.create_collection(collection, tenant_ctx=TENANT)

    payment_docs = [
        (
            "UPI Payment Failure Refund Policy: "
            "For UPI payment failures, the refund timeline is T+1 business day for "
            "auto-reversal. Manual refund requests are processed within T+3 days. "
            "The RBI mandate requires refunds to be credited within 5 business days."
        ),
        (
            "Chargeback Reason Codes: "
            "CB-001: Unauthorized transaction. "
            "CB-002: Item not received. "
            "CB-003: Duplicate transaction. "
            "CB-004: Credit not processed. "
            "CB-005: Subscription cancelled. "
            "All chargeback disputes must be responded to within 7 days of notification."
        ),
        (
            "Payment Gateway SLA: "
            "Standard processing: 2-3 seconds. "
            "Refund processing: T+1 to T+5 business days depending on bank. "
            "UPI reversals: instant to T+1 business day. "
            "Net banking refunds: T+3 to T+5 business days."
        ),
    ]

    # Use random embeddings — trigram search still finds keywords
    for i, content in enumerate(payment_docs):
        import random

        random.seed(i)
        embedding = [random.gauss(0, 1) for _ in range(16)]
        chunk = Chunk(
            document_id=f"doc-{i}",
            content=content,
            embedding=embedding,
            chunk_index=i,
        )
        store.ingest_chunk(chunk, collection_id=collection.collection_id, tenant_ctx=TENANT)

    goal = (
        "Based on our payment system documentation, what is the refund timeline for "
        "UPI failures and what are the chargeback reason codes we need to handle?"
    )
    graph = make_graph(knowledge_store=store)
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    lower = output.lower()
    keywords = ["upi", "refund", "chargeback", "timeline"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:400]}"
    )


# ---------------------------------------------------------------------------
# 9. Engineering — payment retry strategy trade-offs
# ---------------------------------------------------------------------------


async def test_multi_step_technical_analysis():
    """Agent analyses three retry strategies with pros, cons, and implementation guidance."""
    goal = (
        "Analyze the trade-offs between these three payment retry strategies: "
        "1) Immediate retry (retry instantly on failure), "
        "2) Exponential backoff (1s, 2s, 4s, 8s delays), "
        "3) Smart retry (retry based on decline code). "
        "For each: pros, cons, when to use, and implementation complexity."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 350, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["retry", "exponential", "backoff", "decline", "strategy"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 10. Product Management — subscription management PRD
# ---------------------------------------------------------------------------


async def test_product_requirements_goal():
    """Agent writes a full PRD for a Subscription Management feature."""
    goal = (
        "Write product requirements for a 'Subscription Management' feature in a "
        "payment gateway. Include: user stories for 3 personas (merchant, customer, admin), "
        "acceptance criteria, edge cases (failed payments, card expiry, plan upgrades), "
        "and API requirements summary."
    )
    graph = make_graph()
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 400, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["subscription", "user story", "acceptance", "merchant", "customer"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 11. Self-improving agent with ExecutionMemory
# ---------------------------------------------------------------------------


async def test_self_refine_with_memory_real():
    """Agent uses self-refine + execution memory to produce a polished EMI explanation."""
    from app.memory.execution import ExecutionMemory

    mem = ExecutionMemory()
    graph = make_graph(enable_self_refine=True, exec_memory=mem)

    goal = (
        "Write a concise explanation of how EMI (Equated Monthly Installment) works for "
        "credit card purchases. First write a basic explanation, then refine it to be more "
        "precise and include a worked example with Rs 12,000 purchase over 3 months at "
        "15% annual interest."
    )
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 200, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["emi", "monthly", "interest", "installment"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )


# ---------------------------------------------------------------------------
# 12. System Architecture — Tree-of-Thoughts database schema design
# ---------------------------------------------------------------------------


async def test_tree_of_thoughts_architecture_decision():
    """Agent uses Tree-of-Thoughts to explore and select the best payment-DB schema."""
    graph = make_graph(enable_tree_of_thoughts=True, max_iterations=8)

    goal = (
        "I need to design the database schema for a payment transaction system that must: "
        "handle 50,000 TPS, store 3 years of transaction history, "
        "support complex queries for reconciliation, "
        "and comply with PCI DSS data retention rules. "
        "Explore multiple schema approaches and select the optimal design."
    )
    result = await graph.run(goal=goal, tenant_ctx=TENANT)

    assert is_done(result), f"Unexpected status: {result.status}"
    output = get_output(result)
    assert len(output) > 400, f"Output too short ({len(output)} chars): {output[:200]}"
    lower = output.lower()
    keywords = ["schema", "database", "transaction", "design", "pci", "table"]
    assert any(kw in lower for kw in keywords), (
        f"None of {keywords} found in output. Output: {output[:300]}"
    )
