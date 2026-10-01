"""PROV-24: the unwired routing modules are either wired properly or removed.

* capability enforcement — live selection now requires tool use for execution
  (the standalone providers/model_router.py never enforced required_capabilities
  and duplicated the live router with hard-coded cloud slugs: removed);
* shadow traffic — a sampled candidate model is shadowed behind a flag, through
  complete_decision (breaker, timeout, tracing, cost metric), as an uncharged
  platform job, with a cross-replica shadow log and an admin API;
* complexity scorer — superseded by CostLatencyQualityPolicy (removed).
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ai_router import registry_store
from app.ai_router.models import ModelCapability, ModelEndpoint, TaskType
from app.ai_router.registry import ModelRegistry
from app.providers import guarded_completion as gc
from app.providers.base import CompletionRequest, CompletionResponse, Message


def test_dead_duplicate_routers_are_gone() -> None:
    for mod in ("app.providers.model_router", "app.ai_router.complexity_scorer"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_execution_selection_requires_tool_use() -> None:
    from app.ai_router.selection import select_configured_model_id

    reg = ModelRegistry()
    reg.register_configured(
        ModelEndpoint(
            provider="gemini", model_id="chat-only", display_name="c",
            capabilities=[ModelCapability.TEXT_GENERATION], cost_per_1k_input=0.0,
        )
    )
    reg.register_configured(
        ModelEndpoint(
            provider="openai", model_id="tool-model", display_name="t",
            capabilities=[ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE],
            cost_per_1k_input=0.5, supports_tools=True,
        )
    )
    assert select_configured_model_id(TaskType.EXECUTION, registry=reg) == "tool-model"
    assert select_configured_model_id(TaskType.PLANNING, registry=reg) == "chat-only"


class _Provider:
    _default_model = "prod-model"

    def __init__(self, *, fail_shadow: bool = False) -> None:
        self.models: list[str] = []
        self.fail_shadow = fail_shadow

    async def complete(self, request: Any) -> CompletionResponse:
        self.models.append(request.model)
        if self.fail_shadow and request.model == "candidate-model":
            raise ConnectionError("candidate down")
        return CompletionResponse(
            content=f"answer from {request.model}", model=request.model,
            input_tokens=100, output_tokens=10,
        )


class _SyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value


@pytest.fixture
def shadow_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AGENTVERSE_SHADOW_MODEL", "candidate-model")
    monkeypatch.setenv("AGENTVERSE_SHADOW_SAMPLE_RATE", "1.0")
    saved = registry_store.get_model_registry_store()
    registry_store.set_model_registry_store(registry_store.ModelRegistryStore(_SyncRedis()))
    yield
    registry_store._store = saved  # type: ignore[attr-defined]


def _req() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="q")], model="prod-model")


@pytest.fixture
def controller() -> Any:
    recorded: list[str] = []

    class _Ctrl:
        async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any) -> bool:
            recorded.append(tenant_ctx.tenant_id)
            return True

        async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
            return True

    gc.set_platform_cost_services(lambda: (_Ctrl(), None))
    return recorded


async def test_shadow_is_fired_logged_and_not_charged_to_the_tenant(
    shadow_on: Any, controller: list[str]
) -> None:
    from app.ai_router.shadow_router import drain_shadow_tasks, shadow_log

    provider = _Provider()
    resp = await gc.complete_decision(provider, _req(), role="eval_judge", tenant_id="t1")
    await drain_shadow_tasks()
    assert resp.content == "answer from prod-model"  # primary untouched
    assert provider.models == ["prod-model", "candidate-model"]
    assert controller == ["t1"]  # only the primary call was charged to the tenant
    [entry] = shadow_log()
    assert entry["shadow_model"] == "candidate-model" and entry["role"] == "eval_judge"
    assert entry["shadow_cost_usd"] > 0 and entry["error"] is None


async def test_shadow_failure_never_affects_the_primary(shadow_on: Any, controller: Any) -> None:
    from app.ai_router.shadow_router import drain_shadow_tasks, shadow_log

    provider = _Provider(fail_shadow=True)
    resp = await gc.complete_decision(provider, _req(), role="x", tenant_id="t1")
    await drain_shadow_tasks()
    assert resp.content == "answer from prod-model"
    assert "candidate down" in shadow_log()[0]["error"]


async def test_no_shadow_when_the_flag_is_off(monkeypatch: pytest.MonkeyPatch, controller: Any) -> None:
    from app.ai_router.shadow_router import drain_shadow_tasks

    monkeypatch.delenv("AGENTVERSE_SHADOW_MODEL", raising=False)
    provider = _Provider()
    await gc.complete_decision(provider, _req(), role="x", tenant_id="t1")
    await drain_shadow_tasks()
    assert provider.models == ["prod-model"]


async def test_shadow_log_api_is_admin_only(
    shadow_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from app.api.model_registry import get_shadow_log

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "k")
    request = MagicMock()
    request.state = SimpleNamespace(tenant=SimpleNamespace(tenant_id="t"))
    request.headers = {}
    with pytest.raises(HTTPException):
        await get_shadow_log(request, limit=50)
    request.headers = {"x-admin-key": "k"}
    out = await get_shadow_log(request, limit=50)
    assert out["shadow_model"] == "candidate-model" and out["entries"] == []
