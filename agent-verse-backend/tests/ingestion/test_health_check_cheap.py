"""C8 (backend): a Source health check is cheap and shared.

Every open Sources UI polled ``GET /sources/{id}/health``; each call opened a full
MongoDB connection (member discovery + ping + listCollections), and a failing
Source was retried 3 times at once (4 connections, each up to the timeout).
Now the MongoDB probe is a ping, and the result — failures included — is cached
per Source (and per connection config) in the shared Redis for a short TTL, so
immediate retries and other replicas reuse it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import fakeredis.aioredis
import pytest

import app.api.ingestion as ingestion_mod
from app.ingestion.base_connector import ConnectionHealth
from app.ingestion.connectors import mongodb_connector as mc
from app.ingestion.source_config import SourceConfig
from tests.api.test_ingestion_api import _auth, _client, _make_source
from tests.ingestion.fake_mongod import FakeMongod


@pytest.fixture(autouse=True)
def _clear_sources() -> Iterator[None]:
    ingestion_mod._SOURCES.clear()
    yield
    ingestion_mod._SOURCES.clear()


class _CountingConnector:
    calls = 0
    ok = True

    async def validate_connection(self, src: SourceConfig) -> ConnectionHealth:
        raise AssertionError("the periodic health check must use the cheap probe")

    async def health_check(self, src: SourceConfig) -> ConnectionHealth:
        type(self).calls += 1
        if type(self).ok:
            return ConnectionHealth(ok=True, latency_ms=3.0, metadata={"host": "h"})
        return ConnectionHealth(ok=False, error="connection refused")


def _connector(ok: bool) -> type[_CountingConnector]:
    return type("C", (_CountingConnector,), {"calls": 0, "ok": ok})


def test_repeated_health_checks_reuse_one_probe_including_failures() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    failing = _connector(ok=False)
    client = _client(_redis=redis)
    with patch("app.ingestion.connector_registry.get_connector", return_value=failing):
        bodies = [
            client.get(f"/sources/{source.source_id}/health", headers=_auth()).json()
            for _ in range(4)  # the UI's request + its 3 immediate retries
        ]
    assert failing.calls == 1
    assert all(b["ok"] is False and b["error"] == "connection refused" for b in bodies)
    assert bodies[0]["cached"] is False and all(b["cached"] is True for b in bodies[1:])


def test_a_changed_connection_config_is_probed_again() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    source = _make_source(connection_config={"uri": "mongodb://a/"})
    ingestion_mod._SOURCES[source.source_id] = source
    counting = _connector(ok=True)
    client = _client(_redis=redis)
    with patch("app.ingestion.connector_registry.get_connector", return_value=counting):
        assert client.get(f"/sources/{source.source_id}/health", headers=_auth()).json()["ok"]
        source.connection_config = {"uri": "mongodb://b/"}
        body = client.get(f"/sources/{source.source_id}/health", headers=_auth()).json()
    assert counting.calls == 2
    assert body["cached"] is False


def test_without_redis_every_call_probes_no_process_local_cache() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    counting = _connector(ok=True)
    client = _client()
    with patch("app.ingestion.connector_registry.get_connector", return_value=counting):
        for _ in range(3):
            client.get(f"/sources/{source.source_id}/health", headers=_auth())
    assert counting.calls == 3


def test_connectors_without_a_cheap_probe_fall_back_to_validate_connection() -> None:
    class _Plain:
        async def validate_connection(self, src: SourceConfig) -> ConnectionHealth:
            return ConnectionHealth(ok=True, latency_ms=1.0)

    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    with patch("app.ingestion.connector_registry.get_connector", return_value=_Plain):
        body = _client().get(f"/sources/{source.source_id}/health", headers=_auth()).json()
    assert body["ok"] is True


async def test_the_mongodb_health_probe_is_a_ping_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "127.0.0.1")
    get_settings.cache_clear()
    srv = FakeMongod()
    try:
        config: Any = SourceConfig(
            source_id="m", tenant_id="t", name="m",
            family="nosql_database",  # type: ignore[arg-type]
            source_type="mongodb",
            connection_config={"uri": f"mongodb://{srv.me}/", "database": "db",
                               "collection": "c"},
        )
        health = await mc.MongoDBConnector().health_check(config)
    finally:
        srv.close()
        get_settings.cache_clear()
    assert health.ok is True, health.error
    assert "ping" in srv.commands
    assert "listcollections" not in srv.commands
    assert "find" not in srv.commands
