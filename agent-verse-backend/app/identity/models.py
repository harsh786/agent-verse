"""Identity models — principals and their cross-channel identity links."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _hex() -> str:
    return uuid.uuid4().hex


class PrincipalKind:
    ORG_MEMBER = "org_member"
    INDIVIDUAL = "individual"


@dataclass
class Principal:
    """The human/account behind conversations, within a tenant."""

    id: str = field(default_factory=_hex)
    tenant_id: str = ""
    kind: str = PrincipalKind.INDIVIDUAL
    display_name: str | None = None
    created_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class IdentityLink:
    """A (channel, channel_user_id) → principal binding within a tenant."""

    tenant_id: str
    channel: str
    channel_user_id: str
    principal_id: str
    created_at: datetime = field(default_factory=_now)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.channel, self.channel_user_id)
