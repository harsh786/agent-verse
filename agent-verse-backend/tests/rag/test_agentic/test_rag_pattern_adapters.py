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
from tests.rag.colbert_fakes import DeterministicColBERTReranker


def test_all_rag_pattern_adapters_importable():
    adapters = [CorrectiveRAGPattern, AdaptiveRAGPattern, SelfRAGPattern,
                SpeculativeRAGPattern, FusionRAGPattern, FLAREPattern,
                RAPTORPattern, AgenticChunkingPattern, ColBERTPattern]
    for cls in adapters:
        p = (
            cls(reranker=DeterministicColBERTReranker())
            if cls is ColBERTPattern
            else cls()
        )
        assert isinstance(p, RAGPattern)


def test_all_rag_patterns_have_unique_ids():
    adapters = [CorrectiveRAGPattern(), AdaptiveRAGPattern(), SelfRAGPattern(),
                SpeculativeRAGPattern(), FusionRAGPattern(), FLAREPattern(),
                RAPTORPattern(), AgenticChunkingPattern(),
                ColBERTPattern(reranker=DeterministicColBERTReranker())]
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
        p = (
            cls(reranker=DeterministicColBERTReranker())
            if cls is ColBERTPattern
            else cls()
        )
        assert p.state in (
            RAGPatternState.IMPLEMENTED,
            RAGPatternState.PARTIAL,
            RAGPatternState.PLANNED,
        )


def test_all_patterns_list_in_init():
    from app.rag.catalogue import RAG_RUNTIME_CAPABILITIES

    assert patterns_pkg.RAG_RUNTIME_ADAPTERS is RAG_RUNTIME_CAPABILITIES
    assert len(patterns_pkg.RAG_RUNTIME_ADAPTERS) == 20


def test_registry_pattern_ids_match_adapters():
    from app.orchestration.strategy_registry import build_default_registry

    registry = build_default_registry()
    for strategy, adapter in patterns_pkg.RAG_RUNTIME_ADAPTERS.items():
        cap = registry.get(strategy.value)
        assert cap is not None, f"Canonical RAG strategy '{strategy.value}' is not registered"
        assert cap.runtime_adapter is adapter
