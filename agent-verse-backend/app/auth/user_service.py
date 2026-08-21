"""User management service — upsert, lookup, membership management."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.db.models.user import TenantMembership, User
from app.observability.logging import get_logger

logger = get_logger(__name__)


async def upsert_google_user(
    *,
    db_factory: Any,
    email: str,
    google_sub: str,
    name: str = "",
    picture_url: str = "",
) -> tuple[str, str]:
    """Upsert a Google-authenticated user and create a personal tenant.

    Returns (user_id, personal_tenant_id).
    """
    async with db_factory() as session, session.begin():
        # Check for existing user by google_sub or email
        result = await session.execute(
            select(User).where((User.google_sub == google_sub) | (User.email == email)).limit(1)
        )
        user = result.scalar_one_or_none()

        if user is None:
            user_id = uuid.uuid4().hex
            user = User(
                id=user_id,
                email=email,
                name=name,
                picture_url=picture_url,
                google_sub=google_sub,
            )
            session.add(user)
            await session.flush()
        else:
            user_id = user.id
            # Update google_sub if not set
            if not user.google_sub:
                user.google_sub = google_sub
            if name and not user.name:
                user.name = name

        # Ensure personal tenant exists
        # Personal tenant ID = "personal_{user_id[:16]}"
        tenant_id = f"personal_{user_id[:16]}"

        # Check membership
        mem_result = await session.execute(
            select(TenantMembership)
            .where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
            )
            .limit(1)
        )
        membership = mem_result.scalar_one_or_none()

        if membership is None:
            membership = TenantMembership(
                id=uuid.uuid4().hex,
                user_id=user_id,
                tenant_id=tenant_id,
                role="owner",
                status="active",
            )
            session.add(membership)

        logger.info("user_upserted", user_id=user_id, email=email, tenant_id=tenant_id)
        return user_id, tenant_id
