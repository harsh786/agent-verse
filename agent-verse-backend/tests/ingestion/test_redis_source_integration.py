"""SRC-REDIS: the Redis Sources connector against real Redis containers.

Every auth mode is exercised through the real Sources API (create -> health ->
POST /sync -> the worker body the queued task runs): no auth, requirepass, ACL
user, TLS with a custom CA, mutual TLS, redis:// and rediss:// URLs, Sentinel
(with Sentinel auth) and Cluster — plus the egress checks on the addresses a
Sentinel or cluster seed reports.

The containers are reachable on localhost only, so ``localhost`` / ``127.0.0.1``
go on the operator allowlist (the documented on-prem mechanism).
"""

from __future__ import annotations

import random
import re
import socket
from collections.abc import Iterator
from typing import Any

import pytest
import redis
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

import app.api.ingestion as ingestion_mod
from tests.ingestion.sources_harness import SourcesHarness
from tests.ingestion.tls_certs import make_pki

pytestmark = pytest.mark.integration

_ALPINE = "redis:7-alpine"
_STACK = "redis/redis-stack-server:7.4.0-v0"  # RedisJSON for the json type


def _free_port() -> int:
    """A free local port below 55536 (a cluster node also binds port + 10000)."""
    for _ in range(100):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])
        if port < 55536:
            return port
    port = random.randint(20000, 50000)
    return port


def _ready(container: DockerContainer, message: str) -> DockerContainer:
    strategy = LogMessageWaitStrategy(re.compile(re.escape(message)))
    return container.waiting_for(strategy.with_startup_timeout(90))


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost,127.0.0.1")
    get_settings.cache_clear()
    ingestion_mod._SOURCES.clear()
    yield
    ingestion_mod._SOURCES.clear()
    get_settings.cache_clear()


def _harness() -> SourcesHarness:
    return SourcesHarness(family="nosql_database", source_type="redis")


# ── No auth: every data type, caps, discovery, paging across syncs ───────────


@pytest.fixture(scope="module")
def plain() -> Iterator[tuple[str, int]]:
    container = _ready(DockerContainer(_STACK).with_exposed_ports(6379), "Ready to accept")
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(6379))
        r = redis.Redis(host=host, port=port)
        r.set("greeting", "hello world")
        r.set("big:blob", "x" * 5000)
        r.hset("user:1", mapping={"name": "Ada", "role": "admin"})
        r.rpush("queue:jobs", "a", "b", "c")
        r.sadd("tags", "red", "green")
        r.zadd("scores", {"ada": 3, "bob": 1})
        r.xadd("events", {"kind": "login", "user": "ada"})
        r.xadd("events", {"kind": "logout", "user": "ada"})
        r.execute_command("JSON.SET", "doc:1", "$", '{"title": "Guide", "tags": ["a", "b"]}')
        with r.pipeline() as p:
            for i in range(1200):
                p.set(f"bulk:{i}", f"value {i}")
            p.execute()
        r.close()
        yield host, port


async def test_no_auth_validate_discovers_patterns_and_types(plain: tuple[str, int]) -> None:
    host, port = plain
    h = _harness()
    source = h.create({"host": host, "port": port, "auth_type": "none"})

    health = h.health(source["source_id"])

    assert health["ok"] is True, health
    meta = health["metadata"]
    assert meta["mode"] == "standalone"
    assert meta["keys"] == 1208
    assert meta["sampled_keys"] == 1000  # discovery samples, it does not walk everything
    patterns = {p["pattern"]: p for p in meta["key_patterns"]}
    assert patterns["bulk:*"]["types"] == {"string": patterns["bulk:*"]["count"]}

    typed = h.create(
        {
            "host": host,
            "port": port,
            "key_patterns": "greeting, big:*, user:*, queue:*, tags, scores, events, doc:*",
        }
    )
    meta = h.health(typed["source_id"])["metadata"]
    assert meta["sampled_keys"] == 8
    assert meta["types"] == {
        "string": 2, "hash": 1, "list": 1, "set": 1, "zset": 1, "stream": 1, "json": 1
    }


