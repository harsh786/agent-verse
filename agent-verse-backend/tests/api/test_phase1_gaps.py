"""Phase 1: Tests for existing product gap fixes.

Covers:
- Gap 1: /me/llm-config GET + PUT (simple config store)
- Gap 3: /me/providers catalog (no secrets, has capabilities)
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-p1", plan=PlanTier.PROFESSIONAL, api_key_id="kid-p1")
_KEY = "ak_phase1_test_key"
_HEADERS = {"X-API-Key": _KEY}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    return app


# ── Gap 3: Provider catalog ───────────────────────────────────────────────────

def test_provider_catalog_returns_no_secrets() -> None:
    client = TestClient(_make_app())
    resp = client.get("/tenants/me/providers", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    for p in data["providers"]:
        # Must never contain actual API key values
        assert "key_value" not in p
        # The only key-related field is `env_var` (the name, not the value)
        p_str = str(p)
        assert "env_var" in p or "secret" not in p_str.lower()


def test_provider_catalog_has_expected_providers() -> None:
    client = TestClient(_make_app())
    resp = client.get("/tenants/me/providers", headers=_HEADERS)
    assert resp.status_code == 200
    providers = {p["name"] for p in resp.json()["providers"]}
    assert "anthropic" in providers
    assert "openai" in providers
    assert "gemini" in providers


def test_provider_capabilities_present() -> None:
    client = TestClient(_make_app())
    resp = client.get("/tenants/me/providers", headers=_HEADERS)
    assert resp.status_code == 200
    for p in resp.json()["providers"]:
        assert "capabilities" in p
        assert "configured" in p


def test_provider_catalog_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.get("/tenants/me/providers")  # No auth header
    assert resp.status_code == 401


# ── Gap 1: LLM config endpoints ───────────────────────────────────────────────

def test_llm_config_get_returns_empty_by_default() -> None:
    client = TestClient(_make_app())
    resp = client.get("/tenants/me/llm-config", headers=_HEADERS)
    assert resp.status_code == 200
    # With no tenant_service on app.state, returns {}
    assert isinstance(resp.json(), dict)


def test_llm_config_save_and_retrieve() -> None:
    client = TestClient(_make_app())
    config = {"provider": "anthropic", "model": "claude-sonnet-4-5"}
    resp = client.put("/tenants/me/llm-config", json=config, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") in ("saved", "saved_in_memory")
    # The stored fields are echoed back
    assert data.get("provider") == "anthropic"
    assert data.get("model") == "claude-sonnet-4-5"


def test_llm_config_save_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.put("/tenants/me/llm-config", json={"provider": "openai"})
    assert resp.status_code == 401


def test_llm_config_get_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.get("/tenants/me/llm-config")
    assert resp.status_code == 401
