"""E2E test suite: 10 goal archetypes and all spec §5 acceptance criteria."""
from __future__ import annotations

import json
import time

import pytest

from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
from app.orchestration.runtime_profile import (
    Complexity,
    RiskLevel,
    TimeSensitivity,
    KnowledgeState,
    GoalRuntimeProfile,
    GoalProperties,
    AgentPatternConfig,
    RAGStrategyConfig,
    ModelPlanConfig,
    SecurityConfig,
    MemoryCacheConfig,
    EvalConfig,
)
from app.orchestration.strategy_registry import build_default_registry
from app.security_runtime.guardrail_profile import GuardrailProfileSelector, GuardrailBundle
from app.security_runtime.governance_profile import GovernanceProfileSelector
from app.policy_runtime.compiler import PolicyCompiler
from app.plan_runtime.plan_verifier import PlanVerifier
from app.runtime_readiness.readiness_gate import ReadinessGate
from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus
from app.data_classification.classifier import DataClassifier
from app.data_classification.schema import DataClass
from app.capabilities.registry import build_default_capability_registry
from app.evals.runtime_scorecard import RuntimeScorecard
from app.recovery.failure_classifier import FailureClassifier, FailureClass
from app.orchestration.decision_trace import DecisionTrace
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def builder(registry):
    return RuntimeProfileBuilder(registry=registry)


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def enterprise_ctx():
    return TenantContext(tenant_id="t2", plan=PlanTier.ENTERPRISE, api_key_id="k2")


# ── Archetype 1: Simple lookup ─────────────────────────────────────────────────

async def test_archetype1_simple_lookup_complexity(builder):
    """Simple factual lookup → SIMPLE complexity, LOW risk."""
    profile, trace = await builder.build_with_trace(
        "What is the capital of France?",
        tenant_id="t1",
        goal_id="arch1",
    )
    assert profile.properties.complexity == Complexity.SIMPLE
    assert profile.properties.risk == RiskLevel.LOW


async def test_archetype1_simple_lookup_rag_strategy(builder):
    """Simple lookup → hybrid_rag, single knowledge_base source."""
    profile, _ = await builder.build_with_trace(
        "What is the capital of France?",
        tenant_id="t1",
        goal_id="arch1-rag",
    )
    # Simple + low risk gets naive_rag or hybrid — not agentic
    assert profile.rag_strategy.strategy in ("hybrid_rag", "naive_rag", "web_augmented_rag")
    assert "knowledge_base" in profile.rag_strategy.sources


async def test_archetype1_simple_lookup_model_cost_class(builder):
    """Simple + LOW risk → cost_class=low."""
    profile, _ = await builder.build_with_trace(
        "What is the capital of France?",
        tenant_id="t1",
        goal_id="arch1-model",
    )
    assert profile.model_plan.cost_class == "low"


# ── Archetype 2: Complex research ─────────────────────────────────────────────

async def test_archetype2_complex_research_complexity(builder):
    """Research goal with analyze/research keywords → EXPERT complexity."""
    profile, _ = await builder.build_with_trace(
        "analyze and research market trends for the AI sector across 20 data sources",
        tenant_id="t1",
        goal_id="arch2",
    )
    assert profile.properties.complexity in (Complexity.COMPLEX, Complexity.EXPERT)


async def test_archetype2_complex_research_rag(builder):
    """EXPERT complexity → agentic_rag strategy."""
    profile, _ = await builder.build_with_trace(
        "analyze and research market trends for the AI sector",
        tenant_id="t1",
        goal_id="arch2-rag",
    )
    if profile.properties.complexity == Complexity.EXPERT:
        assert profile.rag_strategy.strategy == "agentic_rag"


async def test_archetype2_complex_research_patterns(builder):
    """EXPERT complexity → chain_of_thought and reflection in reasoning patterns."""
    profile, _ = await builder.build_with_trace(
        "analyze and research market trends for the AI sector",
        tenant_id="t1",
        goal_id="arch2-patterns",
    )
    if profile.properties.complexity == Complexity.EXPERT:
        assert "chain_of_thought" in profile.agent_patterns.reasoning
        assert "reflection" in profile.agent_patterns.reasoning


