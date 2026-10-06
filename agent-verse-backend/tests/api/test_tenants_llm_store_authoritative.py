"""a08-F195-01 / a08-F195-03: the LLM config store is the only source when wired.

* F195-03: ``GET /tenants/me/llm`` (and ``/me/llm-config``) read the store
  non-strictly, so a DB read error was answered ``configured: false``; and a
  keep-the-stored-key update then said "no LLM API key is stored". Both are 503.
* F195-01: every save also wrote ``app.state._llm_configs`` and that per-replica
  dict was read whenever the store answered nothing — a config the store no
  longer had was still served by the replica that saved it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.governance.audit import AuditLog
from app.services.llm_config_store import LLMConfigReadError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_H = {"X-API-Key": "k-admin"}
_FAKE_KEY = "fake-provider-key-0123456789"


class _Store:
    def __init__(self) -> None:
        self.configs: dict[str, dict[str, Any]] = {}
        self.read_error = False
        self.strict_reads: list[bool] = []

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        self.strict_reads.append(strict)
        if self.read_error:
            if strict:
                raise LLMConfigReadError("db down")
            return None
        return self.configs.get(tenant_id)

    async def set_config(self, tenant_id: str, **cfg: Any) -> None:
        self.configs[tenant_id] = dict(cfg)

    async def delete_config(self, tenant_id: str) -> None:
        self.configs.pop(tenant_id, None)


def _client(store: _Store) -> tuple[TestClient, FastAPI]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key != "k-admin":
            return None
        return TenantContext(
            tenant_id="t-llm", plan=PlanTier.STARTER, api_key_id=key, roles=("admin",)
        )

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.state.audit_log = AuditLog()
    app.state.llm_config_store = store
    return TestClient(app, raise_server_exceptions=False), app


def test_get_llm_is_503_when_the_store_cannot_be_read() -> None:
    store = _Store()
    store.read_error = True
    client, _ = _client(store)
    for path in ("/tenants/me/llm", "/tenants/me/llm-config"):
        resp = client.get(path, headers=_H)
        assert resp.status_code == 503, (path, resp.text)
    assert store.strict_reads and all(store.strict_reads)


def test_keep_key_update_is_503_not_no_key_stored_on_a_read_error() -> None:
    store = _Store()
    store.read_error = True
    client, _ = _client(store)
    resp = client.put(
        "/tenants/me/llm", json={"provider": "openai", "default_model": "gpt-4o"}, headers=_H
    )
    assert resp.status_code == 503, resp.text


def test_no_process_local_copy_beside_a_store() -> None:
    store = _Store()
    client, app = _client(store)
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai", "api_key": _FAKE_KEY, "default_model": "gpt-4o"},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text
    assert "t-llm" in store.configs
    assert "t-llm" not in getattr(app.state, "_llm_configs", {})

    # The store no longer has it (deleted elsewhere): not served from memory.
    store.configs.clear()
    view = client.get("/tenants/me/llm", headers=_H).json()
    assert view["configured"] is False
