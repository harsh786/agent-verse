"""a08-F195-05: a tenant admin can remove its stored BYOK LLM config and key.

``LLMConfigStore.delete_config`` had no caller and the API exposed only GET/PUT
``/me/llm`` and ``/me/llm-config``, so a stored provider key could never be
removed. ``DELETE /tenants/me/llm`` (admin, audited, idempotent) removes it;
a store failure is a 503, never a 204 for a key that is still in use.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.governance.audit import AuditLog
from app.services.llm_config_store import LLMConfigPersistError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_ADMIN = {"X-API-Key": "k-admin"}
_OPERATOR = {"X-API-Key": "k-op"}


class _Store:
    def __init__(self) -> None:
        self.configs: dict[str, dict[str, Any]] = {
            "t-del": {"provider": "openai", "encrypted_key": "ENC", "model": "gpt-4o"}
        }
        self.fail = False

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        return self.configs.get(tenant_id)

    async def delete_config(self, tenant_id: str) -> None:
        if self.fail:
            raise LLMConfigPersistError("db down")
        self.configs.pop(tenant_id, None)


def _client(store: _Store | None) -> tuple[TestClient, FastAPI]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        roles = {"k-admin": ("admin",), "k-op": ("operator",)}.get(key)
        if roles is None:
            return None
        return TenantContext("t-del", PlanTier.STARTER, key, roles=roles)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.state.audit_log = AuditLog()
    app.state.llm_config_store = store
    return TestClient(app, raise_server_exceptions=False), app


def test_admin_removes_the_stored_config_and_it_is_audited() -> None:
    store = _Store()
    client, app = _client(store)
    assert client.get("/tenants/me/llm", headers=_ADMIN).json()["configured"] is True

    resp = client.delete("/tenants/me/llm", headers=_ADMIN)
    assert resp.status_code == 204, resp.text
    assert "t-del" not in store.configs
    assert client.get("/tenants/me/llm", headers=_ADMIN).json()["configured"] is False
    # Idempotent.
    assert client.delete("/tenants/me/llm", headers=_ADMIN).status_code == 204

    logged: list[Any] = list(app.state.audit_log._log.get("t-del", []))
    events = [e for e in logged if e.tool_name == "tenant.llm_config"]
    assert events and events[-1].outcome == "deleted"
    assert "ENC" not in (events[-1].note or "")


def test_non_admin_cannot_remove_it() -> None:
    store = _Store()
    client, _ = _client(store)
    assert client.delete("/tenants/me/llm", headers=_OPERATOR).status_code == 403
    assert "t-del" in store.configs


def test_store_failure_is_503_and_nothing_reported_removed() -> None:
    store = _Store()
    store.fail = True
    client, _ = _client(store)
    assert client.delete("/tenants/me/llm", headers=_ADMIN).status_code == 503
    assert "t-del" in store.configs


def test_no_store_build_drops_the_process_local_copy() -> None:
    client, app = _client(None)
    app.state._llm_configs = {"t-del": {"provider": "openai", "encrypted_key": "ENC"}}
    assert client.delete("/tenants/me/llm", headers=_ADMIN).status_code == 204
    assert "t-del" not in app.state._llm_configs
