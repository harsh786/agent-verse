"""GoalRuntimeProfile — the unified execution contract for every goal.

Every strategy selector populates one section of this profile.
The profile is serialized into goal.execution_context and emitted
as a runtime_profile_selected SSE event.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel

from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_contracts import PatternLimits
from app.rag.contracts import RAGStrategy


class Complexity(str, enum.Enum):  # noqa: UP042
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EXPERT = "expert"


class RiskLevel(str, enum.Enum):  # noqa: UP042
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Domain(str, enum.Enum):  # noqa: UP042
    TECHNICAL = "technical"
    CREATIVE = "creative"
    ANALYTICAL = "analytical"
    OPERATIONAL = "operational"
    CONVERSATIONAL = "conversational"


class TimeSensitivity(str, enum.Enum):  # noqa: UP042
    REALTIME = "realtime"
    NORMAL = "normal"
    BATCH = "batch"


class KnowledgeState(str, enum.Enum):  # noqa: UP042
    EMPTY = "empty"
    SPARSE = "sparse"
    HEALTHY = "healthy"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass
class GoalProperties:
    """Classified properties of the incoming goal."""

    raw_goal: str
    complexity: Complexity = Complexity.SIMPLE
    domain: Domain = Domain.OPERATIONAL
    risk: RiskLevel = RiskLevel.LOW
    time_sensitivity: TimeSensitivity = TimeSensitivity.NORMAL
    kb_state: KnowledgeState = KnowledgeState.UNKNOWN
    reversibility: Literal["reversible", "irreversible"] = "reversible"
    multi_step: bool = True
    is_generative: bool = False
    requires_web: bool = False
    requires_code: bool = False
    requires_vision: bool = False
    estimated_steps: int = 3
    classifier_confidence: float = 0.8

    @property
    def web_fallback_required(self) -> bool:
        return self.kb_state in (KnowledgeState.EMPTY, KnowledgeState.SPARSE)


@dataclass
class AgentPatternConfig:
    reasoning: list[str] = field(default_factory=lambda: ["react"])
    rag: list[str] = field(default_factory=lambda: [RAGStrategy.HYBRID.value])
    multi_agent: list[str] = field(default_factory=lambda: ["single_agent"])
    safety: list[str] = field(default_factory=lambda: ["guardrails"])
    max_iterations: int = 15
    persistence_mode: bool = False
    max_persistence_attempts: int = 3
    autonomy_mode: str = "bounded-autonomous"
    selection_reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class RAGStrategyConfig:
    strategy: str = RAGStrategy.HYBRID.value
    sources: list[str] = field(default_factory=lambda: ["knowledge_base"])
    chunking_strategy: str = "semantic"
    embedding_model: str = "default"
    reranker: str = "score"
    max_context_tokens: int = 6000
    min_relevance_score: float = 0.35
    max_chunks_per_source: int = 5
    citation_required: bool = True
    deduplication_enabled: bool = True
    web_fallback_enabled: bool = False
    graph_strategy: str = "none"


@dataclass
class ModelPlanConfig:
    planner: str = "default"
    executor: str = "default"
    verifier: str = "default"
    embedder: str = "default"
    classifier: str = "default"
    cost_class: str = "medium"
    latency_class: str = "interactive"
    # Maximum cost budget for this goal execution (USD); used by ModelScorer.score_cost
    max_cost_usd: float = 0.10


@dataclass
class SecurityConfig:
    guardrail_bundle: str = "default"
    governance_bundle: str = "free"
    hitl_required: bool = False
    consensus_required: bool = False
    rollback_required: bool = False
    audit_level: str = "standard"
    compliance_tags: list[str] = field(default_factory=list)
    sandbox_required: bool = False
    identity_scope: str = "tenant"


@dataclass
class MemoryCacheConfig:
    use_session_memory: bool = True
    use_execution_memory: bool = True
    use_long_term_memory: bool = False
    use_semantic_cache: bool = False
    use_knowledge_graph: bool = False
    reflexion_enabled: bool = False
    cache_ttl_seconds: int = 3600


@dataclass
class EvalConfig:
    enabled: bool = True
    eval_suite: str = "default"
    score_threshold: float = 0.72
    reflexion_enabled: bool = True
    prompt_ab_test_enabled: bool = False
    model_ab_test_enabled: bool = False
    creates_regression_case_on_failure: bool = True


def default_pattern_limits() -> PatternLimits:
    return PatternLimits(
        calls=100,
        nodes=100,
        edges=200,
        depth=10,
        fan_out=10,
        rounds=25,
        tokens=100_000,
        duration_seconds=3600,
        cost_usd=10.0,
    )


@dataclass(frozen=True, slots=True)
class StrategySelection:
    strategy_id: str
    adapter_version: str

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.adapter_version:
            raise ValueError("strategy selection fields cannot be empty")


@dataclass(frozen=True, slots=True)
class StrategyRejection:
    strategy_id: str
    reason_code: str

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.reason_code:
            raise ValueError("strategy rejection fields cannot be empty")


@dataclass(frozen=True)
class GoalRuntimeProfile:
    """Complete execution profile assembled before graph execution starts."""

    goal_id: str
    tenant_id: str
    properties: GoalProperties
    agent_patterns: AgentPatternConfig
    rag_strategy: RAGStrategyConfig
    model_plan: ModelPlanConfig
    security: SecurityConfig
    memory_cache: MemoryCacheConfig
    eval_config: EvalConfig
    profile_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    assembly_latency_ms: float = 0.0
    feature_flag_active: bool = True
    # C5 fix: actual tenant plan so GuardrailEnforcer doesn't hardcode PROFESSIONAL
    tenant_plan: str = "professional"
    profile_version: int = 2
    registry_revision: str = "legacy-unversioned"
    primary_strategy: StrategySelection = field(
        default_factory=lambda: StrategySelection("react", "1.0.0")
    )
    auxiliary_strategies: tuple[StrategySelection, ...] = ()
    execution_tier: ExecutionTier = ExecutionTier.LOCAL
    effective_limits: PatternLimits = field(default_factory=default_pattern_limits)
    tenant_limit_ceiling: PatternLimits | None = None
    readiness_snapshot_ref: str = "legacy-unverified"
    policy_snapshot_ref: str = "legacy-unversioned"
    budget_snapshot_ref: str = "legacy-unversioned"
    deadline: datetime | None = None
    selected_alternatives: tuple[StrategySelection, ...] = ()
    rejected_alternatives: tuple[StrategyRejection, ...] = ()
    model_role_assignments: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.profile_version != 2:
            raise ValueError("profile_version must be 2")
        if self.deadline is not None:
            if self.deadline.tzinfo is None or self.deadline.utcoffset() != UTC.utcoffset(
                self.deadline
            ):
                raise ValueError("deadline must be UTC-aware")
            if self.deadline <= datetime.now(UTC):
                raise ValueError("deadline must be in the future")
        auxiliary_ids = [item.strategy_id for item in self.auxiliary_strategies]
        if self.primary_strategy.strategy_id in auxiliary_ids:
            raise ValueError("primary strategy cannot be auxiliary")
        if len(auxiliary_ids) != len(set(auxiliary_ids)):
            raise ValueError("auxiliary strategies must be unique")
        if len(dict(self.model_role_assignments)) != len(self.model_role_assignments):
            raise ValueError("model role assignments must be unique")
        if self.tenant_limit_ceiling is not None:
            for field_name in PatternLimits.model_fields:
                effective = getattr(self.effective_limits, field_name)
                ceiling = getattr(self.tenant_limit_ceiling, field_name)
                if effective > ceiling:
                    raise ValueError(f"effective limit exceeds tenant ceiling: {field_name}")

    def to_dict(self) -> dict[str, Any]:
        import dataclasses

        def _convert(obj: Any) -> Any:
            if isinstance(obj, enum.Enum):
                return obj.value
            if dataclasses.is_dataclass(obj):
                values = dataclasses.asdict(obj)  # type: ignore[arg-type]
                return {k: _convert(v) for k, v in values.items()}
            if isinstance(obj, BaseModel):
                return _convert(obj.model_dump(mode="json"))
            if isinstance(obj, list):
                return [_convert(i) for i in obj]
            if isinstance(obj, tuple):
                return [_convert(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            return obj

        return cast(dict[str, Any], _convert(self))


# ── Named spec profile classes (spec §3.1-3.5 requirements) ──────────────────


class _ToDictMixin:
    """Shared serialization mixin for named spec profile dataclasses.
    Converts enum values to strings (unlike plain dataclasses.asdict).
    """

    def to_dict(self) -> dict[str, Any]:
        import dataclasses as _dc

        def _coerce(obj: Any) -> Any:
            if isinstance(obj, enum.Enum):
                return obj.value
            if isinstance(obj, dict):
                return {k: _coerce(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_coerce(i) for i in obj]
            return obj

        return cast(
            dict[str, Any],
            _coerce(_dc.asdict(self)),  # type: ignore[call-overload]
        )


@dataclass
class MultimodalRuntimeProfile(_ToDictMixin):
    """Runtime profile for multimodal content ingestion (spec §3.1)."""

    content_type: str = "text"
    parser: str = "text_parser"
    chunking_strategy: str = "semantic"
    embedding_model: str = "text-embedding-3-small"
    model_roles: dict[str, str] = field(default_factory=dict)
    provenance_required: bool = True


@dataclass
class SelfImprovementProfile(_ToDictMixin):
    """Self-improvement runtime profile (spec §3.2)."""

    enabled: bool = True
    eval_suite: str = "default"
    score_threshold: float = 0.72
    reflexion_enabled: bool = True
    prompt_ab_test_enabled: bool = False
    model_ab_test_enabled: bool = False
    creates_regression_case_on_failure: bool = True


@dataclass
class ContextRuntimeProfile(_ToDictMixin):
    """Context quality runtime profile (spec §3.3)."""

    reranker: str = "score"
    max_context_tokens: int = 6000
    min_relevance_score: float = 0.35
    max_chunks_per_source: int = 5
    citation_required: bool = True
    deduplication_enabled: bool = True


@dataclass
class KnowledgeRuntimeProfile(_ToDictMixin):
    """Knowledge graph + KB runtime profile (spec §3.5)."""

    kb_state: str = "unknown"
    graph_state: str = "unknown"
    selected_collections: list[str] = field(default_factory=list)
    graph_strategy: str = "none"
    web_fallback_required: bool = False
    citation_required: bool = True

    def __post_init__(self) -> None:
        if self.kb_state == "empty":
            self.web_fallback_required = True


@dataclass
class SecurityRuntimeProfile(_ToDictMixin):
    """Security runtime profile with full spec §3.4 contract."""

    guardrail_bundle: str = "default"
    governance_bundle: str = "free"
    identity_scope: str = "tenant"
    hitl_required: bool = False
    consensus_required: bool = False
    rollback_required: bool = False
    audit_level: str = "standard"
    compliance_tags: list[str] = field(default_factory=list)
