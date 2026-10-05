"""NF-2: a MongoDB connection the call-time policy would refuse is refused at SAVE time.

TLS weakening, file-path / proxy options and ambient-identity mechanisms used to
be refused only when a tool ran; register and update accepted them (201/200)
and the connector looked healthy until first use. Now both return 422 and
nothing is stored.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

CTX = TenantContext(tenant_id="tid-save", plan=PlanTier.PROFESSIONAL, api_key_id="k")
H = {"X-API-Key": "k"}
GOOD = "mongodb://u:p@8.8.8.8:27017/shop"

BAD_URIS = [
    f"{GOOD}?tlsInsecure=true",
    f"{GOOD}?appName=a;tlsInsecure=true",
    f"{GOOD}?tls=false",
    f"{GOOD}?appName=a;tlsCAFile=/etc/passwd",
    f"{GOOD}?authMechanism=MONGODB-AWS",
    f"{GOOD}?appName=a;authMechanism=GSSAPI",
    f"{GOOD}?proxyHost=10.0.0.5",
]
BAD_FIELDS: list[dict[str, Any]] = [
    {"tls_allow_invalid_certificates": True},
    {"tls_allow_invalid_hostnames": "true"},
    {"tls": False},
    {"auth_mechanism": "MONGODB-AWS"},
    {"auth_mechanism": "MONGODB-X509"},  # without a client certificate
]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    app.state.connector_secret_store = {}
    return TestClient(app, raise_server_exceptions=False)


def _body(uri: str, **fields: Any) -> dict[str, Any]:
    return {
        "name": "orders-db",
        "type": "mongodb",
        "url": "builtin://",
        "auth_type": "none",
        "auth_config": {"url": uri, **fields},
    }


@pytest.mark.parametrize("uri", BAD_URIS)
def test_register_refuses_bad_uri(client: TestClient, uri: str) -> None:
    resp = client.post("/connectors", headers=H, json=_body(uri))
    assert resp.status_code == 422, resp.text
    assert client.get("/connectors", headers=H).json() == []


@pytest.mark.parametrize("fields", BAD_FIELDS)
def test_register_refuses_bad_fields(client: TestClient, fields: dict[str, Any]) -> None:
    resp = client.post("/connectors", headers=H, json=_body(GOOD, **fields))
    assert resp.status_code == 422, resp.text


def test_register_refuses_bad_top_level_uri_without_a_type(client: TestClient) -> None:
    resp = client.post(
        "/connectors",
        headers=H,
        json={
            "name": "orders-db",
            "url": f"{GOOD}?tlsInsecure=true",
            "auth_type": "none",
            "auth_config": {},
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("uri", BAD_URIS[:3])
def test_update_refuses_bad_uri_and_keeps_the_old_one(client: TestClient, uri: str) -> None:
    sid = client.post("/connectors", headers=H, json=_body(GOOD)).json()["server_id"]
    resp = client.put(f"/connectors/{sid}", headers=H, json=_body(uri))
    assert resp.status_code == 422, resp.text
    store = client.app.state.connector_secret_store  # type: ignore[attr-defined]
    assert store[f"vault://connectors/{sid}/url"] == GOOD


def test_update_refuses_bad_fields_with_the_sealed_uri_kept(client: TestClient) -> None:
    sid = client.post("/connectors", headers=H, json=_body(GOOD)).json()["server_id"]
    resp = client.put(
        f"/connectors/{sid}",
        headers=H,
        json=_body("<redacted>", tls_allow_invalid_certificates=True),
    )
    assert resp.status_code == 422, resp.text


def test_a_good_connection_saves(client: TestClient) -> None:
    resp = client.post(
        "/connectors", headers=H, json=_body(f"{GOOD}?tls=true&authSource=admin", tls=True)
    )
    assert resp.status_code == 201, resp.text
