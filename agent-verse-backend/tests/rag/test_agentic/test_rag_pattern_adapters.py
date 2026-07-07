"""All RAG pattern adapters from doc-1 must be importable with standard interface."""
from __future__ import annotations

import pytest
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
from app.rag.agentic.patterns.self_rag import SelfRAGPattern
from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
from app.rag.agentic.patterns.fusion import FusionRAGPattern
from app.rag.agentic.patterns.flare import FLAREPattern
from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
from app.rag.agentic.patterns.colbert import ColBERTPattern
import app.rag.agentic.patterns as patterns_pkg


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
        assert p.state in (RAGPatternState.IMPLEMENTED, RAGPatternState.PARTIAL, RAGPatternState.PLANNED)


def test_all_patterns_list_in_init():
    assert hasattr(patterns_pkg, "ALL_RAG_PATTERNS")
    assert len(patterns_pkg.ALL_RAG_PATTERNS) >= 9


def test_registry_pattern_ids_match_adapters():
    from app.orchestration.strategy_registry import build_default_registry
    registry = build_default_registry()
    for pattern in patterns_pkg.ALL_RAG_PATTERNS:
        cap = registry.get(pattern.pattern_id)
        assert cap is not None, f"RAG adapter '{pattern.pattern_id}' not in StrategyRegistry"