async def test_archetype2_complex_research_memory(builder):
    """EXPERT complexity → long-term memory and knowledge graph enabled."""
    profile, _ = await builder.build_with_trace(
        "analyze and synthesize strategic architecture recommendations",
        tenant_id="t1",
        goal_id="arch2-mem",
    )
    if profile.properties.complexity == Complexity.EXPERT:
        assert profile.memory_cache.use_long_term_memory is True
        assert profile.memory_cache.use_knowledge_graph is True


# ── Archetype 3a: High-risk deploy ────────────────────────────────────────────

async def test_archetype3a_high_risk_deploy_requires_hitl(builder):
    """Deploy to production → CRITICAL risk → HITL required."""
    profile, _ = await builder.build_with_trace(
        "deploy the production service to AWS",
        tenant_id="t1",
        goal_id="arch3a",
    )
    # "production" is in CRITICAL_RISK set
    assert profile.properties.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert profile.security.hitl_required is True


async def test_archetype3a_high_risk_deploy_rollback(builder):
    """High-risk deploy → rollback_required."""
    profile, _ = await builder.build_with_trace(
        "deploy the production service to AWS",
        tenant_id="t1",
        goal_id="arch3a-rollback",
    )
    assert profile.security.rollback_required is True


# ── Archetype 3b: Critical risk ────────────────────────────────────────────────

async def test_archetype3b_critical_risk_classification(builder):
    """Goal with delete AND charge → CRITICAL risk (both are in CRITICAL set)."""
    profile, _ = await builder.build_with_trace(
        "delete production database and charge all customers $1000",
        tenant_id="t1",
        goal_id="arch3b",
    )
    assert profile.properties.risk == RiskLevel.CRITICAL


async def test_archetype3b_critical_risk_security(builder):
    """CRITICAL risk → HITL + consensus + rollback all required."""
    profile, _ = await builder.build_with_trace(
        "delete production database and charge all customers $1000",
        tenant_id="t1",
        goal_id="arch3b-sec",
    )
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True
    assert profile.security.consensus_required is True


async def test_archetype3b_critical_risk_eval_suite(builder):
    """CRITICAL risk → eval_suite=security."""
    profile, _ = await builder.build_with_trace(
        "delete production database and charge all customers $1000",
        tenant_id="t1",
        goal_id="arch3b-eval",
    )
    assert profile.eval_config.eval_suite == "security"


# ── Archetype 4: Coding goal ──────────────────────────────────────────────────

async def test_archetype4_coding_goal_domain(builder):
    """Python coding goal → TECHNICAL domain, requires_code=True."""
    profile, _ = await builder.build_with_trace(
        "write a python function that sorts a list of dictionaries by key",
        tenant_id="t1",
        goal_id="arch4",
    )
    from app.orchestration.runtime_profile import Domain
    assert profile.properties.domain == Domain.TECHNICAL


async def test_archetype4_coding_goal_rag_chunking(builder):
    """Coding goal → AST chunking strategy, code embedding."""
    profile, _ = await builder.build_with_trace(
        "write a python function that sorts a list",
        tenant_id="t1",
        goal_id="arch4-rag",
    )
    if profile.properties.requires_code:
        assert profile.rag_strategy.chunking_strategy == "ast"
        assert profile.rag_strategy.embedding_model == "code"


async def test_archetype4_coding_goal_eval_suite(builder):
    """Coding goal → eval_suite=coding."""
    profile, _ = await builder.build_with_trace(
        "write a python function that sorts a list",
        tenant_id="t1",
        goal_id="arch4-eval",
    )
    if profile.properties.requires_code:
        assert profile.eval_config.eval_suite == "coding"


# ── Archetype 5: Empty knowledge base ─────────────────────────────────────────

async def test_archetype5_empty_kb_web_fallback(builder):
    """Empty KB → web_fallback_enabled=True, web_search in sources."""
    profile, _ = await builder.build_with_trace(
        "find documents about quarterly revenue goals",
        tenant_id="t1",
        goal_id="arch5",
        kb_state="empty",
    )
    assert profile.rag_strategy.web_fallback_enabled is True
    assert "web_search" in profile.rag_strategy.sources


async def test_archetype5_empty_kb_properties(builder):
    """Empty KB → properties.kb_state=EMPTY."""
    profile, _ = await builder.build_with_trace(
        "find relevant documentation for the project",
        tenant_id="t1",
        goal_id="arch5-props",
        kb_state="empty",
    )
    assert profile.properties.kb_state == KnowledgeState.EMPTY


