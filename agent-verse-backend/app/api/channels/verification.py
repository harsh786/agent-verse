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
   ``verify_from_inbound`` flips the mapping to ``verified``. Rival pending claims
   on the same channel are dropped; a rival legacy mapping is kept as
   ``superseded`` (no routing) and recorded on its tenant's audit trail.
4. A channel verified by one tenant cannot be claimed by another (409).

Mappings that pre-date this are ``legacy_unverified``: they keep routing and can
be verified with a code issued by ``reissue_code``.

On channels anyone can SEND to (``OPERATOR_APPROVAL_CHANNELS``: sms, email,
form, meeting, voice) a code proves nothing, so steps 1-3 do not apply: a claim
is ``pending_operator_approval`` (no code, no routing) until a platform operator
approves or rejects it (``operator_decide``, audited); legacy mappings on those
channels keep routing and are listed for operator review.

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
# Claim on a channel where a code proves nothing; a platform operator decides.
STATUS_PENDING_OPERATOR = "pending_operator_approval"
# A legacy mapping displaced by another tenant's proof; kept for audit.
STATUS_SUPERSEDED = "superseded"
# Refused by a platform operator.
STATUS_REJECTED = "rejected"
ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_VERIFIED,
    STATUS_LEGACY,
    STATUS_PENDING_OPERATOR,
    STATUS_SUPERSEDED,
    STATUS_REJECTED,
)
# Only these route inbound events to the tenant. A pending claim never does.
ROUTABLE_STATUSES = (STATUS_VERIFIED, STATUS_LEGACY)
# What an operator may decide on (the review queue).
OPERATOR_REVIEWABLE_STATUSES = (STATUS_PENDING_OPERATOR, STATUS_LEGACY)

# Channels whose id anyone can SEND to — a phone number, an email address, a
# public form, a meeting account (participants put text in the transcript). A
# code arriving there proves only that someone could send it, not that they own
# the channel, so these are never verified by code: a platform operator approves
# or rejects the claim. (Slack / Teams / Discord deliver only from the workspace,
# organisation or guild where the AgentVerse app is installed.)
OPERATOR_APPROVAL_CHANNELS = frozenset({"sms", "email", "form", "meeting", "voice"})


def requires_operator_approval(channel_type: str) -> bool:
    return str(channel_type or "").strip().lower() in OPERATOR_APPROVAL_CHANNELS

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


class OperatorApprovalRequiredError(Exception):
    """The channel cannot be verified by code; a platform operator decides."""

    def __init__(self, channel_type: str) -> None:
        super().__init__(
            f"A {channel_type} channel cannot be verified with a code (a code only proves "
            "someone can send to it); it is awaiting operator approval"
        )
        self.channel_type = channel_type


