"""IdentityService — resolve and link principals across channels (Phase 3).

Storage-agnostic: an in-memory store is the default; a durable Postgres-backed
store (``identity_links`` / ``principals`` tables) swaps in later behind the same
async interface, exactly like the chat persistence swap. All lookups are
tenant-scoped.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.identity.models import IdentityLink, Principal, PrincipalKind


class IdentityStore(Protocol):
    async def get_principal(self, principal_id: str, tenant_id: str) -> Principal | None: ...

    async def create_principal(self, principal: Principal) -> None: ...

    async def get_link(
        self, tenant_id: str, channel: str, channel_user_id: str
    ) -> IdentityLink | None: ...

    async def create_link(self, link: IdentityLink) -> None: ...

    async def links_for_principal(
        self, principal_id: str, tenant_id: str
    ) -> list[IdentityLink]: ...


class InMemoryIdentityStore:
    def __init__(self) -> None:
        self._principals: dict[str, Principal] = {}
        self._links: dict[tuple[str, str, str], IdentityLink] = {}

    async def get_principal(self, principal_id: str, tenant_id: str) -> Principal | None:
        p = self._principals.get(principal_id)
        return p if p and p.tenant_id == tenant_id else None

    async def create_principal(self, principal: Principal) -> None:
        self._principals[principal.id] = principal

    async def get_link(
        self, tenant_id: str, channel: str, channel_user_id: str
    ) -> IdentityLink | None:
        return self._links.get((tenant_id, channel, channel_user_id))

    async def create_link(self, link: IdentityLink) -> None:
        self._links[link.key] = link

    async def links_for_principal(
        self, principal_id: str, tenant_id: str
    ) -> list[IdentityLink]:
        return [
            link
            for link in self._links.values()
            if link.principal_id == principal_id and link.tenant_id == tenant_id
        ]


class IdentityService:
    def __init__(self, store: Any | None = None) -> None:
        self._store: Any = store or InMemoryIdentityStore()

    async def resolve_principal(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        display_name: str | None = None,
        kind: str = PrincipalKind.INDIVIDUAL,
    ) -> Principal:
        """Return the principal for a channel identity, creating it on first contact.

        On first sight of ``(channel, channel_user_id)`` a new principal + link are
        created; subsequent sightings (same or different channel, once linked)
        return the existing principal — this is what unifies identities.
        """
        link = await self._store.get_link(tenant_id, channel, channel_user_id)
        if link is not None:
            existing = await self._store.get_principal(link.principal_id, tenant_id)
            if existing is not None:
                return existing
        principal = Principal(
            tenant_id=tenant_id, kind=kind, display_name=display_name or channel_user_id
        )
        await self._store.create_principal(principal)
        await self._store.create_link(
            IdentityLink(
                tenant_id=tenant_id,
                channel=channel,
                channel_user_id=channel_user_id,
                principal_id=principal.id,
            )
        )
        return principal

    async def link_identity(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        channel: str,
        channel_user_id: str,
    ) -> IdentityLink:
        """Bind another channel identity to an existing principal.

        This is how a web-authenticated user attaches their WhatsApp number /
        Telegram id so those channels continue the same principal's threads. If the
        identity is already linked to a *different* principal, that binding wins
        (identities are not silently re-pointed) and it is returned unchanged.
        """
        existing = await self._store.get_link(tenant_id, channel, channel_user_id)
        if existing is not None:
            return existing
        link = IdentityLink(
            tenant_id=tenant_id,
            channel=channel,
            channel_user_id=channel_user_id,
            principal_id=principal_id,
        )
        await self._store.create_link(link)
        return link

    async def get_principal(self, principal_id: str, tenant_id: str) -> Principal | None:
        return await self._store.get_principal(principal_id, tenant_id)

    async def identities_for(
        self, principal_id: str, tenant_id: str
    ) -> list[IdentityLink]:
        return await self._store.links_for_principal(principal_id, tenant_id)
