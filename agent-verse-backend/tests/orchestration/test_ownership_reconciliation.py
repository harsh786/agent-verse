from __future__ import annotations

import inspect

from app.agent.dynamic_graph import DynamicGraphAssembler
from app.agent.model_router import ModelRouter
from app.agent.pattern_config import PatternConfig
from app.orchestration.graph_factory import GraphFactory
from app.providers.fake import FakeProvider


def test_legacy_model_router_delegates_complexity_to_canonical_classifier() -> None:
    source = inspect.getsource(ModelRouter)

    assert "_SIMPLE_PATTERNS" not in source
    assert "_COMPLEX_PATTERNS" not in source
    assert "GoalClassifier" in source


def test_model_orchestrator_status_declares_canonical_live_assignment_ownership() -> None:
    from app.ai_router import model_orchestrator

    assert "canonical live model assignment owner" in model_orchestrator.__doc__.lower()
    assert "not wired" not in model_orchestrator.__doc__.lower()


def test_dynamic_graph_translates_legacy_config_and_calls_graph_factory(monkeypatch) -> None:
    captured: dict[str, object] = {}
    original = GraphFactory.create

    def capture(self: GraphFactory, profile: object, services: object, agent_config: object = None):
        captured["profile"] = profile
        return original(self, profile, services, agent_config)

    monkeypatch.setattr(GraphFactory, "create", capture)
    provider = FakeProvider()
    graph = DynamicGraphAssembler().assemble(
        PatternConfig(reasoning_patterns=["react", "reflection"]),
        planner=provider,
        executor=provider,
        verifier=provider,
    )

    profile = captured["profile"]
    assert profile.profile_version == 2
    assert profile.primary_strategy.strategy_id == "react"
    assert graph.runtime_profile is profile


def test_workflow_mode_translation_is_registry_owned() -> None:
    from app.orchestration.workflow_compatibility import strategy_for_workflow_mode

    assert strategy_for_workflow_mode("single_agent") == "react"
    assert strategy_for_workflow_mode("workflow") == "workflow_dag"
    assert strategy_for_workflow_mode("dag") == "workflow_dag"
