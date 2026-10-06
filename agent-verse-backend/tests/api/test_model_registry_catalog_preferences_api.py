"""Model Registry: who may change it, catalog import, and per-capability order.

* A logged-in tenant ADMIN of an operator tenant may change the registry without
  the platform admin key (it used to answer "Platform admin privileges required
  to modify the model registry" to the admin user).
* The catalog lists NVIDIA, Groq, xAI, Claude, OpenAI, Gemini, on-prem Qwen,
  Ollama and Voyage models; importing copies them into the registry.
* A saved preference order decides the primary model per capability and the
  failover order after it.
"""

# Isolate ambient provider/model env so readiness depends only on this test.
_ISOLATE_PROVIDER_ENV = True

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api.model_registry import router as models_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_ADMIN_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
)
_OPERATOR_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-o", roles=("operator",)
)
_KEYS = {"ak_admin": _ADMIN_CTX, "ak_operator": _OPERATOR_CTX}
_ADMIN = {"X-API-Key": "ak_admin"}
_OPERATOR = {"X-API-Key": "ak_operator"}
_PLATFORM_KEY = "platform-admin-secret"


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


def _client(monkeypatch, *, env="development", admin_tenants=None, platform_key=None):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setenv("ENVIRONMENT", env)
    if admin_tenants is None:
        monkeypatch.delenv("PLATFORM_ADMIN_TENANT_IDS", raising=False)
    else:
        monkeypatch.setenv("PLATFORM_ADMIN_TENANT_IDS", admin_tenants)
    if platform_key is None:
        monkeypatch.delenv("PLATFORM_ADMIN_KEY", raising=False)
    else:
        monkeypatch.setenv("PLATFORM_ADMIN_KEY", platform_key)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))

    app = FastAPI()

    async def _resolve(key):
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return TestClient(app)


def _add(client, model_id, cost, *, provider="custom", headers=_ADMIN, caps=None):
    r = client.post(
        "/models/configured",
        headers=headers,
        json={
            "provider": provider,
            "model_id": model_id,
            "capabilities": caps or ["text_generation", "tool_use"],
            "cost_per_1k_input": cost,
            "supports_tools": True,
        },
    )
    return r


# ── 1. access: the logged-in admin can save ─────────────────────────────────


def test_tenant_admin_can_add_a_model_without_the_admin_key(monkeypatch):
    client = _client(monkeypatch)
    access = client.get("/models/configured/access", headers=_ADMIN).json()
    assert access == {"can_modify": True, "via": "tenant_admin", "needs_admin_key": False,
                      "reason": ""}
    r = _add(client, "llama-3.1-8b-instant", 0.0, provider="groq")
    assert r.status_code == 200, r.text


def test_a_non_admin_role_is_refused_with_a_clear_reason(monkeypatch):
    client = _client(monkeypatch)
    access = client.get("/models/configured/access", headers=_OPERATOR).json()
    assert access["can_modify"] is False and access["needs_admin_key"] is True
    r = _add(client, "m1", 0.0, headers=_OPERATOR)
    assert r.status_code == 403
    assert "sign in as an admin" in r.json()["detail"]


def test_production_needs_the_operator_tenant_list(monkeypatch):
    client = _client(monkeypatch, env="production")
    r = _add(client, "m1", 0.0)
    assert r.status_code == 403
    assert "PLATFORM_ADMIN_TENANT_IDS" in r.json()["detail"]

    client = _client(monkeypatch, env="production", admin_tenants="other-tenant,tid-owner")
    assert _add(client, "m1", 0.0).status_code == 200

    client = _client(monkeypatch, env="production", admin_tenants="*")
    assert _add(client, "m2", 0.0).status_code == 200


def test_a_listed_operator_tenant_still_needs_the_admin_role(monkeypatch):
    client = _client(monkeypatch, env="production", admin_tenants="tid-owner")
    assert _add(client, "m1", 0.0, headers=_OPERATOR).status_code == 403


def test_admin_key_paths_name_the_actual_problem(monkeypatch):
    client = _client(monkeypatch, env="production")
    wrong = {**_OPERATOR, "X-Admin-Key": "nope"}
    r = _add(client, "m1", 0.0, headers=wrong)
    assert r.status_code == 503 and "not configured" in r.json()["detail"]

    client = _client(monkeypatch, env="production", platform_key=_PLATFORM_KEY)
    r = _add(client, "m1", 0.0, headers=wrong)
    assert r.status_code == 403 and "incorrect" in r.json()["detail"]
    ok = {**_OPERATOR, "X-Admin-Key": _PLATFORM_KEY}
    assert _add(client, "m1", 0.0, headers=ok).status_code == 200
    access = client.get("/models/configured/access", headers=ok).json()
    assert access["can_modify"] is True and access["via"] == "admin_key"


# ── 2. catalog ──────────────────────────────────────────────────────────────


def test_catalog_lists_every_requested_provider_per_capability(monkeypatch):
    client = _client(monkeypatch)
    providers = client.get("/models/catalog", headers=_OPERATOR).json()["providers"]
    by_name = {p["provider"]: p for p in providers}
    assert {"nvidia", "groq", "xai", "anthropic", "openai", "gemini", "onprem", "ollama",
            "voyage"} <= set(by_name)
    all_caps = {c for p in providers for m in p["models"] for c in m["capabilities"]}
    assert {"text_generation", "embedding", "vision", "ocr", "rerank"} <= all_caps
    # isolated env: no keys, so cloud providers are not ready
    assert by_name["groq"]["ready"] is False and by_name["groq"]["env_hint"] == "GROQ_API_KEY"


