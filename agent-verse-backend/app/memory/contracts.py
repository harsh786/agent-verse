"""Canonical evidence-backed memory and learning contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryKind = Literal[
    "execution",
    "reflexion",
    "long_term",
    "episodic",
    "procedural",
    "knowledge_graph",
    "prospective",
]
LifecycleState = Literal["active", "quarantined", "disputed", "expired", "deleted"]
Classification = Literal["public", "internal", "confidential", "restricted"]


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    tenant_id: str
    memory_kind: MemoryKind
    content_ref: str
    safe_summary: str = Field(max_length=4_000)
    source_goal_id: str
    source_execution_id: str
    evidence_refs: tuple[str, ...]
    classification: Classification
    confidence: int = Field(ge=0, le=10_000)
    lifecycle_state: LifecycleState
    version: int = Field(gt=0)
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    embedding: tuple[float, ...] | None = None
    outcome_score: int = Field(default=0, ge=-10_000, le=10_000)
    effectiveness_score: int = Field(default=0, ge=-10_000, le=10_000)
    recall_count: int = Field(default=0, ge=0)
    helpful_count: int = Field(default=0, ge=0)
    harmful_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    retention_policy_id: str = "default"
    idempotency_key: str

    @model_validator(mode="after")
    def embedding_matches_profile(self) -> MemoryRecord:
        if self.embedding_dimension != 1536 or self.embedding_model != "memory-embedding-v1":
            raise ValueError("memory embedding profile must be memory-embedding-v1/1536")
        if self.embedding is not None and len(self.embedding) != self.embedding_dimension:
            raise ValueError("memory embedding dimension mismatch")
        return self


class MemoryWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    memory_kind: MemoryKind
    content: str
    source_goal_id: str
    source_execution_id: str
    evidence_refs: tuple[str, ...]
    classification: Classification
    confidence: int = Field(ge=0, le=10_000)
    idempotency_key: str
    retention_policy_id: str


class MemoryRecallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    query: str
    memory_kinds: frozenset[MemoryKind]
    top_k: int = Field(gt=0, le=100)
    min_confidence: int = Field(ge=0, le=10_000)
    allowed_data_classes: frozenset[Classification]
    as_of: datetime
    token_budget: int = Field(gt=0)
    include_disputed: bool = False


class MemoryRecallHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: MemoryRecord
    semantic_score: int = Field(ge=0, le=10_000)
    recency_score: int = Field(ge=0, le=10_000)
    outcome_score: int = Field(ge=-10_000, le=10_000)
    effectiveness_score: int = Field(ge=-10_000, le=10_000)
    final_score: int
    applicability_reason: str
    provenance_status: Literal["verified", "missing", "disputed"]


class MemoryFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    tenant_id: str
    execution_id: str
    was_used: bool
    was_helpful: bool
    was_harmful: bool
    outcome_score: int = Field(ge=-10_000, le=10_000)
    feedback_reason: str
    recorded_at: datetime

    @model_validator(mode="after")
    def helpful_and_harmful_are_exclusive(self) -> MemoryFeedback:
        if self.was_helpful and self.was_harmful:
            raise ValueError("memory feedback cannot be both helpful and harmful")
        return self


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    tenant_id: str
    agent_id: str
    kind: Literal["prompt", "model", "rag"]
    target_key: str
    control_version: str
    candidate_version: str
    assignment_seed: str
    traffic_percent: int = Field(ge=0, le=100)
    primary_metric: str
    guardrail_metrics: tuple[str, ...]
    min_samples_per_arm: int = Field(gt=0)
    confidence_threshold: float = Field(gt=0, le=1)
    status: Literal["draft", "running", "paused", "promoted", "rolled_back", "completed"]
    kill_switch: bool = False


class ImprovementActionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    tenant_id: str
    goal_id: str
    action_type: Literal[
        "store_reflexion_lesson",
        "update_prompt_variant",
        "update_model_routing",
        "update_rag_strategy",
        "blacklist_tool_pattern",
        "create_regression_case",
        "update_prompt",
        "change_model",
        "change_rag",
        "adjust_limit",
        "publish_skill",
        "rollback",
    ]
    payload: dict[str, Any]
    state: Literal["pending", "running", "completed", "failed", "cancelled"]
    idempotency_key: str
    attempts: int = Field(ge=0)
    result: dict[str, Any] | None = None
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


__all__ = [
    "Classification",
    "ExperimentSpec",
    "ImprovementActionRecord",
    "LifecycleState",
    "MemoryFeedback",
    "MemoryKind",
    "MemoryRecallHit",
    "MemoryRecallRequest",
    "MemoryRecord",
    "MemoryWriteRequest",
]
