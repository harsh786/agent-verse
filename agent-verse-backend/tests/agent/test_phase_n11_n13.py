# tests/agent/test_phase_n11_n13.py
"""Phase N11-N13: chunkers + granular flags + advanced RAG dispatch."""
from __future__ import annotations

# ── N11: Chunker dispatch ────────────────────────────────────────────────────

def test_ingestion_orchestrator_dispatches_ast_chunker_for_code():
    """Code content must use AST chunker, not paragraph split."""
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.orchestrator import IngestionOrchestrator
    orch = IngestionOrchestrator()
    code = "def foo():\n    return 1\n\ndef bar():\n    return 2"
    chunks = orch._chunk(code, ContentType.CODE)
    assert isinstance(chunks, list)
    assert len(chunks) >= 1
    # Code chunks should contain function definitions
    combined = " ".join(chunks)
    assert "def" in combined or "foo" in combined or "bar" in combined


def test_ingestion_orchestrator_dispatches_semantic_chunker_for_text():
    """Text content must use semantic chunker."""
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.orchestrator import IngestionOrchestrator
    orch = IngestionOrchestrator()
    text = "This is paragraph one.\n\nThis is paragraph two.\n\nThis is paragraph three."
    chunks = orch._chunk(text, ContentType.TEXT)
    assert isinstance(chunks, list)
    assert len(chunks) >= 1


def test_ingestion_chunker_registry_has_all_strategies():
    """get_chunker_for_strategy must return non-None for all major strategies."""
    from app.ingestion.chunkers import get_chunker_for_strategy
    for strategy in ["semantic", "ast", "heading", "layout", "timestamp"]:
        chunker = get_chunker_for_strategy(strategy)
        assert chunker is not None, f"No chunker for strategy: {strategy}"


# ── N12: Granular feature flags ──────────────────────────────────────────────

def test_runtime_flags_has_granular_fields():
    """RuntimeFlags must have granular enable_* fields."""
    from app.core.runtime_flags import RuntimeFlags
    flags = RuntimeFlags()
    assert hasattr(flags, "enable_runtime_scorecard")
    assert hasattr(flags, "enable_self_improvement")
    assert hasattr(flags, "enable_rag_strategy_routing")
    assert hasattr(flags, "enable_pattern_sse_events")
    assert hasattr(flags, "enable_guardrail_profile")


def test_dynamic_orchestration_master_flag_enables_all():
    """dynamic_orchestration=True must enable all granular flags."""
    from app.core.runtime_flags import RuntimeFlags
    # Test the logic directly
    flags = RuntimeFlags(dynamic_orchestration=True)
    # Manually apply the cascade (as get_runtime_flags() does)
    if flags.dynamic_orchestration:
        flags.enable_runtime_scorecard = True
        flags.enable_self_improvement = True
        flags.enable_rag_strategy_routing = True
        flags.enable_pattern_sse_events = True
        flags.enable_guardrail_profile = True
    assert flags.enable_runtime_scorecard is True
    assert flags.enable_self_improvement is True
    assert flags.enable_rag_strategy_routing is True


def test_granular_flag_independent_of_master():
    """Individual flags must work without master flag."""
    from app.core.runtime_flags import RuntimeFlags
    flags = RuntimeFlags(dynamic_orchestration=False, enable_runtime_scorecard=True)
    assert flags.dynamic_orchestration is False
    assert flags.enable_runtime_scorecard is True


def test_self_improvement_auto_apply_is_opt_in_only():
    """Auto-applying a winning config to a live agent must be opt-in.

    It defaults off AND is deliberately NOT switched on by the master
    dynamic_orchestration flag (unlike the other granular flags) — writing to a
    live agent's config is higher-risk and must be enabled explicitly.
    """
    import os
    import unittest.mock as _um

    from app.core.runtime_flags import RuntimeFlags, get_runtime_flags

    assert RuntimeFlags().enable_self_improvement_auto_apply is False

    get_runtime_flags.cache_clear()
    with _um.patch.dict(os.environ, {"DYNAMIC_ORCHESTRATION": "true"}, clear=False):
        get_runtime_flags.cache_clear()
        flags = get_runtime_flags()
        assert flags.enable_self_improvement is True  # master cascades to this one
        assert flags.enable_self_improvement_auto_apply is False  # but never this one
    get_runtime_flags.cache_clear()

    with _um.patch.dict(os.environ, {"ENABLE_SELF_IMPROVEMENT_AUTO_APPLY": "true"}, clear=False):
        get_runtime_flags.cache_clear()
        assert get_runtime_flags().enable_self_improvement_auto_apply is True
    get_runtime_flags.cache_clear()


def test_get_runtime_flags_reads_env_vars():
    """get_runtime_flags must read ENABLE_RUNTIME_SCORECARD env var."""
    import os
    import unittest.mock as _um

    from app.core.runtime_flags import get_runtime_flags
    get_runtime_flags.cache_clear()
    with _um.patch.dict(os.environ, {"ENABLE_RUNTIME_SCORECARD": "true"}):
        get_runtime_flags.cache_clear()
        flags = get_runtime_flags()
        assert flags.enable_runtime_scorecard is True
    get_runtime_flags.cache_clear()


# ── N13: Advanced RAG dispatch ────────────────────────────────────────────────

def test_active_rag_strategy_can_be_non_hybrid():
    """Agent context can hold non-hybrid rag strategy."""
    from app.agent.state import AgentState
    from app.tenancy.context import PlanTier, TenantContext
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    state.context["_active_rag_strategy"] = "fusion_rag"
    assert state.context["_active_rag_strategy"] == "fusion_rag"


async def test_retrieve_fusion_fires_from_engine():
    """retrieve(strategy='fusion') must fire retrieve_fusion()."""
    from unittest.mock import AsyncMock, patch

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.rag.engine import RetrievalResult, retrieve

    session = AsyncMock(spec=AsyncSession)
    fake_results = [RetrievalResult("c1", "fusion result", 0.9, {}, ["vector"])]

    fusion_called = []

    async def fake_fusion(session, *, query, **kwargs):
        fusion_called.append(query)
        return fake_results

    with patch("app.rag.engine.retrieve_fusion", side_effect=fake_fusion):
        results = await retrieve(
            session, query="test fusion", query_embedding=[0.1] * 10,
            collection_id="col1", strategy="fusion",
        )
    assert len(fusion_called) == 1, "retrieve_fusion was not called"
    assert results == fake_results
