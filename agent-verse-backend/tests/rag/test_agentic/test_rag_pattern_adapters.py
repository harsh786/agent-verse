"""All RAG pattern adapters from doc-1 must be importable with standard interface."""
from __future__ import annotations

import app.rag.agentic.patterns as patterns_pkg
from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.agentic.patterns.colbert import ColBERTPattern
from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
from app.rag.agentic.patterns.flare import FLAREPattern
from app.rag.agentic.patterns.fusion import FusionRAGPattern
from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.agentic.patterns.self_rag import SelfRAGPattern
from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern


def test_all_rag_pattern_adapters_importable():
    adapters = [CorrectiveRAGPattern, AdaptiveRAGPattern, SelfRAGPattern,
                SpeculativeRAGPattern, FusionRAGPattern, FLAREPattern,
                RAPTORPattern, AgenticChunkingPattern, ColBERTPattern]
    for cls in adapters:
        p = cls()
        assert isinstance(p, RAGPattern)


def test_all_rag_patterns_have_unique_ids():
    adapters = [CorrectiveRAGPattern(), AdaptiveRAGPattern(), SelfRAGPattern(),
                SpeculativeRAGPattern(), FusionRAGPattern(), FLAREPattern(),
                RAPTORPattern(), AgenticChunkingPattern(), ColBERTPattern()]
    ids = [p.pattern_id for p in adapters]
    assert len(ids) == len(set(ids))


def test_corrective_rag_has_correct_id():
    assert CorrectiveRAGPattern().pattern_id == "corrective_rag"


def test_adaptive_rag_has_correct_id():
    assert AdaptiveRAGPattern().pattern_id == "adaptive_rag"


def test_self_rag_has_correct_id():
    assert SelfRAGPattern().pattern_id == "self_rag"


def test_flare_has_correct_id():
    assert FLAREPattern().pattern_id == "flare"


def test_raptor_has_correct_id():
    assert RAPTORPattern().pattern_id == "raptor"


def test_all_patterns_have_state():
    for cls in [CorrectiveRAGPattern, AdaptiveRAGPattern, SelfRAGPattern,
                SpeculativeRAGPattern, FusionRAGPattern, FLAREPattern,
                RAPTORPattern, AgenticChunkingPattern, ColBERTPattern]:
        p = cls()
        assert p.state in (
            RAGPatternState.IMPLEMENTED,
            RAGPatternState.PARTIAL,
            RAGPatternState.PLANNED,
        )


def test_all_patterns_list_in_init():
    assert hasattr(patterns_pkg, "ALL_RAG_PATTERNS")
    assert len(patterns_pkg.ALL_RAG_PATTERNS) >= 9


def test_registry_pattern_ids_match_adapters():
    from app.orchestration.strategy_registry import build_default_registry
    from app.rag.contracts import RAGStrategy

    registry = build_default_registry()
    canonical_ids = {
        "corrective_rag": RAGStrategy.CORRECTIVE,
        "adaptive_rag": RAGStrategy.ADAPTIVE,
        "self_rag": RAGStrategy.SELF_RAG,
        "speculative_rag": RAGStrategy.SPECULATIVE,
            "fusion_rag": RAGStrategy.FUSION,
            "graph_rag": RAGStrategy.GRAPH,
            "web_augmented": RAGStrategy.WEB_AUGMENTED,
        "flare": RAGStrategy.FLARE,
        "raptor": RAGStrategy.RAPTOR,
        "agentic_chunking": RAGStrategy.AGENTIC_CHUNKING,
        "colbert_late_interaction": RAGStrategy.COLBERT,
    }
    for pattern in patterns_pkg.ALL_RAG_PATTERNS:
        strategy = canonical_ids[pattern.pattern_id]
        cap = registry.get(strategy.value)
        assert cap is not None, f"Canonical RAG strategy '{strategy.value}' is not registered"
