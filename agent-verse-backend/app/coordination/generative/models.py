"""Generative persona, observation, reflection, and execution contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    persona_id: str
    tenant_id: str
    version: int = Field(gt=0)
    public_traits: tuple[str, ...]
    goals: tuple[str, ...]
    relationships: tuple[str, ...]
    behavioral_constraints: tuple[str, ...]
    memory_namespace: str
    authority_ceiling: frozenset[str]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str
    tenant_id: str
    persona_id: str
    occurred_at: datetime
    safe_summary: str = Field(max_length=2_000)
    importance: int = Field(ge=0, le=10_000)
    relevance: int = Field(ge=0, le=10_000)
    confidence: int = Field(ge=0, le=10_000)
    evidence_references: tuple[str, ...]
    classification: Literal["public", "internal", "confidential", "restricted"]
    expires_at: datetime
    idempotency_key: str
    quarantined: bool = False


class Reflection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reflection_id: str
    tenant_id: str
    persona_id: str
    safe_conclusion: str = Field(max_length=2_000)
    source_observation_ids: tuple[str, ...]
    evidence_references: tuple[str, ...]


class GenerativeState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    session_id: str
    execution_id: str
    phase: Literal[
        "initializing",
        "observing",
        "reflecting",
        "planning",
        "acting",
        "advancing_time",
        "completed",
        "failed",
        "cancelled",
    ] = "initializing"
    event_count: int = Field(default=0, ge=0)
    simulation_time: datetime
    safe_output: str | None = Field(default=None, max_length=8_000)
    terminal_reason: str | None = None
    checkpoint_version: int = 1


__all__ = ["GenerativeState", "Observation", "Persona", "Reflection"]
