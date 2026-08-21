"""QA1 — Tenant User & Role Management.

Three-tier user hierarchy per spec:
  tenant_admin  — full access to all orgs + billing
  org_admin     — full access to specific org(s)
  org_member    — can use org, create missions
  org_viewer    — read-only
  approver      — can only approve/reject HITL items
  billing_admin — billing only, no org access

Includes:
  - TenantUser dataclass
  - Invite-by-email with magic link (7-day expiry)
  - Per-org permission matrix
  - CRUD endpoints: GET/POST/DELETE/PATCH users + invites
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger

_log = get_logger(__name__)


class TenantRole(str, Enum):
    TENANT_ADMIN = "tenant_admin"  # full access to all orgs + billing
    ORG_ADMIN = "org_admin"  # full access to specific org(s)
    ORG_MEMBER = "org_member"  # can use org, create missions
    ORG_VIEWER = "org_viewer"  # read-only
    APPROVER = "approver"  # approve/reject only
    BILLING_ADMIN = "billing_admin"  # billing only


# Scope → permissions mapping
ROLE_PERMISSIONS: dict[str, list[str]] = {
    TenantRole.TENANT_ADMIN: ["read", "write", "delete", "approve", "billing", "admin"],
    TenantRole.ORG_ADMIN: ["read", "write", "delete", "approve"],
    TenantRole.ORG_MEMBER: ["read", "write"],
    TenantRole.ORG_VIEWER: ["read"],
    TenantRole.APPROVER: ["read", "approve"],
    TenantRole.BILLING_ADMIN: ["billing"],
}


@dataclass
class TenantUser:
    """Per spec QA1 — full tenant user record."""

    user_id: str
    tenant_id: str
    email: str
    name: str
    role: TenantRole
    org_permissions: dict[str, list[str]] = field(default_factory=dict)
    # {org_id: ["read", "write", "approve"]}
    invited_by: str | None = None
    joined_at: datetime | None = None
    last_active_at: datetime | None = None
    mfa_enabled: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = True

    def has_permission(self, permission: str, org_id: str | None = None) -> bool:
        """Check if user has a permission (global or for specific org)."""
        # Global role permissions
        global_perms = ROLE_PERMISSIONS.get(self.role, [])
        if permission in global_perms or "admin" in global_perms:
            return True
        # Per-org permissions
        if org_id and org_id in self.org_permissions:
            return permission in self.org_permissions[org_id]
        return False


@dataclass
class TenantInvite:
    """Pending invitation — expires after 7 days."""

    invite_id: str
    tenant_id: str
    email: str
    role: TenantRole
    org_ids: list[str] = field(default_factory=list)
    message: str = ""
    invited_by: str = ""
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(days=7))
    accepted: bool = False

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at

    @property
    def magic_link(self) -> str:
        return f"/auth/accept-invite?token={self.token}&invite={self.invite_id}"


class TenantUserService:
    """
    QA1 — Tenant user management service.
    Backed by in-memory store (production: DB table + email service).
    """

    def __init__(
        self,
        email_service: Any | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._email = email_service
        self._session = session
        self._users: dict[str, TenantUser] = {}  # user_id → TenantUser
        self._invites: dict[str, TenantInvite] = {}  # invite_id → TenantInvite
        self._by_email: dict[str, str] = {}  # email → user_id

    async def invite_user(
        self,
        tenant_id: str,
        email: str,
        role: TenantRole,
        org_ids: list[str] | None = None,
        invited_by: str = "system",
        message: str = "",
    ) -> TenantInvite:
        """
        Create an invite and send a magic link email.
        POST /v1/tenants/users/invite
        """
        invite = TenantInvite(
            invite_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            email=email,
            role=role,
            org_ids=org_ids or [],
            message=message,
            invited_by=invited_by,
        )
        self._invites[invite.invite_id] = invite

        # Send invite email
        if self._email:
            try:
                await self._email.send(
                    to=email,
                    subject="You're invited to join the organization",
                    body=(
                        f"You've been invited as {role.value}.\n"
                        f"Accept here: {invite.magic_link}\n"
                        f"This link expires in 7 days."
                    ),
                )
            except Exception as exc:
                _log.warning("tenant_user.invite_email_failed", error=str(exc))

        _log.info(
            "tenant_user.invited",
            invite_id=invite.invite_id,
            email=email,
            role=role.value,
        )
        return invite

    async def accept_invite(self, token: str, invite_id: str, name: str) -> TenantUser:
        """Accept an invitation and create the user account."""
        invite = self._invites.get(invite_id)
        if not invite:
            raise ValueError(f"Invite {invite_id} not found")
        if invite.is_expired:
            raise ValueError("Invite has expired")
        if invite.accepted:
            raise ValueError("Invite already accepted")
        if not secrets.compare_digest(invite.token, token):
            raise ValueError("Invalid invite token")

        # Create user
        user = TenantUser(
            user_id=str(uuid.uuid4()),
            tenant_id=invite.tenant_id,
            email=invite.email,
            name=name,
            role=invite.role,
            org_permissions={oid: ROLE_PERMISSIONS.get(invite.role, []) for oid in invite.org_ids},
            invited_by=invite.invited_by,
            joined_at=datetime.now(UTC),
        )
        self._users[user.user_id] = user
        self._by_email[invite.email] = user.user_id
        invite.accepted = True

        _log.info("tenant_user.joined", user_id=user.user_id, email=user.email)
        return user

    async def get_user(self, user_id: str) -> TenantUser | None:
        return self._users.get(user_id)

    async def get_user_by_email(self, email: str) -> TenantUser | None:
        uid = self._by_email.get(email.lower())
        return self._users.get(uid) if uid else None

    async def list_users(self, tenant_id: str) -> list[TenantUser]:
        return [u for u in self._users.values() if u.tenant_id == tenant_id and u.is_active]

    async def list_pending_invites(self, tenant_id: str) -> list[TenantInvite]:
        return [
            i
            for i in self._invites.values()
            if i.tenant_id == tenant_id and not i.accepted and not i.is_expired
        ]

    async def change_role(self, user_id: str, new_role: TenantRole) -> bool:
        user = self._users.get(user_id)
        if not user:
            return False
        user.role = new_role
        _log.info("tenant_user.role_changed", user_id=user_id, new_role=new_role.value)
        return True

    async def remove_user(self, user_id: str) -> bool:
        user = self._users.get(user_id)
        if not user:
            return False
        user.is_active = False
        _log.info("tenant_user.removed", user_id=user_id)
        return True

    async def revoke_invite(self, invite_id: str) -> bool:
        invite = self._invites.get(invite_id)
        if not invite:
            return False
        del self._invites[invite_id]
        return True

    async def set_org_permissions(self, user_id: str, org_id: str, permissions: list[str]) -> bool:
        user = self._users.get(user_id)
        if not user:
            return False
        user.org_permissions[org_id] = permissions
        return True


# Global instance
tenant_user_service = TenantUserService()
