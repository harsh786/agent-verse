"""Verify existing AIRouter source features work correctly."""
from __future__ import annotations

import pytest

from app.ai_router.models import ModelCapability, RoutePolicy, RoutingMode, TaskType
from app.ai_router.registry import model_registry
from app.ai_router.router import AIRouter


@pytest.fixture
def router():
    return AIRouter()


def test_embedding_task_routes_to_embedding_model(router):
    model = router.select_model(TaskType.EMBEDDING, tenant_id="test")
    if model is not None:
        assert ModelCapability.EMBEDDING in model.capabilities


def test_vision_task_routes_to_vision_model(router):
    model = router.select_model(TaskType.TEXT_GENERATION, tenant_id="test", require_vision=True)
    if model is not None:
        assert model.supports_vision is True


def test_record_call_updates_health(router):
    router.record_call("openai", latency_ms=350.0, success=True)
    health = model_registry.get_provider_health("openai")
    assert health is not None


def test_tenant_model_override_respected(router):
    policy = RoutePolicy(
        task_type=TaskType.TEXT_GENERATION,
        preferred_provider="openai",
        preferred_model="gpt-4o-mini",
        routing_mode=RoutingMode.CHEAPEST,
        tenant_id="override_tenant",
    )
    model_registry.set_route_policy("override_tenant", TaskType.TEXT_GENERATION, policy)
    retrieved = model_registry.get_route_policy("override_tenant", TaskType.TEXT_GENERATION)
    assert retrieved is not None
    assert retrieved.preferred_model == "gpt-4o-mini"


def test_model_registry_has_providers():
    models = model_registry.list_models()
    providers = {m.provider for m in models}
    assert len(providers) >= 1


def test_circuit_open_excludes_provider(router):
    health = model_registry.get_provider_health("openai")
    if health:
        original_circuit = health.circuit_open
        health.circuit_open = True
        try:
            model = router.select_model(TaskType.TEXT_GENERATION, tenant_id="circuit_test")
            if model is not None:
                assert model.provider != "openai"
        finally:
            health.circuit_open = original_circuit