class NotReviewableError(Exception):
    """The mapping is not awaiting an operator decision."""

    def __init__(self, status: str) -> None:
        super().__init__(f"mapping is {status}, not awaiting operator review")
        self.status = status


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
    if mapping.status == STATUS_VERIFIED:
        return "This channel is verified."
    if mapping.status == STATUS_PENDING_OPERATOR:
        return (
            f"Awaiting operator approval. A code sent to a {mapping.channel_type} channel "
            "only proves someone can send to it, not that they own it, so a platform "
            "operator reviews this claim. Inbound events are not routed until it is approved."
        )
    if mapping.status == STATUS_SUPERSEDED:
        return (
            "Superseded: another organization proved ownership of this channel. The "
            "mapping is kept for the record and no longer routes."
        )
    if mapping.status == STATUS_REJECTED:
        return "Rejected by a platform operator. Inbound events are not routed."
    if mapping.status == STATUS_LEGACY and requires_operator_approval(mapping.channel_type):
        return (
            "Connected before ownership checks existed. It keeps routing and is listed "
            "for operator review."
        )
    if not mapping.verification_code:
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
        operator_only = requires_operator_approval(channel_type)
        initial = STATUS_PENDING_OPERATOR if operator_only else STATUS_PENDING
        if existing is not None:
            mapping_id, status = str(existing[0]), str(existing[1])
            if status == STATUS_VERIFIED or (operator_only and status == STATUS_LEGACY):
                # Verified, or a legacy send-only mapping (routing, in the
                # operator's review queue): nothing to issue.
                return IssuedMapping(mapping_id, channel_type, channel_id, status, None, None)
            if status in (STATUS_SUPERSEDED, STATUS_REJECTED) or (
                operator_only and status != initial
            ):
                # A fresh claim re-opens a displaced / refused mapping (the
                # channel is not verified by anyone else — checked above).
                status = initial
                await session.execute(
                    text(
                        "UPDATE channel_tenant_mappings SET status = :s, "
                        "verification_code_hash = NULL, verification_expires_at = NULL, "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"s": status, "id": mapping_id},
                )
        else:
            mapping_id, status = uuid.uuid4().hex, initial
            inserted = (
                await session.execute(
                    text(
                        "INSERT INTO channel_tenant_mappings "
                        "(id, tenant_id, channel_type, channel_id, status) "
                        "VALUES (:id, :tid, :ct, :ci, :status) "
                        "ON CONFLICT DO NOTHING RETURNING id"
                    ),
                    {"id": mapping_id, "tid": tenant_id, "ct": channel_type,
                     "ci": channel_id, "status": status},
                )
            ).fetchone()
            if inserted is None:
                # A concurrent claim by this same tenant won the insert.
                raise ChannelClaimedError(channel_type, channel_id)
        if operator_only:
            # No code: receiving one here would prove nothing.
            return IssuedMapping(mapping_id, channel_type, channel_id, status, None, None)
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
        if requires_operator_approval(channel_type):
            raise OperatorApprovalRequiredError(channel_type)
        if status in (STATUS_PENDING, STATUS_SUPERSEDED, STATUS_REJECTED):
            if system_db is None:
                raise RuntimeError("channel ownership check needs the maintenance DB factory")
            if await _verified_by_other(system_db, tenant_id, channel_type, channel_id):
                raise ChannelClaimedError(channel_type, channel_id)
        if status in (STATUS_SUPERSEDED, STATUS_REJECTED):
            # Re-opened as a fresh claim; it routes nothing until proven.
            status = STATUS_PENDING
            await session.execute(
                text(
                    "UPDATE channel_tenant_mappings SET status = :s, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"s": status, "id": mapping_id},
            )
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
    if system_db is None or not channel_id or requires_operator_approval(channel_type):
        # On a send-only channel a code proves nothing: never verify by code.
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
            await _displace_rivals(
                session, channel_type, channel_id, tenant_id, by="ownership code"
            )
            await _mark_verified(session, mapping_id)
        _log.info(
            "channel_mapping_verified channel=%s id=%s tenant=%s",
            channel_type, channel_id, tenant_id,
        )
        return tenant_id
    except Exception as exc:
        _log.warning("channel_verification_failed channel=%s: %s", channel_type, exc)
        return None


# ── shared state transitions (run inside the caller's system session) ──────


async def _audit(
    session: Any,
    *,
    tenant_id: str,
    tool_name: str,
    outcome: str,
    approver: str,
    note: str,
) -> None:
    """Append an audit_log row in the SAME transaction as the state change, so a
    decision is never applied without its record (and vice versa)."""
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO audit_log (id, tenant_id, goal_id, tool_name, action_level, "
            "outcome, step_id, approver, note) VALUES "
            "(:id, :tid, '', :tool, 'approval', :outcome, '', :approver, :note)"
        ),
        {"id": uuid.uuid4().hex, "tid": tenant_id, "tool": tool_name,
         "outcome": outcome, "approver": approver[:200], "note": note},
    )


async def _displace_rivals(
    session: Any, channel_type: str, channel_id: str, owner_tenant: str, *, by: str
) -> None:
    """Another tenant now owns the channel. Rival pending claims never routed and
    are dropped; a rival LEGACY mapping (which did route) is kept for audit as
    ``superseded`` — no routing — and the displacement is recorded on its
    tenant's audit trail."""
    from sqlalchemy import text

    params = {"ct": channel_type, "ci": channel_id, "tid": owner_tenant}
    superseded = await session.execute(
        text(
            "UPDATE channel_tenant_mappings SET status = :superseded, "
            "verification_code_hash = NULL, verification_expires_at = NULL, "
            "updated_at = now() WHERE channel_type = :ct AND channel_id = :ci "
            "AND tenant_id <> :tid AND status = :legacy RETURNING tenant_id"
        ),
        {**params, "superseded": STATUS_SUPERSEDED, "legacy": STATUS_LEGACY},
    )
    for (rival_tenant,) in superseded.fetchall():
        _log.warning(
            "channel_mapping_superseded channel=%s id=%s tenant=%s",
            channel_type, channel_id, rival_tenant,
        )
        await _audit(
            session,
            tenant_id=str(rival_tenant),
            tool_name="channel_mapping.superseded",
            outcome="superseded",
            approver="platform",
            note=(
                f"{channel_type}:{channel_id} was verified by another organization "
                f"({by}); this legacy mapping no longer routes"
            ),
        )
    dropped = await session.execute(
        text(
            "DELETE FROM channel_tenant_mappings WHERE channel_type = :ct "
            "AND channel_id = :ci AND tenant_id <> :tid AND status IN (:p1, :p2) "
            "RETURNING tenant_id, status"
        ),
        {**params, "p1": STATUS_PENDING, "p2": STATUS_PENDING_OPERATOR},
    )
    for row in dropped.fetchall():
        _log.info(
            "channel_claim_dropped channel=%s id=%s tenant=%s status=%s",
            channel_type, channel_id, row[0], row[1],
        )


