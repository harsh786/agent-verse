"""Slack user -> AgentVerse principal links (TRG-36).

Slack requests are authenticated as coming from Slack (signing secret) and the
workspace is bound to a tenant (verified ``channel_tenant_mappings``), but that
says nothing about WHO in the workspace clicked "Approve" or typed
``/agentverse``. Every workspace member used to be able to approve HITL requests
and submit goals.

A Slack user acts only through a linked AgentVerse principal:

1. An authenticated AgentVerse user (API key) asks for a one-time link code
   (``POST /channels/identities/link-codes``).
2. In Slack they run ``/agentverse link <code>`` from a workspace bound to the
   same tenant; the signed request proves the Slack identity, the code proves
   the AgentVerse identity, and the pair becomes an ``active`` link.
3. Every later Slack action resolves the link AND the principal's live API key
   (active, unexpired, same tenant) and checks the scope that action needs
   (``governance:approve`` for HITL decisions, ``goals:write`` for goals) with
   the same role -> scope table the HTTP API enforces. No link, a revoked key or
   a missing scope -> refused.

All lookups run in Postgres (multi-replica safe); without a database nothing is
linked and every Slack action is refused (fail closed).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

LINK_CODE_TTL = timedelta(minutes=10)
# Unambiguous alphabet (no 0/O, 1/I/L); 10 chars ~ 49 bits, valid for 10 minutes.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 10

SCOPE_APPROVE = "governance:approve"
SCOPE_SUBMIT_GOALS = "goals:write"


class SlackIdentityStoreUnavailableError(RuntimeError):
    """No database is wired, or the database failed: nothing can be linked."""


class PrincipalNotLinkableError(ValueError):
    """The caller's identity is not a persisted, active API key in its tenant."""


@dataclass(frozen=True)
class SlackPrincipal:
    """The AgentVerse principal a Slack user acts as, with its LIVE roles/scopes."""

    tenant_id: str
    principal_id: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...] = field(default_factory=tuple)

    def effective_scopes(self) -> frozenset[str]:
        from app.auth.scope_enforcement import ROLE_SCOPES

        granted: set[str] = set()
        for role in self.roles:
            granted |= ROLE_SCOPES.get(role, frozenset())
        if self.scopes:  # a key created with explicit scopes may use only those
            granted &= set(self.scopes)
        return frozenset(granted)

    def can(self, scope: str) -> bool:
        return scope in self.effective_scopes()


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


@dataclass(frozen=True)
class IssuedLinkCode:
    link_id: str
    code: str
    expires_at: datetime

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.link_id,
            "status": "pending",
            "code": self.code,
            "expires_at": self.expires_at.isoformat(),
            "instructions": f"In Slack, run: /agentverse link {self.code}",
        }


async def issue_link_code(*, tenant_db: Any, tenant_id: str, principal_id: str) -> IssuedLinkCode:
    """Create a pending link for ``principal_id`` (the caller's API key).

    Refused unless the principal is an active API key of ``tenant_id`` — an
    ephemeral identity (e.g. a stream token) has no live roles to re-check.
    """
    if tenant_db is None:
        raise SlackIdentityStoreUnavailableError("Slack identity links need the database")
    if not principal_id:
        raise PrincipalNotLinkableError("No authenticated principal")
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    code = _new_code()
    link_id = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + LINK_CODE_TTL
    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        key = await session.execute(
            text(
                "SELECT 1 FROM api_keys WHERE id = :pid AND tenant_id = :tid "
                "AND is_active AND (expires_at IS NULL OR expires_at > now())"
            ),
            {"pid": principal_id, "tid": tenant_id},
        )
        if key.first() is None:
            raise PrincipalNotLinkableError(
                "Slack can only be linked to an active API key of this tenant"
            )
        # One outstanding code per principal: a new request replaces the old one.
        await session.execute(
            text(
                "DELETE FROM slack_identity_links WHERE tenant_id = :tid "
                "AND principal_id = :pid AND status = 'pending'"
            ),
            {"tid": tenant_id, "pid": principal_id},
        )
        await session.execute(
            text(
                "INSERT INTO slack_identity_links "
                "(id, tenant_id, principal_id, status, link_code_hash, code_expires_at) "
                "VALUES (:id, :tid, :pid, 'pending', :h, :exp)"
            ),
            {
                "id": link_id,
                "tid": tenant_id,
                "pid": principal_id,
                "h": _hash_code(code),
                "exp": expires_at,
            },
        )
    return IssuedLinkCode(link_id=link_id, code=code, expires_at=expires_at)


