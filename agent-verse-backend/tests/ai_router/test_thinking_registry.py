"""Model Registry ``thinking`` setting (auto / off / on + budget) and Test connection.

* ``POST /models/configured`` stores ``thinking`` / ``thinking_budget_tokens``
  (Redis store → seeder → registry) and ``GET /models/configured`` returns them;
  entries saved before the setting existed read as "auto";
* ``POST /models/configured/test-endpoint`` tells whether the model is a
  thinking model and whether turning thinking off works;
* ``POST /models/test`` reports what the provider saw.
"""

_ISOLATE_PROVIDER_ENV = True

import json
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api.model_registry import router as models_router
from app.providers import openai_compatible as oc
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_LAN = "http://192.168.63.104:30080/v1"
_QWEN = "Qwen/Qwen3.5-4B"
_ADMIN_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
)
_ADMIN = {"X-API-Key": "ak_admin"}


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get(self, k: str) -> Any:
        return self.store.get(k)

    def set(self, k: str, v: Any) -> None:
        self.store[k] = v

    def incr(self, k: str) -> int:
        self.store[k] = str(int(self.store.get(k) or 0) + 1)
        return int(self.store[k])


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    oc._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    oc._THINKING_AUTO_OFF.clear()
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    oc._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    oc._THINKING_AUTO_OFF.clear()
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _client(redis: _FakeRedis | None = None) -> TestClient:
    set_model_registry_store(ModelRegistryStore(redis or _FakeRedis()))
    app = FastAPI()

    async def _resolve(key: str) -> Any:
        return _ADMIN_CTX if key == "ak_admin" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return TestClient(app)


def _row(client: TestClient, model_id: str = _QWEN) -> dict[str, Any]:
    groups = client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
    return next(m for g in groups for m in g["models"] if m["model_id"] == model_id)


_QWEN_BODY = {"provider": "openai_compatible", "model_id": _QWEN,
              "capabilities": ["text_generation"], "base_url": _LAN}


# ── registry setting round-trip ──────────────────────────────────────────────


def test_thinking_setting_round_trips_through_the_api() -> None:
    from app.ai_router.model_endpoints import registry_thinking_settings

    redis = _FakeRedis()
    client = _client(redis)
    r = client.post("/models/configured", headers=_ADMIN,
                    json={**_QWEN_BODY, "thinking": "OFF", "thinking_budget_tokens": 1024})
    assert r.status_code == 200, r.text
    row = _row(client)
    assert row["thinking"] == "off" and row["thinking_budget_tokens"] == 1024
    stored = json.loads(redis.store["model_registry:configured"])
    assert stored[0]["thinking"] == "off" and stored[0]["thinking_budget_tokens"] == 1024
    # What the provider reads at call time.
    assert registry_thinking_settings(_QWEN, _LAN) == ("off", 1024)

    # An edit that leaves the fields out (older client) keeps them ...
    client.post("/models/configured", headers=_ADMIN, json={**_QWEN_BODY, "quality_score": 0.9})
    assert _row(client)["thinking"] == "off"
    assert _row(client)["thinking_budget_tokens"] == 1024
    # ... an explicit value replaces them; null clears the budget.
    client.post("/models/configured", headers=_ADMIN,
                json={**_QWEN_BODY, "thinking": "auto", "thinking_budget_tokens": None})
    row = _row(client)
    assert row["thinking"] == "auto" and row["thinking_budget_tokens"] is None


def test_an_entry_saved_before_the_setting_existed_reads_as_auto() -> None:
    from app.ai_router.seeder import seed_registry_from_config

    redis = _FakeRedis()
    redis.set("model_registry:configured", json.dumps([{
        "provider": "openai_compatible", "model_id": _QWEN,
        "capabilities": ["text_generation"], "base_url": _LAN}]))
    client = _client(redis)
    seed_registry_from_config()
    row = _row(client)
    assert row["thinking"] == "auto" and row["thinking_budget_tokens"] is None


@pytest.mark.parametrize("bad", [
    {"thinking": "maybe"},
    {"thinking_budget_tokens": -5},
    {"thinking_budget_tokens": "lots"},
    {"thinking_budget_tokens": True},
])
def test_invalid_thinking_values_are_refused(bad: dict[str, Any]) -> None:
    r = _client().post("/models/configured", headers=_ADMIN, json={**_QWEN_BODY, **bad})
    assert r.status_code == 400


# ── Test connection (test-endpoint) ──────────────────────────────────────────


def _reply(content: str | None, *, reasoning_tokens: int = 0, finish: str = "stop",
           reasoning: str | None = None) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content,
                                 "reasoning": reasoning}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 16, "completion_tokens": reasoning_tokens or 2,
                  "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}},
    }


