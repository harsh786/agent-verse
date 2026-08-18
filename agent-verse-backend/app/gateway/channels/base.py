"""Base ChannelAdapter — abstract interface for all channel adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.gateway.command import OrgCommand, OrgResponse


class ChannelAdapter(ABC):
    """Abstract base for all channel adapters."""

    channel_name: str = "base"

    @abstractmethod
    async def normalize(self, raw_payload: dict[str, Any], tenant_id: str, org_id: str) -> OrgCommand:
        """Convert channel-specific payload to a normalized OrgCommand."""

    @abstractmethod
    def format_response(self, response: OrgResponse) -> Any:
        """Convert OrgResponse to channel-specific format."""

    async def verify_auth(self, request_headers: dict[str, str], raw_payload: dict[str, Any]) -> bool:
        """Verify channel-specific authentication. Override per channel."""
        return True
