"""OrgCommand and OrgResponse — channel-agnostic normalized command objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class CommandFile:
    filename: str
    content_type: str
    data: bytes | None = None
    url: str | None = None


@dataclass
class ResponseAction:
    action_id: str
    label: str
    action_type: str  # "approve" | "reject" | "view" | "ask"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactRef:
    artifact_id: str
    title: str
    kind: str
    url: str | None = None


@dataclass
class OrgCommand:
    """Normalized command from any channel."""
    command_id: str
    tenant_id: str
    org_id: str

    # What was said
    text: str
    intent: str | None = None  # classified by router

    # Who said it
    actor_id: str = ""
    actor_name: str | None = None
    actor_channel: str = "rest"   # rest|telegram|slack|whatsapp|discord|email|mcp|a2a|teams

    # Context
    conversation_id: str | None = None
    reply_to_command_id: str | None = None

    # Attachments
    files: list[CommandFile] = field(default_factory=list)

    # Routing hints
    explicit_org_id: str | None = None
    urgency: str = "normal"   # urgent|normal|background

    # Channel metadata
    raw_payload: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class OrgResponse:
    """Response sent back to the originating channel."""
    command_id: str
    text: str
    formatted: dict[str, Any] | None = None   # channel-specific format
    actions: list[ResponseAction] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    mission_id: str | None = None
    requires_action: bool = False
    voice_text: str | None = None
    processing_ms: int = 0
    status: str = "complete"   # complete|processing|error
