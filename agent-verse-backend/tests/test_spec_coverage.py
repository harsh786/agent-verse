# tests/test_spec_coverage.py
"""Verifies all 84 spec files exist as importable modules."""
from __future__ import annotations

import importlib

import pytest

SPEC_MODULES = [
    # Layer 0
    "app.core.runtime_flags", "app.core.runtime_profiles",
    # Layer 1
    "app.security_runtime.identity_profile",
    "app.security_runtime.governance_profile",
    "app.security_runtime.action_safety_profile",
    "app.security_runtime.policy_bundle_selector",
    "app.security_runtime.guardrail_profile",
    # Layer 2
    "app.orchestration.goal_classifier", "app.orchestration.runtime_profile",
    "app.orchestration.runtime_profile_builder", "app.orchestration.pattern_selector",
    "app.orchestration.strategy_registry", "app.orchestration.decision_trace",
    # Layer 3
    "app.agent.patterns.base", "app.agent.patterns.react",
    "app.agent.patterns.plan_execute", "app.agent.patterns.loop_engineering",
    "app.agent.patterns.reflection", "app.agent.patterns.reflexion",
    "app.agent.patterns.self_refine", "app.agent.patterns.self_consistency",
    "app.agent.patterns.tree_of_thoughts", "app.agent.patterns.supervisor",
    "app.agent.patterns.debate", "app.agent.patterns.goal_tree",
    "app.agent.patterns.consensus", "app.agent.patterns.dynamic_graph_assembler",
    # Layer 4
    "app.rag.agentic.retriever_tool", "app.rag.agentic.source_inventory",
    "app.rag.agentic.query_reformulator", "app.rag.agentic.query_expander",
    "app.rag.agentic.retrieval_policy", "app.rag.agentic.fallback_chain",
    "app.rag.agentic.citation_threader", "app.rag.agentic.context_gap_detector",
    "app.rag.agentic.rag_trace",
    # Layer 5
    "app.ingestion.orchestrator", "app.ingestion.content_classifier",
    "app.ingestion.parser_registry", "app.ingestion.chunking_strategy_selector",
    "app.ingestion.embedding_policy_selector", "app.ingestion.modality_pipeline",
    "app.ingestion.provenance_builder", "app.ingestion.quality_checks",
    # Layer 6
    "app.embedding.orchestrator", "app.embedding.model_registry",
    "app.embedding.dimension_policy", "app.embedding.reembedding_policy",
    "app.embedding.drift_monitor", "app.embedding.vector_index_policy",
    # Layer 7
    "app.context.prompt_builder", "app.context.context_budget",
    "app.context.rerank_policy", "app.context.citation_manager",
    "app.context.prompt_variant_selector", "app.context.tool_prompt_builder",
    "app.context.output_contract_builder",
    # Layer 8
    "app.ai_router.model_orchestrator", "app.ai_router.role_policy",
    "app.ai_router.provider_health_policy", "app.ai_router.cost_latency_quality_policy",
    # Layer 9
    "app.state_runtime.memory_policy", "app.state_runtime.cache_policy",
    "app.state_runtime.knowledge_policy", "app.state_runtime.session_memory",
    "app.state_runtime.reflexion_store",
    # Layer 10
    "app.evals.goal_score", "app.evals.agent_score",
    "app.evals.rag_score", "app.evals.safety_score",
    "app.evals.model_score", "app.evals.runtime_scorecard",
    "app.evals.regression_gate",
    # Layer 11
    "app.optimization.token_optimizer", "app.optimization.cost_optimizer",
    "app.optimization.latency_optimizer", "app.optimization.prompt_optimizer",
    "app.optimization.model_optimizer", "app.optimization.cache_optimizer",
    "app.optimization.ab_testing",
    # Layer 12
    # NOTE(D-21a): the standalone app.observability.{rag,pattern,model}_trace modules were
    # dead duplicates superseded by RuntimeSSEEmitter and have been deleted.
    "app.observability.runtime_decision_trace",
]


@pytest.mark.parametrize("module_path", SPEC_MODULES)
def test_spec_module_importable(module_path):
    """Every spec-required module must be importable without error."""
    try:
        mod = importlib.import_module(module_path)
        assert mod is not None, f"{module_path} imported as None"
    except ImportError as e:
        pytest.fail(f"SPEC MODULE NOT FOUND: {module_path} — {e}")
