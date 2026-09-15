"""Voice phone-number → tenant registry (Phase 8 live wiring).

A Twilio/Vonage voice webhook is unauthenticated at the tenant layer and carries
no API key — the tenant is identified by the *called* number (the ``To`` field of
the inbound call). This registry maps each provisioned phone number to the tenant
(and org) that owns it, so an inbound call resolves to the right tenant before any
tenant-scoped work happens.

In-memory by default (seedable from ``VOICE_PHONE_NUMBERS`` env as
``number:tenant_id[:org_id]`` comma-separated); a durable table can back the same
interface later. Numbers are normalised to bare digits + leading ``+`` so
formatting differences (spaces, dashes) still match.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceNumberBinding:
    tenant_id: str
    org_id: str = ""


def _normalize(number: str) -> str:
    n = re.sub(r"[^\d+]", "", number or "")
    return n


class VoicePhoneRegistry:
    def __init__(self) -> None:
        self._numbers: dict[str, VoiceNumberBinding] = {}

    def register(self, number: str, tenant_id: str, org_id: str = "") -> None:
        self._numbers[_normalize(number)] = VoiceNumberBinding(tenant_id, org_id)

    def unregister(self, number: str) -> bool:
        return self._numbers.pop(_normalize(number), None) is not None

    def resolve(self, number: str) -> VoiceNumberBinding | None:
        return self._numbers.get(_normalize(number))

    @classmethod
    def from_env(cls, raw: str | None = None) -> VoicePhoneRegistry:
        """Build a registry seeded from ``VOICE_PHONE_NUMBERS``.

        Format: ``+15550001111:tenant_a,+15550002222:tenant_b:org_x``.
        """
        reg = cls()
        value = raw if raw is not None else os.getenv("VOICE_PHONE_NUMBERS", "")
        for entry in (value or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            if len(parts) < 2 or not parts[0] or not parts[1]:
                continue
            number, tenant_id = parts[0], parts[1]
            org_id = parts[2] if len(parts) > 2 else ""
            reg.register(number, tenant_id, org_id)
        return reg
