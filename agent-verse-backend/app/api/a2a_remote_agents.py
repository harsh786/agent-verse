"""Remote A2A agent registry — tenant-scoped, server-side.

Remote agents registered on the A2A page used to live only in the browser's
localStorage: other operators never saw them, and the browser fetched arbitrary
agent-card URLs itself. They are now rows in ``a2a_remote_agents`` (migration
``5ea3dc985432``; ENABLE + FORCE RLS on ``app_current_tenant_uuid()``), and the
server fetches and validates each agent card through the SSRF guard.

Every statement runs under :func:`app.db.rls.sqlalchemy_rls_context` AND carries an
explicit tenant predicate. Without a DB bound (dev / unit tests) a process-local
dict stands in, mirroring ``app.api.a2a``'s task store.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.net.ssrf_guard import SSRFError, assert_public_url_async, public_async_client
from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["a2a"])

#: Dev/no-DB stand-in: tenant_id -> agent_id -> row. Never used when a DB is bound.
_memory: dict[str, dict[str, dict[str, Any]]] = {}

_CARD_MAX_BYTES = 256 * 1024
_LIST_LIMIT = 200
_COLUMNS = "id, name, url, card, last_error, last_checked_at, created_at"


class RemoteAgentRegister(BaseModel):
    url: str = Field(..., min_length=1, max_length=2_000)
    name: str = Field(default="", max_length=200)


def _tenant_id(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    tid = str(getattr(tenant, "tenant_id", "") or "")
    if not tid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return tid


def _db(request: Request) -> Any:
    return getattr(request.app.state, "db_session_factory", None)


async def _fetch_agent_card(url: str) -> dict[str, Any]:
    """Fetch and validate a remote agent card. Raises 422 with the reason."""
    try:
        await assert_public_url_async(url, context="A2A agent card")
    except (SSRFError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Agent card URL rejected: {exc}") from exc
    try:
        async with public_async_client(timeout=10.0) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
    except Exception as exc:
        logger.info("a2a_remote_card_fetch_failed", url=url, error=str(exc)[:200])
        raise HTTPException(
            status_code=422, detail="Agent card could not be fetched from that URL"
        ) from exc
    if resp.status_code != 200:
        raise HTTPException(
            status_code=422, detail=f"Agent card fetch returned HTTP {resp.status_code}"
        )
    if len(resp.content) > _CARD_MAX_BYTES:
        raise HTTPException(status_code=422, detail="Agent card is too large")
    try:
        card = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Agent card is not valid JSON") from exc
    name = card.get("name") if isinstance(card, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(
            status_code=422, detail="Agent card must be a JSON object with a non-empty name"
        )
    return card


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _row(r: Any) -> dict[str, Any]:
    card = r[3]
    if isinstance(card, str):
        card = json.loads(card)
    return {
        "id": str(r[0]),
        "name": r[1],
        "url": r[2],
        "card": card,
        "last_error": r[4],
        "last_checked_at": _iso(r[5]),
        "created_at": _iso(r[6]),
    }


async def _list(db: Any, tid: str) -> list[dict[str, Any]]:
    if db is None:
        rows = list(_memory.get(tid, {}).values())
        return sorted(rows, key=lambda a: a["created_at"] or "", reverse=True)
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as s, s.begin(), sqlalchemy_rls_context(s, tid):
        rows = (
            await s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM a2a_remote_agents "
                    "WHERE tenant_id = CAST(:tid AS uuid) "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"tid": tid, "lim": _LIST_LIMIT},
            )
        ).fetchall()
    return [_row(r) for r in rows]


async def _get(db: Any, tid: str, agent_id: str) -> dict[str, Any] | None:
    if db is None:
        return _memory.get(tid, {}).get(agent_id)
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as s, s.begin(), sqlalchemy_rls_context(s, tid):
        row = (
            await s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM a2a_remote_agents "
                    "WHERE id = :id AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"id": agent_id, "tid": tid},
            )
        ).fetchone()
    return _row(row) if row is not None else None


async def _insert(
    db: Any, tid: str, name: str, url: str, card: dict[str, Any]
) -> dict[str, Any] | None:
    """Insert; ``None`` when this tenant already registered ``url``."""
    agent_id = uuid.uuid4().hex
    if db is None:
        rows = _memory.setdefault(tid, {})
        if any(a["url"] == url for a in rows.values()):
            return None
        now = datetime.now(UTC).isoformat()
        rows[agent_id] = {
            "id": agent_id, "name": name, "url": url, "card": card,
            "last_error": None, "last_checked_at": now, "created_at": now,
        }
        return rows[agent_id]
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as s, s.begin(), sqlalchemy_rls_context(s, tid):
        row = (
            await s.execute(
                text(
                    "INSERT INTO a2a_remote_agents "
                    "(id, tenant_id, name, url, card, last_checked_at) "
                    "VALUES (:id, CAST(:tid AS uuid), :name, :url, CAST(:card AS jsonb), now()) "
                    "ON CONFLICT (tenant_id, url) DO NOTHING "
                    f"RETURNING {_COLUMNS}"
                ),
                {"id": agent_id, "tid": tid, "name": name, "url": url, "card": json.dumps(card)},
            )
        ).fetchone()
    return _row(row) if row is not None else None


async def _record_check(
    db: Any, tid: str, agent_id: str, card: dict[str, Any] | None, error: str | None
) -> dict[str, Any] | None:
    """Store a ping outcome. A failed ping keeps the last good card."""
    if db is None:
        row = _memory.get(tid, {}).get(agent_id)
        if row is None:
            return None
        if card is not None:
            row["card"] = card
        row["last_error"] = error
        row["last_checked_at"] = datetime.now(UTC).isoformat()
        return row
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as s, s.begin(), sqlalchemy_rls_context(s, tid):
        row = (
            await s.execute(
                text(
                    "UPDATE a2a_remote_agents SET "
                    "card = COALESCE(CAST(:card AS jsonb), card), "
                    "last_error = :err, last_checked_at = now() "
                    "WHERE id = :id AND tenant_id = CAST(:tid AS uuid) "
                    f"RETURNING {_COLUMNS}"
                ),
                {
                    "id": agent_id,
                    "tid": tid,
                    "card": json.dumps(card) if card is not None else None,
                    "err": error,
                },
            )
        ).fetchone()
    return _row(row) if row is not None else None


async def _delete(db: Any, tid: str, agent_id: str) -> bool:
    if db is None:
        return _memory.get(tid, {}).pop(agent_id, None) is not None
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as s, s.begin(), sqlalchemy_rls_context(s, tid):
        row = (
            await s.execute(
                text(
                    "DELETE FROM a2a_remote_agents "
                    "WHERE id = :id AND tenant_id = CAST(:tid AS uuid) RETURNING id"
                ),
                {"id": agent_id, "tid": tid},
            )
        ).fetchone()
    return row is not None


@router.get("/a2a/remote-agents")
async def list_remote_agents(request: Request) -> dict[str, Any]:
    """The caller tenant's registered remote A2A agents (newest first)."""
    tid = _tenant_id(request)
    return {"agents": await _list(_db(request), tid)}


