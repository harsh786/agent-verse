"""Who may schedule a deferred intention, and who it runs as (RV-07).

A prospective intention is not "just memory": when it is due the worker runs it
as a full goal (``goal_service.submit_goal``, optionally on a named agent). It
used to need only ``memory:write`` to create and then ran under a synthetic
``TenantContext(api_key_id="prospective-memory")`` carrying none of the
creator's roles or scopes, so a custom-role user or a scoped key without
``goals:write`` could launch goals through it.

Now:

* creating one requires what submitting a goal requires — ``goals:write`` in
  the principal's effective permission (its own explicit key scopes AND its
  role / ``api_key_scopes`` / role-assignment scopes, exactly as
  ``ScopeEnforcementMiddleware`` evaluates them), and the principal must be a
  persisted, active API key so it can be re-verified later;
* the creator (key id, roles, explicit scopes) is stored on the intention
  (``policy_snapshot["principal"]``);
* at fire time the key is re-read from Postgres under the tenant's RLS — it
  must still exist, be active and unexpired, and still hold ``goals:write`` —
  and the goal runs attributed to that key with the narrower of the stored and
  current scopes. Anything else fails closed: the intention is skipped (marked
  ``failed``) and the denial is audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

#: The permission POST /goals requires (ENDPOINT_SCOPES[("POST", "/goals")]).
GOAL_SUBMIT_SCOPE = "goals:write"


class IntentionNotAuthorizedError(PermissionError):
    """The principal may not (or may no longer) run a goal; fail closed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PrincipalCheckUnavailableError(RuntimeError):
    """The principal's key / scopes could not be read; nothing may run."""


@dataclass(frozen=True)
class IntentionPrincipal:
    """The API key an intention was scheduled by (and will run as)."""

    api_key_id: str
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()

    @classmethod
    def from_context(cls, tenant_ctx: Any) -> IntentionPrincipal:
        return cls(
            api_key_id=str(getattr(tenant_ctx, "api_key_id", "") or ""),
            roles=tuple(str(r) for r in (getattr(tenant_ctx, "roles", ()) or ())),
            scopes=tuple(str(s) for s in (getattr(tenant_ctx, "scopes", ()) or ())),
        )

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "api_key_id": self.api_key_id,
            "roles": list(self.roles),
            "scopes": list(self.scopes),
        }

    @classmethod
    def from_snapshot(cls, policy_snapshot: dict[str, Any] | None) -> IntentionPrincipal | None:
        raw = (policy_snapshot or {}).get("principal")
        if not isinstance(raw, dict):
            return None
        key_id = raw.get("api_key_id")
        if not isinstance(key_id, str) or not key_id:
            return None
        roles = raw.get("roles") or []
        scopes = raw.get("scopes") or []
        if not isinstance(roles, list) or not isinstance(scopes, list):
            return None
        return cls(
            api_key_id=key_id,
            roles=tuple(str(r) for r in roles),
            scopes=tuple(str(s) for s in scopes),
        )


def _narrow_scopes(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    """Least privilege of two explicit-scope lists (``()`` = no key-level limit)."""
    if not a:
        return b
    if not b:
        return a
    keep = set(b)
    return tuple(s for s in a if s in keep)


async def load_key_principal(
    db_factory: Any, tenant_id: str, api_key_id: str, *, now: datetime | None = None
) -> IntentionPrincipal | None:
    """The key's CURRENT roles/scopes from Postgres, or None when it may not act.

    None = no such key in this tenant, revoked (``is_active`` false) or expired.
    Read under the tenant's RLS. A DB error raises
    :class:`PrincipalCheckUnavailableError` (never "allowed").
    """
    if db_factory is None:
        raise PrincipalCheckUnavailableError("no database configured to verify the principal")
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db_factory() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_id):
                row = (
                    await session.execute(
                        text(
                            "SELECT roles, scopes, is_active, expires_at FROM api_keys "
                            "WHERE id = :kid AND tenant_id = :tid"
                        ),
                        {"kid": api_key_id, "tid": tenant_id},
                    )
                ).first()
    except Exception as exc:
        raise PrincipalCheckUnavailableError(
            f"principal lookup failed: {type(exc).__name__}"
        ) from exc
    if row is None or not row[2]:
        return None
    expires_at = row[3]
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if (now or datetime.now(UTC)) >= expires_at:
            return None
    roles = row[0]
    if isinstance(roles, str):
        import json

        roles = json.loads(roles)
    # Same default as TenantService._db_resolve_by_hash (what authentication sees).
    role_tuple = tuple(str(r) for r in (roles or ["operator"])) or ("operator",)
    return IntentionPrincipal(
        api_key_id=api_key_id,
        roles=role_tuple,
        scopes=tuple(str(s) for s in (row[1] or ())),
    )


