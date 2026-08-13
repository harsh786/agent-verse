"""Typed handoff commands and immutable state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.coordination.contracts import Classification


class HandoffState(StrEnum):
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HandoffRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    civilization_id: str = Field(min_length=1)
    source_agent_id: str = Field(min_length=1)
    target_agent_id: str = Field(min_length=1)
    task_summary: str = Field(min_length=1, max_length=2_000)
    context_message_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    connector_allowlist: frozenset[str] = frozenset()
    classification: Classification = Classification.INTERNAL
    remaining_budget_usd: float = Field(ge=0)
    deadline: datetime
    acceptance_token_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: HandoffState = HandoffState.REQUESTED
    result_reference: str | None = None
    version: int = Field(default=1, gt=0)
    idempotency_key: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_members_and_time(self) -> HandoffRecord:
        if self.source_agent_id == self.target_agent_id:
            raise ValueError("handoff source and target must differ")
        if self.deadline <= self.created_at:
            raise ValueError("handoff deadline must follow creation")
        return self


class HandoffTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str
    from_state: HandoffState
    to_state: HandoffState
    version: int
    idempotency_key: str
    event_type: str
    occurred_at: datetime


__all__ = ["HandoffRecord", "HandoffState", "HandoffTransition"]