@router.post("/a2a/remote-agents", status_code=201)
async def register_remote_agent(body: RemoteAgentRegister, request: Request) -> dict[str, Any]:
    """Register a remote agent after fetching and validating its agent card."""
    tid = _tenant_id(request)
    url = body.url.strip()
    card = await _fetch_agent_card(url)
    name = body.name.strip() or str(card["name"]).strip()
    row = await _insert(_db(request), tid, name[:200], url, card)
    if row is None:
        raise HTTPException(status_code=409, detail="That agent URL is already registered")
    return row


@router.post("/a2a/remote-agents/{agent_id}/ping")
async def ping_remote_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Re-fetch the agent card; a failure is recorded on the row (``last_error``)."""
    tid = _tenant_id(request)
    db = _db(request)
    existing = await _get(db, tid, agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    card: dict[str, Any] | None = None
    error: str | None = None
    try:
        card = await _fetch_agent_card(str(existing["url"]))
    except HTTPException as exc:
        error = str(exc.detail)
    row = await _record_check(db, tid, agent_id, card, error)
    if row is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    return row


@router.delete("/a2a/remote-agents/{agent_id}", status_code=204)
async def delete_remote_agent(agent_id: str, request: Request) -> Response:
    tid = _tenant_id(request)
    if not await _delete(_db(request), tid, agent_id):
        raise HTTPException(status_code=404, detail="Remote agent not found")
    return Response(status_code=204)
