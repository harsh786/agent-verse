"""Replay guard for HMAC-signed workflow webhooks (WF-REPLAY-1).

``POST /wf-hooks/{token}`` with ``auth: hmac`` used the sender's delivery-id
header (``Idempotency-Key``, ``X-Delivery-Id``, ...) as the run's identity. That
header is not signed: a captured signed delivery replayed under a fresh header
started another run, and without a header nothing was deduplicated at all, so a
captured delivery could be replayed indefinitely.

The identity of a verified delivery now comes only from what was signed (the
rule of the trigger webhooks, B2-GAP-1):

* **Legacy body-only signature** (no ``X-Webhook-Timestamp``) —
  ``signed-body:<sha256(body)>``, unwindowed: nothing signed says how old the
  delivery is, so the same signed body is the same delivery for as long as the
  secret that signed it is accepted. It is the run's idempotency key too. A
  sender whose events can repeat a byte-identical body must sign with
  ``X-Webhook-Timestamp``.
* **Timestamped signature** — ``signed-ts:<sha256("{ts}.{body}")>``, the exact
  signed bytes (a replay inside the 300 s window under a fresh delivery id is the
  same delivery). The delivery id stays the run's identity when present (a
  sender retry re-signed with a new timestamp is still one run).

The key of every delivery that was accepted (a run started, or queued for the
dead-letter retry) is kept in ``workflow_webhook_replay_guard`` with a
fingerprint of the secret that signed it (``secret_ref``, an HMAC under the
secret — never the secret). A guard read error refuses the delivery with a
retryable 503 rather than risk running a replay. A throttled / failed delivery is
not recorded, so the sender's retry still runs.

Retention: a row is dead once its workflow row is gone, once the workflow's
secret changed (rows of another ``secret_ref`` are deleted when the first
delivery under the new secret is recorded; until then they can never match), or —
for ``signed-ts:`` rows — once the replay window has passed. Archived workflows
keep their rows (they can be published again with the same secret).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from app.triggers.webhooks.ingress import REPLAY_WINDOW_SECONDS, TIMESTAMP_HEADERS

_TABLE = "workflow_webhook_replay_guard"
_REF_LABEL = b"agentverse:workflow-webhook-replay-guard:v1"
SIGNED_TS_PREFIX = "signed-ts:"
SIGNED_BODY_PREFIX = "signed-body:"
# A timestamped delivery only verifies inside the replay window; the margin
# covers clock skew between the API replicas and the database.
TIMESTAMPED_GUARD_TTL_SECONDS = 2 * REPLAY_WINDOW_SECONDS + 600


def signed_timestamp(headers: Mapping[str, str]) -> str:
    """The signed-timestamp header value ("" = legacy body-only signature)."""
    for name in TIMESTAMP_HEADERS:
        value = (headers.get(name) or "").strip()
        if value:
            return value
    return ""


def signed_replay_key(headers: Mapping[str, str], body: bytes) -> str:
    """Replay identity of a delivery whose signature was verified (signed bytes only)."""
    timestamp = signed_timestamp(headers)
    if timestamp:
        digest = hashlib.sha256(f"{timestamp}.".encode() + body).hexdigest()[:40]
        return f"{SIGNED_TS_PREFIX}{digest}"
    return f"{SIGNED_BODY_PREFIX}{hashlib.sha256(body).hexdigest()[:40]}"


def run_identity(signed_key: str, delivery_key: str | None) -> str:
    """The run's idempotency key for a signed delivery.

    Body-only: the signed body (the unsigned delivery id is ignored). Timestamped:
    the delivery id when the sender sent one (its retries re-sign), else the
    signed bytes.
    """
    if signed_key.startswith(SIGNED_TS_PREFIX) and delivery_key:
        return delivery_key
    return signed_key


def secret_ref(secret: str) -> str:
    """Fingerprint of the signing secret (HMAC under the secret, not reversible)."""
    return hmac.new(secret.encode(), _REF_LABEL, hashlib.sha256).hexdigest()[:32]


async def already_accepted(
    db: Any, tenant_id: str, workflow_id: str, key: str, ref: str
) -> bool:
    """Was a delivery with this signed key, signed by this secret, already accepted?

    A read error raises (the caller answers 503)."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(
                    f"SELECT 1 FROM {_TABLE} WHERE tenant_id = :t AND workflow_id = :w "
                    "AND signed_key = :k AND secret_ref = :r"
                ),
                {"t": tenant_id, "w": workflow_id, "k": key[:200], "r": ref},
            )
        ).first()
    return row is not None


async def record_accepted(db: Any, tenant_id: str, workflow_id: str, key: str, ref: str) -> None:
    """Remember an accepted delivery; drop the rows of a secret no longer in use."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    params = {"t": tenant_id, "w": workflow_id, "k": key[:200], "r": ref}
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        await session.execute(
            text(
                f"DELETE FROM {_TABLE} WHERE tenant_id = :t AND workflow_id = :w "
                "AND secret_ref <> :r"
            ),
            params,
        )
        await session.execute(
            text(
                f"INSERT INTO {_TABLE} (tenant_id, workflow_id, signed_key, secret_ref) "
                "VALUES (:t, :w, :k, :r) ON CONFLICT (tenant_id, workflow_id, signed_key) "
                "DO UPDATE SET secret_ref = EXCLUDED.secret_ref, first_seen_at = now()"
            ),
            params,
        )


def purge_sql(extra_filter: str = "") -> str:
    """Batched DELETE of dead guard rows for the retention job; ``extra_filter``
    ("AND ...", over ``tenant_id``) narrows it (legal-hold exemption)."""
    return (
        f"DELETE FROM {_TABLE} "
        "WHERE (tenant_id, workflow_id, signed_key) IN ("
        "SELECT tenant_id, workflow_id, signed_key FROM ("
        f"SELECT g.tenant_id, g.workflow_id, g.signed_key FROM {_TABLE} g "
        "LEFT JOIN workflows w ON w.id = g.workflow_id AND w.tenant_id = g.tenant_id "
        "WHERE w.id IS NULL "
        f"OR (left(g.signed_key, {len(SIGNED_TS_PREFIX)}) = '{SIGNED_TS_PREFIX}' "
        f" AND g.first_seen_at < NOW() - make_interval(secs => {TIMESTAMPED_GUARD_TTL_SECONDS}))"
        f") dead WHERE TRUE{extra_filter} LIMIT :lim)"
    )
