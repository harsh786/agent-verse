"""P1c-1: saving a MongoDB Source enforces the shared MongoDB connection policy (422).

The live run (SRC-MONGO-TLS / SRC-MONGO-REFUSAL) created Sources whose URI switched
TLS verification off (``?tlsInsecure=true``, ``;tlsAllowInvalidHostnames=true``, the
``tls_allow_invalid_certificates`` field), read platform files (``tlsCAFile``,
``;tlsCertificateKeyFile``), routed through a proxy (``;proxyHost``) or used the
platform's ambient identity (MONGODB-AWS / OIDC / GSSAPI): 201, and only the sync
failed later. The MCP connector already refuses the same on register (NF-2); a
Source now does too on create, update and validate.

Only a public IP literal is used, so no DNS lookup is needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ingestion as ingestion_mod
from app.api.ingestion import router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-mongo-policy", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_KEY = "av_test_source_mongo_policy"
_AUTH = {"X-API-Key": _KEY}
_HOST = "93.184.216.34:27017"


@pytest.fixture(autouse=True)
def _clear_sources() -> Any:
    ingestion_mod._SOURCES.clear()
    yield
    ingestion_mod._SOURCES.clear()


def _client() -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _body(cc: dict[str, Any]) -> dict[str, Any]:
    return {"name": "orders", "family": "nosql_database", "source_type": "mongodb",
            "connection_config": {"database": "shop", **cc}, "collection_id": "col-1"}


REFUSED: list[tuple[str, dict[str, Any]]] = [
    ("tlsInsecure", {"uri": f"mongodb://{_HOST}/?tlsInsecure=true"}),
    ("tlsAllowInvalidCertificates", {"uri": f"mongodb://{_HOST}/?tls=true&tlsAllowInvalidCertificates=true"}),
    ("';' tlsAllowInvalidHostnames", {"uri": f"mongodb://{_HOST}/?tls=true;tlsAllowInvalidHostnames=true"}),
    ("%3B tlsInsecure", {"uri": f"mongodb://{_HOST}/?tls=true%3BtlsInsecure=true"}),
    ("field", {"uri": f"mongodb://{_HOST}/", "tls": True, "tls_allow_invalid_certificates": True}),
    ("tls=false", {"uri": f"mongodb://{_HOST}/?tls=false"}),
    ("tlsCAFile", {"uri": f"mongodb://{_HOST}/?tls=true&tlsCAFile=/etc/ssl/certs/ca.crt"}),
    ("';' tlsCertificateKeyFile", {"uri": f"mongodb://{_HOST}/?replicaSet=rs0;tlsCertificateKeyFile=/app/.env"}),
    ("';' proxyHost", {"uri": f"mongodb://{_HOST}/?replicaSet=rs0;proxyHost=evil.example.com"}),
    ("MONGODB-AWS", {"uri": f"mongodb://{_HOST}/?authMechanism=MONGODB-AWS"}),
    ("MONGODB-OIDC", {"uri": f"mongodb://{_HOST}/?authMechanism=MONGODB-OIDC"}),
    ("GSSAPI field", {"uri": f"mongodb://{_HOST}/", "auth_mechanism": "GSSAPI"}),
    ("X.509 without a client cert", {"uri": f"mongodb://{_HOST}/", "auth_mechanism": "MONGODB-X509"}),
]


@pytest.mark.parametrize(("label", "cc"), REFUSED, ids=[c[0] for c in REFUSED])
def test_create_refuses_policy_violations(label: str, cc: dict[str, Any]) -> None:
    resp = _client().post("/sources", json=_body(cc), headers=_AUTH)
    assert resp.status_code == 422, (label, resp.status_code, resp.text)
    assert "mongodb" in resp.json()["detail"].lower()
    assert not ingestion_mod._SOURCES


@pytest.mark.parametrize(("label", "cc"), REFUSED[:3], ids=[c[0] for c in REFUSED[:3]])
def test_validate_reports_policy_violations(label: str, cc: dict[str, Any]) -> None:
    resp = _client().post("/sources/validate?check_connection=false", json=_body(cc),
                          headers=_AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is False
    assert any("not allowed" in e for e in body["errors"]), body


def test_update_refuses_policy_violation_and_keeps_config() -> None:
    client = _client()
    created = client.post("/sources", json=_body({"uri": f"mongodb://{_HOST}/?tls=true"}),
                          headers=_AUTH)
    assert created.status_code == 201, created.text
    sid = created.json()["source_id"]
    resp = client.patch(f"/sources/{sid}", json={"connection_config": {
        "uri": f"mongodb://{_HOST}/?tls=true;tlsInsecure=true", "database": "shop"}},
        headers=_AUTH)
    assert resp.status_code == 422, resp.text
    stored = client.get(f"/sources/{sid}", headers=_AUTH).json()["connection_config"]
    assert "tlsInsecure" not in str(stored)


def test_clean_config_is_accepted() -> None:
    resp = _client().post("/sources", json=_body({
        "uri": f"mongodb://{_HOST}/?replicaSet=rs0&authSource=admin", "tls": True,
        "username": "reader", "password": "pw-123456"}), headers=_AUTH)
    assert resp.status_code == 201, resp.text


def test_config_without_uri_is_left_to_configuration_status() -> None:
    """No URI / host yet: not a policy violation (the Source needs configuration)."""
    resp = _client().post("/sources", json=_body({}), headers=_AUTH)
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize(("label", "cc"), [
    ("URL query options", {"uri": "rediss://93.184.216.34:6379/0?ssl_cert_reqs=none"}),
    ("unknown type", {"host": "93.184.216.34", "types": "string,bloom"}),
    ("sentinel without master", {"mode": "sentinel", "sentinels": "93.184.216.34:26379"}),
    ("cluster with a database", {"mode": "cluster", "host": "93.184.216.34", "db": 3}),
    ("cert without key", {"host": "93.184.216.34", "tls_client_cert": "-----BEGIN"}),
], ids=lambda v: v if isinstance(v, str) else "")
def test_redis_config_errors_are_refused_on_save(label: str, cc: dict[str, Any]) -> None:
    """P1c-10: a Redis config the sync would refuse is refused when saved (live:
    a URL with ?ssl_cert_reqs=none was a 201 whose every sync failed)."""
    body = {"name": "cache", "family": "nosql_database", "source_type": "redis",
            "connection_config": cc, "collection_id": "col-1"}
    resp = _client().post("/sources", json=body, headers=_AUTH)
    assert resp.status_code == 422, (label, resp.text)
    assert "redis" in resp.json()["detail"].lower()


def test_redis_without_an_address_is_left_to_configuration_status() -> None:
    body = {"name": "cache", "family": "nosql_database", "source_type": "redis",
            "connection_config": {"key_patterns": "a:*"}, "collection_id": "col-1"}
    assert _client().post("/sources", json=body, headers=_AUTH).status_code == 201
