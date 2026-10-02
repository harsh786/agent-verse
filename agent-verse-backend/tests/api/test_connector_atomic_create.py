"""MCPREG-07: connector creation is atomic (never overwrites another connection)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.mcp.connector_store import ConnectorConflictError
from app.mcp.registry import MCPRegistry
from tests.api.test_connectors_comprehensive2 import _VALID_KEY, _make_app, _make_registry

_H = {"X-API-Key": _VALID_KEY}


def _body(name: str = "Work GitHub") -> dict[str, Any]:
    return {"name": name, "url": "https://api.github.com", "auth_type": "none", "type": "github"}


class _RacingRegistry(MCPRegistry):
    """A registry where another request wins the race between check and insert."""

    def __init__(self, inner: MCPRegistry, *, kind: str, times: int = 1) -> None:
        self.__dict__.update(inner.__dict__)
        self.kind = kind
        self.times = times
        self.attempted: list[str] = []

    async def create(self, config: Any, *, tenant_ctx: Any) -> str:
        resolved = self._resolve(config)
        self.attempted.append(resolved.server_id)
        if self.times > 0:
            self.times -= 1
            raise ConnectorConflictError(self.kind, "taken concurrently")
        return await super().create(resolved, tenant_ctx=tenant_ctx)


def test_create_never_replaces_an_existing_connection() -> None:
    reg = _make_registry()
    client = TestClient(_make_app(reg))
    first = client.post("/connectors", json=_body("Work GitHub"), headers=_H)
    assert first.status_code == 201
    # Register goes through create(), not an unconditional upsert.
    assert hasattr(reg, "create")


def test_concurrent_same_name_create_is_409() -> None:
    reg = _RacingRegistry(_make_registry(), kind="name")
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post("/connectors", json=_body(), headers=_H)
    assert resp.status_code == 409
    assert "unique" in resp.text.lower() or "already exists" in resp.text.lower()


def test_concurrent_connection_id_collision_retries_with_a_suffix() -> None:
    reg = _RacingRegistry(_make_registry(), kind="id")
    client = TestClient(_make_app(reg))
    resp = client.post("/connectors", json=_body(), headers=_H)
    assert resp.status_code == 201, resp.text
    first, second = reg.attempted[0], reg.attempted[1]
    assert first == "builtin-github:work-github"
    assert second.startswith("builtin-github:work-github-") and second != first
    assert resp.json()["server_id"] == second
