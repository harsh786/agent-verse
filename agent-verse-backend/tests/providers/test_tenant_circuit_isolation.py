"""Cross-tenant interference via the process-global LLM circuit breaker / model test.

Regressions:
* ``breaker_key`` was ``llm:{model}`` on a process-global breaker, so one tenant's
  failing BYOK key (revoked, over quota) opened the circuit for that model for
  EVERY tenant in the process.
* ``POST /models/test`` was callable by any tenant, spent a platform LLM call and
  wrote the result into the GLOBAL provider health used by routing.
"""

_ISOLATE_PROVIDER_ENV = True

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.providers import circuit_breaker as cb
from app.providers.base import CompletionRequest, Message
from app.providers.tenant_provider import tenant_circuit_scope


class _Provider:
    _default_model = "shared-model"

    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.calls = 0

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        if self.fail:
            raise RuntimeError("401 invalid key")
        return "ok"


def _req() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="hi")], model="shared-model")


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb, "_provider_cb", cb.ProviderCircuitBreaker(failure_threshold=2))


async def test_one_tenants_failing_byok_key_does_not_open_other_tenants_circuit() -> None:
    bad = _Provider(fail=True)
    bad._circuit_scope = tenant_circuit_scope("tenant-a")  # type: ignore[attr-defined]
    good = _Provider(fail=False)
    good._circuit_scope = tenant_circuit_scope("tenant-b")  # type: ignore[attr-defined]

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.complete_with_failover(bad, _req())
    # tenant-a's circuit is now open (fast-fail without calling the provider) ...
    calls = bad.calls
    with pytest.raises(RuntimeError, match="circuit open"):
        await cb.complete_with_failover(bad, _req())
    assert bad.calls == calls
    # ... but tenant-b, same model, is unaffected.
    assert await cb.complete_with_failover(good, _req()) == "ok"
    # Platform (unscoped) circuit for the model is also unaffected.
    assert await cb.complete_with_failover(_Provider(fail=False), _req()) == "ok"


def test_breaker_key_scopes_tenant_providers_only() -> None:
    p = _Provider(fail=False)
    assert cb.breaker_key(p, _req()) == "llm:shared-model"
    p._circuit_scope = "tenant:t1"  # type: ignore[attr-defined]
    assert cb.breaker_key(p, _req()) == "tenant:t1:llm:shared-model"


def test_breaker_key_sees_scope_through_traced_wrapper() -> None:
    from app.observability.traced_provider import TracedProvider

    p = _Provider(fail=False)
    p._circuit_scope = "tenant:t2"  # type: ignore[attr-defined]
    traced = TracedProvider(p, provider_system="openai", default_role="planner")
    assert cb.breaker_key(traced, _req()) == "tenant:t2:llm:shared-model"


# ── POST /models/test ─────────────────────────────────────────────────────────


def _client(monkeypatch: pytest.MonkeyPatch, provider: Any) -> TestClient:
    from app.ai_router.registry import model_registry
    from app.api.model_registry import router as models_router
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    ctx = TenantContext(tenant_id="tid-mt", plan=PlanTier.FREE, api_key_id="kid-mt")

    async def _resolve(key: str) -> Any:
        return ctx if key == "ak_mt" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(models_router)
    app.state._app_provider = provider
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "admin-secret")
    monkeypatch.setattr(model_registry, "get_model", lambda prov, mid: object())
    return TestClient(app)


def test_models_test_refuses_regular_tenant_and_leaves_global_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai_router.registry import model_registry

    provider = _Provider(fail=True)
    client = _client(monkeypatch, provider)
    health: list[Any] = []
    monkeypatch.setattr(model_registry, "update_health", lambda *a, **k: health.append(a))

    r = client.post(
        "/models/test",
        headers={"X-API-Key": "ak_mt"},
        json={"provider": "openai", "model_id": "shared-model"},
    )
    assert r.status_code == 403
    assert provider.calls == 0  # no platform LLM spend
    assert health == []  # global provider health untouched


def test_models_test_allowed_for_platform_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai_router.registry import model_registry

    provider = _Provider(fail=False)
    client = _client(monkeypatch, provider)
    monkeypatch.setattr(model_registry, "update_health", lambda *a, **k: None)
    provider.complete = _ok_complete  # type: ignore[method-assign]

    r = client.post(
        "/models/test",
        headers={"X-API-Key": "ak_mt", "X-Admin-Key": "admin-secret"},
        json={"provider": "openai", "model_id": "shared-model"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def _ok_complete(request: Any) -> Any:
    class _R:
        content = "OK"

    return _R()
