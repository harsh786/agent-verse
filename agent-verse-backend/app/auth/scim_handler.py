"""SCIM 2.0 user/group provisioning handler (RFC 7644).

Handles automated user lifecycle from identity providers:
  - POST /scim/v2/Users — create user (JIT provisioning from Okta/Azure AD)
  - GET  /scim/v2/Users — list users
  - GET  /scim/v2/Users/{id} — get user
  - PUT  /scim/v2/Users/{id} — full replacement
  - PATCH /scim/v2/Users/{id} — partial update (e.g., deactivate)
  - DELETE /scim/v2/Users/{id} — deprovision

Authentication: SHA-256 hashed bearer token checked against scim_tokens table.

Row-level security
------------------
The token lookup runs BEFORE any tenant is known — it is what establishes one —
so it cannot be scoped by ``app.tenant_id``. It presents the token's hash as
``app.scim_token_hash`` instead; a SELECT-only permissive policy on
``scim_tokens`` (``scim_tokens_by_presented_hash``) makes exactly the row with
that hash visible and nothing else. Knowing a token's SHA-256 is equivalent to
holding the token, so the policy reveals nothing the caller does not already
possess, and an empty/unset GUC matches no row. This mirrors the API-key
pattern (``TenantService._db_resolve_by_hash`` + migration b8c9d0e1f2a3).

Every statement after that runs for the resolved tenant inside one transaction
with the ``app.tenant_id`` GUC set (``sqlalchemy_rls_context``), and keeps its
explicit ``tenant_id`` predicate as defence in depth.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException, Request

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)

# SCIM schema URIs
SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST_RESPONSE = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


# ---------------------------------------------------------------------------
# SCIM Bearer Auth (Amendment 8.2)
# ---------------------------------------------------------------------------


async def require_scim_auth(request: Request) -> str:
    """
    Authenticate SCIM requests via pre-provisioned bearer token.

    Amendment 8.2: Token is SHA-256 hashed and verified against scim_tokens table.
    Returns tenant_id on success; raises HTTP 401 on failure.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail=_scim_error("Bearer token required", "invalidCredentials"),
        )

    raw_token = auth[7:].strip()
    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail=_scim_error("Empty bearer token", "invalidCredentials"),
        )

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(
            status_code=503,
            detail=_scim_error("SCIM service unavailable", "serverError"),
        )

    try:
        from sqlalchemy import text as _t

        # Pre-auth: no tenant yet. Present the hash through the GUC matched by the
        # SELECT-only ``scim_tokens_by_presented_hash`` policy — never switch row
        # security off (a NOBYPASSRLS role may not) and never use the maintenance
        # role on a request path. is_local=true scopes it to this transaction.
        async with db() as session, session.begin():
            await session.execute(
                _t("SELECT set_config('app.scim_token_hash', :h, true)"),
                {"h": token_hash},
            )
            row = (
                await session.execute(
                    _t("""
                        SELECT tenant_id FROM scim_tokens
                        WHERE token_hash = :hash AND revoked_at IS NULL
                        LIMIT 1
                    """),
                    {"hash": token_hash},
                )
            ).fetchone()
    except Exception as exc:
        logger.warning("scim_auth_db_error", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail=_scim_error("SCIM authentication service error", "serverError"),
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=401,
            detail=_scim_error("Invalid or revoked SCIM bearer token", "invalidCredentials"),
        )

    return str(row[0])


# ---------------------------------------------------------------------------
# SCIMHandler — user operations
# ---------------------------------------------------------------------------


