"""Regression: tenant routing policies were process-local and never applied.

PUT /models/routing-policies saved into one API process's ModelRegistry dict,
read only by ai_router.select_model — whose result GoalService merely logged.
Other replicas and the Celery worker never saw a policy, and no goal used it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_router import registry_store
from app.ai_router.models import ModelRoutePolicy, RoutingMode, TaskType
from app.ai_router.registry import (
    ModelRegistry,
    model_registry,
    policy_is_enforced,
    tenant_policy_role_models,
)
from app.ai_router.registry_store import ModelRegistryStore


class _SyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value


class _BrokenRedis:
    def get(self, key: str) -> Any:
        raise ConnectionError("redis down")

    def set(self, key: str, value: str) -> None:
        raise ConnectionError("redis down")


@pytest.fixture
def shared_store(monkeypatch: pytest.MonkeyPatch) -> _SyncRedis:
    redis = _SyncRedis()
    monkeypatch.setattr(registry_store, "_store", ModelRegistryStore(redis))
    return redis


def _pin(task: TaskType, model: str) -> ModelRoutePolicy:
    return ModelRoutePolicy(
        task_type=task,
        routing_mode=RoutingMode.MODEL_PINNED,
        preferred_provider="openai",
        preferred_model=model,
    )


def test_policy_saved_on_one_replica_is_visible_on_another(shared_store: _SyncRedis) -> None:
    replica_a, replica_b = ModelRegistry(), ModelRegistry()
    replica_a.set_route_policy("t-rp", TaskType.PLANNING, _pin(TaskType.PLANNING, "gpt-x"))

    policies = replica_b.list_route_policies("t-rp")

    assert policies["planning"].preferred_model == "gpt-x"
    assert replica_b.get_route_policy("t-rp", TaskType.PLANNING) is not None


def test_policy_write_failure_raises_instead_of_process_local_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry_store, "_store", ModelRegistryStore(_BrokenRedis()))
    reg = ModelRegistry()
    with pytest.raises(ConnectionError):
        reg.set_route_policy("t-rp2", TaskType.PLANNING, _pin(TaskType.PLANNING, "gpt-x"))
    assert reg._tenant_policies.get("t-rp2") is None


def test_policy_role_models_pin_graph_roles(shared_store: _SyncRedis) -> None:
    model_registry.set_route_policy("t-rp3", TaskType.PLANNING, _pin(TaskType.PLANNING, "big"))
    model_registry.set_route_policy(
        "t-rp3", TaskType.VERIFICATION, _pin(TaskType.VERIFICATION, "small")
    )
    model_registry.set_route_policy("t-rp3", TaskType.OCR, _pin(TaskType.OCR, "ocr-model"))

    assert tenant_policy_role_models("t-rp3") == {"planning": "big", "verification": "small"}
    # A multi-endpoint provider that cannot serve a model is not pinned to it.
    assert tenant_policy_role_models("t-rp3", servable={"small"}) == {"verification": "small"}


def test_policy_enforced_flag_is_honest() -> None:
    assert policy_is_enforced(_pin(TaskType.EXECUTION, "m")) is True
    assert policy_is_enforced(_pin(TaskType.OCR, "m")) is False
    assert (
        policy_is_enforced(
            ModelRoutePolicy(task_type=TaskType.PLANNING, routing_mode=RoutingMode.CHEAPEST)
        )
        is False
    )


def test_goal_graph_router_uses_tenant_policy(shared_store: _SyncRedis) -> None:
    from unittest.mock import MagicMock

    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    model_registry.set_route_policy(
        "t-rp4", TaskType.PLANNING, _pin(TaskType.PLANNING, "tenant-planner-model")
    )
    ctx = TenantContext(tenant_id="t-rp4", plan=PlanTier.ENTERPRISE, api_key_id="k")
    app_state = MagicMock()
    for name in (
        "audit_log", "cost_controller", "redis_cost_controller", "hitl_gateway",
        "knowledge_store", "long_term_memory", "eval_runner", "policy_engine",
        "permission_matrix",
    ):
        setattr(app_state, name, None)
    app_state._llm_configs = {}
    svc = GoalService()
    svc._app_state = app_state

    loop = svc._make_agent_loop_for_tenant(ctx, app_state)

    assert loop._model_router.model_for("planning") == "tenant-planner-model"
