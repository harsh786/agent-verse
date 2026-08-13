"""Immutable contracts shared by versioned strategy runtime components."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic_core import PydanticCustomError

SEMANTIC_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
)
SEMANTIC_VERSION_PATTERN = (
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    rf"(?:-{SEMANTIC_PRERELEASE_IDENTIFIER}"
    rf"(?:\.{SEMANTIC_PRERELEASE_IDENTIFIER})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
MACHINE_TOKEN_MAX_LENGTH = 64
FORBIDDEN_REASONING_KEYS = frozenset(
    {
        "chain_of_thought",
        "hidden_reasoning",
        "private_reasoning",
        "raw_thoughts",
    }
)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must be UTC-aware")
    return value


def _reject_surrounding_whitespace(value: object) -> object:
    if isinstance(value, str) and value != value.strip():
        raise ValueError("surrounding whitespace is forbidden")
    return value


def _require_actual_int(value: object) -> int:
    if type(value) is not int:
        raise PydanticCustomError("int_type", "Input should be a valid integer")
    return value


def _require_finite_builtin_number(value: object) -> int | float:
    if type(value) not in (int, float):
        raise PydanticCustomError("float_type", "Input should be a valid number")
    if type(value) is float and not math.isfinite(value):
        raise PydanticCustomError("finite_number", "Input should be a finite number")
    return cast("int | float", value)


def _require_safe_counter_value(value: object) -> int | float:
    numeric_value = _require_finite_builtin_number(value)
    if numeric_value < 0:
        raise ValueError("trace counter values must be nonnegative")
    return numeric_value


def _reject_private_reasoning_counter_key(value: str) -> str:
    normalized_value = value.casefold()
    if any(forbidden in normalized_value for forbidden in FORBIDDEN_REASONING_KEYS):
        raise ValueError("private reasoning counters are forbidden")
    return value


def _normalize_counter_mapping(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    if not all(isinstance(key, str) for key in value):
        raise ValueError("trace counter keys must be strings")
    return tuple(
        {"key": key, "value": item}
        for key, item in sorted(value.items())
    )


NonEmptyString = Annotated[str, Field(min_length=1)]
SemanticVersion = Annotated[
    str,
    BeforeValidator(_reject_surrounding_whitespace),
    Field(pattern=SEMANTIC_VERSION_PATTERN),
]
MachineToken = Annotated[
    str,
    BeforeValidator(_reject_surrounding_whitespace),
    Field(
        min_length=1,
        max_length=MACHINE_TOKEN_MAX_LENGTH,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    ),
]
PositiveInteger = Annotated[int, BeforeValidator(_require_actual_int), Field(gt=0)]
NonNegativeInteger = Annotated[int, BeforeValidator(_require_actual_int), Field(ge=0)]
PositiveFloat = Annotated[
    float,
    BeforeValidator(_require_finite_builtin_number),
    Field(gt=0, allow_inf_nan=False),
]
NonNegativeFloat = Annotated[
    float,
    BeforeValidator(_require_finite_builtin_number),
    Field(ge=0, allow_inf_nan=False),
]
UtcDateTime = Annotated[datetime, AfterValidator(_require_utc)]
MetadataKey = Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class StrategyFamily(StrEnum):
    REASONING = "reasoning"
    RAG = "rag"
    MULTI_AGENT = "multi_agent"
    SAFETY = "safety"
    MEMORY = "memory"
    OPTIMIZATION = "optimization"


class StrategyLifecycleState(StrEnum):
    PLANNED = "planned"
    PARTIAL = "partial"
    IMPLEMENTED = "implemented"
    CERTIFIED = "certified"
    DISABLED = "disabled"


class StrategyRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StrategyCostLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StrategyLatencyClass(StrEnum):
    REALTIME = "realtime"
    INTERACTIVE = "interactive"
    BATCH = "batch"


class ExecutionTerminalState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    LIMIT_EXCEEDED = "limit_exceeded"
    POLICY_DENIED = "policy_denied"


class ProbeStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"
    DEGRADED = "degraded"


class CertificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class EvidenceKind(StrEnum):
    CONTRACT_TEST = "contract_test"
    TEST_RESULT = "test_result"
    TRACE = "trace"
    METRIC = "metric"
    AUDIT_RECORD = "audit_record"


class ArtifactKind(StrEnum):
    REPORT = "report"
    DATASET = "dataset"
    CHECKPOINT = "checkpoint"
    OUTPUT = "output"


class CertificationKind(StrEnum):
    UNIT = "unit"
    INTEGRATION = "integration"
    RESTART = "restart"
    POLICY = "policy"
    SECURITY = "security"
    COST = "cost"
    LOAD = "load"
    CANARY = "canary"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class PatternLimits(_FrozenContract):
    calls: PositiveInteger
    nodes: NonNegativeInteger
    edges: NonNegativeInteger
    depth: PositiveInteger
    fan_out: PositiveInteger
    rounds: PositiveInteger
    tokens: PositiveInteger
    duration_seconds: PositiveInteger
    cost_usd: PositiveFloat


class EvidenceReference(_FrozenContract):
    kind: EvidenceKind
    reference: NonEmptyString
    digest: NonEmptyString


class ArtifactReference(_FrozenContract):
    kind: ArtifactKind
    reference: NonEmptyString
    media_type: NonEmptyString
    digest: NonEmptyString


class SafeTraceCounter(_FrozenContract):
    key: MetadataKey
    value: int | float

    @field_validator("key")
    @classmethod
    def reject_private_reasoning_key(cls, value: str) -> str:
        return _reject_private_reasoning_counter_key(value)

    @field_validator("value", mode="before")
    @classmethod
    def require_safe_value(cls, value: object) -> object:
        return _require_safe_counter_value(value)


TraceCounters = Annotated[
    tuple[SafeTraceCounter, ...],
    BeforeValidator(_normalize_counter_mapping),
]


class SafeTraceSummary(_FrozenContract):
    phase: MachineToken | None = None
    status: MachineToken | None = None
    terminal_state: ExecutionTerminalState | None = None
    reason_codes: tuple[MachineToken, ...] = ()
    limit_type: MachineToken | None = None
    selected_ids: tuple[MachineToken, ...] = ()
    rejected_ids: tuple[MachineToken, ...] = ()
    counts: TraceCounters = ()
    cost_usd: NonNegativeFloat | None = None
    duration_seconds: NonNegativeFloat | None = None
    checkpoint_version: PositiveInteger | None = None

    @field_validator("counts")
    @classmethod
    def require_unique_sorted_counters(
        cls,
        value: tuple[SafeTraceCounter, ...],
    ) -> tuple[SafeTraceCounter, ...]:
        keys = [counter.key for counter in value]
        if len(keys) != len(set(keys)):
            raise ValueError("trace counter keys must be unique")
        return tuple(sorted(value, key=lambda counter: counter.key))


class StrategySpec(_FrozenContract):
    strategy_id: NonEmptyString
    adapter_version: SemanticVersion
    family: StrategyFamily
    risk: StrategyRiskLevel
    cost: StrategyCostLevel
    latency: StrategyLatencyClass
    state_schema_version: PositiveInteger
    lifecycle_state: StrategyLifecycleState
    default_limits: PatternLimits
    dependencies: tuple[NonEmptyString, ...] = ()
    compatible_strategies: tuple[NonEmptyString, ...] = ()
    excluded_strategies: tuple[NonEmptyString, ...] = ()
    readiness_requirements: tuple[NonEmptyString, ...] = ()


class StrategyExecutionRequest(_FrozenContract):
    tenant_id: NonEmptyString
    goal_id: NonEmptyString
    strategy_id: NonEmptyString
    adapter_version: SemanticVersion = "1.0.0"
    state_schema_version: PositiveInteger = 1
    agent_id: NonEmptyString
    runtime_profile_ref: NonEmptyString
    context_snapshot_ref: NonEmptyString
    policy_ref: NonEmptyString
    budget_ref: NonEmptyString
    cancellation_token: NonEmptyString
    deadline: UtcDateTime
    idempotency_key: NonEmptyString


class StrategyExecutionResult(_FrozenContract):
    terminal_state: ExecutionTerminalState
    answer: str | None
    evidence: tuple[EvidenceReference, ...]
    artifacts: tuple[ArtifactReference, ...]
    cost_usd: NonNegativeFloat
    next_action: str | None
    safe_rationale_summary: NonEmptyString
    trace_summary: SafeTraceSummary


class CheckpointMigration(_FrozenContract):
    from_adapter_version: SemanticVersion
    to_adapter_version: SemanticVersion
    from_state_schema_version: PositiveInteger
    to_state_schema_version: PositiveInteger
    migration_id: NonEmptyString
    migrated_at: UtcDateTime


class StrategyCheckpoint(_FrozenContract):
    strategy_id: NonEmptyString
    adapter_version: SemanticVersion
    state_schema_version: PositiveInteger
    cursor: NonEmptyString
    state_ref: NonEmptyString
    migration: CheckpointMigration | None = None
    created_at: UtcDateTime


class ReadinessProbe(_FrozenContract):
    strategy_id: NonEmptyString
    adapter_version: SemanticVersion
    state_schema_version: PositiveInteger
    status: ProbeStatus
    checked_at: UtcDateTime
    static_evidence: Annotated[tuple[EvidenceReference, ...], Field(min_length=1)]
    operational_evidence: Annotated[tuple[EvidenceReference, ...], Field(min_length=1)]
    safe_rationale_summary: NonEmptyString


class CertificationEvidence(_FrozenContract):
    strategy_id: NonEmptyString
    adapter_version: SemanticVersion
    state_schema_version: PositiveInteger
    kind: CertificationKind
    status: CertificationStatus
    evidence: EvidenceReference
    recorded_at: UtcDateTime
    safe_rationale_summary: NonEmptyString