class SCIMHandler:
    """
    SCIM 2.0 user/group provisioning.

    Constructed per-request with tenant_id resolved from bearer auth.
    The ``config`` dict should come from the scim_configs DB row.
    """

    def __init__(
        self,
        tenant_id: str,
        config: dict[str, Any],
        db_factory: Any,
    ) -> None:
        self._tenant_id = tenant_id
        self._config = config
        self._db = db_factory

    @asynccontextmanager
    async def _tenant_tx(self) -> AsyncIterator[Any]:
        """One transaction with ``app.tenant_id`` set to the token's tenant.

        Commits when the block exits cleanly and rolls back on any exception, so
        callers catch errors *outside* the block (after the rollback) rather
        than inside an aborted transaction.
        """
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, self._tenant_id),
        ):
            yield session

    # ── User operations ──────────────────────────────────────────────────
    #
    # Schema: ``users`` is the GLOBAL identity table (id, email, name, ... — no
    # tenant_id, no RLS; migration 0072) and ``tenant_memberships`` scopes a user
    # to a tenant (tenant_id, role, status; FORCE RLS). This handler used raw SQL
    # against ``users.tenant_id / display_name / is_active / scim_id / role`` —
    # columns that never existed — so every SCIM call failed, and list_users
    # turned the failure into an empty 200 ("this tenant has no users"). It now
    # uses the ORM models, so the SQL cannot drift from the schema again, and a
    # SCIM user is "a user WITH a membership in the token's tenant".
    #
    # ``users`` rows are shared across tenants, so one tenant's IdP never
    # rewrites an existing identity's profile (``name`` is only filled when
    # empty); activation state lives on the tenant's own membership row.

    async def _find_member(self, db: Any, scim_id: str) -> tuple[Any, Any] | None:
        """(User, TenantMembership) for *scim_id* (users.id or email) in this tenant."""
        from sqlalchemy import func, or_, select

        from app.db.models.user import TenantMembership, User

        row = (
            await db.execute(
                select(User, TenantMembership)
                .join(TenantMembership, TenantMembership.user_id == User.id)
                .where(
                    TenantMembership.tenant_id == self._tenant_id,
                    or_(User.id == scim_id, func.lower(User.email) == scim_id.lower()),
                )
                .limit(1)
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    async def list_users(
        self,
        start_index: int = 1,
        count: int = 100,
        filter_str: str = "",
    ) -> dict[str, Any]:
        """List tenant users in SCIM ListResponse format."""
        from sqlalchemy import func, select

        from app.db.models.user import TenantMembership, User

        start_index = max(start_index, 1)
        count = max(count, 0)
        try:
            async with self._tenant_tx() as db:
                rows = (
                    await db.execute(
                        select(User, TenantMembership)
                        .join(TenantMembership, TenantMembership.user_id == User.id)
                        .where(TenantMembership.tenant_id == self._tenant_id)
                        .order_by(TenantMembership.created_at.desc(), User.id)
                        .offset(start_index - 1)
                        .limit(count)
                    )
                ).all()
                total = (
                    await db.execute(
                        select(func.count())
                        .select_from(TenantMembership)
                        .where(TenantMembership.tenant_id == self._tenant_id)
                    )
                ).scalar_one()
                resources = [_to_scim_user(u, m) for u, m in rows]
        except Exception as exc:
            # Never an empty 200: an IdP reconciling against "no users" would
            # re-provision (or de-provision) everyone.
            logger.error("scim_list_users_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=_scim_error("User listing failed", "serverError"),
            ) from exc

        return {
            "schemas": [SCIM_LIST_RESPONSE],
            "totalResults": int(total or 0),
            "startIndex": start_index,
            "itemsPerPage": len(resources),
            "Resources": resources,
        }

    async def get_user(self, scim_id: str) -> dict[str, Any]:
        """Get a single tenant user by SCIM id (users.id) or userName (email)."""
        try:
            async with self._tenant_tx() as db:
                found = await self._find_member(db, scim_id)
                resource = _to_scim_user(*found) if found is not None else None
        except Exception as exc:
            logger.error("scim_get_user_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=_scim_error("User lookup failed", "serverError"),
            ) from exc

        if resource is None:
            raise HTTPException(
                status_code=404,
                detail=_scim_error(f"User {scim_id} not found", "notFound"),
            )
        return resource

    async def create_user(self, scim_data: dict[str, Any]) -> dict[str, Any]:
        """
        Create (or re-activate) a tenant user from a SCIM payload.

        Idempotent on userName/email. Maps group memberships to roles via
        group_role_map.
        """
        if not self._config.get("allow_user_create", True):
            raise HTTPException(
                status_code=403,
                detail=_scim_error(
                    "SCIM user creation is disabled for this tenant",
                    "mutability",
                ),
            )

        email = scim_data.get("userName") or (scim_data.get("emails") or [{}])[0].get("value", "")
        email = str(email).strip()
        if not email:
            raise HTTPException(
                status_code=400,
                detail=_scim_error("userName or emails[0].value required", "invalidValue"),
            )

        name = scim_data.get("name", {}) or {}
        display_name = f"{name.get('givenName', '')} {name.get('familyName', '')}".strip()
        display_name = display_name or scim_data.get("displayName") or None
        role = self._map_groups_to_role(scim_data.get("groups", []))
        status = _ACTIVE if scim_data.get("active", True) else _INACTIVE

        from sqlalchemy import func, select

        from app.db.models.user import TenantMembership, User

        try:
            async with self._tenant_tx() as db:
                user = (
                    await db.execute(select(User).where(func.lower(User.email) == email.lower()))
                ).scalar_one_or_none()
                if user is None:
                    user = User(email=email, name=display_name)
                    db.add(user)
                    await db.flush()
                elif user.name is None and display_name:
                    user.name = display_name

                membership = (
                    await db.execute(
                        select(TenantMembership).where(
                            TenantMembership.user_id == user.id,
                            TenantMembership.tenant_id == self._tenant_id,
                        )
                    )
                ).scalar_one_or_none()
                if membership is None:
                    membership = TenantMembership(
                        user_id=user.id,
                        tenant_id=self._tenant_id,
                        role=role,
                        status=status,
                    )
                    db.add(membership)
                else:
                    membership.role = role
                    membership.status = status
                await db.flush()
                await db.refresh(user)
                await db.refresh(membership)
                resource = _to_scim_user(user, membership)
        except Exception as exc:
            logger.error("scim_create_user_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=_scim_error("User creation failed", "serverError"),
            ) from exc
        return resource

    async def update_user(
        self,
        scim_id: str,
        scim_data: dict[str, Any],
        *,
        partial: bool = False,
    ) -> dict[str, Any]:
        """
        Update a user (PUT = full replacement, PATCH = Operations list).
        """
        if not self._config.get("allow_user_update", True):
            raise HTTPException(
                status_code=403,
                detail=_scim_error("SCIM user update is disabled", "mutability"),
            )

        active: bool | None = None
        display_name = ""
        if partial:
            for op in scim_data.get("Operations", []):
                if op.get("op", "").lower() == "replace" and op.get("path", "") == "active":
                    value = op.get("value")
                    active = (
                        value
                        if isinstance(value, bool)
                        else (value.get("active", True) if isinstance(value, dict) else True)
                    )
        else:
            active = bool(scim_data.get("active", True))
            name = scim_data.get("name", {}) or {}
            display_name = f"{name.get('givenName', '')} {name.get('familyName', '')}".strip()
        if active is False and not self._config.get("allow_user_delete", False):
            raise HTTPException(
                status_code=403,
                detail=_scim_error(
                    "User deactivation (delete) disabled for this tenant", "mutability"
                ),
            )

        try:
            async with self._tenant_tx() as db:
                found = await self._find_member(db, scim_id)
                resource = None
                if found is not None:
                    user, membership = found
                    if active is not None:
                        membership.status = _ACTIVE if active else _INACTIVE
                    if display_name and user.name is None:
                        user.name = display_name
                    await db.flush()
                    await db.refresh(user)
                    await db.refresh(membership)
                    resource = _to_scim_user(user, membership)
        except Exception as exc:
            logger.error("scim_update_user_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=_scim_error("User update failed", "serverError"),
            ) from exc

        if resource is None:
            raise HTTPException(
                status_code=404,
                detail=_scim_error(f"User {scim_id} not found", "notFound"),
            )
        return resource

    async def delete_user(self, scim_id: str) -> None:
        """Deprovision a user from this tenant (the global identity is kept)."""
        if not self._config.get("allow_user_delete", False):
            raise HTTPException(
                status_code=403,
                detail=_scim_error("User deletion disabled for this tenant", "mutability"),
            )
        found: tuple[Any, Any] | None = None
        try:
            async with self._tenant_tx() as db:
                found = await self._find_member(db, scim_id)
                if found is not None:
                    found[1].status = _INACTIVE
        except Exception as exc:
            logger.error("scim_delete_user_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=_scim_error("User deprovisioning failed", "serverError"),
            ) from exc
        if found is None:
            raise HTTPException(
                status_code=404,
                detail=_scim_error(f"User {scim_id} not found", "notFound"),
            )

    def _map_groups_to_role(self, groups: list[dict[str, Any]]) -> str:
        group_role_map = self._config.get("group_role_map", {})
        for group in groups:
            name = group.get("display", "")
            if name in group_role_map:
                return group_role_map[name]
        return self._config.get("default_role", "viewer")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Membership status values SCIM toggles (tenant_memberships.status).
_ACTIVE = "active"
_INACTIVE = "deactivated"


def _to_scim_user(user: Any, membership: Any) -> dict[str, Any]:
    """SCIM User resource from a ``User`` + its ``TenantMembership`` in this tenant."""
    uid = str(user.id)
    email = user.email or ""
    return {
        "schemas": [SCIM_USER_SCHEMA],
        "id": uid,
        "userName": email,
        "displayName": user.name or email,
        "active": membership.status == _ACTIVE,
        "emails": [{"value": email, "primary": True}],
        "roles": [{"value": membership.role}] if membership.role else [],
        "meta": {
            "resourceType": "User",
            "created": str(membership.created_at) if membership.created_at else None,
            "lastModified": str(membership.updated_at) if membership.updated_at else None,
            "location": f"/scim/v2/Users/{uid}",
        },
    }


def _scim_error(detail: str, scim_type: str = "invalidValue") -> dict[str, Any]:
    return {
        "schemas": [SCIM_ERROR_SCHEMA],
        "detail": detail,
        "scimType": scim_type,
    }