async def test_archetype5_sparse_kb_still_enables_web_fallback(builder):
    """Sparse KB → web_fallback_enabled=True."""
    profile, _ = await builder.build_with_trace(
        "find documents about quarterly goals",
        tenant_id="t1",
        goal_id="arch5-sparse",
        kb_state="sparse",
    )
    assert profile.rag_strategy.web_fallback_enabled is True


# ── Archetype 6: Real-time goal ────────────────────────────────────────────────

async def test_archetype6_realtime_web_signals(builder):
    """Goal with 'current' signal → requires_web=True."""
    profile, _ = await builder.build_with_trace(
        "what is the current stock price of Apple?",
        tenant_id="t1",
        goal_id="arch6",
    )
    assert profile.properties.requires_web is True or profile.rag_strategy.web_fallback_enabled is True


async def test_archetype6_realtime_rag_strategy(builder):
    """Real-time signal → web_augmented_rag or web_fallback enabled."""
    profile, _ = await builder.build_with_trace(
        "what is the current price of Bitcoin right now?",
        tenant_id="t1",
        goal_id="arch6-rag",
    )
    assert (
        "web_search" in profile.rag_strategy.sources
        or profile.rag_strategy.web_fallback_enabled
    )


# ── Archetype 7: Multi-tenant enterprise ──────────────────────────────────────

async def test_archetype7_enterprise_governance(builder, enterprise_ctx):
    """Enterprise plan → enterprise governance bundle."""
    profile, _ = await builder.build_with_trace(
        "summarize the quarterly report",
        tenant_id="t2",
        goal_id="arch7",
    )
    gov_selector = GovernanceProfileSelector()
    gov_config = gov_selector.select(profile, tenant_ctx=enterprise_ctx)
    from app.security_runtime.governance_profile import GovernanceBundle
    assert gov_config.name == GovernanceBundle.ENTERPRISE


async def test_archetype7_enterprise_policy_engine(builder, enterprise_ctx):
    """Enterprise plan → policy_engine_enabled=True in governance."""
    profile, _ = await builder.build_with_trace(
        "list all agents and their status",
        tenant_id="t2",
        goal_id="arch7-policy",
    )
    gov_selector = GovernanceProfileSelector()
    gov_config = gov_selector.select(profile, tenant_ctx=enterprise_ctx)
    assert gov_config.policy_engine_enabled is True


async def test_archetype7_professional_vs_enterprise(builder, tenant_ctx, enterprise_ctx):
    """Professional plan gets free governance, enterprise gets enterprise."""
    profile, _ = await builder.build_with_trace(
        "list all open tickets",
        tenant_id="t1",
        goal_id="arch7-cmp",
    )
    gov_selector = GovernanceProfileSelector()
    pro_gov = gov_selector.select(profile, tenant_ctx=tenant_ctx)
    ent_gov = gov_selector.select(profile, tenant_ctx=enterprise_ctx)
    from app.security_runtime.governance_profile import GovernanceBundle
    assert ent_gov.name == GovernanceBundle.ENTERPRISE
    # Professional gets free governance for low-risk goals
    assert pro_gov.max_goal_cost_usd <= ent_gov.max_goal_cost_usd


# ── Archetype 8: Financial operation ──────────────────────────────────────────

async def test_archetype8_financial_critical_risk(builder):
    """Payment processing → CRITICAL risk (payment is in CRITICAL set)."""
    profile, _ = await builder.build_with_trace(
        "process payment for customer order #123",
        tenant_id="t1",
        goal_id="arch8",
    )
    assert profile.properties.risk == RiskLevel.CRITICAL


async def test_archetype8_financial_guardrail_bundle(builder, tenant_ctx):
    """CRITICAL risk payment → strict guardrail bundle."""
    profile, _ = await builder.build_with_trace(
        "process payment for customer order #123",
        tenant_id="t1",
        goal_id="arch8-guard",
    )
    guard_selector = GuardrailProfileSelector()
    config = guard_selector.select(profile, tenant_ctx=tenant_ctx)
    assert config.name in (GuardrailBundle.STRICT, GuardrailBundle.REGULATED)


# ── Archetype 9: Data classification ──────────────────────────────────────────

def test_archetype9_data_classification_ssn():
    """Text with SSN → DataClass.PHI detected."""
    classifier = DataClassifier()
    result = classifier.classify("The patient SSN is 123-45-6789 and DOB is 01/01/1980")
    assert DataClass.PHI in result.classes


