"""StrategyRegistry — canonical catalogue of every orchestration strategy.
Every pattern from the architecture docs must be registered here with its state.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from app.orchestration.strategy_adapters import (
    AdapterDescriptor,
    ExecutionTier,
    code_reasoning_descriptor,
    core_execution_descriptor,
    durable_coordination_descriptor,
    local_agent_graph_descriptor,
    local_reasoning_descriptor,
    rag_adapter_descriptor,
    workflow_adapter_descriptor,
)
from app.orchestration.strategy_contracts import (
    PatternLimits,
    StrategyCostLevel,
    StrategyFamily,
    StrategyLatencyClass,
    StrategyLifecycleState,
    StrategyRiskLevel,
    StrategySpec,
)
from app.rag.catalogue import (
    RAG_CAPABILITY_CATALOGUE,
    RAG_RUNTIME_CAPABILITIES,
    RAGRuntimeDependency,
    RAGRuntimeReadiness,
)
from app.rag.contracts import (
    RAGRuntimeAdapter,
    RAGStrategy,
    RAGStrategyTrace,
    is_rag_runtime_adapter,
)


class StrategyState(str, enum.Enum):  # noqa: UP042
    IMPLEMENTED = "implemented"
    CERTIFIED = "certified"
    PARTIAL = "partial"
    PLANNED = "planned"
    DISABLED = "disabled"


class StrategyCategory(str, enum.Enum):  # noqa: UP042
    AGENT = "agent_patterns"
    RAG = "rag_patterns"
    SAFETY = "safety_patterns"
    MEMORY = "memory_patterns"
    OPTIMISATION = "optimisation_patterns"


@dataclass
class StrategyCapability:
    strategy_id: str
    category: StrategyCategory
    state: StrategyState
    adapter_path: str = ""
    description: str = ""
    required_deps: list[str] | tuple[str, ...] = field(default_factory=list)
    optional_deps: list[str] | tuple[str, ...] = field(default_factory=list)
    cost_class: str = "medium"
    latency_class: str = "interactive"
    risk_class: str = "low"
    compatible_goal_properties: Mapping[str, Any] = field(default_factory=dict)
    runtime_adapter: type[RAGRuntimeAdapter] | None = field(
        default=None,
        repr=False,
    )
    adapter_descriptor: AdapterDescriptor | None = field(default=None, repr=False)
    execution_tier: ExecutionTier = ExecutionTier.CROSS_CUTTING
    bundle_components: tuple[str, ...] = ()
    canonical_owner_id: str = ""
    _spec: StrategySpec | None = field(default=None, init=False, repr=False)
    _sealed: bool = field(default=False, init=False, repr=False)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError(f"Strategy capability is sealed: {self.strategy_id}")
        object.__setattr__(self, name, value)

    def _seal(self) -> None:
        object.__setattr__(self, "_sealed", True)

    @property
    def spec(self) -> StrategySpec:
        if self._spec is None:
            raise RuntimeError(f"Strategy capability is not finalized: {self.strategy_id}")
        return self._spec

    @property
    def default_limits(self) -> PatternLimits:
        return self.spec.default_limits

    @property
    def adapter_version(self) -> str:
        return self.spec.adapter_version

    @property
    def state_schema_version(self) -> int:
        return self.spec.state_schema_version

    @property
    def adapter_factory(self) -> Callable[[], Any] | None:
        if self.adapter_descriptor is None:
            return None
        return self.adapter_descriptor.create_adapter

    @property
    def readiness_requirements(self) -> tuple[str, ...]:
        return self.spec.readiness_requirements

    @property
    def compatible_strategies(self) -> tuple[str, ...]:
        return self.spec.compatible_strategies

    @property
    def excluded_strategies(self) -> tuple[str, ...]:
        return self.spec.excluded_strategies

    @property
    def compatibility_metadata(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self.spec.compatible_strategies, self.spec.excluded_strategies


@dataclass(frozen=True, slots=True)
class StrategyResolution:
    requested_id: str
    canonical_id: str
    capability: StrategyCapability

    @property
    def was_aliased(self) -> bool:
        return self.requested_id != self.canonical_id


DEFAULT_STRATEGY_ALIASES: tuple[tuple[str, str], ...] = (
    ("cot", "chain_of_thought"),
    ("zero_shot_cot", "chain_of_thought"),
    ("plan_and_execute", "plan_execute"),
)


DEFAULT_PATTERN_LIMITS = PatternLimits(
    calls=32,
    nodes=64,
    edges=128,
    depth=8,
    fan_out=8,
    rounds=16,
    tokens=64_000,
    duration_seconds=600,
    cost_usd=5.0,
)

LOCAL_REASONING_LIMITS: dict[str, PatternLimits] = {
    "constitutional_ai": PatternLimits(
        calls=2,
        nodes=1,
        edges=0,
        depth=1,
        fan_out=1,
        rounds=1,
        tokens=8_000,
        duration_seconds=60,
        cost_usd=0.20,
    ),
    "few_shot_cot": PatternLimits(
        calls=3,
        nodes=0,
        edges=0,
        depth=1,
        fan_out=1,
        rounds=1,
        tokens=6_000,
        duration_seconds=60,
        cost_usd=0.08,
    ),
    "graph_of_thoughts": PatternLimits(
        calls=24,
        nodes=24,
        edges=48,
        depth=4,
        fan_out=4,
        rounds=6,
        tokens=24_000,
        duration_seconds=180,
        cost_usd=0.40,
    ),
    "least_to_most": PatternLimits(
        calls=10,
        nodes=8,
        edges=12,
        depth=8,
        fan_out=1,
        rounds=8,
        tokens=12_000,
        duration_seconds=120,
        cost_usd=0.20,
    ),
    "rewoo": PatternLimits(
        calls=12,
        nodes=12,
        edges=24,
        depth=6,
        fan_out=4,
        rounds=8,
        tokens=12_000,
        duration_seconds=180,
        cost_usd=0.30,
    ),
    "lats": PatternLimits(
        calls=32,
        nodes=32,
        edges=31,
        depth=6,
        fan_out=4,
        rounds=24,
        tokens=32_000,
        duration_seconds=240,
        cost_usd=0.60,
    ),
    "llm_compiler": PatternLimits(
        calls=16,
        nodes=16,
        edges=32,
        depth=8,
        fan_out=4,
        rounds=8,
        tokens=16_000,
        duration_seconds=180,
        cost_usd=0.35,
    ),
}

CODE_REASONING_LIMITS: dict[str, PatternLimits] = {
    "program_of_thought": PatternLimits(
        calls=3,
        nodes=1,
        edges=0,
        depth=1,
        fan_out=1,
        rounds=1,
        tokens=8_000,
        duration_seconds=30,
        cost_usd=0.15,
    ),
    "codeact": PatternLimits(
        calls=24,
        nodes=8,
        edges=7,
        depth=8,
        fan_out=1,
        rounds=8,
        tokens=24_000,
        duration_seconds=240,
        cost_usd=0.80,
    ),
}


_CATEGORY_FAMILIES = {
    StrategyCategory.AGENT: StrategyFamily.REASONING,
    StrategyCategory.RAG: StrategyFamily.RAG,
    StrategyCategory.SAFETY: StrategyFamily.SAFETY,
    StrategyCategory.MEMORY: StrategyFamily.MEMORY,
    StrategyCategory.OPTIMISATION: StrategyFamily.OPTIMIZATION,
}


def _freeze_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_metadata(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_metadata(item) for item in value)
    return value


def _finalize_capability(capability: StrategyCapability) -> None:
    if capability._sealed:
        return

    capability.required_deps = tuple(str(value) for value in capability.required_deps)
    capability.optional_deps = tuple(str(value) for value in capability.optional_deps)
    capability.bundle_components = tuple(capability.bundle_components)
    capability.compatible_goal_properties = _freeze_metadata(capability.compatible_goal_properties)
    lifecycle_state = StrategyLifecycleState(capability.state.value)
    runtime_requirement = (
        "strategy_runner" if capability.adapter_descriptor is not None else "registry_contract"
    )
    readiness_requirements = tuple(dict.fromkeys([*capability.required_deps, runtime_requirement]))
    capability.canonical_owner_id = capability.canonical_owner_id or capability.strategy_id
    capability._spec = StrategySpec(
        strategy_id=capability.strategy_id,
        adapter_version="1.0.0",
        family=_CATEGORY_FAMILIES[capability.category],
        risk=StrategyRiskLevel(capability.risk_class),
        cost=StrategyCostLevel(capability.cost_class),
        latency=StrategyLatencyClass(capability.latency_class),
        state_schema_version=1,
        lifecycle_state=lifecycle_state,
        default_limits=LOCAL_REASONING_LIMITS.get(
            capability.strategy_id,
            CODE_REASONING_LIMITS.get(capability.strategy_id, DEFAULT_PATTERN_LIMITS),
        ),
        dependencies=tuple(capability.required_deps),
        compatible_strategies=tuple(
            str(value)
            for value in capability.compatible_goal_properties.get("compatible_strategies", ())
        ),
        excluded_strategies=tuple(
            str(value)
            for value in capability.compatible_goal_properties.get("excluded_strategies", ())
        ),
        readiness_requirements=readiness_requirements,
    )
    capability._seal()


def _validate_adapter_descriptor(capability: StrategyCapability) -> None:
    descriptor = capability.adapter_descriptor
    is_strong_state = capability.state in {
        StrategyState.IMPLEMENTED,
        StrategyState.CERTIFIED,
    }
    if descriptor is None:
        if is_strong_state:
            raise ValueError(
                "Implemented or certified strategy requires an executable "
                f"adapter descriptor: {capability.strategy_id}"
            )
        return

    if capability.execution_tier is not descriptor.execution_tier:
        raise ValueError(
            "Strategy capability execution tier mismatch: "
            f"{capability.strategy_id} "
            f"({capability.execution_tier.value} != {descriptor.execution_tier.value})"
        )

    try:
        adapter = descriptor.create_adapter()
        adapter_strategy_id = adapter.strategy_id
    except Exception as exc:
        if is_strong_state:
            message = (
                "Implemented or certified strategy requires an executable "
                f"adapter descriptor: {capability.strategy_id}"
            )
        else:
            message = f"invalid adapter descriptor: {capability.strategy_id}"
        raise ValueError(message) from exc

    if adapter_strategy_id != capability.strategy_id:
        raise ValueError(
            "Strategy adapter strategy ID mismatch: "
            f"{capability.strategy_id} != {adapter_strategy_id}"
        )


@dataclass(frozen=True, slots=True)
class RAGStrategyContractProbe:
    """Static adapter identity/contract evidence, not operational readiness proof."""

    strategy_id: str
    adapter_path: str
    available: bool
    trace: RAGStrategyTrace
    missing_dependencies: tuple[str, ...] = ()

    @property
    def contract_evidence(self) -> str:
        evidence = self.trace.detail.get("evidence")
        return evidence if isinstance(evidence, str) else ""

    @property
    def evidence(self) -> str:
        """Compatibility alias for static contract evidence."""
        return self.contract_evidence


RAGStrategyProbe = RAGStrategyContractProbe


def _readiness_from_available_dependencies(
    available_deps: set[str],
) -> RAGRuntimeReadiness:
    readiness = RAGRuntimeReadiness.all_available()
    for dependency in RAGRuntimeDependency:
        if dependency.value not in available_deps:
            readiness = readiness.without(dependency)
    return readiness


class StrategyRegistry:
    def __init__(
        self,
        entries: list[StrategyCapability],
        *,
        aliases: Iterable[tuple[str, str]] = DEFAULT_STRATEGY_ALIASES,
    ) -> None:
        self._by_id: dict[str, StrategyCapability] = {}
        for entry in entries:
            if entry.strategy_id in self._by_id:
                raise ValueError(f"Duplicate strategy ID: {entry.strategy_id}")
            _validate_adapter_descriptor(entry)
            _finalize_capability(entry)
            self._by_id[entry.strategy_id] = entry

        self._aliases: dict[str, str] = {}
        for alias, canonical_id in aliases:
            if alias in self._aliases:
                raise ValueError(f"Duplicate strategy alias: {alias}")
            if alias in self._by_id:
                raise ValueError(f"Strategy alias conflicts with a canonical strategy ID: {alias}")
            if canonical_id not in self._by_id:
                raise ValueError(f"Unknown canonical strategy ID: {canonical_id}")
            self._aliases[alias] = canonical_id

    def get(self, strategy_id: str) -> StrategyCapability | None:
        return self._by_id.get(strategy_id)

    def resolve(self, strategy_id: str) -> StrategyResolution:
        canonical_id = self._aliases.get(strategy_id, strategy_id)
        capability = self._by_id.get(canonical_id)
        if capability is None:
            raise LookupError(f"Unknown strategy: {strategy_id}")
        return StrategyResolution(strategy_id, canonical_id, capability)

    def resolve_adapter(self, strategy_id: str) -> AdapterDescriptor:
        try:
            resolution = self.resolve(strategy_id)
        except LookupError as exc:
            raise LookupError(f"Unknown adapter descriptor: {strategy_id}") from exc
        descriptor = resolution.capability.adapter_descriptor
        if descriptor is None or not descriptor.is_executable:
            raise LookupError(f"Unknown adapter descriptor: {strategy_id}")
        return descriptor

    def list_all(self) -> list[StrategyCapability]:
        return list(self._by_id.values())

    def list_by_category(self, category: StrategyCategory) -> list[StrategyCapability]:
        return [e for e in self._by_id.values() if e.category == category]

    def filter(
        self,
        *,
        state: StrategyState | None = None,
        category: StrategyCategory | None = None,
        cost_class: str | None = None,
        latency_class: str | None = None,
    ) -> list[StrategyCapability]:
        results = list(self._by_id.values())
        if state is not None:
            results = [r for r in results if r.state == state]
        if category is not None:
            results = [r for r in results if r.category == category]
        if cost_class is not None:
            results = [r for r in results if r.cost_class == cost_class]
        if latency_class is not None:
            results = [r for r in results if r.latency_class == latency_class]
        return results

    def is_available(self, strategy_id: str, *, available_deps: set[str] | None = None) -> bool:
        try:
            cap = self.resolve(strategy_id).capability
        except LookupError:
            return False
        if cap.category is StrategyCategory.RAG and cap.state not in {
            StrategyState.IMPLEMENTED,
            StrategyState.CERTIFIED,
        }:
            return False
        if cap.state in (StrategyState.PLANNED, StrategyState.DISABLED):
            return False
        if cap.category is StrategyCategory.RAG and available_deps is not None:
            strategy = RAGStrategy(strategy_id)
            readiness = _readiness_from_available_dependencies(available_deps)
            return not RAG_CAPABILITY_CATALOGUE[strategy].missing_dependencies(readiness)
        return available_deps is None or not set(cap.required_deps) - available_deps

    def resolve_rag_adapter(self, strategy_id: str) -> type[RAGRuntimeAdapter]:
        capability = self.get(strategy_id)
        if (
            capability is None
            or capability.category is not StrategyCategory.RAG
            or capability.state not in {StrategyState.IMPLEMENTED, StrategyState.CERTIFIED}
            or capability.runtime_adapter is None
        ):
            raise LookupError(f"RAG strategy has no implemented adapter: {strategy_id}")
        return capability.runtime_adapter

    def probe_rag_strategy(
        self,
        strategy_id: str,
        *,
        available_deps: set[str] | None = None,
    ) -> RAGStrategyContractProbe:
        """Compatibility wrapper for the static identity/contract probe."""
        return self.probe_rag_strategy_contract(
            strategy_id,
            available_deps=available_deps,
        )

    def probe_rag_strategy_contract(
        self,
        strategy_id: str,
        *,
        available_deps: set[str] | None = None,
    ) -> RAGStrategyContractProbe:
        """Validate adapter identity and contract without claiming operational evidence."""
        capability = self.get(strategy_id)
        adapter = self.resolve_rag_adapter(strategy_id)
        if capability is None:
            raise LookupError(f"RAG strategy is not registered: {strategy_id}")
        readiness = (
            RAGRuntimeReadiness.all_available()
            if available_deps is None
            else _readiness_from_available_dependencies(available_deps)
        )
        missing = tuple(
            dependency.value
            for dependency in RAG_CAPABILITY_CATALOGUE[
                RAGStrategy(strategy_id)
            ].missing_dependencies(readiness)
        )
        adapter_path = f"{adapter.__module__}:{adapter.__name__}"
        strategy = RAGStrategy(strategy_id)
        raw_trace = adapter.probe_trace()
        trace = raw_trace.model_copy(update={"action": f"identity_contract_{raw_trace.action}"})
        if adapter.strategy is not strategy or trace.strategy is not strategy:
            raise TypeError(f"RAG adapter probe strategy mismatch: {strategy_id}")
        if (
            not trace.action.strip()
            or not RAGStrategyContractProbe(
                strategy_id=strategy_id,
                adapter_path=adapter_path,
                available=True,
                trace=trace,
            ).evidence.strip()
        ):
            raise ValueError(f"RAG adapter probe returned no evidence: {strategy_id}")
        if missing:
            trace = trace.model_copy(
                update={
                    "status": "unavailable",
                    "detail": {
                        **trace.detail,
                        "missing_dependencies": list(missing),
                    },
                }
            )
        return RAGStrategyContractProbe(
            strategy_id=strategy_id,
            adapter_path=adapter_path,
            available=not missing,
            trace=trace,
            missing_dependencies=missing,
        )


def build_default_registry(
    *,
    rag_runtime_capabilities: Mapping[RAGStrategy, type[RAGRuntimeAdapter]] | None = None,
) -> StrategyRegistry:
    A = StrategyCategory.AGENT  # noqa: N806
    R = StrategyCategory.RAG  # noqa: N806
    S = StrategyCategory.SAFETY  # noqa: N806
    M = StrategyCategory.MEMORY  # noqa: N806
    O = StrategyCategory.OPTIMISATION  # noqa: E741, N806
    IMPL = StrategyState.IMPLEMENTED  # noqa: N806
    PART = StrategyState.PARTIAL  # noqa: N806
    PLAN = StrategyState.PLANNED  # noqa: N806

    runtime_capabilities = (
        RAG_RUNTIME_CAPABILITIES if rag_runtime_capabilities is None else rag_runtime_capabilities
    )
    entries: list[StrategyCapability] = [
        # ── Agent Patterns (26 from doc-4) ──────────────────────────────────
        StrategyCapability(
            "react",
            A,
            IMPL,
            "app.agent.patterns.core_execution:ReActStrategyAdapter",
            "ReAct loop",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "plan_execute",
            A,
            IMPL,
            "app.agent.patterns.core_execution:PlanExecuteStrategyAdapter",
            "Plan then execute",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "chain_of_thought",
            A,
            IMPL,
            "app.agent.graph:AgentGraph",
            "CoT reasoning",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "few_shot_cot",
            A,
            IMPL,
            "app.agent.patterns.few_shot_cot:FewShotCoTAdapter",
            "Few-shot CoT",
            ["provider", "checkpoint_store", "reasoning_example_source"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "reflection",
            A,
            IMPL,
            "app.agent.graph:AgentGraph",
            "Post-exec reflection",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "reflexion",
            A,
            IMPL,
            "",
            "Persistent failure lessons",
            [],
            [],
            "medium",
            "batch",
            "low",
        ),
        StrategyCapability(
            "self_refine",
            A,
            IMPL,
            "",
            "Iterative self-refinement",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "self_consistency",
            A,
            IMPL,
            "",
            "Multiple paths + majority vote",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "tree_of_thoughts",
            A,
            IMPL,
            "",
            "Tree search over reasoning",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "graph_of_thoughts",
            A,
            IMPL,
            "app.agent.patterns.graph_of_thoughts:GraphOfThoughtsAdapter",
            "Graph-structured thought",
            ["provider", "checkpoint_store"],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "least_to_most",
            A,
            IMPL,
            "app.agent.patterns.least_to_most:LeastToMostAdapter",
            "Decompose least to most complex",
            ["provider", "checkpoint_store"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "rewoo",
            A,
            IMPL,
            "app.agent.patterns.rewoo:ReWOOAdapter",
            "Reasoning without observation",
            ["provider", "checkpoint_store", "governed_tool_dispatcher"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "program_of_thought",
            A,
            IMPL,
            "app.agent.patterns.program_of_thought:ProgramOfThoughtAdapter",
            "Programs to solve tasks",
            ["code_interpreter", "production_sandbox", "artifact_store", "checkpoint_store"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "codeact",
            A,
            IMPL,
            "app.agent.patterns.codeact:CodeActAdapter",
            "Code execution as action",
            ["code_interpreter", "production_sandbox", "artifact_store", "checkpoint_store"],
            [],
            "medium",
            "interactive",
            "medium",
        ),
        StrategyCapability(
            "goal_tree",
            A,
            IMPL,
            "app.agent.goal_tree:GoalTree",
            "Parallel sub-goals",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "supervisor",
            A,
            IMPL,
            "app.agent.supervisor:Supervisor",
            "Supervisor orchestration",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "debate",
            A,
            IMPL,
            "app.agent.debate:DebateCoordinator",
            "Multi-agent debate",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "group_chat",
            A,
            IMPL,
            "app.coordination.group_chat.adapter:GroupChatAdapter",
            "Durable shared-transcript group chat",
            ["checkpoint_store", "coordination_outbox", "transcript_store"],
            [],
            "high",
            "interactive",
            "medium",
        ),
        StrategyCapability(
            "magentic",
            A,
            IMPL,
            "app.coordination.magentic.adapter:MagenticAdapter",
            "Ledger-driven bounded multi-agent coordination",
            ["checkpoint_store", "coordination_outbox", "progress_ledger"],
            [],
            "high",
            "interactive",
            "medium",
        ),
        StrategyCapability(
            "mixture_of_agents",
            A,
            IMPL,
            "app.coordination.moa.adapter:MoAAdapter",
            "Diverse layered proposal aggregation",
            ["checkpoint_store", "coordination_outbox", "moa_repository"],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "consensus",
            A,
            IMPL,
            "app.agent.consensus:ConsensusVerifier",
            "Consensus verification",
            [],
            [],
            "high",
            "batch",
            "medium",
        ),
        StrategyCapability(
            "peer_review", A, IMPL, "", "Peer agent review", [], [], "high", "batch", "low"
        ),
        StrategyCapability(
            "camel",
            A,
            IMPL,
            "app.coordination.camel.adapter:CamelAdapter",
            "Bounded communicative agents",
            ["checkpoint_store", "transcript_store"],
            [],
            "high",
            "batch",
            "medium",
        ),
        StrategyCapability(
            "decentralized_swarm",
            A,
            IMPL,
            "app.coordination.swarm.adapter:DecentralizedSwarmAdapter",
            "Governor-constrained peer coordination",
            ["checkpoint_store", "coordination_outbox", "lease_store"],
            [],
            "high",
            "batch",
            "high",
        ),
        StrategyCapability(
            "market_auction",
            A,
            IMPL,
            "app.coordination.auction.adapter:MarketAuctionAdapter",
            "Sealed deterministic work allocation",
            ["checkpoint_store", "coordination_outbox", "auction_repository"],
            [],
            "high",
            "batch",
            "high",
        ),
        StrategyCapability(
            "babyagi",
            A,
            IMPL,
            "app.agent.patterns.babyagi:BabyAGIAdapter",
            "Bounded durable task creation and prioritization",
            ["checkpoint_store", "coordination_outbox"],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "autogpt",
            A,
            IMPL,
            "app.agent.patterns.autogpt:AutoGPTAdapter",
            "Bounded gated autonomous execution",
            ["checkpoint_store", "policy_runtime", "production_sandbox"],
            [],
            "high",
            "batch",
            "medium",
        ),
        StrategyCapability(
            "lats",
            A,
            IMPL,
            "app.agent.patterns.lats:LATSAdapter",
            "LLM Monte Carlo tree search",
            ["provider", "checkpoint_store", "governed_tool_dispatcher"],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "llm_compiler",
            A,
            IMPL,
            "app.agent.patterns.llm_compiler:LLMCompilerAdapter",
            "Parallel task compilation",
            ["provider", "checkpoint_store", "governed_tool_dispatcher"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        # Additional from doc-1
        StrategyCapability(
            "structured_planning",
            A,
            IMPL,
            "app.agent.structured_plan",
            "Structured plan with depends_on",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "persistence_strategy",
            A,
            IMPL,
            "app.agent.persistence",
            "Smart retry with strategy rotation",
            [],
            [],
            "low",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "loop_engineering",
            A,
            IMPL,
            "app.agent.graph:AgentGraph",
            "loop_until + wave execution",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "loop_until",
            A,
            IMPL,
            "app.agent.graph:AgentGraph",
            "Loop step until condition",
            [],
            [],
            "low",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "wave_execution",
            A,
            IMPL,
            "app.agent.graph:AgentGraph",
            "Parallel wave execution",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "workflow_dag",
            A,
            IMPL,
            "app.agent.workflow_planner:WorkflowPlanner",
            "Static DAG workflow",
            [],
            [],
            "medium",
            "batch",
            "low",
        ),
        StrategyCapability(
            "skill_selector",
            A,
            IMPL,
            "app.agent.skill_selector",
            "Dynamic skill selection",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "intent_router",
            A,
            IMPL,
            "app.agent.router",
            "Auto-route to best agent",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "meta_agent_planner",
            A,
            PART,
            "app.intelligence.meta_agent",
            "NL → agent config",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            "scratchpad",
            A,
            IMPL,
            "app.agent.graph:AgentGraph",
            "CoT scratchpad",
            [],
            [],
            "low",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "budget_control",
            O,
            IMPL,
            "app.governance.cost:CostController",
            "Cost circuit breaker",
            [],
            [],
            "low",
            "realtime",
            "medium",
        ),
        StrategyCapability(
            "circuit_breaker",
            S,
            IMPL,
            "app.reliability.circuit_breaker:CircuitBreaker",
            "CLOSED→OPEN→HALF_OPEN",
            [],
            [],
            "low",
            "realtime",
            "medium",
        ),
        StrategyCapability(
            "grounding_checker",
            S,
            IMPL,
            "app.agent.grounding:GroundingChecker",
            "Hallucination detection",
            [],
            [],
            "low",
            "interactive",
            "medium",
        ),
        StrategyCapability(
            "constitutional_ai",
            S,
            IMPL,
            "app.agent.patterns.constitutional_ai:ConstitutionalAIAdapter",
            "Constitutional AI critique",
            ["policy_runtime", "provider"],
            [],
            "medium",
            "batch",
            "medium",
        ),
        StrategyCapability(
            "voyager", A, IMPL, "app.agent.patterns.voyager:VoyagerAdapter",
            "Governed evidence-only skill-learning agent",
            ["checkpoint_store", "memory_repository", "production_sandbox"], [],
            "high", "batch", "medium",
        ),
        StrategyCapability(
            "generative_agents",
            A,
            IMPL,
            "app.coordination.generative.adapter:GenerativeAgentsAdapter",
            "Memory-grounded bounded simulation",
            ["checkpoint_store", "memory_repository"],
            [],
            "high",
            "batch",
            "medium",
        ),
        # ── RAG Patterns (17 from doc-4 + raft) ──────────────────────────────
        StrategyCapability(
            RAGStrategy.NAIVE.value,
            R,
            PART,
            "app.rag.store:KnowledgeStore",
            "Simple vector retrieval",
            ["knowledge_store"],
            ["embedder"],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.HYBRID.value,
            R,
            PART,
            "app.rag.store:KnowledgeStore",
            "Vector + trigram hybrid",
            ["knowledge_store"],
            ["embedder"],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.HYDE.value,
            R,
            PART,
            "app.rag_platform.retriever:RAGRetriever",
            "Hypothetical doc embeddings",
            ["knowledge_store", "embedder"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.MULTI_HOP.value,
            R,
            PART,
            "app.rag_platform.retriever:RAGRetriever",
            "Multi-hop retrieval",
            ["knowledge_store", "embedder"],
            ["kg_store"],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.GRAPH.value,
            R,
            PART,
            "app.rag_platform.retriever:RAGRetriever",
            "KG augmented retrieval",
            ["kg_store"],
            ["knowledge_store"],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.CORRECTIVE.value,
            R,
            PART,
            "app.rag.agentic.patterns.corrective:CorrectiveRAGPattern",
            "Corrective retrieval",
            ["knowledge_store"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.ADAPTIVE.value,
            R,
            PART,
            "app.rag.agentic.patterns.adaptive:AdaptiveRAGPattern",
            "Adaptive strategy per query",
            ["knowledge_store"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.MODULAR.value,
            R,
            PLAN,
            "",
            "Pluggable RAG modules",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.SPECULATIVE.value,
            R,
            PART,
            "app.rag.agentic.patterns.speculative:SpeculativeRAGPattern",
            "Speculative retrieval",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.AGENTIC.value,
            R,
            PART,
            "app.rag_platform.retriever:RAGRetriever",
            "Agent-owned retrieval",
            ["knowledge_store"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.WEB_AUGMENTED.value,
            R,
            PART,
            "app.tools.web_search:WebSearchTool",
            "Web search RAG",
            ["web_search"],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.FUSION.value,
            R,
            PART,
            "app.rag.agentic.patterns.fusion:FusionRAGPattern",
            "Multi-query RRF fusion",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.SELF_RAG.value,
            R,
            PART,
            "app.rag.agentic.patterns.self_rag:SelfRAGPattern",
            "Self-reflective retrieval",
            [],
            [],
            "high",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.FLARE.value,
            R,
            PART,
            "app.rag.agentic.patterns.flare:FLAREPattern",
            "Forward-looking active retrieval",
            [],
            [],
            "high",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.RAPTOR.value,
            R,
            PART,
            "app.rag.agentic.patterns.raptor:RAPTORPattern",
            "Recursive abstractive processing",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.AGENTIC_CHUNKING.value,
            R,
            PART,
            "app.rag.agentic.patterns.agentic_chunking:AgenticChunkingPattern",
            "LLM-driven chunking",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.COLBERT.value,
            R,
            PART,
            "app.rag.agentic.patterns.colbert:ColBERTPattern",
            "ColBERT late interaction",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            RAGStrategy.RAFT.value,
            R,
            PLAN,
            "",
            "RAG fine-tuning pattern",
            [],
            [],
            "high",
            "batch",
            "low",
        ),
        # ── Safety Patterns (10 from doc-4 + extras) ────────────────────────
        StrategyCapability(
            "guardrails",
            S,
            IMPL,
            "app.guardrails_v2.engine:guardrails_engine",
            "Input/output scanning",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "hitl",
            S,
            IMPL,
            "app.governance.hitl:HITLGateway",
            "Human approval gate",
            [],
            [],
            "low",
            "interactive",
            "high",
        ),
        StrategyCapability(
            "consensus_verification",
            S,
            IMPL,
            "app.agent.consensus:ConsensusVerifier",
            "Multi-agent consensus",
            [],
            [],
            "high",
            "batch",
            "medium",
        ),
        StrategyCapability(
            "exfiltration_guard",
            S,
            IMPL,
            "app.agent.exfil_guard",
            "Prevent data exfil",
            [],
            [],
            "low",
            "realtime",
            "high",
        ),
        StrategyCapability(
            "permission_matrix",
            S,
            IMPL,
            "app.governance.permissions:PermissionMatrix",
            "RBAC enforcement",
            [],
            [],
            "low",
            "realtime",
            "high",
        ),
        StrategyCapability(
            "policy_compiler",
            S,
            PLAN,
            "app.policy_runtime.compiler",
            "Compile policy constraints",
            [],
            [],
            "low",
            "realtime",
            "high",
        ),
        StrategyCapability(
            "sandbox",
            S,
            PLAN,
            "app.sandbox_runtime.executor",
            "Isolated execution",
            [],
            [],
            "medium",
            "interactive",
            "high",
        ),
        StrategyCapability(
            "plan_verification",
            S,
            PLAN,
            "app.plan_runtime.plan_verifier",
            "Verify plan before exec",
            [],
            [],
            "low",
            "realtime",
            "high",
        ),
        StrategyCapability(
            "data_classification",
            S,
            PLAN,
            "app.data_classification.classifier",
            "Classify data before prompt",
            [],
            [],
            "low",
            "realtime",
            "high",
        ),
        StrategyCapability(
            "provenance_verification",
            S,
            PLAN,
            "app.provenance.ledger",
            "Claim-level provenance",
            [],
            [],
            "medium",
            "batch",
            "medium",
        ),
        StrategyCapability(
            "rollback",
            S,
            IMPL,
            "app.reliability.rollback:RollbackEngine",
            "LIFO rollback",
            [],
            [],
            "low",
            "realtime",
            "high",
        ),
        # ── Memory Patterns (8 from doc-4) ───────────────────────────────────
        StrategyCapability(
            "working_memory",
            M,
            IMPL,
            "app.agent.state:AgentState",
            "In-context working memory",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "session_memory",
            M,
            IMPL,
            "app.memory.execution:ExecutionMemory",
            "Session execution memory",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "execution_memory",
            M,
            IMPL,
            "app.memory.execution:ExecutionMemory",
            "Cross-run plan memory",
            [],
            [],
            "low",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "long_term_memory",
            M,
            IMPL,
            "app.memory.long_term:LongTermMemoryStore",
            "Cross-session learnings",
            [],
            [],
            "low",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "semantic_memory",
            M,
            IMPL,
            "app.rag.semantic_cache:SemanticCache",
            "Semantic cache",
            ["embedder"],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "prospective_memory",
            M,
            PLAN,
            "",
            "Future task scheduling",
            [],
            [],
            "low",
            "batch",
            "low",
        ),
        StrategyCapability(
            "reflexion_memory",
            M,
            PART,
            "app.state_runtime.reflexion_store",
            "Failure lesson store",
            [],
            [],
            "low",
            "batch",
            "low",
        ),
        StrategyCapability(
            "knowledge_graph_memory",
            M,
            PART,
            "app.knowledge_graph.store:KnowledgeGraphStore",
            "KG memory",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "episodic_memory",
            M,
            IMPL,
            "app.memory.episodic:EpisodicMemoryStore",
            "Past experience recall — stores goal outcomes for contextual planning",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        StrategyCapability(
            "procedural_memory",
            M,
            IMPL,
            "app.memory.procedural:ProceduralMemoryStore",
            "Learned tool-use patterns — suggests proven tool sequences for goal types",
            [],
            [],
            "medium",
            "interactive",
            "low",
        ),
        # ── Optimisation Patterns (11 from doc-4) ────────────────────────────
        StrategyCapability(
            "model_routing",
            O,
            IMPL,
            "app.ai_router.router:AIRouter",
            "Dynamic model selection",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "embedding_routing",
            O,
            PART,
            "",
            "Dynamic embedding selection",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "token_optimisation",
            O,
            PART,
            "app.agent.prompt_compressor",
            "Prompt compression",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "cost_optimisation",
            O,
            PART,
            "app.intelligence.cost_optimizer",
            "Cost-aware selection",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "latency_optimisation",
            O,
            PLAN,
            "",
            "Latency-aware routing",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "semantic_cache",
            O,
            IMPL,
            "app.rag.semantic_cache:SemanticCache",
            "Semantic LLM caching",
            ["embedder"],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "llm_response_cache",
            O,
            IMPL,
            "app.rag.llm_response_cache",
            "Deterministic LLM cache",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "prompt_ab_testing",
            O,
            PLAN,
            "",
            "Prompt A/B testing",
            [],
            [],
            "low",
            "batch",
            "low",
        ),
        StrategyCapability(
            "model_ab_testing",
            O,
            PLAN,
            "",
            "Model routing A/B testing",
            [],
            [],
            "low",
            "batch",
            "low",
        ),
        StrategyCapability(
            "prompt_compression",
            O,
            PART,
            "app.agent.prompt_compressor",
            "Compress prompts",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
        StrategyCapability(
            "context_budgeting",
            O,
            PLAN,
            "app.context.context_budget",
            "Context token budgets",
            [],
            [],
            "low",
            "realtime",
            "low",
        ),
    ]
    for capability in entries:
        if capability.category is not R:
            continue
        strategy = RAGStrategy(capability.strategy_id)
        catalogue_entry = RAG_CAPABILITY_CATALOGUE[strategy]
        capability.required_deps = [
            dependency.value for dependency in catalogue_entry.required_dependencies
        ]
        adapter = runtime_capabilities.get(strategy)
        if adapter is not None and is_rag_runtime_adapter(strategy, adapter):
            capability.state = IMPL
            capability.adapter_path = f"{adapter.__module__}:{adapter.__name__}"
            capability.runtime_adapter = adapter
            capability.adapter_descriptor = rag_adapter_descriptor(
                RAGStrategy(capability.strategy_id), adapter
            )
            capability.execution_tier = ExecutionTier.RAG
        elif capability.state not in (PLAN, StrategyState.DISABLED):
            capability.state = PART

    local_agent_graph_strategies = {
        "react",
        "plan_execute",
        "chain_of_thought",
        "reflection",
        "reflexion",
        "self_refine",
        "self_consistency",
        "tree_of_thoughts",
        "peer_review",
        "loop_until",
        "wave_execution",
        "scratchpad",
    }
    local_reasoning_strategies = frozenset(LOCAL_REASONING_LIMITS)
    code_reasoning_strategies = frozenset(CODE_REASONING_LIMITS)
    durable_coordination_strategies = frozenset(
        {
            "consensus",
            "consensus_verification",
            "autogpt",
            "babyagi",
            "camel",
            "decentralized_swarm",
            "debate",
            "goal_tree",
            "group_chat",
            "magentic",
            "mixture_of_agents",
            "generative_agents",
            "market_auction",
            "supervisor",
            "voyager",
        }
    )
    for capability in entries:
        if capability.strategy_id in {"react", "plan_execute"}:
            capability.adapter_descriptor = core_execution_descriptor(capability.strategy_id)
            capability.execution_tier = ExecutionTier.LOCAL
        elif capability.strategy_id in local_agent_graph_strategies:
            capability.adapter_descriptor = local_agent_graph_descriptor(capability.strategy_id)
            capability.execution_tier = ExecutionTier.LOCAL
        elif capability.strategy_id in local_reasoning_strategies:
            capability.adapter_descriptor = local_reasoning_descriptor(capability.strategy_id)
            capability.execution_tier = ExecutionTier.LOCAL
        elif capability.strategy_id in code_reasoning_strategies:
            capability.adapter_descriptor = code_reasoning_descriptor(capability.strategy_id)
            capability.execution_tier = ExecutionTier.SANDBOX
        elif capability.strategy_id in durable_coordination_strategies:
            capability.adapter_descriptor = durable_coordination_descriptor(capability.strategy_id)
            capability.execution_tier = ExecutionTier.DISTRIBUTED
        elif capability.strategy_id == "workflow_dag":
            capability.adapter_descriptor = workflow_adapter_descriptor()
            capability.execution_tier = ExecutionTier.WORKFLOW

        if capability.strategy_id == "loop_engineering":
            capability.state = PART
            capability.adapter_descriptor = None
            capability.bundle_components = ("loop_until", "wave_execution")
        elif capability.state in {StrategyState.IMPLEMENTED, StrategyState.CERTIFIED} and (
            capability.adapter_descriptor is None
        ):
            capability.state = PART

    ownership = {
        "session_memory": "execution_memory",
        "execution_memory": "execution_memory",
    }
    for capability in entries:
        capability.canonical_owner_id = ownership.get(
            capability.strategy_id,
            capability.strategy_id,
        )

    return StrategyRegistry(entries)


@lru_cache(maxsize=1)
def get_strategy_registry() -> StrategyRegistry:
    """Return the default strategy registry, built once at startup."""
    return build_default_registry()
