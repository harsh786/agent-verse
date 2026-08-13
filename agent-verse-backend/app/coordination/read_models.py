"""Stable, safe read models for the public coordination API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CoordinationSessionRead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    next_sequence: int = Field(ge=1)
    version: int = Field(ge=1)


class CoordinationTransitionRead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    state: str = Field(min_length=1)
    version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)


__all__ = ["CoordinationSessionRead", "CoordinationTransitionRead"]
