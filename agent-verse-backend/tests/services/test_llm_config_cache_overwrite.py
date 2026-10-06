"""a08-F195-04: a failed cache overwrite never leaves replicas on the old config.

``set_config`` wrote Postgres, then overwrote ``llm_config:{tenant}`` in Redis;
a failed overwrite was only logged, so every other replica kept serving the
previous config — the old key included — for up to the 300 s TTL. It now
deletes the entry instead (the next read loads the new row), and when even the
delete fails it raises ``LLMConfigCacheStaleError`` (the API answers 503; the
write is idempotent, so the client retries). ``delete_config`` likewise.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.llm_config_store import (
    LLMConfigCacheStaleError,
    LLMConfigStore,
)


class _Redis:
    def __init__(self, *, fail_set: bool = False, fail_delete: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.fail_set = fail_set
        self.fail_delete = fail_delete

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.fail_set:
            raise ConnectionError("set refused")
        self.data[key] = value

    async def delete(self, *keys: str) -> int:
        if self.fail_delete:
            raise ConnectionError("delete refused")
        return sum(1 for k in keys if self.data.pop(k, None) is not None)


def _store(redis: _Redis) -> tuple[LLMConfigStore, dict[str, dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    store = LLMConfigStore(redis_client=redis, db_factory=object())

    async def _upsert(tenant_id: str, config: dict[str, Any]) -> None:
        rows[tenant_id] = dict(config)

    async def _get(tenant_id: str) -> dict[str, Any] | None:
        return rows.get(tenant_id)

    async def _delete(tenant_id: str) -> None:
        rows.pop(tenant_id, None)

    store._db_upsert = _upsert  # type: ignore[method-assign]
    store._db_get = _get  # type: ignore[method-assign]
    store._db_delete = _delete  # type: ignore[method-assign]
    return store, rows


def _cached_old(redis: _Redis) -> None:
    redis.data["llm_config:t1"] = json.dumps(
        {"provider": "openai", "encrypted_key": "OLD", "model": "m", "base_url": None}
    )


async def test_failed_overwrite_drops_the_old_entry() -> None:
    redis = _Redis(fail_set=True)
    _cached_old(redis)
    store, _rows = _store(redis)
    await store.set_config("t1", "openai", "NEW", "m")
    assert "llm_config:t1" not in redis.data
    # The next read (any replica: the cache is shared) loads the new row.
    cfg = await store.get_config("t1")
    assert cfg is not None and cfg["encrypted_key"] == "NEW"


async def test_overwrite_and_delete_both_failing_is_reported() -> None:
    redis = _Redis(fail_set=True, fail_delete=True)
    _cached_old(redis)
    store, rows = _store(redis)
    with pytest.raises(LLMConfigCacheStaleError):
        await store.set_config("t1", "openai", "NEW", "m")
    assert rows["t1"]["encrypted_key"] == "NEW"  # durably saved; retry is safe


async def test_delete_config_reports_a_cache_entry_it_could_not_drop() -> None:
    redis = _Redis(fail_delete=True)
    _cached_old(redis)
    store, rows = _store(redis)
    rows["t1"] = {"provider": "openai", "encrypted_key": "OLD"}
    with pytest.raises(LLMConfigCacheStaleError):
        await store.delete_config("t1")
    assert "t1" not in rows


async def test_api_put_answers_503_when_replicas_may_keep_the_old_config() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.tenants import router as tenants_router
    from app.governance.audit import AuditLog
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    redis = _Redis(fail_set=True, fail_delete=True)
    store, _rows = _store(redis)
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return TenantContext("t1", PlanTier.STARTER, key, roles=("admin",))

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.state.audit_log = AuditLog()
    app.state.llm_config_store = store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai", "api_key": "fake-provider-key-0123456789"},
        headers={"X-API-Key": "k"},
    )
    assert resp.status_code == 503
    assert "retry" in resp.json()["detail"]