def _qwen_side_effect(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if (body.get("chat_template_kwargs") or {}).get("enable_thinking") is False:
        return httpx.Response(200, json=_reply("OK"))
    return httpx.Response(200, json=_reply(None, reasoning_tokens=16, finish="length",
                                           reasoning="Thinking Process:"))


@respx.mock
def test_test_endpoint_reports_a_thinking_model_and_that_disabling_works() -> None:
    respx.get(f"{_LAN}/models").mock(return_value=httpx.Response(
        200, json={"data": [{"id": _QWEN}]}))
    chat = respx.post(f"{_LAN}/chat/completions").mock(side_effect=_qwen_side_effect)
    out = _client().post("/models/configured/test-endpoint", headers=_ADMIN,
                         json={**_QWEN_BODY}).json()
    assert out["ok"] is True, out
    assert "OK" in out["detail"] and "thinking off" in out["detail"]
    t = out["thinking"]
    assert t["mode"] == "auto" and t["thinking_model"] is True and t["reasoning_tokens"] == 16
    assert t["disable_supported"] is True and t["disabled_works"] is True
    assert '"thinking": "off"' in t["recommendation"]
    assert chat.call_count == 2


@respx.mock
def test_test_endpoint_with_thinking_on_and_a_small_budget_reports_the_failure() -> None:
    respx.get(f"{_LAN}/models").mock(return_value=httpx.Response(500))
    respx.post(f"{_LAN}/chat/completions").mock(side_effect=_qwen_side_effect)
    out = _client().post("/models/configured/test-endpoint", headers=_ADMIN,
                         json={**_QWEN_BODY, "thinking": "on"}).json()
    assert out["ok"] is False
    assert "reasoning" in out["error"]
    assert out["thinking"]["thinking_model"] is True
    assert out["thinking"]["disabled_works"] is True  # the operator sees the way out


@respx.mock
def test_test_endpoint_uses_the_saved_mode_and_reports_an_unsupported_switch() -> None:
    respx.get(f"{_LAN}/models").mock(return_value=httpx.Response(500))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "chat_template_kwargs" in body:
            return httpx.Response(400, json={"error": {
                "message": "chat_template_kwargs: extra inputs are not permitted"}})
        return httpx.Response(200, json=_reply(None, reasoning_tokens=16, finish="length"))

    respx.post(f"{_LAN}/chat/completions").mock(side_effect=handler)
    client = _client()
    assert client.post("/models/configured", headers=_ADMIN,
                       json={**_QWEN_BODY, "thinking": "off"}).status_code == 200
    out = client.post("/models/configured/test-endpoint", headers=_ADMIN,
                      json={**_QWEN_BODY}).json()
    t = out["thinking"]
    assert t["mode"] == "off" and t["disable_supported"] is False
    assert out["ok"] is False and '"thinking": "on"' in t["recommendation"]
    assert _LAN in oc._TEMPLATE_KWARGS_UNSUPPORTED


@respx.mock
def test_test_endpoint_non_thinking_model_makes_one_call() -> None:
    respx.get(f"{_LAN}/models").mock(return_value=httpx.Response(500))
    chat = respx.post(f"{_LAN}/chat/completions").mock(
        return_value=httpx.Response(200, json=_reply("OK")))
    out = _client().post("/models/configured/test-endpoint", headers=_ADMIN,
                         json={**_QWEN_BODY}).json()
    assert out["ok"] is True and out["thinking"]["thinking_model"] is False
    assert out["thinking"]["disable_supported"] is None
    assert chat.call_count == 1


# ── POST /models/test ────────────────────────────────────────────────────────


def test_models_test_reports_an_auto_disabled_thinking_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.openai_compatible import OpenAICompatibleProvider

    seen: list[dict[str, Any]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return _qwen_side_effect(request)

    provider = OpenAICompatibleProvider(
        api_key="EMPTY", base_url=_LAN, default_model=_QWEN,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    provider._agentverse_provider_type = "onprem"  # type: ignore[attr-defined]
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "admin-secret")
    monkeypatch.setattr(model_registry, "get_model", lambda prov, mid: object())
    monkeypatch.setattr(model_registry, "update_health", lambda *a, **k: None)
    client = _client()
    client.app.state._app_provider = provider  # type: ignore[attr-defined]

    r = client.post("/models/test", headers={**_ADMIN, "X-Admin-Key": "admin-secret"},
                    json={"provider": "onprem", "model_id": _QWEN})
    out = r.json()
    assert r.status_code == 200 and out["status"] == "ok", out
    assert out["response"] == "OK"
    assert out["thinking"]["thinking_model"] is True
    assert out["thinking"]["thinking_disabled"] is True
    assert len(seen) == 2
