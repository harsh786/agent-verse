"""MCPREG-05: DELETE /connectors/{id} erases the connector's credentials too.

Unregister removed only the config; the encrypted secrets (and the OAuth token
row and tool capability rows) stayed forever — an erasure gap.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi.testclient import TestClient

from tests.api.test_connectors_comprehensive2 import _VALID_KEY, _make_app

_H = {"X-API-Key": _VALID_KEY}


def _create(client: TestClient) -> str:
    resp = client.post(
        "/connectors",
        json={
            "name": "Secret Holder",
            "url": "https://api.github.com/mcp",
            "auth_type": "bearer",
            "auth_config": {"token": "ghp_secret_value"},
        },
        headers=_H,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["server_id"])


def test_delete_removes_stored_secrets() -> None:
    app = _make_app()
    client = TestClient(app)
    sid = _create(client)
    store = app.state.connector_secret_store
    assert any(sid in ref for ref in store), "the token was stored as a secret"

    assert client.delete(f"/connectors/{sid}", headers=_H).status_code == 204
    assert not any(sid in ref for ref in store)


class _Store:
    production_safe = True

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}

    async def store(self, ref: str, value: str, *, tenant_ctx: Any = None) -> None:
        self.values[ref] = value

    async def resolve(self, ref: str, *, tenant_ctx: Any = None) -> str | None:
        return self.values.get(ref)

    async def delete_server(self, server_id: str, *, tenant_ctx: Any = None) -> int:
        if self.fail:
            raise RuntimeError("store down")
        self.deleted.append((tenant_ctx.tenant_id, server_id))
        return 1


def test_delete_purges_durable_secrets_tokens_and_capabilities() -> None:
    executed: list[tuple[str, dict[str, Any]]] = []

    class _Session:
        async def execute(self, stmt: Any, params: Any = None) -> Any:
            executed.append((str(stmt), dict(params or {})))
            return None

        def begin(self) -> Any:
            return self

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

    @asynccontextmanager
    async def _db() -> Any:
        yield _Session()

    app = _make_app()
    store = _Store()
    app.state.connector_secret_store = store
    client = TestClient(app)
    sid = _create(client)
    app.state.db_session_factory = _db

    assert client.delete(f"/connectors/{sid}", headers=_H).status_code == 204
    assert store.deleted and store.deleted[0][1] == sid
    deletes = [sql for sql, p in executed if sql.lstrip().upper().startswith("DELETE")]
    assert any("oauth_tokens" in sql for sql in deletes)
    assert any("tool_capabilities" in sql for sql in deletes)
    for sql, params in executed:
        if sql.lstrip().upper().startswith("DELETE"):
            assert "tenant_id = :t" in sql and params["sid"] == sid


def test_secret_erasure_failure_keeps_the_connector_and_is_503() -> None:
    app = _make_app()
    store = _Store(fail=True)
    app.state.connector_secret_store = store
    client = TestClient(app, raise_server_exceptions=False)
    sid = _create(client)

    resp = client.delete(f"/connectors/{sid}", headers=_H)
    assert resp.status_code == 503
    assert "store down" not in resp.text
    # Nothing was half-deleted: the connector is still there to retry on.
    assert client.get(f"/connectors/{sid}", headers=_H).status_code == 200
