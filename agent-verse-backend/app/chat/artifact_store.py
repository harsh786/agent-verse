"""Binary artifact store for chat-generated files (Phase 4, ORG-42).

Holds generated documents (PDF/doc/sheet/...) so a chat turn can hand back a
downloadable file. Tenant-scoped, size-capped, with a retention window.

One ``ChatArtifactStore`` object lives on ``app.state.chat_artifact_store`` and is
captured by the ``generate_document`` skill at registry build time. The lifespan
calls :meth:`ChatArtifactStore.attach_repository` with the Postgres chat
repository, after which every put/get goes to the ``chat_artifacts`` table: a
document generated on one replica downloads from any other and survives restarts.
With a repository attached, a failed write or read raises -- it never falls back
to process memory.

Without a repository (unit tests, the in-memory app path) documents live in a
bounded LRU so memory cannot grow without limit.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.chat.ownership import SYSTEM_SCOPE, ChatScope

# A generated document larger than this is refused rather than stored.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
# Generated documents are kept this long, then purged.
DEFAULT_RETENTION_DAYS = 30
# In-memory (no repository) bound: oldest entries are evicted beyond this.
DEFAULT_MEMORY_ENTRIES = 256


class ChatArtifactTooLargeError(ValueError):
    """The artifact exceeds the configured size cap."""


@dataclass(frozen=True)
class StoredArtifact:
    id: str
    tenant_id: str
    filename: str
    mime: str
    content: bytes
    expires_at: datetime | None = None
    # The chat session it was generated in (None: not tied to one).
    session_id: str | None = None


class ChatArtifactStore:
    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        memory_entries: int = DEFAULT_MEMORY_ENTRIES,
    ) -> None:
        self._max_bytes = max_bytes
        self._retention = timedelta(days=retention_days)
        self._memory_entries = memory_entries
        self._items: OrderedDict[str, StoredArtifact] = OrderedDict()
        self._repo: Any = None

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def durable(self) -> bool:
        return self._repo is not None

    def attach_repository(self, repository: Any) -> None:
        """Switch to durable storage (``PostgresChatRepository``)."""
        self._repo = repository
        self._items.clear()

    async def put(
        self,
        *,
        tenant_id: str,
        content: bytes,
        mime: str,
        filename: str,
        session_id: str | None = None,
    ) -> str:
        if len(content) > self._max_bytes:
            raise ChatArtifactTooLargeError(
                f"artifact is {len(content)} bytes; the limit is {self._max_bytes}"
            )
        artifact_id = uuid.uuid4().hex
        expires_at = datetime.now(UTC) + self._retention
        if self._repo is not None:
            await self._repo.put_artifact(
                artifact_id=artifact_id,
                tenant_id=tenant_id,
                kind="document",
                title=filename[:255],
                mime=mime,
                content=content,
                session_id=session_id,
                expires_at=expires_at,
                scope=SYSTEM_SCOPE,
            )
            return artifact_id
        self._items[artifact_id] = StoredArtifact(
            id=artifact_id,
            tenant_id=tenant_id,
            filename=filename,
            mime=mime,
            content=content,
            expires_at=expires_at,
            session_id=session_id,
        )
        while len(self._items) > self._memory_entries:
            self._items.popitem(last=False)
        return artifact_id

    async def get(
        self, artifact_id: str, tenant_id: str, *, scope: ChatScope
    ) -> StoredArtifact | None:
        """A live document. One generated in a chat session is returned only
        within that session's scope (CHAT-SEC-1); the caller re-checks the
        session in memory mode, where this store does not know its owner."""
        if self._repo is not None:
            row = await self._repo.get_artifact(
                artifact_id, tenant_id, scope=scope, kind="document"
            )
            if row is None:
                return None
            return StoredArtifact(
                id=str(row["id"]),
                tenant_id=str(row["tenant_id"]),
                filename=str(row["title"]),
                mime=str(row["mime"]),
                content=bytes(row["content"]),
                expires_at=row.get("expires_at"),
                session_id=row.get("session_id"),
            )
        art = self._items.get(artifact_id)
        if art is None or art.tenant_id != tenant_id:
            return None
        if art.expires_at is not None and art.expires_at <= datetime.now(UTC):
            self._items.pop(artifact_id, None)
            return None
        return art


async def purge_expired_chat_artifacts(
    session_factory: Any, *, batch_size: int = 1000, max_batches: int = 100
) -> int:
    """Delete expired chat artifacts across tenants in bounded batches.

    Runs under ``system_session`` (needs the BYPASSRLS maintenance role): under the
    app role it fails loudly instead of deleting nothing. ``SKIP LOCKED`` lets two
    beat runs overlap without blocking each other.
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    total = 0
    for _ in range(max_batches):
        async with session_factory() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(
                    "DELETE FROM chat_artifacts WHERE id IN ("
                    "  SELECT id FROM chat_artifacts "
                    "  WHERE expires_at IS NOT NULL AND expires_at <= now() "
                    "  ORDER BY expires_at LIMIT :n FOR UPDATE SKIP LOCKED)"
                ),
                {"n": batch_size},
            )
            deleted = int(result.rowcount or 0)
        total += deleted
        if deleted < batch_size:
            break
    return total
