"""A1: auth_type 'connection_string' (the catalog's MongoDB auth type) is accepted.

RegisterConnectorRequest.auth_type is the AuthType enum, which had no
CONNECTION_STRING: registering from the real catalog payload was a 422.
"""

from __future__ import annotations

import fakeredis.aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.registry import AuthType, MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

CTX = TenantContext(tenant_id="tid-a1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def test_enum_has_connection_string() -> None:
    assert AuthType("connection_string") is AuthType.CONNECTION_STRING
    cfg = MCPServerConfig(name="m", auth_type="connection_string")  # type: ignore[arg-type]
    assert cfg.auth_type == AuthType.CONNECTION_STRING


def test_register_and_update_with_connection_string() -> None:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    app.state.connector_secret_store = {}
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "name": "orders-db",
        "type": "mongodb",
        "url": "builtin://",
        "auth_type": "connection_string",
        "auth_config": {"url": "mongodb://u:p@8.8.8.8:27017/shop"},
    }
    created = client.post("/connectors", headers={"X-API-Key": "k"}, json=body)
    assert created.status_code == 201, created.text
    sid = created.json()["server_id"]
    updated = client.put(f"/connectors/{sid}", headers={"X-API-Key": "k"}, json=body)
    assert updated.status_code == 200, updated.text
    assert updated.json()["auth_type"] == "connection_string"
