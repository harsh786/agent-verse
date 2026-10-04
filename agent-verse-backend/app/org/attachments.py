"""Durable mission attachments (a08-F177-01).

An attachment is a file a user uploads with a mission so the mission's agent can
read it (OCR / vision via ``extract_document``). It used to be written to the API
host's filesystem and referenced by absolute path: no other replica or worker
could read it, nothing deleted it, and the agent-callable OCR tool refuses
``file_path`` anyway.

Now each attachment is one row in ``org_attachments`` (RLS-forced, tenant
predicate on every query, ``expires_at`` retention). The agent reads it with
``extract_document(attachment_id=...)``; the utility tool resolves the id for the
calling tenant only (:func:`load_attachment`). The
``purge_expired_org_attachments`` beat task deletes expired rows in bounded
batches.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_RETENTION_DAYS = 30


def _retention() -> timedelta:
    raw = os.getenv("ORG_ATTACHMENT_RETENTION_DAYS", "")
    try:
        days = int(raw) if raw else DEFAULT_RETENTION_DAYS
    except ValueError:
        days = DEFAULT_RETENTION_DAYS
    return timedelta(days=max(1, days))


@dataclass(frozen=True)
class AttachmentBlob:
    attachment_id: str
    filename: str
    content_type: str
    content: bytes


async def store_attachment(
    session: Any,
    *,
    tenant_id: str,
    org_id: uuid.UUID,
    filename: str,
    content_type: str,
    content: bytes,
    uploaded_by: str | None = None,
) -> dict[str, Any]:
    """Insert one attachment row in the caller's RLS-scoped transaction.

    Raises on any DB error: the upload must fail rather than answer 201 for a
    file nobody can read.
    """
    from sqlalchemy import text

    attachment_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + _retention()
    await session.execute(
        text(
            "INSERT INTO org_attachments (id, tenant_id, org_id, filename, content_type, "
            "size_bytes, sha256, content, uploaded_by, expires_at) "
            "VALUES (:id, :tid, :org, :fn, :ct, :size, :sha, :content, :by, :exp)"
        ),
        {
            "id": attachment_id,
            "tid": uuid.UUID(tenant_id),
            "org": org_id,
            "fn": filename[:255],
            "ct": content_type,
            "size": len(content),
            "sha": hashlib.sha256(content).hexdigest(),
            "content": content,
            "by": uploaded_by,
            "exp": expires_at,
        },
    )
    return {
        "attachment_id": attachment_id.hex,
        "filename": filename,
        "content_type": content_type,
        "size": len(content),
        "expires_at": expires_at.isoformat(),
    }


async def load_attachment(tenant_id: str, attachment_id: str) -> AttachmentBlob | None:
    """Read an unexpired attachment for ``tenant_id`` (None when absent/foreign).

    Uses its own RLS-scoped session (the tool runs outside any request) and an
    explicit tenant predicate, so a superuser/BYPASSRLS connection cannot leak
    another tenant's file either. DB errors raise.
    """
    try:
        att_uuid = uuid.UUID(str(attachment_id))
        tenant_uuid = uuid.UUID(str(tenant_id))
    except ValueError:
        return None
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with (
        factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, str(tenant_uuid)),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT id, filename, content_type, content FROM org_attachments "
                    "WHERE tenant_id = :tid AND id = :id AND expires_at > now()"
                ),
                {"tid": tenant_uuid, "id": att_uuid},
            )
        ).first()
    if row is None:
        return None
    return AttachmentBlob(
        attachment_id=row.id.hex if isinstance(row.id, uuid.UUID) else str(row.id),
        filename=str(row.filename),
        content_type=str(row.content_type),
        content=bytes(row.content),
    )


async def purge_expired_org_attachments(
    session_factory: Any, *, batch_size: int = 500, max_batches: int = 100
) -> int:
    """Delete expired attachments across tenants in bounded batches.

    Runs under ``system_session`` (BYPASSRLS maintenance role); ``SKIP LOCKED``
    lets overlapping beat runs proceed without blocking each other.
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    total = 0
    for _ in range(max_batches):
        async with session_factory() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(
                    "DELETE FROM org_attachments WHERE id IN ("
                    "  SELECT id FROM org_attachments WHERE expires_at <= now() "
                    "  ORDER BY expires_at LIMIT :n FOR UPDATE SKIP LOCKED)"
                ),
                {"n": batch_size},
            )
            deleted = int(result.rowcount or 0)
        total += deleted
        if deleted < batch_size:
            break
    return total
