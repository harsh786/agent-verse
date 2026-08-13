"""Magentic progress and stall decision contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProgressAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    progressed: bool
    accepted_kinds: tuple[str, ...] = ()
    rejected_reasons: tuple[str, ...] = ()


class StallAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stalled: bool
    reasons: tuple[str, ...] = ()
    consecutive_no_progress: int = Field(ge=0)
    repeated_action_count: int = Field(ge=0)
    unchanged_blocker_count: int = Field(ge=0)


class ParticipantCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    capabilities: frozenset[str]
    available: bool
    policy_eligible: bool
    current_load: int = Field(ge=0)
    estimated_latency_ms: int = Field(ge=0)
    recent_no_progress_assignments: int = Field(default=0, ge=0)


class ParticipantDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_agent_id: str
    rejected: tuple[tuple[str, str], ...]


class MagenticState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    session_id: str
    execution_id: str
    phase: Literal[
        "initializing",
        "planning",
        "executing",
        "assessing",
        "replanning",
        "awaiting_human",
        "synthesizing",
        "completed",
        "failed",
        "cancelled",
    ] = "initializing"
    ledger_version: int = 0
    round_number: int = 0
    reset_count: int = 0
    selected_agent_id: str | None = None
    human_review_token_digest: str | None = None
    safe_output: str | None = Field(default=None, max_length=8_000)
    terminal_reason: str | None = None
    checkpoint_version: int = 1


__all__ = [
    "MagenticState",
    "ParticipantCandidate",
    "ParticipantDecision",
    "ProgressAssessment",
    "StallAssessment",
]
