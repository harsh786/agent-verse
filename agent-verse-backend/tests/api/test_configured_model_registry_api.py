"""Model-registry UI API: manage the CONFIGURED models selection picks from."""

# Isolate ambient provider/model env so the model registry / on-prem backfill
# start from a clean provider environment (see tests/conftest.py).
_ISOLATE_PROVIDER_ENV = True

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api.model_registry import router as models_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-mr", plan=PlanTier.PROFESSIONAL, api_key_id="kid-mr")
_KEY = "ak_mr_test"
_ADMIN = "platform-admin-secret"
_HEADERS = {"X-API-Key": _KEY}  # read-only tenant headers
_ADMIN_HEADERS = {"X-API-Key": _KEY, "X-Admin-Key": _ADMIN}  # platform-admin headers


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v


def _make_app():
    app = FastAPI()

    async def _resolve(key):
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return app


def _client(monkeypatch):
    # Isolate: fresh store, empty configured set, no env auto-seed noise.
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN)  # enable admin-gated mutations
    model_registry.clear_configured()
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    return TestClient(_make_app())


def test_upsert_then_list_and_select_cheapest(monkeypatch):
    client = _client(monkeypatch)
    # register two reasoning models with different cost
    for mid, cost in (("pricey-llm", 0.02), ("cheap-llm", 0.0)):
        r = client.post(
            "/models/configured",
            headers=_ADMIN_HEADERS,
            json={
                "provider": "custom",
                "model_id": mid,
                "capabilities": ["text_generation", "tool_use"],
                "cost_per_1k_input": cost,
                "supports_tools": True,
            },
        )
        assert r.status_code == 200, r.text

    listing = client.get("/models/configured", headers=_HEADERS).json()
    tg = next(g for g in listing["capabilities"] if g["capability"] == "text_generation")
    assert {m["model_id"] for m in tg["models"]} == {"pricey-llm", "cheap-llm"}
    # cheapest is the selected one
    assert tg["selected_model_id"] == "cheap-llm"


def test_reads_allowed_for_tenant_but_writes_require_platform_admin(monkeypatch):
    client = _client(monkeypatch)
    payload = {"provider": "custom", "model_id": "sneaky",
               "capabilities": ["text_generation"], "cost_per_1k_input": 0.0}
    # A regular tenant (no admin key) can READ...
    assert client.get("/models/configured", headers=_HEADERS).status_code == 200
    # ...but cannot mutate the global registry.
    assert client.post("/models/configured", headers=_HEADERS, json=payload).status_code == 403
    assert client.delete("/models/configured/custom/x", headers=_HEADERS).status_code == 403
    assert client.post("/models/configured/reseed", headers=_HEADERS).status_code == 403
    # An invalid admin key is rejected too.
    bad = {"X-API-Key": _KEY, "X-Admin-Key": "wrong"}
    assert client.post("/models/configured", headers=bad, json=payload).status_code == 403


def test_unknown_provider_rejected(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/models/configured", headers=_ADMIN_HEADERS,
                    json={"provider": "evilcorp", "model_id": "x",
                          "capabilities": ["text_generation"]})
    assert r.status_code == 400


def test_upsert_validation_requires_capability(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/models/configured", headers=_ADMIN_HEADERS,
                    json={"model_id": "x", "capabilities": []})
    assert r.status_code == 400


def test_delete_configured_model(monkeypatch):
    client = _client(monkeypatch)
    client.post("/models/configured", headers=_ADMIN_HEADERS,
                json={"provider": "custom", "model_id": "gone",
                      "capabilities": ["embedding"], "cost_per_1k_input": 0.0})
    d = client.delete("/models/configured/custom/gone", headers=_ADMIN_HEADERS)
    assert d.status_code == 200 and d.json()["removed"] is True
    listing = client.get("/models/configured", headers=_HEADERS).json()
    assert all(g["capability"] != "embedding" for g in listing["capabilities"])


def test_persistence_survives_reseed(monkeypatch):
    client = _client(monkeypatch)
    client.post("/models/configured", headers=_ADMIN_HEADERS,
                json={"provider": "custom", "model_id": "persist-me",
                      "capabilities": ["rerank"], "cost_per_1k_input": 0.0})
    # reseed (simulates a restart re-reading the persistent store)
    r = client.post("/models/configured/reseed", headers=_ADMIN_HEADERS)
    assert r.status_code == 200
    listing = client.get("/models/configured", headers=_HEADERS).json()
    rr = next(g for g in listing["capabilities"] if g["capability"] == "rerank")
    assert any(m["model_id"] == "persist-me" for m in rr["models"])
