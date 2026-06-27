"""RPA session state shared by agents and runner adapters."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

RPASessionStatus = Literal["created", "running", "complete", "failed"]


@dataclass
class RPASession:
    """State container for one tenant-scoped RPA automation session."""

    session_id: str
    tenant_id: str
    goal_id: str
    status: RPASessionStatus = "created"
    current_url: str | None = None
    screenshots: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── HTTP-level session management (used by the RPA API layer) ──────────────────

@dataclass
class RPAManagedSession:
    """Lightweight session record tracked by RPASessionStore for API consumers."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = ""
    status: str = "active"  # active | closed
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_used_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class RPASessionStore:
    """Redis-backed RPA session store with 24-hour TTL.

    Per-session key:  rpa_session:{session_id}        → JSON (TTL 24h)
    Per-tenant index: rpa_tenant_sessions:{tenant_id} → Redis Set of session_ids

    Falls back to an in-process dict when Redis is not available (tests / dev
    without Redis).
    """

    _SESSION_PREFIX = "rpa_session:"
    _TENANT_INDEX_PREFIX = "rpa_tenant_sessions:"
    _SESSION_TTL = 86_400  # 24 hours
    _INDEX_TTL = 86_400 * 2  # keep index a bit longer than sessions

    def __init__(self, redis: Any = None) -> None:
        self._redis = redis
        self._fallback: dict[str, RPAManagedSession] = {}

    # ── internal helpers ───────────────────────────────────────────────────────

    def _skey(self, session_id: str) -> str:
        return f"{self._SESSION_PREFIX}{session_id}"

    def _tkey(self, tenant_id: str) -> str:
        return f"{self._TENANT_INDEX_PREFIX}{tenant_id}"

    async def _redis_save(self, session: RPAManagedSession) -> None:
        await self._redis.set(
            self._skey(session.session_id),
            json.dumps(asdict(session)),
            ex=self._SESSION_TTL,
        )
        await self._redis.sadd(self._tkey(session.tenant_id), session.session_id)
        await self._redis.expire(self._tkey(session.tenant_id), self._INDEX_TTL)

    async def _redis_load(self, session_id: str) -> RPAManagedSession | None:
        raw = await self._redis.get(self._skey(session_id))
        if raw is None:
            return None
        return RPAManagedSession(**json.loads(raw))

    # ── public API (all async for uniformity) ──────────────────────────────────

    async def create(self, *, tenant_id: str) -> RPAManagedSession:
        """Create and persist a new active RPA session."""
        session = RPAManagedSession(tenant_id=tenant_id)
        if self._redis is not None:
            try:
                await self._redis_save(session)
                return session
            except Exception:
                pass  # fall through to in-memory
        self._fallback[session.session_id] = session
        return session

    async def get(self, session_id: str, *, tenant_id: str) -> RPAManagedSession | None:
        """Retrieve a session by ID, scoped to the given tenant."""
        if self._redis is not None:
            try:
                s = await self._redis_load(session_id)
                if s is not None:
                    return s if s.tenant_id == tenant_id else None
                return None
            except Exception:
                pass  # fall through to in-memory
        s = self._fallback.get(session_id)
        if s is None or s.tenant_id != tenant_id:
            return None
        return s

    async def list_active(self, *, tenant_id: str) -> list[RPAManagedSession]:
        """Return all active sessions for a tenant."""
        if self._redis is not None:
            try:
                session_ids: set[str] = await self._redis.smembers(self._tkey(tenant_id))
                sessions: list[RPAManagedSession] = []
                for sid in session_ids:
                    s = await self._redis_load(sid)
                    if s is not None and s.tenant_id == tenant_id and s.status == "active":
                        sessions.append(s)
                return sessions
            except Exception:
                pass  # fall through to in-memory
        return [
            s for s in self._fallback.values()
            if s.tenant_id == tenant_id and s.status == "active"
        ]

    async def close(self, session_id: str, *, tenant_id: str) -> bool:
        """Mark a session as closed."""
        s = await self.get(session_id, tenant_id=tenant_id)
        if s is None:
            return False
        s.status = "closed"
        if self._redis is not None:
            try:
                await self._redis_save(s)
                return True
            except Exception:
                pass  # fall through to in-memory
        self._fallback[session_id] = s
        return True
