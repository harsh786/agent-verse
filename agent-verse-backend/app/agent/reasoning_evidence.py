"""Privacy-safe, bounded evidence emitted by reasoning strategies."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReasoningEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    adapter_version: str = "1.0.0"
    status: Literal["completed", "degraded", "rejected", "exhausted"]
    call_count: int = Field(ge=0)
    valid_samples: int = Field(default=0, ge=0)
    invalid_samples: int = Field(default=0, ge=0)
    quorum: int = Field(default=0, ge=0)
    selected_ids: tuple[str, ...] = ()
    pruned_ids: tuple[str, ...] = ()
    scores: tuple[float, ...] = ()
    critique_categories: tuple[str, ...] = ()
    approved: bool | None = None
    limit_reason: str | None = None
    checkpoint_cursor: dict[str, int] = Field(default_factory=dict)
    safe_rationale_summary: str = Field(default="", max_length=240)


class ReasoningExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    result: Any
    evidence: ReasoningEvidence


def opaque_evidence_id(value: str) -> str:
    """Identify a private candidate without retaining its content."""
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def critique_categories(value: str) -> tuple[str, ...]:
    """Map free-form critique to bounded categories without copying its text."""
    lowered = value.casefold()
    categories = []
    for category, needles in (
        ("accuracy", ("accur", "incorrect", "wrong", "fact")),
        ("completeness", ("complete", "missing", "partial")),
        ("safety", ("unsafe", "risk", "policy")),
        ("clarity", ("clear", "confus", "ambiguous")),
        ("evidence", ("citation", "source", "evidence")),
    ):
        if any(needle in lowered for needle in needles):
            categories.append(category)
    return tuple(categories or ("quality",))


__all__ = [
    "ReasoningEvidence",
    "ReasoningExecution",
    "critique_categories",
    "opaque_evidence_id",
]