async def test_sync_reads_every_type_with_caps(plain: tuple[str, int]) -> None:
    host, port = plain
    h = _harness()
    source = h.create(
        {
            "host": host,
            "port": port,
            "key_patterns": "greeting, big:*, user:*, queue:*, tags, scores, events, doc:*",
            "max_value_bytes": 1000,
        }
    )

    result, pipeline = await h.sync(source["source_id"])

    assert result.get("docs_indexed") == 8, result
    docs = {d.metadata["key"]: d for d in pipeline.docs}
    assert b"hello world" in docs["greeting"].content
    assert docs["big:blob"].metadata["truncated"] is True
    assert len(docs["big:blob"].content) < 1100
    assert b"name: Ada" in docs["user:1"].content
    assert b"a\nb\nc" in docs["queue:jobs"].content
    assert b"green\nred" in docs["tags"].content
    assert b"bob (1)\nada (3)" in docs["scores"].content
    assert b"kind=login" in docs["events"].content and b"kind=logout" in docs["events"].content
    assert b'"title": "Guide"' in docs["doc:1"].content
    assert {d.metadata["type"] for d in pipeline.docs} == {
        "string", "hash", "list", "set", "zset", "stream", "json"
    }
    # Stable ids: a re-sync of the same keys produces the same documents.
    again, pipeline2 = await h.sync(source["source_id"])
    assert again.get("docs_indexed") == 8, again
    assert {d.doc_id for d in pipeline2.docs} == {d.doc_id for d in pipeline.docs}


async def test_type_filter(plain: tuple[str, int]) -> None:
    host, port = plain
    h = _harness()
    source = h.create({"host": host, "port": port, "types": ["hash", "json"]})

    result, pipeline = await h.sync(source["source_id"])

    assert result.get("docs_indexed") == 2, result
    assert {d.metadata["key"] for d in pipeline.docs} == {"user:1", "doc:1"}


async def test_key_budget_resumes_over_several_syncs_then_starts_a_new_pass(
    plain: tuple[str, int],
) -> None:
    host, port = plain
    h = _harness()
    source = h.create(
        {"host": host, "port": port, "key_patterns": "bulk:*", "max_keys_per_sync": 500}
    )

    seen: set[str] = set()
    counts = []
    for _ in range(3):
        result, pipeline = await h.sync(source["source_id"])
        counts.append(result.get("docs_indexed"))
        seen |= {d.metadata["key"] for d in pipeline.docs}

    assert counts[0] == 500 and counts[1] == 500, counts
    # SCAN may return a key twice across batches, never miss one.
    assert seen == {f"bulk:{i}" for i in range(1200)}
    # The pass finished, so the next sync starts over instead of stalling.
    result, pipeline = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 500, result


async def test_url_input(plain: tuple[str, int]) -> None:
    host, port = plain
    h = _harness()
    source = h.create({"uri": f"redis://{host}:{port}/0", "key_patterns": "greeting"})

    assert h.health(source["source_id"])["ok"] is True
    result, _ = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 1, result


# ── requirepass and ACL users ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def secured() -> Iterator[tuple[str, int]]:
    container = _ready(
        DockerContainer(_ALPINE)
        .with_command(
            [
                "redis-server",
                "--requirepass", "default-p@ss",
                "--user", "alice", "on", ">alice:pw/1", "~app:*", "&*", "+@all",
            ]
        )
        .with_exposed_ports(6379),
        "Ready to accept",
    )
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(6379))
        r = redis.Redis(host=host, port=port, password="default-p@ss")
        r.set("app:config", "ok")
        r.set("secret:other", "alice may not read this")
        r.close()
        yield host, port


async def test_password_auth(secured: tuple[str, int]) -> None:
    host, port = secured
    h = _harness()
    source = h.create(
        {"host": host, "port": port, "auth_type": "password", "password": "default-p@ss"}
    )
    assert source["connection_config"]["password"] == "********"

    assert h.health(source["source_id"])["ok"] is True
    result, _ = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 2, result


async def test_acl_user_auth_is_scoped_by_the_acl(secured: tuple[str, int]) -> None:
    host, port = secured
    h = _harness()
    source = h.create(
        {
            "host": host,
            "port": port,
            "auth_type": "acl",
            "username": "alice",
            "password": "alice:pw/1",
            "key_patterns": "app:*",
        }
    )

    assert h.health(source["source_id"])["ok"] is True
    result, pipeline = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 1, result
    assert pipeline.docs[0].metadata["key"] == "app:config"


async def test_acl_credentials_in_a_url(secured: tuple[str, int]) -> None:
    host, port = secured
    h = _harness()
    source = h.create(
        {"uri": f"redis://alice:alice%3Apw%2F1@{host}:{port}/0", "key_patterns": "app:*"}
    )
    assert source["connection_config"]["uri"] == "********"
    assert h.health(source["source_id"])["ok"] is True


