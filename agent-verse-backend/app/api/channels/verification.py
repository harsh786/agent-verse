"""Channel ownership proof for ``channel_tenant_mappings`` (TRG-03).

Claiming a channel used to be first-come: any tenant could register another
tenant's Slack workspace, Discord guild, Teams organisation, number or address
and receive its inbound events (and its Slack HITL clicks). Proof now comes from
the channel itself — no external app registrations:

1. ``claim_channel`` creates the mapping ``pending_verification`` and returns a
   short one-time code (``AV-XXXXXXXX``, 40 random bits). Only a SHA-256 hash of
   the code, bound to the channel, is stored, with a 24h expiry.
2. A pending mapping routes nothing (see ``ROUTABLE_STATUSES``).
3. When an authenticated inbound message on that channel carries the code,
   ``verify_from_inbound`` flips the mapping to ``verified``. Rival unproven
   claims on the same channel (other tenants' pending or legacy mappings) are
   dropped, since the channel itself has just vouched for one tenant.
4. A channel verified by one tenant cannot be claimed by another (409).

Mappings that pre-date this are ``legacy_unverified``: they keep routing and can
be verified with a code issued by ``reissue_code``.

The DB access splits like the rest of the channel module: the tenant's own rows
are written on the tenant-scoped (RLS) factory; the cross-tenant checks — is the
channel verified by someone else, which claim does this inbound code belong to —
run on the maintenance (BYPASSRLS) factory, as the inbound tenant lookup does.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import secrets
import uuid
from dataclasses import asdict, dataclass
from typing import Any

_log = logging.getLogger(__name__)

STATUS_PENDING = "pending_verification"
STATUS_VERIFIED = "verified"
STATUS_LEGACY = "legacy_unverified"
ALL_STATUSES = (STATUS_PENDING, STATUS_VERIFIED, STATUS_LEGACY)
# Only these route inbound events to the tenant. A pending claim never does.
ROUTABLE_STATUSES = (STATUS_VERIFIED, STATUS_LEGACY)

CODE_TTL = datetime.timedelta(hours=24)
MAX_CODES_PER_MESSAGE = 5
# Crockford-ish alphabet without the look-alikes 0/O and 1/I.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8
_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])AV-([A-HJ-NP-Z2-9]{8})(?![A-Za-z0-9])", re.IGNORECASE
)


class ChannelClaimedError(Exception):
    """The channel is already verified by another tenant."""

    def __init__(self, channel_type: str, channel_id: str) -> None:
        super().__init__(f"{channel_type}:{channel_id} is verified by another tenant")
        self.channel_type = channel_type
        self.channel_id = channel_id


class MappingNotFoundError(Exception):
    """No such mapping for this tenant."""


@dataclass(frozen=True)
class IssuedMapping:
    id: str
    channel_type: str
    channel_id: str
    status: str
    verification_code: str | None
    verification_expires_at: str | None

    def to_response(self) -> dict[str, Any]:
        body = asdict(self)
        body["instructions"] = instructions(self)
        return body


def instructions(mapping: IssuedMapping) -> str:
    if mapping.status == STATUS_VERIFIED or not mapping.verification_code:
        return "This channel is verified."
    return (
        f"Send the code {mapping.verification_code} as a message on this "
        f"{mapping.channel_type} channel (for example, post it where the AgentVerse app "
        f"is installed). The code expires at {mapping.verification_expires_at}. "
        + (
            "The mapping keeps routing until then."
            if mapping.status == STATUS_LEGACY
            else "Inbound events are not routed until the channel is verified."
        )
    )


# ── code helpers ──────────────────────────────────────────────────────────


def generate_code() -> str:
    return "AV-" + "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def hash_code(channel_type: str, channel_id: str, code: str) -> str:
    """Channel-bound SHA-256 of a code: a code only proves the channel it was issued for."""
    material = f"{channel_type}\x00{channel_id}\x00{code.strip().upper()}"
    return hashlib.sha256(material.encode()).hexdigest()


def extract_codes(payload: Any) -> list[str]:
    """Distinct candidate codes anywhere in an inbound payload (bounded)."""
    try:
        haystack = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return []
    found: list[str] = []
    for match in _CODE_RE.finditer(haystack):
        code = "AV-" + match.group(1).upper()
        if code not in found:
            found.append(code)
            if len(found) >= MAX_CODES_PER_MESSAGE:
                break
    return found


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# ── tenant actions ────────────────────────────────────────────────────────


async def _verified_by_other(
    system_db: Any, tenant_id: str, channel_type: str, channel_id: str
) -> bool:
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as session, session.begin(), system_session(session):
        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM channel_tenant_mappings WHERE channel_type = :ct "
                    "AND channel_id = :ci AND status = :verified AND tenant_id <> :tid LIMIT 1"
                ),
                {"ct": channel_type, "ci": channel_id, "verified": STATUS_VERIFIED,
                 "tid": tenant_id},
            )
        ).fetchone()
    return row is not None


async def _issue(
    session: Any, mapping_id: str, channel_type: str, channel_id: str
) -> tuple[str, datetime.datetime]:
    from sqlalchemy import text

    code = generate_code()
    expires = _now() + CODE_TTL
    await session.execute(
        text(
            "UPDATE channel_tenant_mappings SET verification_code_hash = :h, "
            "verification_expires_at = :exp, updated_at = now() WHERE id = :id"
        ),
        {"h": hash_code(channel_type, channel_id, code), "exp": expires, "id": mapping_id},
    )
    return code, expires


async def claim_channel(
    *,
    tenant_db: Any,
    system_db: Any,
    tenant_id: str,
    channel_type: str,
    channel_id: str,
) -> IssuedMapping:
    """Create (or re-issue a code for) this tenant's claim on a channel.

    Raises :class:`ChannelClaimedError` when another tenant has verified it.
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    if system_db is None:
        raise RuntimeError("channel ownership check needs the maintenance DB factory")
    if await _verified_by_other(system_db, tenant_id, channel_type, channel_id):
        raise ChannelClaimedError(channel_type, channel_id)

    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        existing = (
            await session.execute(
                text(
                    "SELECT id, status, verified_at FROM channel_tenant_mappings "
                    "WHERE tenant_id = :tid AND channel_type = :ct AND channel_id = :ci "
                    "FOR UPDATE"
                ),
                {"tid": tenant_id, "ct": channel_type, "ci": channel_id},
            )
        ).fetchone()
        if existing is not None:
            mapping_id, status = str(existing[0]), str(existing[1])
            if status == STATUS_VERIFIED:
                return IssuedMapping(mapping_id, channel_type, channel_id, status, None, None)
        else:
            mapping_id, status = uuid.uuid4().hex, STATUS_PENDING
            inserted = (
                await session.execute(
                    text(
                        "INSERT INTO channel_tenant_mappings "
                        "(id, tenant_id, channel_type, channel_id, status) "
                        "VALUES (:id, :tid, :ct, :ci, :status) "
                        "ON CONFLICT DO NOTHING RETURNING id"
                    ),
                    {"id": mapping_id, "tid": tenant_id, "ct": channel_type,
                     "ci": channel_id, "status": STATUS_PENDING},
                )
            ).fetchone()
            if inserted is None:
                # A concurrent claim by this same tenant won the insert.
                raise ChannelClaimedError(channel_type, channel_id)
        code, expires = await _issue(session, mapping_id, channel_type, channel_id)
    return IssuedMapping(mapping_id, channel_type, channel_id, status, code, expires.isoformat())


