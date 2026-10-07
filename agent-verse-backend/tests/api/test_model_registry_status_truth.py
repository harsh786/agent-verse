"""GET /models/configured reports what can ACTUALLY serve each capability.

Owner report: models were configured in the registry, yet the page mis-reported
the configuration per capability:

* a registry model saved with its OWN API key but no endpoint URL was marked
  "No API key — skipped at runtime" (readiness checked the provider's env key
  only), and it really was skipped;
* a model merely named in env (``DEFAULT_MODEL`` …) with no key or endpoint
  behind it counted as a working model, so a capability looked covered (and that
  model was badged "Primary") when nothing could serve it.
"""

# Isolate ambient provider/model env so readiness depends only on this test.
_ISOLATE_PROVIDER_ENV = True

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api.model_registry import router as models_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

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


def _client(monkeypatch):
    import app.ai_router.registry_store as store_mod
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")  # a LAN model endpoint
    monkeypatch.delenv("PLATFORM_ADMIN_TENANT_IDS", raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))

    app = FastAPI()

    async def _resolve(key):
        return _ADMIN_CTX if key == "ak_admin" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return TestClient(app)


def _group(client, capability):
    groups = client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
    return next((g for g in groups if g["capability"] == capability), None)


def _env_seeded(model_id: str, caps: list[ModelCapability], provider: str = "openai") -> None:
    model_registry.register_configured(
        ModelEndpoint(provider=provider, model_id=model_id, display_name=model_id,
                      capabilities=caps, extra={"source": "env"})
    )


def test_a_model_with_its_own_key_and_no_url_is_ready(monkeypatch):
    client = _client(monkeypatch)
    r = client.post(
        "/models/configured",
        headers=_ADMIN,
        json={"provider": "groq", "model_id": "llama-3.3-70b-versatile",
              "capabilities": ["text_generation", "tool_use"], "supports_tools": True,
              "api_key": "gsk-own-key"},
    )
    assert r.status_code == 200, r.text
    tg = _group(client, "text_generation")
    (row,) = tg["models"]
    assert row["has_api_key"] is True
    assert row["provider_ready"] is True  # was False: "No API key"
    assert row["servable"] is True
    assert tg["status"] == "ready" and tg["ready_count"] == 1
    assert tg["selected_model_id"] == "llama-3.3-70b-versatile"


def test_a_registry_model_with_its_own_endpoint_is_ready(monkeypatch):
    client = _client(monkeypatch)
    r = client.post(
        "/models/configured",
        headers=_ADMIN,
        json={"provider": "openai_compatible", "model_id": "qwen-lan",
              "capabilities": ["text_generation"], "base_url": "http://10.1.2.3:8000/v1"},
    )
    assert r.status_code == 200, r.text
    tg = _group(client, "text_generation")
    assert tg["status"] == "ready"
    assert tg["models"][0]["provider_ready"] is True and tg["models"][0]["servable"] is True


def test_an_env_named_model_with_nothing_behind_it_does_not_cover_its_capability(
    monkeypatch,
):
    client = _client(monkeypatch)
    _env_seeded("text-embedding-3-small", [ModelCapability.EMBEDDING])
    _env_seeded("gpt-4o", [ModelCapability.TEXT_GENERATION, ModelCapability.VISION])
    for cap in ("text_generation", "vision", "embedding"):
        group = _group(client, cap)
        assert group is not None
        assert group["status"] == "not_ready", cap
        assert group["ready_count"] == 0
        assert all(m["servable"] is False for m in group["models"])


def test_the_primary_is_what_actually_runs_not_an_unservable_env_model(monkeypatch):
    client = _client(monkeypatch)
    _env_seeded("gpt-4o", [ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE])
    client.post(
        "/models/configured",
        headers=_ADMIN,
        json={"provider": "openai_compatible", "model_id": "qwen-lan",
              "capabilities": ["text_generation", "tool_use"], "supports_tools": True,
              "base_url": "http://10.1.2.3:8000/v1", "cost_per_1k_input": 0.5},
    )
    tg = _group(client, "text_generation")
    assert tg["status"] == "ready" and tg["ready_count"] == 1
    assert tg["selected_model_id"] == "qwen-lan"
    assert "gpt-4o" not in tg["fallback_model_ids"]


def test_an_env_model_with_its_provider_key_is_ready(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    client = _client(monkeypatch)
    _env_seeded("gpt-4o", [ModelCapability.TEXT_GENERATION])
    tg = _group(client, "text_generation")
    assert tg["status"] == "ready" and tg["models"][0]["servable"] is True
