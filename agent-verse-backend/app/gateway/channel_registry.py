"""Channel → tenant registry for messaging channels (Telegram/WhatsApp/…).

A messaging webhook is unauthenticated at the tenant layer and identifies the
tenant by the *addressee* — the bot that received the message (Telegram bot id /
WhatsApp business number). This registry maps ``(channel, addressee)`` to the
owning tenant so an inbound message resolves to the right tenant before any
tenant-scoped work. Each binding carries its OWN inbound secret: the addressee
selects the binding, the binding's secret authenticates the request.

This class is the OPERATOR fallback, seeded from ``CHANNEL_TENANT_MAP`` —
DEPRECATED (DEF-3): it is per-process, needs identical env on every replica and
a redeploy per change, and keeps secrets in plain env. Tenants manage durable,
verified bindings themselves (``/channels/bindings``, Settings > Gateway); rows
in ``channel_tenant_mappings`` resolved by
:class:`app.gateway.binding_store.ChannelBindingStore` are consulted first and
the env map only for an addressee no tenant has bound. A warning is logged at
startup while the variable is set.
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
    # Teams only: the tenant's Bot Framework app id — the audience its inbound
    # JWTs must carry (TRG-42).
    app_id: str = ""
    # WhatsApp only: the hub.verify_token Meta must echo when subscribing the
    # binding's webhook URL (DEF-3).
    verify_token: str = ""


class ChannelRegistry:
    def __init__(self) -> None:
        self._map: dict[tuple[str, str], ChannelBinding] = {}

    @staticmethod
    def _key(channel: str, addressee: str) -> tuple[str, str]:
        return (channel.strip().lower(), str(addressee).strip())

    def register(
        self, channel: str, addressee: str, tenant_id: str, *, org_id: str = "",
        outbound_token: str = "", secret: str = "", verify_token: str = "",
    ) -> None:
        self._map[self._key(channel, addressee)] = ChannelBinding(
            tenant_id, org_id, outbound_token, secret, verify_token=verify_token
        )

    def resolve(self, channel: str, addressee: str) -> ChannelBinding | None:
        return self._map.get(self._key(channel, addressee))

    @classmethod
    def from_env(cls, raw: str | None = None) -> ChannelRegistry:
        """Seed from ``CHANNEL_TENANT_MAP``.

        Entry format (comma-separated entries)::

            channel:addressee:tenant[:org[:secret]][;outbound_token=TOKEN]

        ``addressee`` is what selects the binding: the Telegram bot id (used in
        the webhook URL ``/v1/gateway/telegram/chat/<bot id>``, since updates do
        not carry it), the WhatsApp ``phone_number_id``, or a generic webhook's
        ``addressee``. ``secret`` is the binding's per-tenant inbound credential
        (it may contain ``:``); an entry without one is still registered but every
        inbound message for it is refused until a secret is configured.
        ``outbound_token`` (after ``;``, so a Telegram ``id:hash`` token parses)
        is used to send the reply back.
        """
        reg = cls()
        value = raw if raw is not None else os.getenv("CHANNEL_TENANT_MAP", "")
        if raw is None and (value or "").strip():
            import logging

            logging.getLogger(__name__).warning(
                "CHANNEL_TENANT_MAP is deprecated: move these bindings to tenant-managed "
                "/channels/bindings (Settings > Gateway); the env map is only a fallback "
                "for addressees no tenant has bound"
            )
        for entry in (value or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            head, *options = entry.split(";")
            opts: dict[str, str] = {}
            for option in options:
                key, sep, val = option.partition("=")
                if sep:
                    opts[key.strip().lower()] = val.strip()
            parts = head.strip().split(":")
            if len(parts) < 3 or not all(parts[:3]):
                continue
            reg.register(
                parts[0], parts[1], parts[2],
                org_id=parts[3] if len(parts) > 3 else "",
                secret=":".join(parts[4:]) if len(parts) > 4 else "",
                outbound_token=opts.get("outbound_token", ""),
            )
        return reg
