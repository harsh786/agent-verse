"""Per-model endpoint URLs (Model Registry ``base_url``).

* a LAN vLLM endpoint (http://192.168.63.104:30080/v1) is usable once the
  private access is on (default); refused with an actionable message when it is off;
* metadata / link-local addresses are never usable;
* a model with its own endpoint is called there (dispatcher), eligible without a
  provider key, and its reranker endpoint is picked up by the hosted reranker;
* ``POST /models/configured/test-endpoint`` makes one real call per capability;
* a catalog import of the deployment's own env model keeps its standing (MR-5).
"""

_ISOLATE_PROVIDER_ENV = True

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.model_endpoints import ModelEndpointError, check_model_endpoint
from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api.model_registry import router as models_router
from app.providers.model_dispatch import ModelDispatchProvider
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_LAN = "http://192.168.63.104:30080/v1"
_ADMIN_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
)
_ADMIN = {"X-API-Key": "ak_admin"}


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def incr(self, k):
        self.store[k] = str(int(self.store.get(k) or 0) + 1)
        return int(self.store[k])


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _client():
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    app = FastAPI()

    async def _resolve(key):
        return _ADMIN_CTX if key == "ak_admin" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return TestClient(app)


# ── egress policy (shared SSRF guard + ALLOW_PRIVATE_NETWORK_ACCESS) ─────────


def test_lan_endpoint_follows_the_private_network_flag(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    assert check_model_endpoint(_LAN + "/") == _LAN
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    with pytest.raises(ModelEndpointError, match="ALLOW_PRIVATE_NETWORK_ACCESS"):
        check_model_endpoint(_LAN)


def test_metadata_and_bad_schemes_are_never_allowed(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    for url in ("http://169.254.169.254/latest", "http://100.100.100.200/",
                "ftp://192.168.63.104/v1", "not a url"):
        with pytest.raises(ModelEndpointError):
            check_model_endpoint(url)


# ── dispatch + eligibility + reranker ───────────────────────────────────────


def _register(model_id, caps, *, base_url=None, provider="onprem"):
    model_registry.register_configured(
        ModelEndpoint(provider=provider, model_id=model_id, display_name=model_id,
                      capabilities=caps, base_url=base_url, extra={"source": "override"})
    )


class _Base:
    _agentverse_provider_type = "nvidia"

    async def complete(self, request):
        return "base"


def test_model_with_its_own_endpoint_is_dispatched_there(monkeypatch):
    _register("Qwen/Qwen3.5-4B", [ModelCapability.TEXT_GENERATION], base_url=_LAN)
    p = ModelDispatchProvider(_Base())
    target = p.target_for("Qwen/Qwen3.5-4B")
    assert target is not p.inner
    assert str(target._client.base_url).rstrip("/") == _LAN
    assert p.target_for("Qwen/Qwen3.5-4B") is target  # cached
    assert p.target_for("other-model") is p.inner


def test_endpoint_refused_by_policy_falls_back_to_the_platform_provider(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    _register("Qwen/Qwen3.5-4B", [ModelCapability.TEXT_GENERATION], base_url=_LAN)
    p = ModelDispatchProvider(_Base())  # private hosts off: endpoint refused
    assert p.target_for("Qwen/Qwen3.5-4B") is p.inner


def test_endpoint_model_is_eligible_without_a_provider_key():
    from app.ai_router.selection import select_configured_model_id

    _register("lan-llm", [ModelCapability.TEXT_GENERATION], base_url=_LAN, provider="custom")
    _register("keyless", [ModelCapability.TEXT_GENERATION], provider="groq")
    assert select_configured_model_id("planning") == "lan-llm"


def test_hosted_reranker_uses_a_registry_reranker_endpoint(monkeypatch):
    from types import SimpleNamespace

    from app.rag_platform.hosted_reranker import (
        hosted_reranker_from_settings,
        is_hosted_reranker_configured,
    )

    settings = SimpleNamespace(rag_hosted_reranker_url="")
    assert hosted_reranker_from_settings(settings) is None
    _register("Qwen/Qwen3-Reranker-0.6B", [ModelCapability.RERANK],
              base_url="http://192.168.63.104:30083/v1")
    rr = hosted_reranker_from_settings(settings)
    assert rr is not None and is_hosted_reranker_configured(settings)
    assert rr._url == "http://192.168.63.104:30083/v1/rerank"


# ── API: save with endpoint + test-endpoint ─────────────────────────────────


def test_save_refuses_a_private_endpoint_only_when_private_access_is_off(monkeypatch):
    client = _client()
    body = {"provider": "onprem", "model_id": "Qwen/Qwen3.5-4B",
            "capabilities": ["text_generation", "tool_use"], "base_url": _LAN}
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    r = client.post("/models/configured", headers=_ADMIN, json=body)
    assert r.status_code == 400 and "ALLOW_PRIVATE_NETWORK_ACCESS" in r.json()["detail"]
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")

    assert client.post("/models/configured", headers=_ADMIN, json=body).status_code == 200
    groups = client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
    row = next(m for g in groups for m in g["models"] if m["model_id"] == "Qwen/Qwen3.5-4B")
    assert row["base_url"] == _LAN and row["provider_ready"] is True


@respx.mock
def test_test_endpoint_chat_probe(monkeypatch):
    respx.get(f"{_LAN}/models").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "Qwen/Qwen3.5-4B"}]}))
    chat = respx.post(f"{_LAN}/chat/completions").mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": "OK"}}]}))
    r = _client().post("/models/configured/test-endpoint", headers=_ADMIN, json={
        "provider": "onprem", "model_id": "Qwen/Qwen3.5-4B", "base_url": _LAN,
        "capabilities": ["text_generation"]})
    out = r.json()
    assert r.status_code == 200 and out["ok"] is True, out
    assert out["probe"] == "chat" and out["model_listed"] is True
    assert "OK" in out["detail"]
    sent = chat.calls.last.request
    assert b"enable_thinking" in sent.content  # on-prem vLLM: no chain-of-thought


