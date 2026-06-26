"""RPA session state shared by agents and runner adapters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
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
    """In-memory RPA session store (production: use Redis with TTL)."""

    def __init__(self) -> None:
        self._sessions: dict[str, RPAManagedSession] = {}

    def create(self, *, tenant_id: str) -> RPAManagedSession:
        session = RPAManagedSession(tenant_id=tenant_id)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str, *, tenant_id: str) -> RPAManagedSession | None:
        s = self._sessions.get(session_id)
        if s is None or s.tenant_id != tenant_id:
            return None
        return s

    def list_active(self, *, tenant_id: str) -> list[RPAManagedSession]:
        return [
            s for s in self._sessions.values()
            if s.tenant_id == tenant_id and s.status == "active"
        ]

    def close(self, session_id: str, *, tenant_id: str) -> bool:
        s = self.get(session_id, tenant_id=tenant_id)
        if s is None:
            return False
        s.status = "closed"
        return True
