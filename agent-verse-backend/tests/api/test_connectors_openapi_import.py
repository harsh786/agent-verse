"""POST /connectors/import-openapi — secrets encrypted, tools visible to the agent.

Regressions:
* ``auth_config`` (API keys / tokens / passwords) was stored on the connector in
  plaintext, bypassing the tenant secret store every other write path uses.
* the registered config carried no ``tool_definitions``, and discovery for a
  non-MCP REST base URL found nothing, so the imported operations were invisible
  to the planner and could not be dispatched.
* capability-index persistence ran under ``suppress(Exception)``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from app.mcp.client import MCPClient
from app.providers.vault import is_connector_secret_ref
from tests.api.test_connectors_comprehensive2 import (
    _CTX,
    _VALID_KEY,
    _make_app,
    _make_registry,
)

_SECRET = "sk-live-DO-NOT-STORE-PLAIN"
_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "info": {"title": "Billing API", "version": "1.0.0"},
        "components": {
            "securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-Billing"}}
        },
        "security": [{"key": []}],
        "paths": {
            "/invoices": {"get": {"summary": "List invoices", "responses": {}}},
            "/invoices/{invoice_id}": {
                "get": {
                    "summary": "Get invoice",
                    "parameters": [
                        {"name": "invoice_id", "in": "path", "required": True,
                         "schema": {"type": "string"}}
                    ],
                    "responses": {},
                }
            },
        },
    }
)


def _import(client: TestClient, **overrides: Any) -> Any:
    payload = {
        "openapi_spec": _SPEC,
        "base_url": "https://93.184.216.34",
        "auth_config": {"api_key": _SECRET},
        **overrides,
    }
    return client.post(
        "/connectors/import-openapi", json=payload, headers={"X-API-Key": _VALID_KEY}
    )


def test_import_stores_secret_in_secret_store_not_on_connector() -> None:
    registry = _make_registry()
    app = _make_app(registry=registry)
    client = TestClient(app)
    resp = _import(client)
    assert resp.status_code == 201, resp.text
    server_id = resp.json()["server_id"]

    import asyncio

    cfg = asyncio.run(registry.get(server_id, tenant_ctx=_CTX))
    assert cfg is not None
    assert cfg.auth_config["api_key"] != _SECRET
    assert is_connector_secret_ref(cfg.auth_config["api_key"])
    # the spec's securityScheme decided type + placement
    assert cfg.auth_type == "api_key"
    assert cfg.auth_config["header_name"] == "X-Billing"
    # the raw secret is only in the tenant secret store
    assert _SECRET not in json.dumps(cfg.model_dump(mode="json"), default=str)
    store = app.state.connector_secret_store
    assert _SECRET in store.values()  # dev fallback store is a plain mapping
    # and never echoed back by the API
    listed = client.get(f"/connectors/{server_id}", headers={"X-API-Key": _VALID_KEY})
    assert _SECRET not in listed.text


def test_imported_tools_are_on_the_config_and_discoverable() -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry))
    resp = _import(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tools_imported"] == 2
    server_id = body["server_id"]

    import asyncio

    cfg = asyncio.run(registry.get(server_id, tenant_ctx=_CTX))
    names = {t["name"] for t in cfg.tool_definitions}
    assert names == {"get_invoices", "get_invoices_invoice_id"}
    assert all(t.get("http_method") and t.get("http_path") for t in cfg.tool_definitions)

    tools = asyncio.run(MCPClient(registry).discover_tools(server_id=server_id, tenant_ctx=_CTX))
    assert {t.name for t in tools} == names


def test_secret_store_failure_rolls_back_the_connector() -> None:
    registry = _make_registry()
    app = _make_app(registry=registry)

    class _BrokenStore:
        production_safe = True

        async def store(self, *_a: Any, **_kw: Any) -> None:
            raise RuntimeError("redis down")

    app.state.connector_secret_store = _BrokenStore()
    client = TestClient(app, raise_server_exceptions=False)
    resp = _import(client)
    assert resp.status_code == 503

    import asyncio

    assert asyncio.run(registry.list_servers(tenant_ctx=_CTX)) == []


def test_spec_without_operations_is_rejected() -> None:
    client = TestClient(_make_app())
    empty = json.dumps({"openapi": "3.0.0", "info": {"title": "x"}, "paths": {}})
    resp = _import(client, openapi_spec=empty)
    assert resp.status_code == 422