@respx.mock
def test_test_endpoint_reports_failures_and_unlisted_models(monkeypatch):
    base = "http://192.168.63.104:30082/v1"
    respx.get(f"{base}/models").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "Qwen/Qwen3-Embedding-0.6B"}]}))
    respx.post(f"{base}/embeddings").mock(return_value=httpx.Response(404, text="no model"))
    out = _client().post("/models/configured/test-endpoint", headers=_ADMIN, json={
        "provider": "onprem", "model_id": "wrong-embed", "base_url": base,
        "capabilities": ["embedding"]}).json()
    assert out["ok"] is False and out["probe"] == "embedding"
    assert out["model_listed"] is False and "HTTP 404" in out["error"]
    assert out["served_models"] == ["Qwen/Qwen3-Embedding-0.6B"]


@respx.mock
def test_test_endpoint_rerank_probe(monkeypatch):
    base = "http://192.168.63.104:30083/v1"
    respx.get(f"{base}/models").mock(return_value=httpx.Response(500))
    respx.post(f"{base}/rerank").mock(return_value=httpx.Response(
        200, json={"results": [{"index": 0, "relevance_score": 0.9},
                               {"index": 1, "relevance_score": 0.1}]}))
    out = _client().post("/models/configured/test-endpoint", headers=_ADMIN, json={
        "provider": "onprem", "model_id": "Qwen/Qwen3-Reranker-0.6B", "base_url": base,
        "capabilities": ["rerank"]}).json()
    assert out["ok"] is True and out["detail"] == "2 documents scored"
    assert out["model_listed"] is None


# ── MR-5 follow-up: importing the deployment's own model keeps its standing ──


def test_catalog_import_of_the_env_model_stays_a_deployment_model(monkeypatch):
    from app.ai_router.registry_store import get_model_registry_store
    from app.ai_router.seeder import seed_registry_from_config
    from app.ai_router.selection import select_configured_model_id

    _client()  # fresh store
    monkeypatch.setattr(
        "app.ai_router.seeder._reasoning_model_ids", lambda: ["nvidia/env-reasoner"]
    )
    monkeypatch.setattr("app.ai_router.seeder._provider_for_model", lambda m: "nvidia")
    monkeypatch.setattr("app.ai_router.registry.ModelRegistry.price_for",
                        lambda self, m: (0.5, 0.5))
    store = get_model_registry_store()
    store.upsert_many([
        {"provider": "nvidia", "model_id": "nvidia/env-reasoner", "capabilities":
         ["text_generation", "tool_use"], "cost_per_1k_input": 0.5, "origin": "catalog"},
        {"provider": "nvidia", "model_id": "nvidia/cheaper-import", "capabilities":
         ["text_generation", "tool_use"], "cost_per_1k_input": 0.01, "origin": "catalog"},
    ])
    seed_registry_from_config()
    env_model = model_registry.get_configured("nvidia", "nvidia/env-reasoner")
    assert env_model is not None
    assert (env_model.extra or {}).get("source") == "env"
    assert (env_model.extra or {}).get("origin") == "deployment"
    # the cheaper catalog import does not take over from the deployment's model
    assert select_configured_model_id("planning") == "nvidia/env-reasoner"
