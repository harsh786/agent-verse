"""Channel → tenant registry for messaging channels (Telegram/WhatsApp/…).

A messaging webhook is unauthenticated at the tenant layer and identifies the
tenant by the *addressee* — the bot that received the message (Telegram bot id /
WhatsApp business number). This registry maps ``(channel, addressee)`` to the
owning tenant so an inbound message resolves to the right tenant before any
tenant-scoped work. Each binding carries its OWN inbound secret: the addressee
selects the binding, the binding's secret authenticates the request. Process
memory, seeded from ``CHANNEL_TENANT_MAP``; the ``channel_tenant_mappings`` table
is not read yet (every replica must be seeded with the same env).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelBinding:
    tenant_id: str
    org_id: str = ""
    # Optional outbound bot token / number for sending the reply back.
    outbound_token: str = ""
    # Per-tenant inbound credential for this binding (Telegram secret_token,
    # WhatsApp app secret, generic-webhook HMAC key). The addressee in the body
    # only *selects* a binding; the request is then authenticated with THIS
    # secret, so a tenant holding its own channel secret cannot address another
    # tenant's bot. A binding without a secret is refused (fail closed).
    secret: str = ""


class ChannelRegistry:
    def __init__(self) -> None:
        self._map: dict[tuple[str, str], ChannelBinding] = {}

    @staticmethod
    def _key(channel: str, addressee: str) -> tuple[str, str]:
        return (channel.strip().lower(), str(addressee).strip())

    def register(
        self, channel: str, addressee: str, tenant_id: str, *, org_id: str = "",
        outbound_token: str = "", secret: str = "",
    ) -> None:
        self._map[self._key(channel, addressee)] = ChannelBinding(
            tenant_id, org_id, outbound_token, secret
        )

    def resolve(self, channel: str, addressee: str) -> ChannelBinding | None:
        return self._map.get(self._key(channel, addressee))

    @classmethod
    def from_env(cls, raw: str | None = None) -> ChannelRegistry:
        """Seed from ``CHANNEL_TENANT_MAP`` = ``channel:addressee:tenant[:org[:secret]]``.

        Entries are comma-separated. ``secret`` is the binding's per-tenant inbound
        credential; an entry without one is still registered but every inbound
        message for it is refused until a secret is configured.
        """
        reg = cls()
        value = raw if raw is not None else os.getenv("CHANNEL_TENANT_MAP", "")
        for entry in (value or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            if len(parts) < 3 or not all(parts[:3]):
                continue
            reg.register(
                parts[0], parts[1], parts[2],
                org_id=parts[3] if len(parts) > 3 else "",
                secret=":".join(parts[4:]) if len(parts) > 4 else "",
            )
        return reg
