"""Replay protection for vendor webhooks whose signature covers only the body.

GitHub, Jira, Confluence, Linear, Sentry, PagerDuty, Salesforce and Grafana sign
the request body and nothing else: no signed timestamp, no signed delivery id
(``X-GitHub-Delivery`` / ``X-Atlassian-Webhook-Identifier`` are plain headers an
attacker can change). A delivery is deduplicated on its signed body
(``signed-body:<sha256>``, DEF-5) through the dispatcher's durable gate — the
``trigger_events`` row — but that row is purged after ``DATA_RETENTION_DAYS``
(DEF-NEW-3). A captured delivery replayed after the purge verified again and
fired again. The acceptance window must therefore never outlive the dedup
record. Per vendor:

* **Jira** — the signed body carries the event time (``timestamp``, epoch ms).
  A delivery older than :func:`max_signed_age_seconds` (72 h, never more than
  the trigger-event retention) or more than :data:`FUTURE_SKEW_SECONDS` in the
  future is refused, so every accepted delivery is still covered by its
  ``trigger_events`` dedup row. Jira's own retries arrive within minutes.
* **GitHub and the other body-only vendors** (and a Jira body without a
  timestamp) — nothing signed says how old a delivery is, so the delivery stays
  acceptable as long as the signing secret does. The signed-body key of every
  delivery that ran is kept in ``vendor_webhook_replay_guard`` (one small row,
  not the payload) for as long as the trigger exists and the secret that signed
  it is still accepted; the retention job deletes rows of deleted triggers and
  rows signed before a completed secret rotation (they no longer verify).
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import HTTPException

# How old a vendor-signed timestamp may be (capped by the dedup retention).
SIGNED_MAX_AGE_SECONDS = 72 * 3600
# Clock skew tolerated for a signed timestamp in the future.
FUTURE_SKEW_SECONDS = 300
# rotate-secret keeps the previous secret valid for this long (app/api/triggers).
SECRET_GRACE_SECONDS = 300

# Vendors whose signature covers only the body (no signed timestamp / id).
BODY_SIGNED_VENDORS = frozenset(
    {"github", "jira", "confluence", "linear", "sentry", "pagerduty", "salesforce", "grafana"}
)


def _retention_seconds() -> float:
    try:
        days = int(os.getenv("DATA_RETENTION_DAYS", "90"))
    except ValueError:
        days = 90
    return float(max(days, 1) * 86400)


def max_signed_age_seconds() -> float:
    """Oldest accepted signed timestamp: never past the trigger_events retention,
    so the durable dedup row of an accepted delivery outlives its acceptance."""
    return min(float(SIGNED_MAX_AGE_SECONDS), _retention_seconds())


def signed_event_time(webhook_type: str, body: Any) -> float | None:
    """Epoch seconds of the event, read from the SIGNED body (None = not carried)."""
    if webhook_type != "jira" or not isinstance(body, dict):
        return None
    raw = body.get("timestamp")
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    # Jira sends epoch milliseconds; tolerate seconds.
    return value / 1000.0 if value > 1e11 else value


def check_signed_freshness(event_time: float, *, now: float | None = None) -> None:
    """401 unless the signed event time is inside the acceptance window."""
    current = time.time() if now is None else now
    if event_time > current + FUTURE_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="Webhook timestamp is in the future")
    max_age = max_signed_age_seconds()
    if current - event_time > max_age:
        raise HTTPException(
            status_code=401,
            detail=f"Stale webhook delivery (older than {int(max_age)} s): replay refused",
        )


def needs_durable_guard(webhook_type: str, body: Any) -> bool:
    """True for a body-signed vendor delivery that carries no signed event time."""
    return webhook_type in BODY_SIGNED_VENDORS and signed_event_time(webhook_type, body) is None


async def already_delivered(db: Any, tenant_id: str, trigger_id: str, key: str) -> bool:
    """Has a delivery with this signed key already run for this trigger?

    A read error raises: the caller answers 503 (the sender retries) rather than
    risk firing a replay."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM vendor_webhook_replay_guard "
                    "WHERE tenant_id = :t AND trigger_id = :tr AND signed_key = :k"
                ),
                {"t": tenant_id, "tr": trigger_id, "k": key[:200]},
            )
        ).first()
    return row is not None


async def record_delivered(db: Any, tenant_id: str, trigger_id: str, key: str) -> None:
    """Remember a delivery that ran (idempotent)."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        await session.execute(
            text(
                "INSERT INTO vendor_webhook_replay_guard (tenant_id, trigger_id, signed_key) "
                "VALUES (:t, :tr, :k) ON CONFLICT DO NOTHING"
            ),
            {"t": tenant_id, "tr": trigger_id, "k": key[:200]},
        )


# Retention (app.scaling.tasks): a guard row is dead once its trigger is gone,
# or once a secret rotation completed after it was recorded (the secret that
# signed it is no longer accepted, so the delivery cannot verify again).
def purge_sql(extra_filter: str = "") -> str:
    """Batched DELETE of dead guard rows; ``extra_filter`` ("AND ...", over the
    columns ``tenant_id`` / ``trigger_id``) narrows it (e.g. legal-hold exemption)."""
    return (
        "DELETE FROM vendor_webhook_replay_guard "
        "WHERE (tenant_id, trigger_id, signed_key) IN ("
        "SELECT tenant_id, trigger_id, signed_key FROM ("
        "SELECT g.tenant_id, g.trigger_id, g.signed_key FROM vendor_webhook_replay_guard g "
        "LEFT JOIN schedules s ON s.id = g.trigger_id AND s.tenant_id = g.tenant_id "
        "WHERE s.id IS NULL OR (s.webhook_secret_grace_until IS NOT NULL "
        " AND s.webhook_secret_grace_until < NOW() "
        " AND g.first_seen_at < s.webhook_secret_grace_until "
        f"   - make_interval(secs => {SECRET_GRACE_SECONDS}))"
        f") dead WHERE TRUE{extra_filter} LIMIT :lim)"
    )