async def _holds_goal_scope(
    db_factory: Any,
    tenant_id: str,
    api_key_id: str,
    roles: tuple[str, ...],
    scopes: tuple[str, ...],
) -> bool:
    """Whether (roles, scopes) grant ``goals:write`` the way the middleware decides it."""
    from app.auth.scope_enforcement import ScopeEnforcementMiddleware, ScopeLookupUnavailableError

    if scopes and GOAL_SUBMIT_SCOPE not in scopes:
        return False
    if not roles:
        # Role-less legacy keys may write only in the explicit legacy mode.
        from app.core.config import get_settings

        return bool(get_settings().scope_enforcement_legacy_allow)
    try:
        granted = await ScopeEnforcementMiddleware._load_scopes(
            db_factory=db_factory, tenant_id=tenant_id, key_id=api_key_id, roles=roles
        )
    except ScopeLookupUnavailableError as exc:
        raise PrincipalCheckUnavailableError(str(exc)) from exc
    return GOAL_SUBMIT_SCOPE in granted


async def authorize_intention_principal(
    db_factory: Any,
    tenant_id: str,
    principal: IntentionPrincipal | None,
    *,
    now: datetime | None = None,
) -> IntentionPrincipal:
    """Verify *principal* may run a goal NOW; return the principal to run as.

    Used both when an intention is created (with the caller's context) and when
    it fires (with the stored snapshot). The key must be an active, unexpired
    API key of *tenant_id*, and ``goals:write`` must be held both by the stored
    roles/scopes and by the key's current ones. The returned principal keeps
    the stored roles and the narrower of the stored and current scopes.

    Raises :class:`IntentionNotAuthorizedError` (deny) or
    :class:`PrincipalCheckUnavailableError` (could not verify — also deny).
    """
    if principal is None or not principal.api_key_id:
        raise IntentionNotAuthorizedError("no creating principal recorded")
    current = await load_key_principal(db_factory, tenant_id, principal.api_key_id, now=now)
    if current is None:
        raise IntentionNotAuthorizedError(
            "the creating principal is not an active API key of this tenant "
            "(revoked, expired or not a key)"
        )
    for roles, scopes in ((principal.roles, principal.scopes), (current.roles, current.scopes)):
        if not await _holds_goal_scope(db_factory, tenant_id, principal.api_key_id, roles, scopes):
            raise IntentionNotAuthorizedError(
                f"the creating principal does not hold {GOAL_SUBMIT_SCOPE}"
            )
    return IntentionPrincipal(
        api_key_id=principal.api_key_id,
        roles=principal.roles or current.roles,
        scopes=_narrow_scopes(principal.scopes, current.scopes),
    )


async def audit_intention_denied(
    db_factory: Any, *, tenant_id: str, memory_id: str, api_key_id: str | None, reason: str
) -> None:
    """Durable audit row for a fire-time denial (best effort: the skip stands)."""
    from app.governance.audit import AuditEvent, AuditLog
    from app.governance.permissions import ActionLevel
    from app.tenancy.context import PlanTier, TenantContext

    event = AuditEvent(
        goal_id="",
        tool_name="prospective_memory.fire",
        action_level=ActionLevel.DENY,
        outcome="denied",
        step_id=memory_id,
        note=f"intention {memory_id} skipped: {reason}"[:1000],
        api_key_id=api_key_id or None,
        auth_type="prospective_memory",
    )
    ctx = TenantContext(
        tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id=api_key_id or "prospective-memory"
    )
    try:
        await AuditLog(db_session_factory=db_factory).record_async(event, tenant_ctx=ctx)
    except Exception as exc:
        _log.error(
            "prospective_denial_audit_failed",
            tenant_id=tenant_id,
            memory_id=memory_id,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )


__all__ = [
    "GOAL_SUBMIT_SCOPE",
    "IntentionNotAuthorizedError",
    "IntentionPrincipal",
    "PrincipalCheckUnavailableError",
    "audit_intention_denied",
    "authorize_intention_principal",
    "load_key_principal",
]
