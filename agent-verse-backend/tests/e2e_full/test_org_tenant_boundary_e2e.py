"""e2e_full: an org belongs to one tenant — over HTTP, over WebSocket, and in the DB.

Regressions for three defects found by the cross-tenant sweep:

1. Org handlers acted on the path's ``org_id`` without checking who owned it, so
   a tenant that knew another tenant's org id could read and create that org's
   custom roles, read its command history, and stream its live events. A
   router-wide ownership dependency now answers 404 for a foreign org.
2. Custom roles and command history lived in per-process dicts keyed by org id
   alone — lost on restart and invisible to other replicas. They are now rows
   (``org_custom_roles`` / ``org_commands``) under FORCE'd RLS.
3. The org MCP WebSocket accepted every connection and took its tenant from an
   attacker-controlled ``X-Tenant-Id`` header. HTTP middleware never runs for
   WebSocket scopes, so nothing else stopped it. It now authenticates before
   ``accept()`` and refuses a foreign org.

The WebSocket half is driven at the raw ASGI level (httpx has no WebSocket
transport) against the same booted app, pools and database.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _signup(app: Any, client: Any) -> tuple[AsyncClient, str]:
    email = f"org-boundary-{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post("/tenants/signup", json={"name": "Org Boundary", "email": email})
    assert r.status_code == 201, r.text
    key = r.json()["api_key"]
    return (
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://e2e-full",
            headers={"X-API-Key": key},
        ),
        key,
    )


async def _ws_handshake(app: Any, path: str, headers: dict[str, str]) -> dict[str, Any]:
    """Open a WebSocket at the ASGI level; return the server's first reply."""
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 5000),
        "server": ("e2e-full", 80),
        "subprotocols": [],
        "state": {},
    }
    inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await inbox.put({"type": "websocket.connect"})
    sent: list[dict[str, Any]] = []
    first = asyncio.Event()

    async def receive() -> dict[str, Any]:
        return await inbox.get()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        first.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(first.wait(), timeout=15)
    finally:
        await inbox.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(task, timeout=15)
        except (TimeoutError, Exception):
            task.cancel()
    return sent[0]


async def test_foreign_tenant_gets_404_on_every_org_surface(app: Any, client: Any) -> None:
    owner, _ = await _signup(app, client)
    intruder, _ = await _signup(app, client)
    try:
        created = await owner.post("/v1/org", json={"name": f"Boundary {uuid.uuid4().hex[:6]}"})
        assert created.status_code in (200, 201), created.text
        org_id = created.json()["id"]

        role = await owner.post(
            f"/v1/org/{org_id}/roles",
            json={"name": "Auditor", "permissions": [{"feature": "missions", "view": True}]},
        )
        assert role.status_code == 201, role.text

        for method, path, body in [
            ("GET", f"/v1/org/{org_id}", None),
            ("GET", f"/v1/org/{org_id}/roles", None),
            ("POST", f"/v1/org/{org_id}/roles", {"name": "Backdoor"}),
            ("DELETE", f"/v1/org/{org_id}/roles/{role.json()['id']}", None),
            ("GET", f"/v1/org/{org_id}/commands", None),
            ("POST", f"/v1/org/{org_id}/command", {"command": "list missions"}),
        ]:
            r = await intruder.request(method, path, json=body)
            assert r.status_code == 404, f"{method} {path} -> {r.status_code} {r.text}"

        # The intruder's attempts changed nothing the owner can see.
        roles = (await owner.get(f"/v1/org/{org_id}/roles")).json()
        custom = [r for r in roles if not r["is_built_in"]]
        assert [r["name"] for r in custom] == ["Auditor"]
    finally:
        await owner.aclose()
        await intruder.aclose()


async def test_roles_and_commands_are_durable_rows(app: Any, client: Any) -> None:
    owner, _ = await _signup(app, client)
    tenant_id = (await owner.get("/tenants/me")).json()["tenant_id"]
    try:
        org_id = (await owner.post("/v1/org", json={"name": "Durable Org"})).json()["id"]
        role = (await owner.post(f"/v1/org/{org_id}/roles", json={"name": "Reviewer"})).json()
        cmd = await owner.post(f"/v1/org/{org_id}/command", json={"command": "delete old drafts"})
        assert cmd.status_code == 202, cmd.text
        command_id = cmd.json()["command_id"]
        assert cmd.json()["requires_2fa"] is True  # high-risk: stays pending, not routed

        # The in-process fallback is not what served these — the rows are in Postgres.
        from app.org import runtime_store

        assert not runtime_store._MEM_ROLES
        assert not runtime_store._MEM_COMMANDS
        from app.db.rls import sqlalchemy_rls_context

        async with (
            app.state.db_session_factory() as s,
            s.begin(),
            sqlalchemy_rls_context(s, tenant_id),
        ):
            n_roles = (
                await s.execute(
                    text("SELECT count(*) FROM org_custom_roles WHERE id = CAST(:i AS uuid)"),
                    {"i": role["id"]},
                )
            ).scalar_one()
            n_cmds = (
                await s.execute(
                    text("SELECT count(*) FROM org_commands WHERE command_id = CAST(:i AS uuid)"),
                    {"i": command_id},
                )
            ).scalar_one()
        assert (n_roles, n_cmds) == (1, 1)

        got = await owner.get(f"/v1/org/{org_id}/commands/{command_id}")
        assert got.status_code == 200, got.text
        assert got.json()["status"] == "pending_2fa"
        listed = (await owner.get(f"/v1/org/{org_id}/commands")).json()
        assert listed["total"] == 1 and listed["commands"][0]["command_id"] == command_id

        upd = await owner.put(
            f"/v1/org/{org_id}/roles/{role['id']}", json={"name": "Senior Reviewer"}
        )
        assert upd.status_code == 200, upd.text
        names = [r["name"] for r in (await owner.get(f"/v1/org/{org_id}/roles")).json()]
        assert "Senior Reviewer" in names and "Reviewer" not in names

        assert (await owner.delete(f"/v1/org/{org_id}/roles/{role['id']}")).status_code == 204
        assert (await owner.delete(f"/v1/org/{org_id}/roles/{role['id']}")).status_code == 404
        assert (await owner.delete(f"/v1/org/{org_id}/roles/not-a-uuid")).status_code == 404
    finally:
        await owner.aclose()


async def test_org_mcp_socket_authenticates_before_accept(app: Any, client: Any) -> None:
    owner, owner_key = await _signup(app, client)
    intruder, intruder_key = await _signup(app, client)
    try:
        org_id = (await owner.post("/v1/org", json={"name": "Socket Org"})).json()["id"]
        path = f"/v1/org/{org_id}/mcp"

        # No credentials, and the old spoofable header path.
        for headers in ({}, {"X-Tenant-Id": "anything"}, {"Authorization": "Bearer nope"}):
            first = await _ws_handshake(app, path, headers)
            assert first["type"] == "websocket.close", (headers, first)
            assert first["code"] == 4401

        # A real key of ANOTHER tenant: authenticated, but not this org.
        first = await _ws_handshake(app, path, {"X-API-Key": intruder_key})
        assert first["type"] == "websocket.close" and first["code"] == 4404, first

        # The owner is let in.
        first = await _ws_handshake(app, path, {"X-API-Key": owner_key})
        assert first["type"] == "websocket.accept", first
    finally:
        await owner.aclose()
        await intruder.aclose()


async def test_civilization_socket_rejects_unauthenticated(app: Any) -> None:
    first = await _ws_handshake(app, f"/civilizations/{uuid.uuid4()}/ws", {})
    assert first["type"] == "websocket.close" and first["code"] == 4401, first
