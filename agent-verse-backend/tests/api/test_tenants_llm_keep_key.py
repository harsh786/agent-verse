"""QA-4: changing the LLM model must not require re-typing the provider key.

Regression: ``PUT /tenants/me/llm`` required ``api_key`` (min_length=1) and
always re-encrypted it, so an admin who only wanted another model had to paste
the secret again. ``api_key`` is now optional on update: omitted, the stored
encrypted key is kept. It stays required on first setup (nothing stored), and
when the provider or base URL changes — the stored secret is never sent to a
different provider/host than the one it was entered for.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_FAKE_KEY = "fake-provider-key-0123456789"
_H = {"X-API-Key": "k-admin"}


def _client() -> tuple[TestClient, FastAPI]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key != "k-admin":
            return None
        return TenantContext(
            tenant_id="t-keep", plan=PlanTier.STARTER, api_key_id=key, roles=("admin",)
        )

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.state.audit_log = AuditLog()
    # No shared store in this unit app: the process-local copy is the record.
    app.state.llm_config_store = None
    return TestClient(app, raise_server_exceptions=False), app


def _stored(app: FastAPI) -> dict[str, object]:
    return dict(app.state._llm_configs["t-keep"])


def _setup(client: TestClient) -> None:
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai", "api_key": _FAKE_KEY, "default_model": "gpt-4o"},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text


def test_first_setup_without_key_is_422_with_clear_message() -> None:
    client, _ = _client()
    resp = client.put(
        "/tenants/me/llm", json={"provider": "openai", "default_model": "gpt-4o"}, headers=_H
    )
    assert resp.status_code == 422, resp.text
    assert "api_key" in resp.text


def test_model_change_without_key_keeps_the_stored_key() -> None:
    client, app = _client()
    _setup(client)
    before = _stored(app)
    resp = client.put(
        "/tenants/me/llm", json={"provider": "openai", "default_model": "gpt-4o-mini"}, headers=_H
    )
    assert resp.status_code == 200, resp.text
    after = _stored(app)
    assert after["default_model"] == "gpt-4o-mini"
    assert after["encrypted_key"] == before["encrypted_key"]
    assert after["masked_key"] == before["masked_key"]
    view = client.get("/tenants/me/llm", headers=_H).json()
    assert view["default_model"] == "gpt-4o-mini"
    assert view["masked_key"] == before["masked_key"]


def test_empty_string_key_is_treated_as_omitted() -> None:
    client, app = _client()
    _setup(client)
    before = _stored(app)["encrypted_key"]
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai", "api_key": "", "default_model": "gpt-4.1"},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text
    assert _stored(app)["encrypted_key"] == before


def test_provider_change_without_key_is_422() -> None:
    client, app = _client()
    _setup(client)
    before = _stored(app)
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "anthropic", "default_model": "claude-sonnet"},
        headers=_H,
    )
    assert resp.status_code == 422, resp.text
    assert _stored(app) == before


def test_base_url_change_without_key_is_422() -> None:
    client, app = _client()
    _setup(client)
    before = _stored(app)
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai", "default_model": "gpt-4o", "base_url": "https://other.example.com/v1"},
        headers=_H,
    )
    assert resp.status_code == 422, resp.text
    assert _stored(app) == before


def test_new_key_still_replaces_the_stored_one() -> None:
    client, app = _client()
    _setup(client)
    before = _stored(app)["encrypted_key"]
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai", "api_key": "fake-provider-key-replaced-9876", "default_model": "gpt-4o"},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text
    assert _stored(app)["encrypted_key"] != before
