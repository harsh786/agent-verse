"""Immutable public contracts for the durable coordination runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Classification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class AuthorizationContext(Contract):
    actor_id: str = Field(min_length=1)
    permissions: frozenset[str]
    policy_version: str = "current"


class EventPayload(Contract):
    kind: str = Field(min_length=1)
    data: dict[str, Any]


class CoordinationEvent(Contract):
    event_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(gt=0)
    schema_version: int = Field(default=1, gt=0)
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    idempotency_key: str = Field(min_length=1)
    classification: Classification
    payload: EventPayload
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_times(self) -> CoordinationEvent:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware UTC")
        if self.occurred_at.utcoffset() != UTC.utcoffset(self.occurred_at):
            raise ValueError("occurred_at must use UTC")
        if self.expires_at is not None and self.expires_at <= self.occurred_at:
            raise ValueError("expires_at must follow occurred_at")
        return self


class Participant(Contract):
    agent_id: str
    civilization_id: str
    role: str


class SessionSnapshot(Contract):
    session_id: str
    tenant_id: str
    civilization_id: str
    goal_id: str
    state: str = "pending"
    participants: tuple[Participant, ...] = ()
    policy_snapshot: dict[str, Any]
    budget_snapshot: dict[str, Any]
    deadline: datetime | None = None
    version: int = Field(default=1, gt=0)


class ContextMessage(Contract):
    message_id: str
    session_id: str
    sender_agent_id: str
    recipient_agent_ids: tuple[str, ...] = ()
    message_type: str
    safe_content: str | None = None
    artifact_reference: str | None = None
    classification: Classification
    idempotency_key: str

    @model_validator(mode="after")
    def require_one_content_source(self) -> ContextMessage:
        if (self.safe_content is None) == (self.artifact_reference is None):
            raise ValueError("provide exactly one safe content or artifact reference")
        return self


class WorkItem(Contract):
    work_item_id: str
    session_id: str
    safe_summary: str
    dependencies: tuple[str, ...] = ()
    state: str = "pending"
    owner_agent_id: str | None = None
    version: int = Field(default=1, gt=0)


class StrategyCheckpoint(Contract):
    checkpoint_id: str
    session_id: str
    execution_id: str
    adapter_id: str
    adapter_version: str
    state_schema_version: int = Field(gt=0)
    sequence: int = Field(gt=0)
    state_reference: str


class HandoffCommand(Contract):
    command_type: Literal["handoff"] = "handoff"
    tenant_id: str
    session_id: str
    source_agent_id: str
    target_agent_id: str
    source_civilization_id: str
    target_civilization_id: str
    idempotency_key: str
    expires_at: datetime
    authorization: AuthorizationContext


class StateCommand(Contract):
    tenant_id: str
    session_id: str
    expected_version: int = Field(gt=0)
    idempotency_key: str
    authorization: AuthorizationContext
