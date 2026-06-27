"""ORM models for RBAC: user roles and IP allowlist."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class UserRole(Base):
    """Maps a user (identified by sub/api_key_id) to a role within a tenant."""

    __tablename__ = "user_roles"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # user_id is the Keycloak `sub` claim or the api_key_id for API key users
    user_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IPAllowlistEntry(Base):
    """CIDR range allowed to access this tenant's API."""

    __tablename__ = "ip_allowlist"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cidr: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