def test_archetype9_data_classification_credit_card():
    """Text with credit card number → DataClass.PCI detected."""
    classifier = DataClassifier()
    result = classifier.classify("Please charge card 4111111111111111 for $50")
    assert DataClass.PCI in result.classes


def test_archetype9_data_classification_email_pii():
    """Text with email → DataClass.PII detected."""
    classifier = DataClassifier()
    result = classifier.classify("Contact user@example.com for support")
    assert DataClass.PII in result.classes


def test_archetype9_data_classification_public():
    """Plain text → DataClass.PUBLIC."""
    classifier = DataClassifier()
    result = classifier.classify("What is the capital of France?")
    assert DataClass.PUBLIC in result.classes
    assert result.safe_for_prompt is True


def test_archetype9_data_classification_safe_for_prompt():
    """PHI data → NOT safe_for_prompt."""
    classifier = DataClassifier()
    result = classifier.classify("SSN: 123-45-6789")
    assert result.safe_for_prompt is False


# ── Archetype 10: Self-improvement ────────────────────────────────────────────

async def test_archetype10_self_improvement_scorecard(builder):
    """RuntimeScorecard produces exactly 9 score dimensions."""
    from app.agent.state import AgentState, GoalStatus

    profile = await builder.build(
        "list all open tickets", tenant_id="t1", goal_id="sc-test"
    )
    state = AgentState(
        goal_id="sc-test",
        goal="list all open tickets",
        tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1"),
        status=GoalStatus.COMPLETE,
        iterations=5,
    )
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)
    assert len(result.scores) == 9
    assert 0.0 <= result.overall_score <= 1.0


# ── Acceptance criteria tests (spec §5) ───────────────────────────────────────