async def _mark_verified(session: Any, mapping_id: str) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "UPDATE channel_tenant_mappings SET status = :verified, "
            "verified_at = now(), verification_code_hash = NULL, "
            "verification_expires_at = NULL, updated_at = now() WHERE id = :id"
        ),
        {"verified": STATUS_VERIFIED, "id": mapping_id},
    )


# ── platform operator review (X-Admin-Key, app/api/admin.py) ───────────────


_REVIEW_LIMIT = 500


async def list_operator_review(system_db: Any) -> list[dict[str, Any]]:
    """Claims awaiting an operator (every ``pending_operator_approval``) plus the
    legacy send-only mappings that still route unproven — cross-tenant."""
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as session, session.begin(), system_session(session):
        rows = await session.execute(
            text(
                "SELECT id, tenant_id, channel_type, channel_id, status, created_at "
                "FROM channel_tenant_mappings WHERE status = :pending "
                "OR (status = :legacy AND lower(channel_type) IN :send_only) "
                "ORDER BY created_at LIMIT :lim"
            ).bindparams(_expanding("send_only")),
            {"pending": STATUS_PENDING_OPERATOR, "legacy": STATUS_LEGACY,
             "send_only": sorted(OPERATOR_APPROVAL_CHANNELS), "lim": _REVIEW_LIMIT},
        )
        out = []
        for r in rows.mappings():
            item = dict(r)
            created = item.get("created_at")
            if hasattr(created, "isoformat"):
                item["created_at"] = created.isoformat()
            out.append(item)
        return out


def _expanding(name: str) -> Any:
    from sqlalchemy import bindparam

    return bindparam(name, expanding=True)


async def operator_decide(
    system_db: Any, mapping_id: str, *, approve: bool, operator: str, reason: str
) -> IssuedMapping:
    """Approve (→ ``verified``, routes) or reject (→ ``rejected``, no routing) a
    mapping in the review queue, with an audit record in the same transaction.

    Raises :class:`MappingNotFoundError`, :class:`NotReviewableError`, or
    :class:`ChannelClaimedError` (approving a channel another tenant verified).
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    actor = f"platform_admin:{operator.strip() or 'unnamed'}"
    async with system_db() as session, session.begin(), system_session(session):
        row = (
            await session.execute(
                text(
                    "SELECT tenant_id, channel_type, channel_id, status "
                    "FROM channel_tenant_mappings WHERE id = :id FOR UPDATE"
                ),
                {"id": mapping_id},
            )
        ).fetchone()
        if row is None:
            raise MappingNotFoundError(mapping_id)
        tenant_id, channel_type, channel_id, status = (str(v) for v in row)
        if status not in OPERATOR_REVIEWABLE_STATUSES:
            raise NotReviewableError(status)
        note = f"{channel_type}:{channel_id} (was {status})" + (
            f": {reason.strip()}" if reason.strip() else ""
        )
        if approve:
            rival = (
                await session.execute(
                    text(
                        "SELECT 1 FROM channel_tenant_mappings WHERE channel_type = :ct "
                        "AND channel_id = :ci AND tenant_id <> :tid AND status = :verified "
                        "FOR UPDATE"
                    ),
                    {"ct": channel_type, "ci": channel_id, "tid": tenant_id,
                     "verified": STATUS_VERIFIED},
                )
            ).fetchone()
            if rival is not None:
                raise ChannelClaimedError(channel_type, channel_id)
            await _displace_rivals(
                session, channel_type, channel_id, tenant_id, by="operator approval"
            )
            await _mark_verified(session, mapping_id)
            new_status, tool, outcome = STATUS_VERIFIED, "operator_approved", "approved"
        else:
            await session.execute(
                text(
                    "UPDATE channel_tenant_mappings SET status = :rejected, "
                    "verification_code_hash = NULL, verification_expires_at = NULL, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"rejected": STATUS_REJECTED, "id": mapping_id},
            )
            new_status, tool, outcome = STATUS_REJECTED, "operator_rejected", "rejected"
        await _audit(
            session,
            tenant_id=tenant_id,
            tool_name=f"channel_mapping.{tool}",
            outcome=outcome,
            approver=actor,
            note=note,
        )
    _log.info(
        "channel_mapping_operator_decision channel=%s id=%s tenant=%s decision=%s by=%s",
        channel_type, channel_id, tenant_id, outcome, actor,
    )
    return IssuedMapping(mapping_id, channel_type, channel_id, new_status, None, None)