async def test_wrong_or_missing_password_is_an_honest_error(secured: tuple[str, int]) -> None:
    host, port = secured
    h = _harness()
    wrong = h.create({"host": host, "port": port, "auth_type": "password", "password": "nope"})
    health = h.health(wrong["source_id"])
    assert health["ok"] is False
    assert "invalid" in health["error"].lower() or "wrongpass" in health["error"].lower()
    assert "nope" not in health["error"]

    missing = h.create({"host": host, "port": port, "auth_type": "none"})
    health = h.health(missing["source_id"])
    assert health["ok"] is False
    assert "auth" in health["error"].lower()


# ── TLS: server verification with a custom CA, and mutual TLS ────────────────


def _tls_container(pki: Any, *, auth_clients: str) -> DockerContainer:
    script = (
        'printf "%s" "$SERVER_CERT" > /tmp/server.crt && '
        'printf "%s" "$SERVER_KEY" > /tmp/server.key && '
        'printf "%s" "$CA_CERT" > /tmp/ca.crt && '
        "exec redis-server --port 0 --tls-port 6379 --tls-cert-file /tmp/server.crt "
        "--tls-key-file /tmp/server.key --tls-ca-cert-file /tmp/ca.crt "
        f"--tls-auth-clients {auth_clients}"
    )
    return _ready(
        DockerContainer(_ALPINE)
        .with_env("SERVER_CERT", pki.server_cert)
        .with_env("SERVER_KEY", pki.server_key)
        .with_env("CA_CERT", pki.ca_cert)
        .with_kwargs(entrypoint=["sh", "-c"])
        .with_command([script])
        .with_exposed_ports(6379),
        "Ready to accept",
    )


@pytest.fixture(scope="module")
def tls_server() -> Iterator[tuple[str, int, Any]]:
    pki = make_pki()
    with _tls_container(pki, auth_clients="no") as container:
        yield container.get_container_host_ip(), int(container.get_exposed_port(6379)), pki


async def test_tls_verifies_the_server_against_the_custom_ca(
    tls_server: tuple[str, int, Any],
) -> None:
    host, port, pki = tls_server
    h = _harness()

    untrusted = h.create({"uri": f"rediss://{host}:{port}"})
    health = h.health(untrusted["source_id"])
    assert health["ok"] is False
    assert "certificate" in health["error"].lower()

    trusted = h.create({"uri": f"rediss://{host}:{port}", "tls_ca_pem": pki.ca_cert})
    assert h.health(trusted["source_id"])["ok"] is True

    fields = h.create({"host": host, "port": port, "tls": True, "tls_ca_pem": pki.ca_cert})
    assert h.health(fields["source_id"])["ok"] is True


@pytest.fixture(scope="module")
def mtls_server() -> Iterator[tuple[str, int, Any]]:
    pki = make_pki()
    with _tls_container(pki, auth_clients="yes") as container:
        yield container.get_container_host_ip(), int(container.get_exposed_port(6379)), pki


async def test_mutual_tls_requires_and_accepts_the_client_certificate(
    mtls_server: tuple[str, int, Any],
) -> None:
    host, port, pki = mtls_server
    h = _harness()

    no_cert = h.create({"host": host, "port": port, "tls": True, "tls_ca_pem": pki.ca_cert})
    assert h.health(no_cert["source_id"])["ok"] is False

    with_cert = h.create(
        {
            "host": host,
            "port": port,
            "tls": True,
            "tls_ca_pem": pki.ca_cert,
            "tls_client_cert": pki.client_cert,
            "tls_client_private_key": pki.client_key,
        }
    )
    assert with_cert["connection_config"]["tls_client_private_key"] == "********"
    health = h.health(with_cert["source_id"])
    assert health["ok"] is True, health
    result, _ = await h.sync(with_cert["source_id"])
    assert result.get("docs_indexed") == 0 and result.get("docs_failed") == 0, result