async def reissue_code(
    *, tenant_db: Any, system_db: Any, tenant_id: str, mapping_id: str
) -> IssuedMapping:
    """The UI's "verify" action: a fresh code for a pending or legacy mapping."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT channel_type, channel_id, status FROM channel_tenant_mappings "
                    "WHERE id = :id AND tenant_id = :tid FOR UPDATE"
                ),
                {"id": mapping_id, "tid": tenant_id},
            )
        ).fetchone()
        if row is None:
            raise MappingNotFoundError(mapping_id)
        channel_type, channel_id, status = str(row[0]), str(row[1]), str(row[2])
        if status == STATUS_VERIFIED:
            return IssuedMapping(mapping_id, channel_type, channel_id, status, None, None)
        if (
            status == STATUS_PENDING
            and system_db is not None
            and await _verified_by_other(system_db, tenant_id, channel_type, channel_id)
        ):
            raise ChannelClaimedError(channel_type, channel_id)
        code, expires = await _issue(session, mapping_id, channel_type, channel_id)
    return IssuedMapping(mapping_id, channel_type, channel_id, status, code, expires.isoformat())


# ── inbound proof ─────────────────────────────────────────────────────────


async def verify_from_inbound(
    system_db: Any, channel_type: str, channel_id: str, payload: Any
) -> str | None:
    """Verify the claim whose code this (already authenticated) inbound message
    carries on ``(channel_type, channel_id)``. Returns the verified tenant, or
    None when the message proves nothing (no code, wrong / expired code, another
    channel's code). Never raises: a failure here just leaves the claim pending.
    """
    if system_db is None or not channel_id:
        return None
    codes = extract_codes(payload)
    if not codes:
        return None
    try:
        from sqlalchemy import text

        from app.db.rls import system_session

        async with system_db() as session, session.begin(), system_session(session):
            claim = None
            for code in codes:
                claim = (
                    await session.execute(
                        text(
                            "SELECT id, tenant_id FROM channel_tenant_mappings "
                            "WHERE channel_type = :ct AND channel_id = :ci "
                            "AND status IN (:pending, :legacy) "
                            "AND verification_code_hash = :h "
                            "AND verification_expires_at > now() FOR UPDATE"
                        ),
                        {"ct": channel_type, "ci": channel_id, "pending": STATUS_PENDING,
                         "legacy": STATUS_LEGACY, "h": hash_code(channel_type, channel_id, code)},
                    )
                ).fetchone()
                if claim is not None:
                    break
            if claim is None:
                return None
            mapping_id, tenant_id = str(claim[0]), str(claim[1])
            params = {"ct": channel_type, "ci": channel_id, "tid": tenant_id}
            rival = (
                await session.execute(
                    text(
                        "SELECT tenant_id FROM channel_tenant_mappings "
                        "WHERE channel_type = :ct AND channel_id = :ci AND tenant_id <> :tid "
                        "AND status = :verified FOR UPDATE"
                    ),
                    {**params, "verified": STATUS_VERIFIED},
                )
            ).fetchone()
            if rival is not None:
                _log.warning(
                    "channel_verification_refused_already_verified channel=%s id=%s",
                    channel_type, channel_id,
                )
                return None
            dropped = await session.execute(
                text(
                    "DELETE FROM channel_tenant_mappings WHERE channel_type = :ct "
                    "AND channel_id = :ci AND tenant_id <> :tid RETURNING tenant_id, status"
                ),
                params,
            )
            for row in dropped.fetchall():
                _log.info(
                    "channel_claim_superseded channel=%s id=%s tenant=%s status=%s",
                    channel_type, channel_id, row[0], row[1],
                )
            await session.execute(
                text(
                    "UPDATE channel_tenant_mappings SET status = :verified, "
                    "verified_at = now(), verification_code_hash = NULL, "
                    "verification_expires_at = NULL, updated_at = now() WHERE id = :id"
                ),
                {"verified": STATUS_VERIFIED, "id": mapping_id},
            )
        _log.info(
            "channel_mapping_verified channel=%s id=%s tenant=%s",
            channel_type, channel_id, tenant_id,
        )
        return tenant_id
    except Exception as exc:
        _log.warning("channel_verification_failed channel=%s: %s", channel_type, exc)
        return None
