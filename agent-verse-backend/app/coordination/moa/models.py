"""Immutable model identity, layer, proposal, and admission contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    provider_id: str
    model_family: str
    deployment_id: str
    region: str
    failure_domain: str
    healthy: bool
    context_limit: int = Field(gt=0)
    estimated_cost_usd: float = Field(ge=0)
    estimated_latency_ms: int = Field(ge=0)
    capabilities: frozenset[str] = frozenset()


class MoALayer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layer_id: str
    tenant_id: str
    session_id: str
    strategy_execution_id: str
    layer_index: int = Field(ge=0)
    aggregator_deployment_id: str
    quorum: int = Field(gt=0)
    deployment_ids: tuple[str, ...] = ()
    aggregate_reference: str | None = None
    idempotency_key: str


class MoAProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    tenant_id: str
    session_id: str
    strategy_execution_id: str
    layer_index: int = Field(ge=0)
    participant_id: str
    provider_id: str
    model_family: str
    deployment_id: str
    region: str
    failure_domain: str
    proposal_reference: str
    safe_excerpt: str = Field(max_length=2_000)
    evidence_references: tuple[str, ...] = ()
    predecessor_proposal_ids: tuple[str, ...] = ()
    valid: bool
    rejection_reason: str | None = None
    tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    quality_score: int = Field(default=0, ge=0, le=10_000)
    attempt: int = Field(gt=0)
    idempotency_key: str


class MoAPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layers: tuple[MoALayer, ...]
    worst_case_cost_usd: float
    worst_case_latency_ms: int
    replacement_waves: int = Field(ge=0, le=1)


__all__ = ["MoALayer", "MoAPlan", "MoAProposal", "ModelCandidate"]
