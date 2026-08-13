"""Safe, ordered transcript contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.coordination.contracts import Classification


class TranscriptMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(gt=0)
    sender_agent_id: str = Field(min_length=1)
    recipient_agent_ids: tuple[str, ...] = ()
    message_type: Literal["message", "decision", "question", "evidence", "summary", "human"]
    safe_content: str | None = Field(default=None, max_length=16_000)
    artifact_reference: str | None = None
    classification: Classification
    trust_label: Literal["trusted", "untrusted", "quarantined"]
    provenance_chain: tuple[str, ...]
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    clearance_decision: Literal["allowed", "redacted", "denied"]
    compacts_from_sequence: int | None = Field(default=None, gt=0)
    compacts_to_sequence: int | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_content_and_interval(self) -> TranscriptMessage:
        if (self.safe_content is None) == (self.artifact_reference is None):
            raise ValueError("exactly one safe content or artifact reference is required")
        interval = (self.compacts_from_sequence, self.compacts_to_sequence)
        if (interval[0] is None) != (interval[1] is None):
            raise ValueError("compaction interval requires both bounds")
        if interval[0] is not None and interval[1] is not None and interval[0] > interval[1]:
            raise ValueError("compaction interval is reversed")
        if self.message_type == "summary" and interval[0] is None:
            raise ValueError("summary requires a compaction interval")
        return self


__all__ = ["TranscriptMessage"]
