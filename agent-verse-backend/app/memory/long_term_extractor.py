"""Typed evidence-only long-term memory extraction."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LongTermCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    safe_summary: str = Field(max_length=2_000)
    evidence_refs: tuple[str, ...]
    confidence: int = Field(ge=0, le=10_000)
    classification: str
    contradiction_keys: tuple[str, ...] = ()


def validate_extraction(candidate: LongTermCandidate) -> LongTermCandidate:
    if not candidate.evidence_refs:
        raise ValueError("long-term memory requires evidence")
    if candidate.confidence < 5_000:
        raise ValueError("long-term memory confidence below threshold")
    return candidate


__all__ = ["LongTermCandidate", "validate_extraction"]
