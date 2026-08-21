"""User and TenantMembership ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.models import Base


class User(Base):
    """Global user identity (email-unique, cross-tenant)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    keycloak_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[TenantMembership]] = relationship(
        "TenantMembership", back_populates="user", lazy="select"
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "picture_url": self.picture_url,
            "mfa_enabled": self.mfa_enabled,
        }


class TenantMembership(Base):
    """User ↔ Tenant membership with role."""

    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="viewer")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    invited_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship("User", back_populates="memberships")
