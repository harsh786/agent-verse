"""Base ChannelAdapter — abstract interface for all channel adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.gateway.command import OrgCommand, OrgResponse


class ChannelAdapter(ABC):
    """Abstract base for all channel adapters."""

    channel_name: str = "base"

    @abstractmethod
    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str
    ) -> OrgCommand:
        """Convert channel-specific payload to a normalized OrgCommand."""

    @abstractmethod
    def format_response(self, response: OrgResponse) -> Any:
        """Convert OrgResponse to channel-specific format."""

    async def verify_auth(
        self,
        request_headers: dict[str, str],
        raw_payload: dict[str, Any],
        raw_body: bytes | None = None,
    ) -> bool:
        """Verify channel-specific authentication. Override per channel.

        ``raw_body`` is the untouched request body bytes, for adapters whose
        signature scheme (e.g. HMAC-SHA256 over the raw payload, as Slack/
        WhatsApp/generic-webhook all use) must verify against the exact bytes
        the caller signed — re-serializing the parsed ``raw_payload`` dict is
        not guaranteed byte-identical to the original and can silently
        reject genuine, correctly-signed requests.
        """
        return True
