"""NF-3: the frontend's MongoDB fixtures are the REAL backend output.

* ``mongodb_catalog.json`` (agent-verse-frontend/src/test/fixtures/) is
  GENERATED here from ``GET /connectors/catalog`` and the register / GET
  responses for the shared request below — never hand-copied. The test fails
  when the committed file drifts from what the backend serves; regenerate it
  with ``AGENTVERSE_WRITE_FE_FIXTURES=1 uv run pytest <this file>``.
* ``mongo_register_request.json`` is the body the connectors register form
  produces (asserted by ConnectorsMongoFixtures.test.tsx). Here the SAME body
  is POSTed to /connectors and must register a working built-in MongoDB
  connection — so the form is proven against the real registration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

FIXTURES = (
    Path(__file__).resolve().parents[3] / "agent-verse-frontend" / "src" / "test" / "fixtures"
)
REQUEST_FILE = FIXTURES / "mongo_register_request.json"
CATALOG_FILE = FIXTURES / "mongodb_catalog.json"

TENANT = TenantContext(tenant_id="tid-fe-fixture", plan=PlanTier.PROFESSIONAL, api_key_id="kf")
H = {"X-API-Key": "kf"}


class _Coll:
    def count_documents(self, query: dict[str, Any]) -> int:
        return 7


class _DB:
    def command(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": 1.0}

    def __getitem__(self, name: str) -> _Coll:
        return _Coll()

    def list_collection_names(self) -> list[str]:
        return ["orders"]


class _Mongo:
    dsns: list[str] = []
    kwargs: list[dict[str, Any]] = []

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        _Mongo.dsns.append(dsn)
        _Mongo.kwargs.append(kwargs)

    def __getitem__(self, name: str) -> _DB:
        return _DB()

    def close(self) -> None:
        pass


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import pymongo

    _Mongo.dsns, _Mongo.kwargs = [], []
    monkeypatch.setattr(pymongo, "MongoClient", _Mongo)
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return TENANT if key == "kf" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    reg = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    app.state.mcp_registry = reg
    secrets: dict[str, str] = {}
    app.state.connector_secret_store = secrets

    async def _secret(ref: str, tenant_ctx: Any = None) -> str | None:
        return secrets.get(ref)

    app.state.mcp_client = MCPClient(registry=reg, secret_resolver=_secret)
    return {"client": TestClient(app, raise_server_exceptions=False), "app": app}


def _shared_request() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(REQUEST_FILE.read_text())
    return body


def _backend_output(client: TestClient) -> dict[str, Any]:
    entry = next(
        e for e in client.get("/connectors/catalog", headers=H).json() if e["name"] == "mongodb"
    )
    created = client.post("/connectors", headers=H, json=_shared_request())
    assert created.status_code == 201, created.text
    row = client.get(f"/connectors/{created.json()['server_id']}", headers=H).json()
    return {
        "_generated_by": "agent-verse-backend/tests/api/test_connectors_fe_fixtures.py "
        "(AGENTVERSE_WRITE_FE_FIXTURES=1 to regenerate)",
        "catalog_entry": entry,
        "register_response": created.json(),
        "registered_row": row,
    }


def test_frontend_catalog_fixture_is_the_backend_output(world: dict[str, Any]) -> None:
    out = _backend_output(world["client"])
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if os.environ.get("AGENTVERSE_WRITE_FE_FIXTURES") == "1":
        CATALOG_FILE.write_text(text)
    assert CATALOG_FILE.exists(), f"missing {CATALOG_FILE}; regenerate it"
    assert json.loads(CATALOG_FILE.read_text()) == json.loads(text), (
        "agent-verse-frontend/src/test/fixtures/mongodb_catalog.json drifted from the backend; "
        "regenerate with AGENTVERSE_WRITE_FE_FIXTURES=1"
    )
    # The contract the frontend codes against.
    entry = out["catalog_entry"]
    assert entry["auth_type"] == "connection_string"
    assert entry["has_builtin"] is True and entry["builtin_server_id"] == "builtin-mongodb"
    keys = [f["key"] for f in entry["auth_fields"]]
    assert keys[0] == "url" and "tls_client_cert" in keys
    assert "tls_allow_invalid_certificates" not in keys
    for resp in (out["register_response"], out["registered_row"]):
        assert resp["url"] == "builtin://"
        assert resp["display_url"] == "mongodb://8.8.8.8:27017/shop"
        assert "S3cretPw" not in json.dumps(resp) and "alice:" not in json.dumps(resp)
    assert out["registered_row"]["auth_config"]["url"] == "<redacted>"
    assert out["registered_row"]["auth_config"]["password"] == "<redacted>"


async def test_the_form_body_registers_a_working_mongodb_connection(world: dict[str, Any]) -> None:
    """The exact body the register form sends (mongo_register_request.json)."""
    client = world["client"]
    resp = client.post("/connectors", headers=H, json=_shared_request())
    assert resp.status_code == 201, resp.text
    sid = resp.json()["server_id"]
    row = client.get(f"/connectors/{sid}", headers=H).json()
    assert row["has_builtin"] is True
    assert row["builtin_type"] == "builtin-mongodb"
    assert row["auth_type"] == "connection_string"

    tools = client.get(f"/connectors/{sid}/tools", headers=H)
    assert tools.status_code == 200, tools.text
    names = {t.get("name") for t in tools.json()}
    assert "mongodb_count" in names

    result = await world["app"].state.mcp_client.call_tool(
        server_id=sid,
        tool_name="mongodb_count",
        arguments={"collection": "orders"},
        tenant_ctx=TENANT,
    )
    assert result.success, result.error
    assert result.output["count"] == 7
    # The sealed URI and the separate credential fields reached the driver.
    assert "8.8.8.8" in _Mongo.dsns[-1]
    assert _Mongo.kwargs[-1].get("username") == "alice"
    assert _Mongo.kwargs[-1].get("authSource") == "admin"

    test = client.post(f"/connectors/{sid}/test", headers=H)
    assert test.status_code == 200, test.text
    assert test.json()["status"] == "passed", test.json()
