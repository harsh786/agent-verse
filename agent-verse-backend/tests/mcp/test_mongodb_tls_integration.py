"""MDB-05 / C5 / TG-04: the MongoDB MCP builtin against requireTLS servers.

The MCP path supported a CA bundle only: a server that requires a client
certificate (mutual TLS) closed the connection, and MONGODB-X509 (the tenant's
OWN certificate as the identity) was refused. Real ``mongod --tlsMode
requireTLS`` containers, through the real handler:

* no CA -> the server certificate does not verify; the right CA -> connected;
  another CA -> refused; tlsInsecure / tls_allow_invalid_certificates -> refused
  before any connection (MDB-07);
* a server requiring client certificates: CA only -> refused; client cert +
  key -> connected (mTLS);
* with authorization on: mTLS alone is not authorised; MONGODB-X509 with the
  client certificate authenticates as ``CN=agentverse-client``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from app.mcp.servers import mongodb_server

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_CLIENT_SUBJECT = "CN=agentverse-client"


def _pki() -> Any:
    from tests.ingestion.tls_certs import make_pki

    return make_pki(("localhost",))


def _mongod(pki: Any, *, client_certs: bool, auth: bool) -> Any:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    flags = "--bind_ip_all --tlsMode requireTLS --tlsCertificateKeyFile /tmp/server.pem "
    flags += "--tlsCAFile /tmp/ca.pem"
    if not client_certs:
        flags += " --tlsAllowConnectionsWithoutCertificates"
    if auth:
        flags += " --auth"
    return (
        DockerContainer("mongo:7.0")
        .with_env("SERVER_PEM", pki.server_cert + pki.server_key)
        .with_env("CA_PEM", pki.ca_cert)
        .with_env("CLIENT_PEM", pki.client_cert + pki.client_key)
        .with_exposed_ports(27017)
        .with_kwargs(entrypoint=["bash", "-c"])
        .with_command(
            [
                'printf "%s" "$SERVER_PEM" > /tmp/server.pem && '
                'printf "%s" "$CA_PEM" > /tmp/ca.pem && '
                'printf "%s" "$CLIENT_PEM" > /tmp/client.pem && '
                f"exec mongod {flags}"
            ]
        )
        .waiting_for(LogMessageWaitStrategy("Waiting for connections").with_startup_timeout(90))
    )


def _need_docker() -> None:
    if not os.environ.get("DOCKER_HOST"):
        pytest.skip("Docker not configured (DOCKER_HOST unset)")


@pytest.fixture(scope="module")
def server_tls() -> Iterator[dict[str, Any]]:
    """requireTLS; client certificates optional; no authorization."""
    _need_docker()
    pki = _pki()
    with _mongod(pki, client_certs=False, auth=False) as c:
        yield {
            "host": c.get_container_host_ip(),
            "port": int(c.get_exposed_port(27017)),
            "pki": pki,
        }


@pytest.fixture(scope="module")
def mutual_tls() -> Iterator[dict[str, Any]]:
    """requireTLS + client certificate required + authorization on, $external user."""
    _need_docker()
    pki = _pki()
    with _mongod(pki, client_certs=True, auth=True) as c:
        # Localhost exception: the first (admin) user, then the X.509 user as it.
        shell = [
            "mongosh",
            "--quiet",
            "--tls",
            "--tlsCAFile",
            "/tmp/ca.pem",
            "--tlsCertificateKeyFile",
            "/tmp/client.pem",
            "--host",
            "localhost",
        ]
        code, out = c.exec(
            [
                *shell,
                "--eval",
                "db.getSiblingDB('admin').createUser({user: 'root', pwd: 'rootpw', "
                "roles: ['root']})",
            ]
        )
        assert code == 0, out
        code, out = c.exec(
            [
                *shell,
                "-u",
                "root",
                "-p",
                "rootpw",
                "--authenticationDatabase",
                "admin",
                "--eval",
                "db.getSiblingDB('$external').runCommand({createUser: "
                f"'{_CLIENT_SUBJECT}', roles: [{{role: 'readWrite', db: 'shop'}}]}})",
            ]
        )
        assert code == 0, out
        yield {
            "host": c.get_container_host_ip(),
            "port": int(c.get_exposed_port(27017)),
            "pki": pki,
        }


@pytest.fixture(autouse=True)
def _allow_container_host(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    import app.ingestion.connector_egress as egress

    monkeypatch.setattr(egress, "_effective_allowlist", lambda: ["localhost", "127.0.0.1"])


def _uri(srv: dict[str, Any], query: str = "") -> str:
    base = f"mongodb://{srv['host']}:{srv['port']}/shop"
    return f"{base}?{query}" if query else base


async def _list(credentials: dict[str, Any]) -> dict[str, Any]:
    return await mongodb_server.call_tool("mongodb_list_collections", {}, credentials=credentials)


async def test_server_certificate_is_verified(server_tls: dict[str, Any]) -> None:
    pki = server_tls["pki"]
    no_ca = await _list({"url": _uri(server_tls), "tls": True})
    with_ca = await _list({"url": _uri(server_tls), "tls": True, "tls_ca_pem": pki.ca_cert})
    other_ca = await _list({"url": _uri(server_tls), "tls": True, "tls_ca_pem": _pki().ca_cert})

    assert "collections" not in no_ca, no_ca
    assert "collections" in with_ca, with_ca
    assert "collections" not in other_ca, other_ca


@pytest.mark.parametrize(
    "creds",
    [
        {"query": "tls=true&tlsInsecure=true"},
        {"query": "tls=true&tlsAllowInvalidCertificates=true"},
        {"query": "tls=true", "tls_allow_invalid_certificates": True},
    ],
)
async def test_certificate_bypass_is_refused(
    server_tls: dict[str, Any], creds: dict[str, Any]
) -> None:
    extra = {k: v for k, v in creds.items() if k != "query"}
    result = await _list({"url": _uri(server_tls, creds["query"]), **extra})
    assert result.get("status") == "tls_refused", result


async def test_client_certificate_is_presented(mutual_tls: dict[str, Any]) -> None:
    pki = mutual_tls["pki"]
    ca_only = await _list({"url": _uri(mutual_tls), "tls": True, "tls_ca_pem": pki.ca_cert})
    mtls = await mongodb_server.call_tool(
        "mongodb_count",
        {"collection": "c"},
        credentials={
            "url": _uri(mutual_tls),
            "tls_ca_pem": pki.ca_cert,
            "tls_client_cert": pki.client_cert,
            "tls_client_private_key": pki.client_key,
        },
    )

    assert "collections" not in ca_only, ca_only
    # TLS handshake succeeded; with authorization on, an unauthenticated client
    # is refused by the SERVER (not by a closed connection).
    assert "error" in mtls
    assert "closed" not in mtls["error"].lower(), mtls
    assert "auth" in mtls["error"].lower() or "requires" in mtls["error"].lower(), mtls


async def test_x509_authenticates_with_the_client_certificate(mutual_tls: dict[str, Any]) -> None:
    pki = mutual_tls["pki"]
    creds = {
        "url": _uri(mutual_tls),
        "tls_ca_pem": pki.ca_cert,
        "tls_client_cert": pki.client_cert,
        "tls_client_private_key": pki.client_key,
        "auth_mechanism": "MONGODB-X509",
    }
    inserted = await mongodb_server.call_tool(
        "mongodb_insert_one", {"collection": "c", "document": {"a": 1}}, credentials=creds
    )
    counted = await mongodb_server.call_tool(
        "mongodb_count", {"collection": "c"}, credentials=creds
    )

    assert inserted.get("success") is True, inserted
    assert counted.get("count") == 1, counted


async def test_x509_without_a_client_certificate_is_refused() -> None:
    result = await _list(
        {"url": "mongodb://8.8.8.8:27017/shop", "tls": True, "auth_mechanism": "MONGODB-X509"}
    )
    assert result.get("status") == "credentials_required", result
    assert "tls_client_cert" in result["error"]