async def redeem_link_code(
    *, system_db: Any, bound_tenant_id: str, team_id: str, slack_user_id: str, code: str
) -> bool:
    """Activate the pending link whose code was typed by ``slack_user_id``.

    Only a code issued in ``bound_tenant_id`` (the tenant the workspace is bound
    to) redeems, so a code cannot link a Slack user into another tenant. Any
    previous link of the same Slack user is replaced. Returns False for an
    unknown, expired or foreign code.
    """
    if system_db is None:
        raise SlackIdentityStoreUnavailableError("Slack identity links need the database")
    if not (bound_tenant_id and team_id and slack_user_id and code.strip()):
        return False
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as session, session.begin(), system_session(session):
        row = (
            await session.execute(
                text(
                    "SELECT id FROM slack_identity_links WHERE status = 'pending' "
                    "AND link_code_hash = :h AND tenant_id = :tid "
                    "AND code_expires_at > now() FOR UPDATE"
                ),
                {"h": _hash_code(code), "tid": bound_tenant_id},
            )
        ).first()
        if row is None:
            return False
        await session.execute(
            text(
                "DELETE FROM slack_identity_links WHERE status = 'active' "
                "AND team_id = :team AND slack_user_id = :uid"
            ),
            {"team": team_id, "uid": slack_user_id},
        )
        await session.execute(
            text(
                "UPDATE slack_identity_links SET status = 'active', team_id = :team, "
                "slack_user_id = :uid, link_code_hash = NULL, code_expires_at = NULL, "
                "linked_at = now() WHERE id = :id AND tenant_id = :tid"
            ),
            {"team": team_id, "uid": slack_user_id, "id": row[0], "tid": bound_tenant_id},
        )
    _log.info("slack_identity_linked", tenant_id=bound_tenant_id, team_id=team_id, link_id=row[0])
    return True


async def resolve_slack_principal(
    *, system_db: Any, tenant_id: str, team_id: str, slack_user_id: str
) -> SlackPrincipal | None:
    """The live AgentVerse principal linked to this Slack user, or None.

    None also when the linked API key was revoked, expired, or belongs to another
    tenant. A database error raises :class:`SlackIdentityStoreUnavailableError`
    (the caller refuses the action; it never falls back to "allowed").
    """
    if system_db is None:
        raise SlackIdentityStoreUnavailableError("Slack identity links need the database")
    if not (tenant_id and team_id and slack_user_id):
        return None
    from sqlalchemy import text

    from app.db.rls import system_session

    try:
        async with system_db() as session, session.begin(), system_session(session):
            row = (
                await session.execute(
                    text(
                        "SELECT l.principal_id, k.roles, k.scopes "
                        "FROM slack_identity_links l "
                        "JOIN api_keys k ON k.id = l.principal_id AND k.tenant_id = l.tenant_id "
                        "WHERE l.status = 'active' AND l.tenant_id = :tid "
                        "AND l.team_id = :team AND l.slack_user_id = :uid "
                        "AND k.is_active AND (k.expires_at IS NULL OR k.expires_at > now()) "
                        "LIMIT 1"
                    ),
                    {"tid": tenant_id, "team": team_id, "uid": slack_user_id},
                )
            ).first()
    except Exception as exc:
        raise SlackIdentityStoreUnavailableError(str(exc)[:200]) from exc
    if row is None:
        return None
    roles = tuple(str(r) for r in (row[1] or ())) or ("operator",)
    return SlackPrincipal(
        tenant_id=tenant_id,
        principal_id=str(row[0]),
        roles=roles,
        scopes=tuple(str(s) for s in (row[2] or ())),
    )


async def list_links(
    *, tenant_db: Any, tenant_id: str, principal_id: str | None = None
) -> list[dict[str, Any]]:
    """The tenant's links; only ``principal_id``'s own when given (non-admins)."""
    if tenant_db is None:
        raise SlackIdentityStoreUnavailableError("Slack identity links need the database")
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        sql = (
            "SELECT id, principal_id, team_id, slack_user_id, status, code_expires_at, "
            "created_at, linked_at FROM slack_identity_links WHERE tenant_id = :tid "
            "AND (status = 'active' OR code_expires_at > now())"
        )
        params: dict[str, Any] = {"tid": tenant_id}
        if principal_id is not None:
            sql += " AND principal_id = :pid"
            params["pid"] = principal_id
        rows = await session.execute(text(sql + " ORDER BY created_at"), params)
        return [_jsonable(dict(r._mapping)) for r in rows]


async def delete_link(
    *, tenant_db: Any, tenant_id: str, link_id: str, principal_id: str | None = None
) -> bool:
    """Unlink; restricted to ``principal_id``'s own link when given (non-admins)."""
    if tenant_db is None:
        raise SlackIdentityStoreUnavailableError("Slack identity links need the database")
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        sql = "DELETE FROM slack_identity_links WHERE id = :id AND tenant_id = :tid"
        params: dict[str, Any] = {"id": link_id, "tid": tenant_id}
        if principal_id is not None:
            sql += " AND principal_id = :pid"
            params["pid"] = principal_id
        result = await session.execute(text(sql), params)
        return bool(getattr(result, "rowcount", 0))


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