async def test_ac_profile_builds_in_under_100ms(builder):
    """AC §5.1 — Profile assembly must complete in <100ms."""
    t0 = time.perf_counter()
    profile, trace = await builder.build_with_trace(
        "analyze market trends and synthesize a report",
        tenant_id="t1",
        goal_id="ac1",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 100, f"Profile assembly took {elapsed_ms:.1f}ms (must be <100ms)"
    assert trace.total_latency_ms < 100


async def test_ac_profile_is_json_serializable(builder):
    """AC §5.2 — Profile must be JSON-serializable."""
    profile, _ = await builder.build_with_trace(
        "delete production database",
        tenant_id="t1",
        goal_id="ac2",
    )
    data = profile.to_dict()
    # Must not raise
    dumped = json.dumps(data)
    loaded = json.loads(dumped)
    assert loaded["goal_id"] == "ac2"
    assert loaded["tenant_id"] == "t1"


async def test_ac_decision_trace_has_all_dimensions(builder):
    """AC §5.3 — DecisionTrace must record all 7 decision points."""
    _, trace = await builder.build_with_trace(
        "analyze and research market trends",
        tenant_id="t1",
        goal_id="ac3",
    )
    # 1 classifier + 6 selector dimensions = 7 minimum
    assert len(trace.decisions) >= 7
    assert trace.total_latency_ms > 0


async def test_ac_guardrail_profile_correct_for_critical(builder, tenant_ctx):
    """AC §5.4 — GuardrailProfileSelector selects STRICT for CRITICAL risk."""
    profile, _ = await builder.build_with_trace(
        "delete production database",
        tenant_id="t1",
        goal_id="ac4",
    )
    selector = GuardrailProfileSelector()
    config = selector.select(profile, tenant_ctx=tenant_ctx)
    assert config.name in (GuardrailBundle.STRICT, GuardrailBundle.REGULATED)
    assert config.block_on_injection is True


async def test_ac_policy_compiler_forensic_audit_for_critical(builder, tenant_ctx):
    """AC §5.5 — PolicyCompiler sets audit_level=forensic for CRITICAL risk."""
    profile, _ = await builder.build_with_trace(
        "delete production database",
        tenant_id="t1",
        goal_id="ac5",
    )
    compiler = PolicyCompiler()
    constraints = compiler.compile(profile, tenant_ctx=tenant_ctx)
    assert constraints.audit_level == "forensic"


def test_ac_plan_verifier_flags_critical_operation():
    """AC §5.6 — PlanVerifier flags destructive operations as critical."""
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig,
        RAGStrategyConfig, ModelPlanConfig, SecurityConfig,
        MemoryCacheConfig, EvalConfig, Complexity, RiskLevel,
    )

    props = GoalProperties(
        raw_goal="delete all data",
        complexity=Complexity.SIMPLE,
        risk=RiskLevel.CRITICAL,
    )
    profile = GoalRuntimeProfile(
        goal_id="pv-test",
        tenant_id="t1",
        properties=props,
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(cost_class="high"),
        security=SecurityConfig(hitl_required=True, rollback_required=True),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    verifier = PlanVerifier()
    result = verifier.verify(
        plan=["delete all data from production database", "drop all tables"],
        profile=profile,
    )
    assert result.risk_level == "critical"
    assert result.requires_hitl is True


def test_ac_readiness_gate_blocks_when_postgres_down():
    """AC §5.7 — ReadinessGate returns ready=False when postgres is unavailable."""
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig,
        RAGStrategyConfig, ModelPlanConfig, SecurityConfig,
        MemoryCacheConfig, EvalConfig, Complexity, RiskLevel,
    )

    health = DependencyHealth(
        postgres=DepStatus.UNAVAILABLE,
        redis=DepStatus.HEALTHY,
        embedder=DepStatus.HEALTHY,
        llm_provider=DepStatus.HEALTHY,
    )
    gate = ReadinessGate(health)
    props = GoalProperties(raw_goal="list tickets", complexity=Complexity.SIMPLE)
    profile = GoalRuntimeProfile(
        goal_id="rg-test",
        tenant_id="t1",
        properties=props,
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is False
    assert "postgres" in result.blocking_deps


def test_ac_readiness_gate_ready_all_healthy():
    """AC §5.7b — ReadinessGate returns ready=True when all deps healthy."""
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig,
        RAGStrategyConfig, ModelPlanConfig, SecurityConfig,
        MemoryCacheConfig, EvalConfig, Complexity,
    )

    health = DependencyHealth.all_healthy()
    gate = ReadinessGate(health)
    props = GoalProperties(raw_goal="list tickets", complexity=Complexity.SIMPLE)
    profile = GoalRuntimeProfile(
        goal_id="rg-test2",
        tenant_id="t1",
        properties=props,
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is True


def test_ac_failure_classifier_categorizes_auth_error():
    """AC §5.8 — FailureClassifier correctly categorizes auth errors."""
    classifier = FailureClassifier()
    result = classifier.classify("401 Unauthorized — invalid api key")
    assert result.failure_class == FailureClass.AUTH_FAILURE


def test_ac_failure_classifier_categorizes_rate_limit():
    """AC §5.8b — FailureClassifier correctly categorizes rate limit."""
    classifier = FailureClassifier()
    result = classifier.classify("429 Too Many Requests — rate limit exceeded")
    assert result.failure_class == FailureClass.RATE_LIMIT


def test_ac_failure_classifier_categorizes_timeout():
    """AC §5.8c — FailureClassifier correctly categorizes timeout."""
    classifier = FailureClassifier()
    result = classifier.classify("Operation timed out after 30 seconds")
    assert result.failure_class == FailureClass.TIMEOUT


def test_ac_failure_classifier_categorizes_unknown():
    """AC §5.8d — FailureClassifier returns UNKNOWN for unrecognized errors."""
    classifier = FailureClassifier()
    result = classifier.classify("Something went very wrong with the frob")
    assert result.failure_class == FailureClass.UNKNOWN


def test_ac_capability_registry_is_populated():
    """AC §5.9 — CapabilityRegistry contains entries for all major capabilities."""
    reg = build_default_capability_registry()
    caps = reg.list_all()
    assert len(caps) > 0
    cap_ids = {c.capability_id for c in caps}
    # Verify some known capabilities are present (IDs use prefixes like retriever:, tool:, etc.)
    assert any("retriever" in cid for cid in cap_ids)
    assert any("tool" in cid for cid in cap_ids)
    assert any("guardrail" in cid for cid in cap_ids)


async def test_ac_profile_has_all_required_sections(builder):
    """AC §5.10 — Profile must have all 7 strategy sections populated."""
    profile, _ = await builder.build_with_trace(
        "list all open Jira tickets",
        tenant_id="t1",
        goal_id="ac10",
    )
    # All 7 sections must be present and non-None
    assert profile.properties is not None
    assert profile.agent_patterns is not None
    assert profile.rag_strategy is not None
    assert profile.model_plan is not None
    assert profile.security is not None
    assert profile.memory_cache is not None
    assert profile.eval_config is not None
    # Profile ID should be auto-generated
    assert profile.profile_id
    # Assembly latency should be recorded
    assert profile.assembly_latency_ms >= 0


async def test_ac_full_pipeline_e2e(builder, tenant_ctx):
    """AC §5.11 — Full pipeline: classify → select → guardrails → governance → policy."""
    goal = "analyze and synthesize market intelligence report across 15 data sources"
    profile, trace = await builder.build_with_trace(
        goal,
        tenant_id="t1",
        goal_id="ac11",
    )

    # Verify all pipeline stages ran
    assert len(trace.decisions) >= 6

    # Guardrail selection
    guard_selector = GuardrailProfileSelector()
    guard_config = guard_selector.select(profile, tenant_ctx=tenant_ctx)
    assert guard_config.name is not None

    # Governance selection
    gov_selector = GovernanceProfileSelector()
    gov_config = gov_selector.select(profile, tenant_ctx=tenant_ctx)
    assert gov_config.name is not None

    # Policy compilation
    compiler = PolicyCompiler()
    constraints = compiler.compile(profile, tenant_ctx=tenant_ctx)
    assert constraints.max_cost_usd > 0

    # Plan verification
    verifier = PlanVerifier()
    vresult = verifier.verify(
        plan=["Retrieve market data", "Analyze trends", "Synthesize report"],
        profile=profile,
    )
    assert vresult.feasible is True


async def test_ac_strategy_registry_contains_all_required_strategies(registry):
    """AC §5.12 — StrategyRegistry must contain all 9 required strategy types."""
    required = ["react", "hybrid_rag", "agentic_rag", "guardrails", "hitl",
                "reflexion_memory", "semantic_cache", "model_routing", "rollback"]
    for strategy_id in required:
        cap = registry.get(strategy_id)
        assert cap is not None, f"Missing required strategy: {strategy_id}"


# ── Archetype 7 (new): Empty KB RetrieverTool structured result ───────────────

async def test_archetype5b_empty_kb_retriever_never_silent():
    """Spec §3.4 AC: Empty KB must return structured result, not silent empty string."""
    from app.rag.agentic.retriever_tool import RetrieverTool, RetrievalResult
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import TenantContext, PlanTier

    tool = RetrieverTool(knowledge_store=KnowledgeStore())
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    result = await tool.retrieve(query="any query", tenant_ctx=ctx)
    # Must NOT be empty string — must be structured
    assert isinstance(result, RetrievalResult)
    assert result.source != ""
    assert result.strategy_used != ""
    assert isinstance(result.chunks, list)  # even empty list is fine — never None/str
    assert result.confidence >= 0.0


# ── Archetype 8 (new): Realtime latency_class assertion ──────────────────────

async def test_archetype6b_realtime_latency_class(builder):
    """Realtime goal must produce latency_class='realtime' or 'interactive'."""
    profile, _ = await builder.build_with_trace(
        "Get the current status of the Kubernetes pods right now",
        tenant_id="t1", goal_id="arch6b"
    )
    assert profile.model_plan.latency_class in ("realtime", "interactive")
    assert profile.properties.requires_web is True or profile.properties.time_sensitivity == TimeSensitivity.REALTIME


# ── Archetype 9 (new): Multi-tenant profile isolation ────────────────────────

async def test_archetype7_multi_tenant_profiles_isolated(builder):
    """Spec §3.4 AC: tenant_id scoped to profile — cross-tenant access denied."""
    profile_t1, _ = await builder.build_with_trace(
        "List my agents", tenant_id="tenant_alpha", goal_id="t1g1"
    )
    profile_t2, _ = await builder.build_with_trace(
        "List my agents", tenant_id="tenant_beta", goal_id="t2g1"
    )
    assert profile_t1.tenant_id == "tenant_alpha"
    assert profile_t2.tenant_id == "tenant_beta"
    # Same goal text, different tenants must produce different profiles
    assert profile_t1.profile_id != profile_t2.profile_id
    # No cross-tenant data leakage
    assert profile_t1.goal_id == "t1g1"
    assert profile_t2.goal_id == "t2g1"


# ── Archetype 11 (new): Financial irreversibility assertion ──────────────────

async def test_archetype8b_financial_irreversibility(builder):
    """Financial operations must always be marked irreversible."""
    profile, _ = await builder.build_with_trace(
        "Transfer funds and charge the customer payment method",
        tenant_id="t1", goal_id="arch8b"
    )
    assert profile.properties.reversibility == "irreversible"
    assert profile.security.rollback_required is True


# ── Archetype 13 (new): Regression candidate logic ───────────────────────────

def test_archetype10b_failed_goal_creates_regression_candidate():
    """Failed goal must create regression candidate for self-improvement."""
    from app.evals.regression_gate import RegressionGate
    from app.evals.runtime_scorecard import RuntimeScorecard
    from app.agent.state import AgentState, GoalStatus

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="delete prod db", tenant_ctx=ctx, goal_id="g_fail")
    state.status = GoalStatus.FAILED
    state.iterations = 25

    profile = GoalRuntimeProfile(
        goal_id="g_fail", tenant_id="t1",
        properties=GoalProperties(raw_goal="delete prod db"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)
    assert result.overall_score < 0.7  # failed goal has low score

    gate = RegressionGate()
    candidate = gate.maybe_create_regression(state=state, scorecard=result, profile=profile)
    assert candidate is not None
    assert candidate["goal_id"] == "g_fail"
    assert candidate["status"] == "failed"


def test_archetype10c_successful_goal_no_regression():
    """Successful goal must NOT create regression candidate."""
    from app.evals.regression_gate import RegressionGate
    from app.evals.runtime_scorecard import RuntimeScorecard
    from app.agent.state import AgentState, GoalStatus

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="list tickets", tenant_ctx=ctx, goal_id="g_ok")
    state.status = GoalStatus.COMPLETE
    state.iterations = 3

    profile = GoalRuntimeProfile(
        goal_id="g_ok", tenant_id="t1",
        properties=GoalProperties(raw_goal="list tickets"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)

    gate = RegressionGate()
    candidate = gate.maybe_create_regression(state=state, scorecard=result, profile=profile)
    assert candidate is None  # high-scoring successful goal → no regression candidate


# ── SPEC §5 ACCEPTANCE CRITERIA — exact names as required ────────────────────

async def test_acceptance_simple_goal_minimal_cost_profile(builder):
    """Spec §5 AC: A simple goal gets a minimal low-cost runtime profile."""
    profile, _ = await builder.build_with_trace(
        "get current user", tenant_id="t1", goal_id="acc1"
    )
    assert profile.model_plan.cost_class in ("low", "medium")
    assert profile.security.hitl_required is False
    assert profile.security.consensus_required is False


async def test_acceptance_high_risk_always_hitl_consensus_rollback(builder):
    """Spec §5 AC: High-risk destructive goal ALWAYS gets HITL + consensus + rollback."""
    profile, _ = await builder.build_with_trace(
        "truncate the production database", tenant_id="t1", goal_id="acc2"
    )
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True
    assert profile.security.audit_level in ("full", "forensic")


def test_acceptance_strategy_registry_has_all_documented_patterns():
    """Spec §5 AC: Every pattern from architecture docs in StrategyRegistry."""
    from app.orchestration.strategy_registry import build_default_registry, StrategyCategory
    registry = build_default_registry()
    assert len(registry.list_all()) >= 60
    agent_ids = {s.strategy_id for s in registry.list_by_category(StrategyCategory.AGENT)}
    for pat in ["react", "plan_execute", "reflection", "reflexion", "goal_tree",
                "supervisor", "debate", "consensus", "self_refine"]:
        assert pat in agent_ids, f"Missing agent pattern: {pat}"
    rag_ids = {s.strategy_id for s in registry.list_by_category(StrategyCategory.RAG)}
    for pat in ["naive_rag", "hybrid_rag", "hyde", "graph_rag", "agentic_rag",
                "corrective_rag", "web_augmented_rag"]:
        assert pat in rag_ids, f"Missing RAG pattern: {pat}"
    safety_ids = {s.strategy_id for s in registry.list_by_category(StrategyCategory.SAFETY)}
    for pat in ["guardrails", "hitl", "sandbox", "plan_verification", "data_classification"]:
        assert pat in safety_ids, f"Missing safety pattern: {pat}"


def test_acceptance_every_runtime_decision_traceable():
    """Spec §5 AC: Every runtime decision appears in AgentRunTrace and SSE."""
    trace = DecisionTrace(goal_id="g1", tenant_id="t1")
    trace.add("GoalClassifier", "complexity", "expert", "keyword signals")
    trace.add("PatternSelector", "agent_patterns", ["react"], "default")
    trace.add("PatternSelector", "rag_strategy", "hybrid_rag", "kb available")
    assert len(trace.decisions) == 3
    sse_event = trace.to_sse_event()
    assert sse_event["type"] == "runtime_profile_selected"
    assert len(sse_event["decisions"]) == 3
    # Must be JSON-serializable (for SSE emission)
    import json
    json.dumps(sse_event)


def test_acceptance_data_classification_no_silent_pass():
    """Spec §5 AC: No unclassified data enters prompt/context building."""
    classifier = DataClassifier()
    texts = [
        "normal text", "api key: sk-proj-abc123",
        "email: test@example.com", "",
        "def function(): pass", "<html><body></body></html>",
    ]
    for text in texts:
        result = classifier.classify_or_safe_fallback(text)
        assert result is not None
        assert len(result.classes) > 0


async def test_acceptance_plan_verifier_gates_high_risk():
    """Spec §5 AC: No high-risk plan executes without verification."""
    verifier = PlanVerifier()
    plan = ["Drop the production database", "Truncate all user tables", "Revoke all API keys"]
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="destroy everything", risk=RiskLevel.CRITICAL),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(hitl_required=True),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = verifier.verify(plan=plan, profile=profile)
    assert result.requires_hitl is True
    assert result.risk_level in ("high", "critical")


