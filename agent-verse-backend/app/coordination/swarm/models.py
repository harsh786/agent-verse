"""Typed swarm gossip and fenced claim contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GossipMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    tenant_id: str
    civilization_id: str
    origin_agent_id: str
    origin_credential: str
    message_type: Literal["advertisement", "claim", "heartbeat", "result"]
    payload_digest: str
    expires_at: datetime
    hops_remaining: int = Field(ge=0, le=16)


class SwarmClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    work_item_id: str
    owner_agent_id: str
    fencing_token: int = Field(gt=0)
    lease_expires_at: datetime
    attempt: int = Field(gt=0)
    state: Literal["claimed", "completed"] = "claimed"
    result_reference: str | None = None


__all__ = ["GossipMessage", "SwarmClaim"]
