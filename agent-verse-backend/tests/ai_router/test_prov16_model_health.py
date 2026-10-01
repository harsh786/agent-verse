"""PROV-16: model health is "unverified" until a call or probe actually checks it.

Health defaulted to healthy, was only ever written by the admin test ping (per
process), and ``AIRouter.record_call`` had no callers; the ping always called
the app provider but recorded the result against whichever provider was named.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ai_router import registry as registry_mod
from app.ai_router.registry import ModelRegistry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.providers.base import CompletionResponse


class _SyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value


@pytest.fixture
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> ModelRegistry:
    reg = ModelRegistry()
    monkeypatch.setattr(registry_mod, "model_registry", reg)
    import app.ai_router.router as router_mod
    import app.api.model_registry as api_mod

    monkeypatch.setattr(router_mod, "model_registry", reg)
    monkeypatch.setattr(api_mod, "model_registry", reg)
    return reg


@pytest.fixture
def no_store() -> Any:
    from app.ai_router import registry_store

    saved = registry_store.get_model_registry_store()
    registry_store._store = None  # type: ignore[attr-defined]
    yield
    registry_store._store = saved  # type: ignore[attr-defined]


def test_fresh_registry_reports_unverified(fresh_registry: ModelRegistry, no_store: Any) -> None:
    h = fresh_registry.get_provider_health("nvidia")
    assert h.is_healthy is None and h.last_checked_at is None

    from app.api.model_registry import _health_dict

    assert _health_dict("nvidia")["is_healthy"] is None


def test_health_is_shared_across_replicas_via_the_store(no_store: Any) -> None:
    redis = _SyncRedis()
    set_model_registry_store(ModelRegistryStore(redis))
    a, b = ModelRegistry(), ModelRegistry()
    a.update_health("nvidia", latency_ms=120, error=False)
    hb = b.get_provider_health("nvidia")
    assert hb.is_healthy is True and hb.last_checked_at is not None


class _Provider:
    def __init__(self, ptype: str = "nvidia", *, fail: bool = False, byok: bool = False) -> None:
        self._default_model = "m1"
        self._agentverse_provider_type = ptype
        if byok:
            self._byok_tenant_id = "t1"
        self.fail = fail

    async def complete(self, request: Any) -> CompletionResponse:
        if self.fail:
            raise ConnectionError("endpoint down")
        return CompletionResponse(content="ok", model="m1", input_tokens=1, output_tokens=1)


def _req(model: str = "m1") -> Any:
    from app.providers.base import CompletionRequest, Message

    return CompletionRequest(messages=[Message(role="user", content="x")], model=model)


async def test_decision_calls_feed_provider_health(
    fresh_registry: ModelRegistry, no_store: Any
) -> None:
    from app.providers.guarded_completion import complete_decision

    await complete_decision(_Provider("nvidia"), _req(), role="x", charge=False)
    assert fresh_registry.get_provider_health("nvidia").is_healthy is True

    with pytest.raises(ConnectionError):
        await complete_decision(_Provider("openai", fail=True), _req("m2"), role="x", charge=False)
    h = fresh_registry.get_provider_health("openai")
    assert h.last_error and "endpoint down" in h.last_error


async def test_byok_failures_never_touch_platform_health(
    fresh_registry: ModelRegistry, no_store: Any
) -> None:
    from app.providers.guarded_completion import complete_decision

    with pytest.raises(ConnectionError):
        await complete_decision(
            _Provider("openai", fail=True, byok=True), _req("m3"), role="x", charge=False
        )
    assert fresh_registry.get_provider_health("openai").is_healthy is None


def test_executor_outcomes_feed_provider_health(
    fresh_registry: ModelRegistry, no_store: Any
) -> None:
    import time

    from app.agent.nodes.executor_mixin import ExecutorMixin
    from app.ai_router.models import ModelEndpoint

    fresh_registry.register_configured(
        ModelEndpoint(provider="nvidia", model_id="served-model", display_name="s")
    )
    host = SimpleNamespace(_model_router=None)
    ExecutorMixin._record_provider_health(host, "served-model", ok=False, start=time.monotonic())  # type: ignore[arg-type]
    assert fresh_registry.get_provider_health("nvidia").error_rate_5m > 0


def _admin_request(app_provider: Any, body: dict[str, Any]) -> Any:
    request = MagicMock()
    request.state = SimpleNamespace(tenant=SimpleNamespace(tenant_id="t"))
    request.headers = {"x-admin-key": "k"}
    request.app.state = SimpleNamespace(_app_provider=app_provider)

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json
    return request


async def test_probe_of_a_provider_not_backing_the_app_is_skipped(
    fresh_registry: ModelRegistry, no_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai_router.models import ModelEndpoint
    from app.api.model_registry import test_model

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "k")
    fresh_registry._models["anthropic/claude-x"] = ModelEndpoint(
        provider="anthropic", model_id="claude-x", display_name="c"
    )
    out = await test_model(
        _admin_request(_Provider("nvidia"), {"provider": "anthropic", "model_id": "claude-x"})
    )
    assert out["status"] == "skipped"
    assert fresh_registry.get_provider_health("anthropic").is_healthy is None


async def test_probe_of_the_backing_provider_records_its_health(
    fresh_registry: ModelRegistry, no_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai_router.models import ModelEndpoint
    from app.api.model_registry import test_model

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "k")
    fresh_registry._models["nvidia/m1"] = ModelEndpoint(
        provider="nvidia", model_id="m1", display_name="m"
    )
    out = await test_model(_admin_request(_Provider("nvidia"), {"provider": "nvidia", "model_id": "m1"}))
    assert out["status"] == "ok"
    assert fresh_registry.get_provider_health("nvidia").is_healthy is True