# ── Sentinel (with Sentinel auth) ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def sentinel() -> Iterator[tuple[str, int, int]]:
    """Master and Sentinel in one container. The master listens on the same port
    inside and outside the container, so the address the Sentinel reports
    (127.0.0.1:<port>) is reachable from the test too."""
    master_port = _free_port()
    script = (
        f"redis-server --port {master_port} --requirepass master-pw --daemonize yes && "
        "printf 'port 26379\\nsentinel monitor mymaster 127.0.0.1 "
        f"{master_port} 1\\nsentinel auth-pass mymaster master-pw\\n"
        "requirepass sentinel-pw\\n' > /tmp/sentinel.conf && "
        f"until redis-cli -p {master_port} -a master-pw --no-auth-warning ping; "
        "do sleep 0.1; done && "
        f"redis-cli -p {master_port} -a master-pw --no-auth-warning set app:k v && "
        "exec redis-server /tmp/sentinel.conf --sentinel"
    )
    container = _ready(
        DockerContainer(_ALPINE)
        .with_kwargs(entrypoint=["sh", "-c"])
        .with_command([script])
        .with_bind_ports(master_port, master_port)
        .with_exposed_ports(26379),
        "+monitor master mymaster",
    )
    with container:
        yield (
            container.get_container_host_ip(),
            int(container.get_exposed_port(26379)),
            master_port,
        )


async def test_sentinel_with_sentinel_auth(sentinel: tuple[str, int, int]) -> None:
    host, sentinel_port, master_port = sentinel
    h = _harness()
    source = h.create(
        {
            "mode": "sentinel",
            "sentinels": f"{host}:{sentinel_port}",
            "sentinel_master": "mymaster",
            "sentinel_password": "sentinel-pw",
            "password": "master-pw",
        }
    )
    assert source["connection_config"]["sentinel_password"] == "********"

    health = h.health(source["source_id"])
    assert health["ok"] is True, health
    assert health["metadata"]["master"] == f"127.0.0.1:{master_port}"
    result, pipeline = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 1, result
    assert pipeline.docs[0].metadata["key"] == "app:k"

    wrong = h.create(
        {
            "mode": "sentinel",
            "sentinels": f"{host}:{sentinel_port}",
            "sentinel_master": "mymaster",
            "sentinel_password": "bad",
            "password": "master-pw",
        }
    )
    assert h.health(wrong["source_id"])["ok"] is False


async def test_sentinel_reported_master_is_egress_checked(
    sentinel: tuple[str, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    host, sentinel_port, _ = sentinel
    # The Sentinel host is allowed; the master address it reports is not.
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost")
    get_settings.cache_clear()
    h = _harness()
    source = h.create(
        {
            "mode": "sentinel",
            "sentinels": f"localhost:{sentinel_port}",
            "sentinel_master": "mymaster",
            "sentinel_password": "sentinel-pw",
            "password": "master-pw",
        }
    )

    health = h.health(source["source_id"])

    assert health["ok"] is False
    assert "reported by Sentinel is not allowed" in health["error"]


# ── Cluster ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def cluster() -> Iterator[int]:
    """A one-node cluster announcing 127.0.0.1:<port> (same port inside and out)."""
    port = _free_port()
    script = (
        f"redis-server --port {port} --cluster-enabled yes --cluster-config-file "
        "/tmp/nodes.conf --cluster-announce-ip 127.0.0.1 --daemonize yes && "
        f"until redis-cli -p {port} ping; do sleep 0.2; done && "
        f"redis-cli -p {port} cluster addslotsrange 0 16383 && "
        f"until redis-cli -p {port} cluster info | grep -q cluster_state:ok; "
        "do sleep 0.2; done && "
        f"redis-cli -p {port} set '{{app}}:a' 1 && redis-cli -p {port} set '{{app}}:b' 2 && "
        "echo CLUSTER-READY && tail -f /dev/null"
    )
    container = _ready(
        DockerContainer(_ALPINE)
        .with_kwargs(entrypoint=["sh", "-c"])
        .with_command([script])
        .with_bind_ports(port, port),
        "CLUSTER-READY",
    )
    with container:
        yield port


async def test_cluster_and_its_announced_nodes_are_egress_checked(cluster: int) -> None:
    port = cluster
    h = _harness()
    source = h.create({"mode": "cluster", "cluster_nodes": f"127.0.0.1:{port}"})

    health = h.health(source["source_id"])
    assert health["ok"] is True, health
    assert health["metadata"]["primaries"] == [f"127.0.0.1:{port}"]
    result, pipeline = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 2, result
    assert {d.metadata["key"] for d in pipeline.docs} == {"{app}:a", "{app}:b"}

    # Make the node announce a private address: the connector must refuse to dial it.
    r = redis.Redis(host="127.0.0.1", port=port)
    try:
        r.config_set("cluster-announce-ip", "10.0.0.9")
        blocked = h.health(source["source_id"])
    finally:
        r.config_set("cluster-announce-ip", "127.0.0.1")
        r.close()
    assert blocked["ok"] is False
    assert "10.0.0.9" in blocked["error"]
    assert "not allowed by the egress policy" in blocked["error"]
