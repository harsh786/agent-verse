"""SCIM 2.0 user/group provisioning handler (RFC 7644).

Handles automated user lifecycle from identity providers:
  - POST /scim/v2/Users — create user (JIT provisioning from Okta/Azure AD)
  - GET  /scim/v2/Users — list users
  - GET  /scim/v2/Users/{id} — get user
  - PUT  /scim/v2/Users/{id} — full replacement
  - PATCH /scim/v2/Users/{id} — partial update (e.g., deactivate)
  - DELETE /scim/v2/Users/{id} — deprovision

Authentication: SHA-256 hashed bearer token checked against scim_tokens table.
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException, Request

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

        async with db() as session:
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

    # ── User operations ──────────────────────────────────────────────────

    async def list_users(
        self,
        start_index: int = 1,
        count: int = 100,
        filter_str: str = "",
    ) -> dict[str, Any]:
        """List tenant users in SCIM ListResponse format."""
        from sqlalchemy import text as _t

        async with self._db() as db:
            try:
                rows = (
                    await db.execute(
                        _t("""
                            SELECT id, email, display_name, is_active,
                                   scim_id, created_at, updated_at
                            FROM users
                            WHERE tenant_id = :tid
                            ORDER BY created_at DESC
                            OFFSET :off LIMIT :lim
                        """),
                        {
                            "tid": self._tenant_id,
                            "off": start_index - 1,
                            "lim": count,
                        },
                    )
                ).fetchall()

                total = (
                    await db.execute(
                        _t("SELECT COUNT(*) FROM users WHERE tenant_id = :tid"),
                        {"tid": self._tenant_id},
                    )
                ).scalar() or 0
            except Exception as exc:
                logger.warning("scim_list_users_failed", error=str(exc))
                rows, total = [], 0

        return {
            "schemas": [SCIM_LIST_RESPONSE],
            "totalResults": total,
            "startIndex": start_index,
            "itemsPerPage": len(rows),
            "Resources": [_db_row_to_scim_user(r) for r in rows],
        }

    async def get_user(self, scim_id: str) -> dict[str, Any]:
        """Get a single user by SCIM external ID or internal DB id."""
        from sqlalchemy import text as _t

        async with self._db() as db:
            try:
                row = (
                    await db.execute(
                        _t("""
                            SELECT id, email, display_name, is_active,
                                   scim_id, created_at, updated_at
                            FROM users
                            WHERE tenant_id = :tid
                              AND (scim_id = :sid OR id::text = :sid)
                            LIMIT 1
                        """),
                        {"tid": self._tenant_id, "sid": scim_id},
                    )
                ).fetchone()
            except Exception as exc:
                logger.warning("scim_get_user_failed", error=str(exc))
                row = None

        if row is None:
            raise HTTPException(
                status_code=404,
                detail=_scim_error(f"User {scim_id} not found", "notFound"),
            )
        return _db_row_to_scim_user(row)

    async def create_user(self, scim_data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a user from SCIM payload.

        Maps group memberships to roles via group_role_map.
        """
        if not self._config.get("allow_user_create", True):
            raise HTTPException(
                status_code=403,
                detail=_scim_error(
                    "SCIM user creation is disabled for this tenant",
                    "mutability",
                ),
            )

        email = scim_data.get("userName") or (
            scim_data.get("emails") or [{}]
        )[0].get("value", "")
        if not email:
            raise HTTPException(
                status_code=400,
                detail=_scim_error("userName or emails[0].value required", "invalidValue"),
            )

        name = scim_data.get("name", {})
        display_name = (
            f"{name.get('givenName', '')} {name.get('familyName', '')}".strip()
            or email
        )
        groups = scim_data.get("groups", [])
        role = self._map_groups_to_role(groups)
        external_id = scim_data.get("externalId") or scim_data.get("id") or email
        is_active = scim_data.get("active", True)

        from sqlalchemy import text as _t

        async with self._db() as db:
            try:
                # Upsert — idempotent on externalId
                await db.execute(
                    _t("""
                        INSERT INTO users
                            (tenant_id, email, display_name, role, scim_id,
                             is_active, created_at, updated_at)
                        VALUES
                            (:tid, :email, :display_name, :role, :scim_id,
                             :active, NOW(), NOW())
                        ON CONFLICT (tenant_id, email) DO UPDATE
                          SET display_name = EXCLUDED.display_name,
                              role = EXCLUDED.role,
                              scim_id = EXCLUDED.scim_id,
                              is_active = EXCLUDED.is_active,
                              updated_at = NOW()
                        RETURNING id, email, display_name, is_active,
                                  scim_id, created_at, updated_at
                    """),
                    {
                        "tid": self._tenant_id,
                        "email": email,
                        "display_name": display_name,
                        "role": role,
                        "scim_id": external_id,
                        "active": is_active,
                    },
                )
                await db.commit()

                # Fetch the created/updated row
                row = (
                    await db.execute(
                        _t("""
                            SELECT id, email, display_name, is_active,
                                   scim_id, created_at, updated_at
                            FROM users
                            WHERE tenant_id = :tid AND email = :email
                            LIMIT 1
                        """),
                        {"tid": self._tenant_id, "email": email},
                    )
                ).fetchone()
            except Exception as exc:
                logger.error("scim_create_user_failed", error=str(exc))
                raise HTTPException(
                    status_code=500,
                    detail=_scim_error(f"User creation failed: {exc}", "serverError"),
                ) from exc

        if row is None:
            raise HTTPException(
                status_code=500,
                detail=_scim_error("User created but could not be retrieved", "serverError"),
            )
        return _db_row_to_scim_user(row)

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

        from sqlalchemy import text as _t

        async with self._db() as db:
            try:
                if partial:
                    # PATCH: process Operations array
                    for op in scim_data.get("Operations", []):
                        op_type = op.get("op", "").lower()
                        path = op.get("path", "")
                        value = op.get("value")
                        if op_type == "replace" and path == "active":
                            active_val = value if isinstance(value, bool) else (
                                value.get("active", True) if isinstance(value, dict) else True
                            )
                            if not active_val and not self._config.get("allow_user_delete", False):
                                raise HTTPException(
                                    status_code=403,
                                    detail=_scim_error(
                                        "User deactivation (delete) disabled for this tenant",
                                        "mutability",
                                    ),
                                )
                            await db.execute(
                                _t("""
                                    UPDATE users SET is_active = :active, updated_at = NOW()
                                    WHERE tenant_id = :tid
                                      AND (scim_id = :sid OR id::text = :sid)
                                """),
                                {
                                    "active": active_val,
                                    "tid": self._tenant_id,
                                    "sid": scim_id,
                                },
                            )
                else:
                    # PUT: full replacement
                    active = scim_data.get("active", True)
                    if not active and not self._config.get("allow_user_delete", False):
                        raise HTTPException(
                            status_code=403,
                            detail=_scim_error(
                                "User deactivation disabled for this tenant", "mutability"
                            ),
                        )
                    name = scim_data.get("name", {})
                    display_name = (
                        f"{name.get('givenName', '')} {name.get('familyName', '')}".strip()
                    )
                    await db.execute(
                        _t("""
                            UPDATE users
                            SET display_name = :display_name,
                                is_active = :active,
                                updated_at = NOW()
                            WHERE tenant_id = :tid
                              AND (scim_id = :sid OR id::text = :sid)
                        """),
                        {
                            "display_name": display_name,
                            "active": active,
                            "tid": self._tenant_id,
                            "sid": scim_id,
                        },
                    )
                await db.commit()

                row = (
                    await db.execute(
                        _t("""
                            SELECT id, email, display_name, is_active,
                                   scim_id, created_at, updated_at
                            FROM users
                            WHERE tenant_id = :tid
                              AND (scim_id = :sid OR id::text = :sid)
                            LIMIT 1
                        """),
                        {"tid": self._tenant_id, "sid": scim_id},
                    )
                ).fetchone()
            except HTTPException:
                raise
            except Exception as exc:
                logger.error("scim_update_user_failed", error=str(exc))
                raise HTTPException(
                    status_code=500,
                    detail=_scim_error(str(exc), "serverError"),
                ) from exc

        if row is None:
            raise HTTPException(
                status_code=404,
                detail=_scim_error(f"User {scim_id} not found", "notFound"),
            )
        return _db_row_to_scim_user(row)

    async def delete_user(self, scim_id: str) -> None:
        """Deprovision (soft-delete) a user."""
        if not self._config.get("allow_user_delete", False):
            raise HTTPException(
                status_code=403,
                detail=_scim_error("User deletion disabled for this tenant", "mutability"),
            )
        from sqlalchemy import text as _t

        async with self._db() as db:
            try:
                await db.execute(
                    _t("""
                        UPDATE users SET is_active = FALSE, updated_at = NOW()
                        WHERE tenant_id = :tid
                          AND (scim_id = :sid OR id::text = :sid)
                    """),
                    {"tid": self._tenant_id, "sid": scim_id},
                )
                await db.commit()
            except Exception as exc:
                logger.error("scim_delete_user_failed", error=str(exc))
                raise HTTPException(
                    status_code=500,
                    detail=_scim_error(str(exc), "serverError"),
                ) from exc

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


