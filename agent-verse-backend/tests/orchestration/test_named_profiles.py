# tests/orchestration/test_named_profiles.py
"""All spec-named profile classes must exist at the exact spec-required location."""
from __future__ import annotations

import json


def test_multimodal_runtime_profile_importable():
    from app.orchestration.runtime_profile import MultimodalRuntimeProfile
    p = MultimodalRuntimeProfile(
        content_type="pdf", parser="layout_pdf", chunking_strategy="layout",
        embedding_model="text-embedding-3-small",
        model_roles={"extractor": "gpt-4o", "reasoner": "gpt-5.2"},
        provenance_required=True,
    )
    assert p.content_type == "pdf"
    assert p.model_roles["extractor"] == "gpt-4o"
    json.dumps(p.to_dict())


def test_self_improvement_profile_importable():
    from app.orchestration.runtime_profile import SelfImprovementProfile
    p = SelfImprovementProfile(
        enabled=True, eval_suite="security", score_threshold=0.72,
        reflexion_enabled=True, prompt_ab_test_enabled=False,
        model_ab_test_enabled=False, creates_regression_case_on_failure=True,
    )
    assert p.eval_suite == "security"
    json.dumps(p.to_dict())


def test_context_runtime_profile_importable():
    from app.orchestration.runtime_profile import ContextRuntimeProfile
    p = ContextRuntimeProfile(
        reranker="rrf", max_context_tokens=6000, min_relevance_score=0.35,
        max_chunks_per_source=5, citation_required=True, deduplication_enabled=True,
    )
    assert p.reranker == "rrf"
    json.dumps(p.to_dict())


def test_knowledge_runtime_profile_importable():
    from app.orchestration.runtime_profile import KnowledgeRuntimeProfile
    p = KnowledgeRuntimeProfile(
        kb_state="healthy", graph_state="partial",
        selected_collections=["col1", "col2"],
        graph_strategy="entity", web_fallback_required=False, citation_required=True,
    )
    assert p.kb_state == "healthy"
    json.dumps(p.to_dict())


def test_security_runtime_profile_importable():
    from app.orchestration.runtime_profile import SecurityRuntimeProfile
    p = SecurityRuntimeProfile(
        guardrail_bundle="strict", governance_bundle="enterprise",
        identity_scope="tenant", hitl_required=True,
        consensus_required=False, rollback_required=True,
        audit_level="forensic", compliance_tags=["gdpr"],
    )
    assert p.guardrail_bundle == "strict"
    assert "gdpr" in p.compliance_tags
    json.dumps(p.to_dict())


def test_dynamic_graph_assembler_at_spec_location():
    from app.agent.patterns.dynamic_graph_assembler import DynamicGraphAssembler
    assert DynamicGraphAssembler is not None
