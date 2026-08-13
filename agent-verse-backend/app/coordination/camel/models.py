"""Versioned CAMEL role and inception contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RoleContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role_name: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=2_000)
    responsibilities: tuple[str, ...] = Field(min_length=1)
    prohibited_actions: tuple[str, ...] = Field(min_length=1)
    tool_allowlist: frozenset[str] = frozenset()
    connector_allowlist: frozenset[str] = frozenset()
    data_scopes: frozenset[str] = frozenset()
    authority_ceiling: frozenset[str] = frozenset()
    communication_schema: str = Field(min_length=1)
    termination_conditions: tuple[str, ...] = Field(min_length=1)
    version: int = Field(gt=0)

    @field_validator("objective", "responsibilities", "prohibited_actions")
    @classmethod
    def reject_policy_injection(cls, value: object) -> object:
        strings = (value,) if isinstance(value, str) else tuple(value)  # type: ignore[arg-type]
        unsafe = ("ignore previous", "override platform", "reveal secret", "change policy")
        if any(any(marker in str(item).lower() for marker in unsafe[:3]) for item in strings):
            raise ValueError("role contract contains policy-override language")
        return value


class InceptionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_digest: str
    role_names: tuple[str, ...]
    safe_summary: str
    communication_schema: str
    version: int = 1


class CamelState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    session_id: str
    execution_id: str
    phase: Literal[
        "validating_roles",
        "inception",
        "dialoguing",
        "evaluating_termination",
        "awaiting_human",
        "completed",
        "failed",
        "cancelled",
    ] = "validating_roles"
    contract_digest: str | None = None
    turn_count: int = Field(default=0, ge=0)
    token_count: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    last_normalized_utterance: str = ""
    repeated_utterances: int = Field(default=0, ge=0)
    safe_output: str | None = Field(default=None, max_length=8_000)
    terminal_reason: str | None = None
    checkpoint_version: int = 1


__all__ = ["CamelState", "InceptionArtifact", "RoleContract"]
