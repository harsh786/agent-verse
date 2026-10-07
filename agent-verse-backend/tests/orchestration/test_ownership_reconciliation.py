from __future__ import annotations

import inspect

from app.agent.model_router import ModelRouter


def test_legacy_model_router_delegates_complexity_to_canonical_classifier() -> None:
    source = inspect.getsource(ModelRouter)

    assert "_SIMPLE_PATTERNS" not in source
    assert "_COMPLEX_PATTERNS" not in source
    assert "GoalClassifier" in source


def test_model_orchestrator_status_declares_canonical_live_assignment_ownership() -> None:
    from app.ai_router import model_orchestrator

    assert "canonical live model assignment owner" in model_orchestrator.__doc__.lower()
    assert "not wired" not in model_orchestrator.__doc__.lower()

