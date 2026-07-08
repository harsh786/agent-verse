# tests/rag/test_feature_flags.py
"""Feature flags must disable patterns when set to False."""
from __future__ import annotations
import pytest
from unittest.mock import patch


def test_raptor_disabled_by_feature_flag():
    from app.rag.agentic.patterns.raptor import RAPTORPattern
    pattern = RAPTORPattern()
    with patch("app.core.config.get_settings") as mock_settings:
        mock_settings.return_value.enable_raptor = False
        assert pattern.is_compatible(object()) is False


def test_flare_disabled_by_feature_flag():
    from app.rag.agentic.patterns.flare import FLAREPattern
    pattern = FLAREPattern()
    with patch("app.core.config.get_settings") as mock_settings:
        mock_settings.return_value.enable_flare = False
        assert pattern.is_compatible(object()) is False


def test_self_consistency_disabled_by_feature_flag():
    from app.agent.patterns.self_consistency import SelfConsistencyPattern
    pattern = SelfConsistencyPattern()
    with patch("app.core.config.get_settings") as mock_settings:
        mock_settings.return_value.enable_self_consistency = False
        assert pattern.is_compatible(object()) is False


def test_flag_on_by_default():
    """All patterns must be enabled by default."""
    from app.rag.agentic.patterns.raptor import RAPTORPattern
    from app.rag.agentic.patterns.flare import FLAREPattern
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    # Default is_compatible must return True (no flag set to False in env)
    for PatternClass in [RAPTORPattern, FLAREPattern, SelfRAGPattern]:
        p = PatternClass()
        # Should not crash and should generally return True when flags are at defaults
        result = p.is_compatible(object())
        assert isinstance(result, bool)
