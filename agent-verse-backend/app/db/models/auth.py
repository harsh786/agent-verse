"""SQLAlchemy ORM models for scopes, custom roles, role assignments, and IP allowlist.

Tables added in migration 0049:
  custom_roles          — tenant-specific and builtin role definitions with permission sets
  role_assignments      — maps users/api-keys to roles within a tenant
  api_key_scopes        — normalized scope grants for API keys (replaces JSONB array)
  ip_allowlist_entries  — per-tenant CIDR allowlist (new; distinct from legacy ip_allowlist)
  scope_definitions     — canonical registry of all available scopes
  scope_grants          — explicit scope delegation between principals
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class CustomRole(Base):
    """Custom or builtin role definition for a tenant.

    Builtin roles have ``tenant_id = NULL`` and are visible to every tenant.
    Tenant-specific roles are scoped via the RLS policy.
    """

    __tablename__ = "custom_roles"

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Nullable: NULL means it's a builtin/template role (all tenants can see it)
    tenant_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_role_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("custom_roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    system_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_template: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RoleAssignment(Base):
    """Assigns a role (custom or system) to a user / API key within a tenant.

    ``user_id`` is the Keycloak ``sub`` claim or the ``api_key_id`` for API-key actors.
    Exactly one of ``role_id`` (custom role FK) or ``system_role`` (text) must be set.
    """

    __tablename__ = "role_assignments"

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    role_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("custom_roles.id", ondelete="CASCADE"),
        nullable=True,
    )
    system_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=True, server_default=text("'{}'::jsonb")
    )
    granted_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class APIKeyScope(Base):
    """Normalized scope grant for a specific API key.

    Replaces the JSONB ``scopes`` array on the ``api_keys`` table with a
    proper normalized table for query efficiency and partial scope grants.
    """

    __tablename__ = "api_key_scopes"

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    api_key_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IPAllowlistEntry(Base):
    """Per-tenant IP CIDR allowlist entry (enforcement-ready).

    Distinct from the legacy ``ip_allowlist`` table (which stored metadata only
    and was never enforced).  This table is checked by ScopeEnforcementMiddleware
    on every request.
    """

    __tablename__ = "ip_allowlist_entries"

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Stored as text; validated as CIDR at application layer
    cidr: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScopeDefinition(Base):
    """Canonical registry of all scopes supported by the platform.

    Seeded at startup by ``app.auth.scope_seeder.seed_scope_definitions()``.
    Treated as immutable after initial seeding; additions require a migration.
    """

    __tablename__ = "scope_definitions"

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'low'")
    )
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScopeGrant(Base):
    """Explicit scope delegation from one principal to another.

    Supports least-privilege delegation: a grantor can delegate only scopes
    they themselves hold (enforced at application layer).
    """

    __tablename__ = "scope_grants"

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    grantor_id: Mapped[str] = mapped_column(Text, nullable=False)
    grantee_id: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    resource_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
