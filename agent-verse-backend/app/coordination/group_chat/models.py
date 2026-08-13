"""Immutable group-chat execution state."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.group_chat.state_machine import GroupChatState


class GroupChatParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1)
    active: bool = True
    is_human: bool = False
    clearance: str = "internal"


class GroupChatExecutionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    state: GroupChatState = GroupChatState.CREATED
    participants: tuple[GroupChatParticipant, ...]
    round_number: int = Field(default=0, ge=0)
    token_count: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    last_sequence: int = Field(default=0, ge=0)
    selected_speaker_id: str | None = None
    terminal_reason: str | None = None
    deadline: datetime
    checkpoint_version: int = 1


__all__ = ["GroupChatExecutionState", "GroupChatParticipant"]
