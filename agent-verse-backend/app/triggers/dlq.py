"""Dead Letter Queue (DLQ) helpers for trigger firings."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

_log = logging.getLogger(__name__)

FAILURE_TYPES = frozenset(
    [
        "SIGNATURE_INVALID",
        "PAYLOAD_TOO_LARGE",
        "CONDITION_ERROR",
        "TEMPLATE_RENDER_ERROR",
        "GOAL_ENQUEUE_FAILED",
        "RATE_LIMITED",
        "DEDUP_BLOCKED",
        "BULKHEAD_FULL",
        "CIRCUIT_OPEN",
        "QUOTA_EXCEEDED",
        "RBAC_DENIED",
    ]
)

# Retry delays in seconds (exponential backoff: 5s, 15s, 45s)
RETRY_DELAYS = [5, 15, 45]


async def write_to_dlq(
    db_session: object,
    *,
    tenant_id: str,
    trigger_id: str,
    failure_type: str,
    error_message: str,
    raw_payload: dict,
    retry_count: int = 0,
) -> None:
    """Write a failed trigger firing to the trigger_dlq table.

    The INSERT runs inside :func:`sqlalchemy_rls_context` so ``app.tenant_id`` is
    set for the statement. ``trigger_dlq`` is RLS-protected (and, since migration
    ``a1b2c3d4e5f6``, FORCE-protected); without the GUC the row matches no policy
    and the INSERT is rejected under any least-privilege (non-BYPASSRLS) role —
    which the ``except Exception`` below would then swallow, losing exactly the
    failure record the DLQ exists to keep.
    """
    if db_session is None:
        _log.warning(
            "dlq_no_db tenant_id=%s trigger_id=%s failure=%s",
            tenant_id,
            trigger_id,
            failure_type,
        )
        return

    try:
        import json
        import uuid

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        now = datetime.now(UTC)
        async with sqlalchemy_rls_context(db_session, tenant_id):  # type: ignore[arg-type]
            await db_session.execute(  # type: ignore[attr-defined]
                text(
                    "INSERT INTO trigger_dlq "
                    "(id, tenant_id, trigger_id, failed_at, created_at, failure_type, "
                    " error_message, raw_payload, retry_count) "
                    "VALUES (:id, :tenant_id, :trigger_id, :failed_at, :created_at, "
                    "        :failure_type, :error_message, CAST(:raw_payload AS json), "
                    "        :retry_count)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "trigger_id": trigger_id,
                    # tz-aware UTC — matches the timestamptz columns (migrations
                    # 0129 and a1b2c3d4e5f6).
                    "failed_at": now,
                    "created_at": now,
                    "failure_type": failure_type,
                    "error_message": error_message[:2048],
                    # asyncpg binds a JSON column from a JSON string, not a raw dict.
                    "raw_payload": json.dumps(raw_payload),
                    "retry_count": retry_count,
                },
            )
            await db_session.commit()  # type: ignore[attr-defined]
    except Exception as exc:
        _log.error("dlq_write_failed tenant_id=%s error=%s", tenant_id, exc)
