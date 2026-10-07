"""SECRET-01 / RV-04: connector secrets are durable (Postgres), never Redis-only.

Unit level: the store fails closed when Postgres is unavailable, a Redis outage
never fails a request, and no process (API lifespan, Celery goal worker,
mission publisher, workflow worker) wires the Redis-only store any more. The
real-Postgres behaviour lives in test_connector_registry_durable_integration.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.mcp.connector_secrets import DurableConnectorSecretStore, parse_secret_ref
from app.providers.vault import ConnectorSecretUnavailableError, get_vault
from app.tenancy.context import PlanTier, TenantContext

_APP = Path(__file__).resolve().parents[2] / "app"


def _ctx(tid: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _broken_db() -> Any:
    raise RuntimeError("postgres down")


class _FakeRedis:
    def __init__(self, data: dict[str, str] | None = None, *, broken: bool = False) -> None:
        self.data = dict(data or {})
        self.broken = broken
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        if self.broken:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.broken:
            raise ConnectionError("redis down")
        self.data[key] = value

    async def delete(self, *keys: str) -> None:
        if self.broken:
            raise ConnectionError("redis down")
        self.deleted.extend(keys)
        for k in keys:
            self.data.pop(k, None)


def test_no_process_wires_the_redis_only_store() -> None:
    """API lifespan and every worker build the durable store (RV-04)."""
    offenders = []
    for path in _APP.rglob("*.py"):
        if path.name == "vault.py" and path.parent.name == "providers":
            continue  # the legacy class definition itself
        if re.search(r"RedisConnectorSecretStore\s*\(", path.read_text()):
            offenders.append(str(path.relative_to(_APP)))
    assert offenders == []


def test_builder_returns_durable_store() -> None:
    from app.mcp.connector_wiring import build_connector_secret_store

    store = build_connector_secret_store(_FakeRedis(), db_factory=_broken_db)
    assert isinstance(store, DurableConnectorSecretStore)
    assert store.production_safe is True


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("vault://connectors/builtin-github:work/token", ("builtin-github:work", "token")),
        ("secret://connector/srv/api_key", ("srv", "api_key")),
    ],
)
def test_parse_secret_ref(ref: str, expected: tuple[str, str]) -> None:
    assert parse_secret_ref(ref) == expected


@pytest.mark.parametrize("ref", ["vault://connectors/noslash", "https://x/y", "vault://connectors//k"])
def test_parse_secret_ref_rejects_malformed(ref: str) -> None:
    with pytest.raises(ValueError):
        parse_secret_ref(ref)


@pytest.mark.asyncio
async def test_resolve_fails_closed_when_postgres_is_down() -> None:
    store = DurableConnectorSecretStore(db_factory=_broken_db, redis=_FakeRedis())
    with pytest.raises(ConnectorSecretUnavailableError):
        await store.resolve("vault://connectors/srv/token", tenant_ctx=_ctx())


@pytest.mark.asyncio
async def test_store_fails_closed_and_leaves_redis_untouched() -> None:
    redis = _FakeRedis()
    store = DurableConnectorSecretStore(db_factory=_broken_db, redis=redis)
    with pytest.raises(ConnectorSecretUnavailableError):
        await store.store("vault://connectors/srv/token", "s3cret", tenant_ctx=_ctx())
    assert redis.data == {} and redis.deleted == []


@pytest.mark.asyncio
async def test_delete_server_fails_closed_when_postgres_is_down() -> None:
    store = DurableConnectorSecretStore(db_factory=_broken_db, redis=_FakeRedis())
    with pytest.raises(ConnectorSecretUnavailableError):
        await store.delete_server("srv", tenant_ctx=_ctx())


@pytest.mark.asyncio
async def test_ciphertext_cache_hit_serves_and_redis_outage_falls_through() -> None:
    ciphertext = get_vault().encrypt("cached-value")
    cache_key = "mcp:secretcache:v1:t1:srv:token"
    store = DurableConnectorSecretStore(
        db_factory=_broken_db, redis=_FakeRedis({cache_key: ciphertext})
    )
    assert await store.resolve("vault://connectors/srv/token", tenant_ctx=_ctx()) == (
        "cached-value"
    )
    # Redis down: the request goes to Postgres (down too here) and fails closed —
    # it never returns an empty secret.
    broken = DurableConnectorSecretStore(db_factory=_broken_db, redis=_FakeRedis(broken=True))
    with pytest.raises(ConnectorSecretUnavailableError):
        await broken.resolve("vault://connectors/srv/token", tenant_ctx=_ctx())
