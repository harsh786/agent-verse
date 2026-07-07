"""PatternSelector — translates GoalProperties into concrete strategy configs.
CRITICAL rules: safety rules can only ADD, never remove.
"""
from __future__ import annotations

from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    Complexity,
    Domain,
    EvalConfig,
    GoalProperties,
    KnowledgeState,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    RiskLevel,
    SecurityConfig,
    TimeSensitivity,
)
from app.orchestration.strategy_registry import StrategyRegistry


class PatternSelector:
    def __init__(self, registry: StrategyRegistry) -> None:
        self._registry = registry

    def select_agent_patterns(self, props: GoalProperties) -> AgentPatternConfig:
        reasoning: list[str] = ["react"]
        multi_agent: list[str] = ["single_agent"]
        safety: list[str] = ["guardrails"]
        reasons: dict[str, str] = {"react": "default reasoning loop"}
        max_iter = 15
        persistence = False
        autonomy = "bounded-autonomous"

        # CRITICAL: High/critical risk always adds HITL + rollback
        if props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            safety = list(dict.fromkeys(safety + ["hitl", "rollback"]))
            reasons["hitl"] = f"risk={props.risk.value}"
            reasons["rollback"] = f"reversibility={props.reversibility}"
            autonomy = "supervised"

        if props.risk == RiskLevel.CRITICAL:
            safety = list(dict.fromkeys(safety + ["consensus_verification"]))
            reasons["consensus_verification"] = "critical risk requires consensus"

        if props.complexity in (Complexity.COMPLEX, Complexity.EXPERT):
            reasoning = list(dict.fromkeys(reasoning + ["chain_of_thought", "reflection"]))
            reasons["chain_of_thought"] = f"complexity={props.complexity.value}"
            reasons["reflection"] = "complex goals benefit from reflection"
            max_iter = 25

        if props.complexity == Complexity.EXPERT:
            max_iter = 50
            persistence = True
            if self._registry.is_available("goal_tree"):
                multi_agent = ["goal_tree"]
                reasons["goal_tree"] = "expert complexity → parallel sub-goals"

        if props.requires_code and self._registry.is_available("self_refine"):
            reasoning = list(dict.fromkeys(reasoning + ["self_refine"]))
            reasons["self_refine"] = "coding tasks benefit from iterative refinement"

        if props.is_generative or props.domain == Domain.CREATIVE:
            if "self_refine" not in reasoning and self._registry.is_available("self_refine"):
                reasoning = list(dict.fromkeys(reasoning + ["self_refine"]))
                reasons["self_refine"] = (
                    "generative/creative task — self-refinement improves quality"
                )

        return AgentPatternConfig(
            reasoning=reasoning,
            rag=["hybrid_rag"],
            multi_agent=multi_agent,
            safety=safety,
            max_iterations=max_iter,
            persistence_mode=persistence,
            max_persistence_attempts=3,
            autonomy_mode=autonomy,
            selection_reasons=reasons,
        )

    def select_rag_strategy(self, props: GoalProperties) -> RAGStrategyConfig:
        strategy = "hybrid_rag"
        sources = ["knowledge_base"]
        chunking = "semantic"
        embedding = "default"
        reranker = "score"
        graph_strategy = "none"
        web_fallback = False
        max_tokens = 6000
        min_relevance = 0.35

        if props.requires_web or props.time_sensitivity == TimeSensitivity.REALTIME:
            web_fallback = True
            sources = list(dict.fromkeys(sources + ["web_search"]))
            strategy = "web_augmented_rag"

        if props.kb_state in (KnowledgeState.EMPTY, KnowledgeState.SPARSE):
            web_fallback = True
            if "web_search" not in sources:
                sources.append("web_search")

        if props.complexity == Complexity.EXPERT:
            strategy = "agentic_rag"
            sources = list(
                dict.fromkeys(sources + ["long_term_memory", "knowledge_graph"])
            )
            graph_strategy = "entity"
            reranker = "rrf"
            max_tokens = 8000
            min_relevance = 0.25
        elif props.complexity == Complexity.COMPLEX:
            strategy = "hybrid_rag"
            sources = list(dict.fromkeys(sources + ["long_term_memory"]))
            reranker = "rrf"
            max_tokens = 7000

        if props.requires_code:
            chunking = "ast"
            embedding = "code"

        if props.time_sensitivity == TimeSensitivity.REALTIME:
            max_tokens = 2000
            if not web_fallback:
                strategy = "naive_rag"

        return RAGStrategyConfig(
            strategy=strategy,
            sources=sources,
            chunking_strategy=chunking,
            embedding_model=embedding,
            reranker=reranker,
            max_context_tokens=max_tokens,
            min_relevance_score=min_relevance,
            max_chunks_per_source=5,
            citation_required=True,
            deduplication_enabled=True,
            web_fallback_enabled=web_fallback,
            graph_strategy=graph_strategy,
        )

    def select_model_plan(self, props: GoalProperties) -> ModelPlanConfig:
        latency = "interactive"
        cost = "medium"
        if props.time_sensitivity == TimeSensitivity.REALTIME:
            latency = "realtime"
            cost = "low"
        elif props.complexity == Complexity.SIMPLE and props.risk == RiskLevel.LOW:
            cost = "low"
        elif props.complexity == Complexity.EXPERT or props.risk in (
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ):
            cost = "high"
        if props.complexity == Complexity.SIMPLE:
            latency = "realtime"
        return ModelPlanConfig(
            planner="default",
            executor="default",
            verifier="default",
            embedder="default",
            classifier="default",
            cost_class=cost,
            latency_class=latency,
        )

    def select_security_profile(self, props: GoalProperties) -> SecurityConfig:
        hitl = False
        consensus = False
        rollback = False
        audit = "standard"
        guardrail = "default"
        governance = "free"
        sandbox = False
        if props.risk == RiskLevel.CRITICAL:
            hitl = True
            consensus = True
            rollback = True
            audit = "forensic"
            guardrail = "strict"
            governance = "enterprise"
        elif props.risk == RiskLevel.HIGH:
            hitl = True
            rollback = True
            audit = "full"
            guardrail = "strict"
        elif props.risk == RiskLevel.MEDIUM:
            audit = "full"
        if props.reversibility == "irreversible":
            rollback = True
        if props.requires_code:
            sandbox = True
        return SecurityConfig(
            guardrail_bundle=guardrail,
            governance_bundle=governance,
            hitl_required=hitl,
            consensus_required=consensus,
            rollback_required=rollback,
            audit_level=audit,
            compliance_tags=[],
            sandbox_required=sandbox,
        )

    def select_memory_cache_policy(self, props: GoalProperties) -> MemoryCacheConfig:
        use_ltm = props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
        use_kg = props.complexity == Complexity.EXPERT
        reflexion = props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
        semantic_cache = (
            props.complexity == Complexity.SIMPLE
            and props.risk == RiskLevel.LOW
            and props.time_sensitivity != TimeSensitivity.REALTIME
        )
        return MemoryCacheConfig(
            use_session_memory=True,
            use_execution_memory=True,
            use_long_term_memory=use_ltm,
            use_semantic_cache=semantic_cache,
            use_knowledge_graph=use_kg,
            reflexion_enabled=reflexion,
        )

    def select_eval_config(self, props: GoalProperties) -> EvalConfig:
        suite = "default"
        if props.requires_code:
            suite = "coding"
        elif props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            suite = "security"
        elif props.complexity == Complexity.EXPERT:
            suite = "rag"
        return EvalConfig(
            enabled=True,
            eval_suite=suite,
            score_threshold=0.72,
            reflexion_enabled=True,
            creates_regression_case_on_failure=True,
        )