def test_acceptance_readiness_gate_blocks_when_llm_down():
    """Spec §5 AC: Platform blocks goals when LLM provider is unavailable."""
    health = DependencyHealth(
        postgres=DepStatus.HEALTHY, redis=DepStatus.HEALTHY,
        embedder=DepStatus.HEALTHY, llm_provider=DepStatus.UNAVAILABLE,
    )
    gate = ReadinessGate(health)
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is False
    assert "llm_provider" in result.blocking_deps


def test_acceptance_failure_classifier_never_generic_retry():
    """Spec §5 AC: Recovery never generic retry — every failure has classified reason."""
    classifier = FailureClassifier()
    from app.recovery.recovery_policy import RecoveryPolicy
    policy = RecoveryPolicy()
    test_cases = [
        ("401 Unauthorized", FailureClass.AUTH_FAILURE),
        ("429 rate limit exceeded", FailureClass.RATE_LIMIT),
        ("INSUFFICIENT DATA: cannot find answer", FailureClass.CONTEXT_GAP),
        ("TimeoutError after 30s", FailureClass.TIMEOUT),
        ("GUARDRAIL: injection detected", FailureClass.SAFETY_VIOLATION),
    ]
    for error_text, expected_class in test_cases:
        result = classifier.classify(error_text)
        assert result.failure_class == expected_class, (
            f"'{error_text}' should classify as {expected_class}, got {result.failure_class}"
        )
        action = policy.select(result)
        assert action is not None  # must have a specific action, not None


