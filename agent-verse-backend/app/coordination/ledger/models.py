"""Typed immutable progress-ledger revision."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class LedgerRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    version: int = Field(gt=0)
    objective: str = Field(min_length=1, max_length=4_000)
    open_work: tuple[str, ...] = ()
    completed_work: tuple[str, ...] = ()
    claimed_completed_work: tuple[str, ...] = ()
    verified_facts: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    satisfaction_criteria: tuple[str, ...] = ()
    satisfied_criteria: tuple[str, ...] = ()
    last_action_signature: str = ""
    assignment_history: tuple[str, ...] = ()
    confidence: float = Field(default=0, ge=0, le=1)
    reset_count: int = Field(default=0, ge=0)
    predecessor_version: int | None = Field(default=None, ge=1)
    stall_evidence_references: tuple[str, ...] = ()
    idempotency_key: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = ["LedgerRevision"]