def _db_row_to_scim_user(row: Any) -> dict[str, Any]:
    """Convert a DB row (tuple or Row) to SCIM User resource."""
    if hasattr(row, "_mapping"):
        m = dict(row._mapping)
        uid = str(m.get("id", ""))
        email = m.get("email", "")
        display_name = m.get("display_name", email)
        is_active = m.get("is_active", True)
        scim_id = m.get("scim_id") or uid
        created_at = m.get("created_at")
        updated_at = m.get("updated_at")
    else:
        # Positional tuple: id, email, display_name, is_active, scim_id, created_at, updated_at
        uid = str(row[0]) if row[0] else ""
        email = row[1] or ""
        display_name = row[2] or email
        is_active = row[3] if row[3] is not None else True
        scim_id = str(row[4]) if row[4] else uid
        created_at = row[5] if len(row) > 5 else None
        updated_at = row[6] if len(row) > 6 else None

    return {
        "schemas": [SCIM_USER_SCHEMA],
        "id": uid,
        "externalId": scim_id,
        "userName": email,
        "displayName": display_name,
        "active": is_active,
        "emails": [{"value": email, "primary": True}],
        "meta": {
            "resourceType": "User",
            "created": str(created_at) if created_at else None,
            "lastModified": str(updated_at) if updated_at else None,
            "location": f"/scim/v2/Users/{uid}",
        },
    }


def _scim_error(detail: str, scim_type: str = "invalidValue") -> dict[str, Any]:
    return {
        "schemas": [SCIM_ERROR_SCHEMA],
        "detail": detail,
        "scimType": scim_type,
    }
