"""MONGO-CREDS: a MongoDB connector's connection URI is egress-checked per host.

Registration ran the HTTP-only SSRF check on the connector URL, so every
``mongodb://`` / ``mongodb+srv://`` URI was refused on scheme alone (the tenant
could never configure its own database). The URI is now checked host by host —
every replica-set seed — and a private host is still refused.
"""

from __future__ import annotations

import fakeredis.aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-mongo", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "av_professional_mongo"


def _client() -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    return TestClient(app, raise_server_exceptions=False)


def _register(client: TestClient, url: str) -> int:
    resp = client.post(
        "/connectors",
        json={
            "name": "MongoDB",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": url},
        },
        headers={"X-API-Key": _KEY},
    )
    return resp.status_code


def test_public_mongodb_uri_registers() -> None:
    assert _register(_client(), "mongodb://u:p@8.8.8.8:27017,8.8.4.4:27017/db") == 201


def test_private_mongodb_replica_member_is_refused() -> None:
    assert _register(_client(), "mongodb://u:p@8.8.8.8:27017,10.0.0.7:27017/db") == 400