def test_catalog_import_is_admin_only_and_keeps_operator_edits(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/models/catalog/import", headers=_OPERATOR,
                       json={"providers": ["groq"]}).status_code == 403

    r = client.post("/models/catalog/import", headers=_ADMIN, json={"providers": ["groq"]})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["imported"] >= 5 and first["skipped"] == 0

    again = client.post("/models/catalog/import", headers=_ADMIN,
                        json={"providers": ["groq"]}).json()
    assert again["imported"] == 0 and again["skipped"] == first["imported"]

    catalog = client.get("/models/catalog", headers=_ADMIN).json()["providers"]
    groq = next(p for p in catalog if p["provider"] == "groq")
    assert all(m["already_configured"] for m in groq["models"])


def test_import_by_provider_qualified_key_picks_one_provider(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/models/catalog/import", headers=_ADMIN,
                    json={"model_ids": ["groq/openai/gpt-oss-120b"]})
    assert r.json()["imported"] == 1
    listing = client.get("/models/configured", headers=_ADMIN).json()
    keys = {m["key"] for g in listing["capabilities"] for m in g["models"]}
    assert keys == {"groq/openai/gpt-oss-120b"}


def test_imported_models_without_a_key_are_listed_but_not_selected(monkeypatch):
    client = _client(monkeypatch)
    client.post("/models/catalog/import", headers=_ADMIN, json={"providers": ["groq"]})
    _add(client, "local-llm", 0.5)  # custom provider: always ready
    listing = client.get("/models/configured", headers=_ADMIN).json()
    tg = next(g for g in listing["capabilities"] if g["capability"] == "text_generation")
    groq_rows = [m for m in tg["models"] if m["provider"] == "groq"]
    assert groq_rows and not any(m["provider_ready"] for m in groq_rows)
    assert tg["selected_model_id"] == "local-llm"

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    listing = client.get("/models/configured", headers=_ADMIN).json()
    tg = next(g for g in listing["capabilities"] if g["capability"] == "text_generation")
    assert tg["selected_model_id"] == "llama-3.1-8b-instant"  # now the cheapest ready one


# ── 3. preference order ─────────────────────────────────────────────────────


def test_preference_order_decides_primary_and_fallbacks(monkeypatch):
    client = _client(monkeypatch)
    for mid, cost in (("cheap", 0.0), ("mid", 0.01), ("best", 0.05)):
        assert _add(client, mid, cost).status_code == 200

    tg = next(g for g in client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
              if g["capability"] == "text_generation")
    assert tg["order_mode"] == "cost" and tg["selected_model_id"] == "cheap"

    r = client.put("/models/preferences/text_generation", headers=_ADMIN,
                   json={"order": ["custom/best", "custom/mid"]})
    assert r.status_code == 200, r.text

    tg = next(g for g in client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
              if g["capability"] == "text_generation")
    assert tg["order_mode"] == "preference"
    assert [m["model_id"] for m in tg["models"]] == ["best", "mid", "cheap"]
    assert [m["rank"] for m in tg["models"]] == [1, 2, 3]
    assert tg["selected_model_id"] == "best"
    assert tg["fallback_model_ids"] == ["mid", "cheap"]

    prefs = client.get("/models/preferences", headers=_OPERATOR).json()["preferences"]
    assert prefs["text_generation"] == ["custom/best", "custom/mid"]

    assert client.delete("/models/preferences/text_generation",
                         headers=_ADMIN).status_code == 200
    tg = next(g for g in client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
              if g["capability"] == "text_generation")
    assert tg["order_mode"] == "cost" and tg["selected_model_id"] == "cheap"


def test_preference_rejects_unknown_models_and_non_admins(monkeypatch):
    client = _client(monkeypatch)
    _add(client, "cheap", 0.0)
    r = client.put("/models/preferences/text_generation", headers=_ADMIN,
                   json={"order": ["custom/ghost"]})
    assert r.status_code == 400 and "custom/ghost" in r.json()["detail"]
    r = client.put("/models/preferences/text_generation", headers=_OPERATOR,
                   json={"order": ["custom/cheap"]})
    assert r.status_code == 403
    r = client.put("/models/preferences/bogus", headers=_ADMIN, json={"order": []})
    assert r.status_code == 400


def test_preferred_model_without_a_key_falls_to_the_next_ready_one(monkeypatch):
    client = _client(monkeypatch)
    client.post("/models/catalog/import", headers=_ADMIN,
                json={"model_ids": ["anthropic/claude-sonnet-5-5"]})
    _add(client, "local-llm", 0.0)
    client.put("/models/preferences/text_generation", headers=_ADMIN,
               json={"order": ["anthropic/claude-sonnet-5-5", "custom/local-llm"]})
    tg = next(g for g in client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
              if g["capability"] == "text_generation")
    assert tg["models"][0]["model_id"] == "claude-sonnet-5-5"
    assert tg["models"][0]["provider_ready"] is False
    assert tg["selected_model_id"] == "local-llm"


def test_embedding_group_explains_there_is_no_cross_model_failover(monkeypatch):
    client = _client(monkeypatch)
    _add(client, "embed-a", 0.0, caps=["embedding"])
    groups = client.get("/models/configured", headers=_ADMIN).json()["capabilities"]
    emb = next(g for g in groups if g["capability"] == "embedding")
    assert "re-index" in emb["note"]