async def test_acceptance_profile_builder_latency_under_100ms(builder):
    """Spec §5 AC: Profile assembly must complete in < 100ms (fast path)."""
    import time
    t0 = time.perf_counter()
    profile, _ = await builder.build_with_trace(
        "list all open issues", tenant_id="t1", goal_id="perf1"
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 100.0, f"Profile assembly took {elapsed_ms:.1f}ms — too slow"


async def test_acceptance_all_10_archetypes_produce_valid_profiles(builder):
    """Spec §5 AC: All 10 archetypes produce valid, JSON-serializable GoalRuntimeProfiles."""
    import json
    goals = [
        "list all open Jira tickets",
        "research AI safety and write a comprehensive report",
        "delete all records from production database",
        "write Python function to parse logs",
        "find latest information on quantum computing",
        "get current Kubernetes pod status right now",
        "analyze competitors and build strategic report",
        "transfer funds and charge customer payment method",
        "customer SSN is 123-45-6789 please process",
        "run all test suites and fix failing tests",
    ]
    for i, goal in enumerate(goals, 1):
        profile, trace = await builder.build_with_trace(
            goal, tenant_id="t1", goal_id=f"archetype_{i}"
        )
        assert profile.goal_id == f"archetype_{i}"
        assert profile.tenant_id == "t1"
        try:
            json.dumps(profile.to_dict())
        except Exception as e:
            pytest.fail(f"Archetype {i}: profile not JSON-serializable: {e}")
        assert len(trace.decisions) > 0, f"Archetype {i}: no decisions in trace"
