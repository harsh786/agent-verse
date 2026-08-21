"""MFA configuration DB model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class TenantMFA(Base):
    """Per-tenant MFA configuration stored in the database."""

    __tablename__ = "tenant_mfa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # TOTP secret — encrypted at rest using Fernet symmetric encryption.
    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    # MFA state
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Recovery codes — newline-separated SHA-256 hashes, never plaintext.
    recovery_codes_hashed: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<TenantMFA tenant_id={self.tenant_id} enabled={self.enabled}>"
