"""Durable audit → SIEM forwarding (AUDIT-06).

Every ``AuditLog`` write — API replica or Celery worker — inserts an
``audit_siem_outbox`` row in the same transaction as its ``audit_log`` row
(see ``AuditLog._db_record``). :func:`drain_siem_outbox`, run by one beat task
(``app.scaling.tasks.forward_siem_outbox``), ships due rows in batches:

* rows are claimed with ``FOR UPDATE SKIP LOCKED`` so concurrent drainers never
  send the same row twice;
* a batch the SIEM accepted is deleted;
* a failed batch stays: ``attempts`` +1 and ``next_attempt_at`` pushed back
  exponentially; at ``max_attempts`` a row is parked as ``dead`` (the DLQ) —
  never silently dropped.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_ATTEMPTS = 10
_MAX_BACKOFF_SECONDS = 3600


def siem_outbox_enabled() -> bool:
    """True when a SIEM is configured: only then do audit writes enqueue rows."""
    if os.getenv("SIEM_TYPE"):
        return True
    try:
        from app.core.config import get_settings

        return bool(get_settings().siem_type)
    except Exception:
        return False


def siem_config_from_settings() -> Any:
    """The configured :class:`SIEMConfig` (``None`` when no SIEM is configured)."""
    from app.core.config import get_settings
    from app.governance.siem_adapters import SIEMConfig, SIEMType

    settings = get_settings()
    siem_type = os.getenv("SIEM_TYPE") or settings.siem_type
    if not siem_type:
        return None
    token = os.getenv("SIEM_TOKEN") or settings.siem_token
    return SIEMConfig(
        siem_type=SIEMType(siem_type),
        endpoint=os.getenv("SIEM_ENDPOINT") or settings.siem_endpoint,
        api_key=os.getenv("SIEM_API_KEY") or token or settings.siem_api_key,
        extra={"token": token},
    )


async def drain_siem_outbox(
    system_db: Any,
    adapter: Any,
    config: Any,
    *,
    batch_size: int = 500,
    max_batches: int = 20,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, int]:
    """Forward due outbox rows; returns ``{"sent", "failed", "dead"}`` counts.

    *system_db* must be the cross-tenant (BYPASSRLS maintenance) session factory.
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    sent = failed = dead = 0
    ok = True
    for _ in range(max_batches):
        async with system_db() as session, session.begin():  # noqa: SIM117
            async with system_session(session):
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, payload, attempts FROM audit_siem_outbox "
                            "WHERE status = 'pending' AND next_attempt_at <= now() "
                            "ORDER BY next_attempt_at, id LIMIT :lim "
                            "FOR UPDATE SKIP LOCKED"
                        ),
                        {"lim": batch_size},
                    )
                ).all()
                if not rows:
                    break
                ids = [int(r[0]) for r in rows]
                events = [r[1] if isinstance(r[1], dict) else json.loads(r[1]) for r in rows]
                error = ""
                try:
                    ok = bool(await adapter.send(events, config))
                    if not ok:
                        error = "rejected by SIEM"
                except Exception as exc:
                    ok = False
                    error = f"{type(exc).__name__}: {exc}"[:500]
                if ok:
                    await session.execute(
                        text("DELETE FROM audit_siem_outbox WHERE id = ANY(:ids)"), {"ids": ids}
                    )
                    sent += len(ids)
                    continue
                result = await session.execute(
                    text(
                        "UPDATE audit_siem_outbox SET attempts = attempts + 1, "
                        "last_error = :err, "
                        "status = CASE WHEN attempts + 1 >= :max THEN 'dead' ELSE 'pending' END, "
                        "next_attempt_at = now() + make_interval(secs => "
                        "LEAST(:cap, 5 * power(2, attempts))) "
                        "WHERE id = ANY(:ids) RETURNING status"
                    ),
                    {"ids": ids, "err": error, "max": max_attempts, "cap": _MAX_BACKOFF_SECONDS},
                )
                statuses = [r[0] for r in result.all()]
                failed += len(ids)
                dead += sum(1 for s in statuses if s == "dead")
                logger.warning("siem_outbox_send_failed", count=len(ids), error=error, dead=dead)
        if not ok:
            break  # the SIEM is failing: wait for the next run (backoff applies)
    if sent or failed:
        logger.info("siem_outbox_drained", sent=sent, failed=failed, dead=dead)
    return {"sent": sent, "failed": failed, "dead": dead}
