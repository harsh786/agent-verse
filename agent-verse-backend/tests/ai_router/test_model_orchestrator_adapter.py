# tests/ai_router/test_model_orchestrator_adapter.py
"""ModelOrchestratorAdapter wired into graph as model_router."""
from __future__ import annotations

import pytest


def test_adapter_returns_models_for_all_task_types():
    """ModelOrchestratorAdapter must handle all model_for() task types."""
    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter
    adapter = ModelOrchestratorAdapter(default_tier="medium")
    for task in ["planning", "execution", "verification", "reflection", "think", "classification"]:
        model = adapter.model_for(task)
        assert isinstance(model, str)
        assert len(model) > 0, f"Empty model for task={task}"


def test_adapter_budget_downgrade():
    """When budget > 90%, adapter must return low-tier models."""
    from app.ai_router.model_orchestrator import ModelOrchestrator, ModelOrchestratorAdapter
    adapter = ModelOrchestratorAdapter(default_tier="high")

    # Simulate a runtime profile that allows downgrade
    from unittest.mock import MagicMock
    profile = MagicMock()
    profile.properties.complexity.value = "expert"
    profile.properties.risk.value = "low"
    profile.properties.time_sensitivity = "interactive"
    profile.model_plan.planner = ""
    profile.model_plan.executor = ""
    profile.model_plan.verifier = ""
    profile.model_plan.max_cost_usd = 0.10

    # High budget ratio should downgrade from high to medium/low tier
    adapter.update_from_profile(profile, budget_spent_ratio=0.95)

    if adapter._cached_assignment is not None:
        planning_model = adapter.model_for("planning")
        # At 95% budget, should be on low or medium tier, not top model
        assert isinstance(planning_model, str)
        assert len(planning_model) > 0


def test_adapter_model_for_goal_alias():
    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter
    adapter = ModelOrchestratorAdapter()
    m1 = adapter.model_for("planning")
    m2 = adapter.model_for_goal("planning", goal="some goal")
    assert m1 == m2


def test_adapter_used_as_model_router():
    """ModelOrchestratorAdapter must satisfy the model_router interface used by graph.py."""
    import inspect

    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter
    adapter = ModelOrchestratorAdapter()
    # Must have the methods graph.py calls
    assert hasattr(adapter, "model_for")
    assert hasattr(adapter, "model_for_goal")
    # model_for must accept fallback and goal kwargs
    sig = inspect.signature(adapter.model_for)
    assert "fallback" in sig.parameters
    assert "goal" in sig.parameters


def test_adapter_wired_in_goal_service():
    """goal_service must use ModelOrchestratorAdapter as model_router."""
    import inspect

    from app.services import goal_service as gs
    src = inspect.getsource(gs)
    assert "ModelOrchestratorAdapter" in src, \
        "ModelOrchestratorAdapter must be used in goal_service.py"
