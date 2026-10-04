"""NEO4J-EGRESS against a real Neo4j server (testcontainers).

With a ``neo4j://`` URI the driver asks the seed server for a routing table and
then connects to every router / reader / writer it names. Only the seed host was
egress-checked, so a tenant-controlled Neo4j could advertise an internal address
and the platform would dial it (the Kafka advertised-broker hole again).

A stock standalone Neo4j echoes the address the client used, so the hostile
routing table is crafted by a small Bolt proxy in front of the real server: it
rewrites the seed name ``seed.neo4j-egress.test`` to ``rout.neo4j-egress.test``
in server-to-client traffic (same length, so Bolt chunk sizes stay valid). A stub
resolver maps both names to the proxy on the loopback. Only the seed is
operator-allowlisted, so the routed connection must be refused — even though it
reaches the very same server, which is what makes the test honest: without the
check the sync simply succeeds.
"""

from __future__ import annotations

import contextlib
import select
import socket
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.connector_egress import ConnectorEgressBlockedError
from app.ingestion.source_config import SourceConfig, SourceFamily

pytestmark = pytest.mark.integration

_PASSWORD = "egress-test-pw"
_DOMAIN = "neo4j-egress.test"
SEED = f"seed.{_DOMAIN}"
ROUTER = f"rout.{_DOMAIN}"  # same length as SEED: the proxy rewrites in place


def _pump(src: socket.socket, dst: socket.socket, old: bytes, new: bytes) -> None:
    """Copy ``src`` to ``dst``, replacing ``old`` with ``new`` (never split a match)."""
    carry = b""
    try:
        while True:
            ready, _, _ = select.select([src], [], [], 0.05 if carry else None)
            if not ready:  # a held-back partial match that never completed
                dst.sendall(carry)
                carry = b""
                continue
            data = src.recv(65536)
            if not data:
                break
            data = (carry + data).replace(old, new) if old else data
            carry = b""
            for k in range(min(len(old) - 1, len(data)) if old else 0, 0, -1):
                if old.startswith(data[-k:]):
                    carry, data = data[-k:], data[:-k]
                    break
            if data:
                dst.sendall(data)
        if carry:
            dst.sendall(carry)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)


class _RewritingBoltProxy:
    """Loopback TCP proxy to the Neo4j container that forges its routing table."""

    def __init__(self, upstream_port: int) -> None:
        self._upstream_port = upstream_port
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port = int(self._listener.getsockname()[1])
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(("127.0.0.1", self._upstream_port))
            threading.Thread(target=_pump, args=(client, upstream, b"", b""), daemon=True).start()
            threading.Thread(
                target=_pump, args=(upstream, client, SEED.encode(), ROUTER.encode()), daemon=True
            ).start()

    def close(self) -> None:
        self._listener.close()


@pytest.fixture(scope="module")
def neo4j_port() -> Iterator[int]:
    """Port of the forging proxy in front of a real Neo4j holding one node."""
    from neo4j import GraphDatabase
    from testcontainers.neo4j import Neo4jContainer

    container = Neo4jContainer("neo4j:5-community", password=_PASSWORD)
    container.with_env("NEO4J_server_memory_heap_initial__size", "256m")
    container.with_env("NEO4J_server_memory_heap_max__size", "256m")
    container.with_env("NEO4J_server_memory_pagecache_size", "64m")
    with container:
        upstream = int(container.get_exposed_port(7687))
        with (
            GraphDatabase.driver(f"bolt://127.0.0.1:{upstream}", auth=("neo4j", _PASSWORD)) as drv,
            drv.session() as session,
        ):
            session.run("CREATE (:Doc {id: 'd1', title: 'routed'})").consume()
        proxy = _RewritingBoltProxy(upstream)
        try:
            yield proxy.port
        finally:
            proxy.close()


@pytest.fixture(autouse=True)
def _allow_connector_test_hosts() -> None:
    """Opt out of the connector-unit-test placeholder allowlist (tests/ingestion/
    conftest.py). It answers every ``*.test`` name with a fixed private address
    straight from the SSRF guard's resolver (SSRF-02), which would bypass
    ``stub_dns`` below, so the driver dialled 10.255.0.1 instead of the proxy.
    Each test here sets exactly the operator allowlist it means (``allowlist``)."""


@pytest.fixture(autouse=True)
def stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve ``*.neo4j-egress.test`` to the proxy on the loopback."""
    real = socket.getaddrinfo

    def _getaddrinfo(
        host: Any,
        port: Any,
        family: int = 0,
        type: int = 0,  # noqa: A002 - socket.getaddrinfo's own parameter name
        proto: int = 0,
        flags: int = 0,
    ) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if isinstance(name, str) and name.lower().rstrip(".").endswith("." + _DOMAIN):
            host = "127.0.0.1"
        return real(host, port, family, type, proto, flags)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)


@pytest.fixture
def allowlist(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.config import get_settings

    def _set(hosts: str) -> None:
        monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
        monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", hosts)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _config(uri: str) -> SourceConfig:
    return SourceConfig(
        source_id="src-neo4j",
        tenant_id="tenant-neo4j",
        name="neo4j",
        family=SourceFamily.WEB,
        source_type="neo4j",
        collection_id="col-1",
        connection_config={"uri": uri, "username": "neo4j", "password": _PASSWORD},
    )


async def _sync(uri: str) -> list[Any]:
    from app.ingestion.connectors.neo4j_connector import Neo4jConnector

    return [doc async for doc, _cursor in Neo4jConnector().get_delta(_config(uri), None)]


async def test_routing_table_address_outside_the_allowlist_is_refused(
    neo4j_port: int, allowlist: Any
) -> None:
    allowlist(SEED)
    with pytest.raises(ConnectorEgressBlockedError, match=r"rout\.neo4j-egress"):
        await _sync(f"neo4j://{SEED}:{neo4j_port}")


async def test_validate_connection_refuses_the_routed_address(
    neo4j_port: int, allowlist: Any
) -> None:
    from app.ingestion.connectors.neo4j_connector import Neo4jConnector

    allowlist(SEED)
    health = await Neo4jConnector().validate_connection(_config(f"neo4j://{SEED}:{neo4j_port}"))
    assert health.ok is False
    assert "SSRF guard" in (health.error or "")


async def test_direct_bolt_to_the_allowlisted_seed_still_works(
    neo4j_port: int, allowlist: Any
) -> None:
    allowlist(SEED)
    docs = await _sync(f"bolt://{SEED}:{neo4j_port}")
    assert any(b"routed" in d.content for d in docs)


async def test_routing_works_when_every_advertised_host_is_allowed(
    neo4j_port: int, allowlist: Any
) -> None:
    allowlist(f"{SEED},{ROUTER}")
    docs = await _sync(f"neo4j://{SEED}:{neo4j_port}")
    assert any(b"routed" in d.content for d in docs)
