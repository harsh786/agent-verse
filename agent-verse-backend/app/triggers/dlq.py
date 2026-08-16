"""Dead Letter Queue (DLQ) helpers for trigger firings."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

_log = logging.getLogger(__name__)

FAILURE_TYPES = frozenset([
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
])

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
    """Write a failed trigger firing to the trigger_dlq table."""
    if db_session is None:
        _log.warning(
            "dlq_no_db tenant_id=%s trigger_id=%s failure=%s",
            tenant_id, trigger_id, failure_type,
        )
        return

    try:
        import uuid

        from sqlalchemy import text
        await db_session.execute(
            text(
                "INSERT INTO trigger_dlq "
                "(id, tenant_id, trigger_id, failed_at, failure_type, error_message, "
                " raw_payload, retry_count) "
                "VALUES (:id, :tenant_id, :trigger_id, :failed_at, :failure_type, "
                "        :error_message, :raw_payload, :retry_count)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "trigger_id": trigger_id,
                "failed_at": datetime.now(UTC),
                "failure_type": failure_type,
                "error_message": error_message[:2048],
                "raw_payload": raw_payload,
                "retry_count": retry_count,
            },
        )
        await db_session.commit()
    except Exception as exc:
        _log.error("dlq_write_failed tenant_id=%s error=%s", tenant_id, exc)
