"""OAPI-01: POST /connectors/{id}/discover reports only what was committed."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.mcp.client import ToolDefinition
from tests.api.test_connectors_comprehensive2 import _VALID_KEY, _make_app

_H = {"X-API-Key": _VALID_KEY}


def _tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(name="a", description="", input_schema={}),
        ToolDefinition(name="b", description="", input_schema={}),
    ]


class _Tx:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, exc_type: Any, *a: Any) -> None:
        if exc_type is None and self.session.fail_commit:
            raise RuntimeError("commit failed: connection reset")


class _Session:
    def __init__(self, fail_commit: bool) -> None:
        self.fail_commit = fail_commit
        self.inserts = 0

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        if "INSERT INTO tool_capabilities" in str(stmt):
            self.inserts += 1
        return None

    def begin(self) -> _Tx:
        return _Tx(self)

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None


def _app(fail_commit: bool) -> Any:
    app = _make_app()
    client_mock = AsyncMock()
    client_mock.discover_tools.return_value = _tools()
    app.state.mcp_client = client_mock
    app.state.db_session_factory = lambda: _Session(fail_commit)
    return app


def test_tools_saved_counts_committed_rows() -> None:
    resp = TestClient(_app(False)).post("/connectors/srv/discover", headers=_H)
    assert resp.status_code == 200
    assert resp.json()["tools_discovered"] == 2
    assert resp.json()["tools_saved"] == 2


def test_rolled_back_persist_is_503_not_a_saved_count() -> None:
    resp = TestClient(_app(True), raise_server_exceptions=False).post(
        "/connectors/srv/discover", headers=_H
    )
    assert resp.status_code == 503
    assert "connection reset" not in resp.text


def test_no_database_is_503() -> None:
    app = _app(False)
    app.state.db_session_factory = None
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/connectors/srv/discover", headers=_H
    )
    assert resp.status_code == 503
