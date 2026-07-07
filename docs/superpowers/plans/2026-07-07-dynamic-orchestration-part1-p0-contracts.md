# AgentVerse Core Dynamic Orchestration — Part 1: P0 Core Contracts

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the foundational orchestration contracts that every other layer depends on — GoalRuntimeProfile, StrategyRegistry, GoalClassifier, RuntimeProfileBuilder, and DecisionTrace — with zero behaviour change to existing goal execution (feature-flagged).

**Architecture:** New `app/orchestration/` package wires into `GoalService.submit_goal()` behind a `DYNAMIC_ORCHESTRATION` feature flag. Every strategy selection is explicit, typed, serializable, and traceable. Nothing in `graph.py` changes yet.

**Tech Stack:** Python 3.12, pydantic v2, pytest-asyncio (auto mode), `from __future__ import annotations`, uv

**Run all tests in this phase:**
```bash
cd agent-verse-backend
uv run pytest tests/orchestration/ -v --no-cov
```

---

## Phase 1 File Map

| File | Action | Purpose |
|------|--------|---------|
| `app/orchestration/__init__.py` | Create | Package init |
| `app/orchestration/runtime_profile.py` | Create | All GoalRuntimeProfile dataclasses |
| `app/orchestration/strategy_registry.py` | Create | StrategyCapability + StrategyRegistry |
| `app/orchestration/goal_classifier.py` | Create | Fast keyword + optional LLM classification |
| `app/orchestration/runtime_profile_builder.py` | Create | Assembles profile from classifier + registry |
| `app/orchestration/decision_trace.py` | Create | Serializable trace of every selector decision |
| `app/orchestration/pattern_selector.py` | Create | Selects agent/RAG/safety/memory patterns |
| `app/core/runtime_flags.py` | Create | Feature flags loaded from env |
| `tests/orchestration/__init__.py` | Create | Test package |
| `tests/orchestration/test_runtime_profile.py` | Create | Profile dataclass tests |
| `tests/orchestration/test_strategy_registry.py` | Create | Registry lookup tests |
| `tests/orchestration/test_goal_classifier.py` | Create | Classifier accuracy on 10 archetypes |
| `tests/orchestration/test_runtime_profile_builder.py` | Create | Builder integration tests |
| `tests/orchestration/test_decision_trace.py` | Create | Trace serialization tests |
| `tests/orchestration/test_pattern_selector.py` | Create | Pattern selection correctness |

---

## Task 1: Runtime Feature Flags

**Files:**
- Create: `app/core/runtime_flags.py`
- Test: `tests/orchestration/test_runtime_profile.py` (flags section)

- [ ] **Step 1.1: Write the failing test**

```python
# tests/orchestration/test_runtime_profile.py  (top of file)
"""Tests for runtime flags and GoalRuntimeProfile contracts."""
from __future__ import annotations
import os
import pytest
from app.core.runtime_flags import RuntimeFlags, get_runtime_flags


def test_flags_default_values():
    flags = RuntimeFlags()
    assert flags.dynamic_orchestration is False
    assert flags.agentic_rag is False
    assert flags.plan_verification is False
    assert flags.data_classification is False
    assert flags.capability_registry is False
    assert flags.policy_compiler is False


def test_flags_from_env(monkeypatch):
    monkeypatch.setenv("DYNAMIC_ORCHESTRATION", "true")
    monkeypatch.setenv("AGENTIC_RAG", "true")
    flags = RuntimeFlags.from_env()
    assert flags.dynamic_orchestration is True
    assert flags.agentic_rag is True


def test_get_runtime_flags_singleton():
    f1 = get_runtime_flags()
    f2 = get_runtime_flags()
    assert f1 is f2
```

- [ ] **Step 1.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_runtime_profile.py::test_flags_default_values -v --no-cov
```
Expected: `ERROR` — `tests/orchestration/` does not exist yet.

- [ ] **Step 1.3: Create package directories**

```bash
mkdir -p agent-verse-backend/app/orchestration
mkdir -p agent-verse-backend/tests/orchestration
touch agent-verse-backend/app/orchestration/__init__.py
touch agent-verse-backend/tests/orchestration/__init__.py
```

- [ ] **Step 1.4: Implement `app/core/runtime_flags.py`**

```python
"""Runtime feature flags loaded from environment variables.

All new orchestration behaviours are off by default.
Set env var to "true" (case-insensitive) to enable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool_env(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class RuntimeFlags:
    # P0 flags
    dynamic_orchestration: bool = False
    agentic_rag: bool = False
    plan_verification: bool = False
    data_classification: bool = False
    capability_registry: bool = False
    policy_compiler: bool = False
    # P1 flags
    ingestion_orchestrator: bool = False
    embedding_orchestrator: bool = False
    runtime_scorecard: bool = False
    tool_trust: bool = False
    provenance_ledger: bool = False
    recovery_classifier: bool = False
    qos_scheduler: bool = False
    # Safety
    guardrail_profile: bool = False
    readiness_gate: bool = False

    @classmethod
    def from_env(cls) -> "RuntimeFlags":
        return cls(
            dynamic_orchestration=_bool_env("DYNAMIC_ORCHESTRATION"),
            agentic_rag=_bool_env("AGENTIC_RAG"),
            plan_verification=_bool_env("PLAN_VERIFICATION"),
            data_classification=_bool_env("DATA_CLASSIFICATION"),
            capability_registry=_bool_env("CAPABILITY_REGISTRY"),
            policy_compiler=_bool_env("POLICY_COMPILER"),
            ingestion_orchestrator=_bool_env("INGESTION_ORCHESTRATOR"),
            embedding_orchestrator=_bool_env("EMBEDDING_ORCHESTRATOR"),
            runtime_scorecard=_bool_env("RUNTIME_SCORECARD"),
            tool_trust=_bool_env("TOOL_TRUST"),
            provenance_ledger=_bool_env("PROVENANCE_LEDGER"),
            recovery_classifier=_bool_env("RECOVERY_CLASSIFIER"),
            qos_scheduler=_bool_env("QOS_SCHEDULER"),
            guardrail_profile=_bool_env("GUARDRAIL_PROFILE"),
            readiness_gate=_bool_env("READINESS_GATE"),
        )


@lru_cache(maxsize=1)
def get_runtime_flags() -> RuntimeFlags:
    """Return cached RuntimeFlags loaded from env once at startup."""
    return RuntimeFlags.from_env()
```

- [ ] **Step 1.5: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_runtime_profile.py -k "flags" -v --no-cov
```
Expected: `3 passed`

- [ ] **Step 1.6: Commit**

```bash
cd agent-verse-backend
git add app/core/runtime_flags.py app/orchestration/__init__.py tests/orchestration/__init__.py tests/orchestration/test_runtime_profile.py
git commit -m "feat(orchestration): add RuntimeFlags + test package skeleton"
```

---

## Task 2: GoalRuntimeProfile Dataclasses

**Files:**
- Create: `app/orchestration/runtime_profile.py`
- Test: `tests/orchestration/test_runtime_profile.py`

- [ ] **Step 2.1: Write failing tests for runtime profile**

Append to `tests/orchestration/test_runtime_profile.py`:

```python
from app.orchestration.runtime_profile import (
    Complexity,
    RiskLevel,
    Domain,
    TimeSensitivity,
    KnowledgeState,
    GoalProperties,
    AgentPatternConfig,
    RAGStrategyConfig,
    ModelPlanConfig,
    SecurityConfig,
    MemoryCacheConfig,
    EvalConfig,
    GoalRuntimeProfile,
)


def test_goal_properties_defaults():
    props = GoalProperties(raw_goal="list all open tickets")
    assert props.complexity == Complexity.SIMPLE
    assert props.risk == RiskLevel.LOW
    assert props.domain == Domain.OPERATIONAL
    assert props.requires_web is False
    assert props.multi_step is True


def test_goal_runtime_profile_is_serializable():
    import json
    profile = GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="deploy to prod"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(hitl_required=True),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    data = profile.to_dict()
    assert data["goal_id"] == "g1"
    assert data["tenant_id"] == "t1"
    assert data["security"]["hitl_required"] is True
    # Must be JSON-serializable
    json.dumps(data)


def test_high_risk_profile_flags():
    profile = GoalRuntimeProfile(
        goal_id="g2",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="delete production db", risk=RiskLevel.CRITICAL),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(hitl_required=True, rollback_required=True, consensus_required=True),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True
    assert profile.security.consensus_required is True


def test_goal_properties_kb_state_enum():
    props = GoalProperties(raw_goal="search docs", kb_state=KnowledgeState.EMPTY)
    assert props.kb_state == KnowledgeState.EMPTY
    assert props.web_fallback_required is True  # empty KB forces web fallback
```

- [ ] **Step 2.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_runtime_profile.py -k "not flags" -v --no-cov
```
Expected: `ImportError` — module does not exist.

- [ ] **Step 2.3: Implement `app/orchestration/runtime_profile.py`**

```python
"""GoalRuntimeProfile — the unified execution contract for every goal.

Every strategy selector populates one section of this profile.
The profile is serialized into goal.execution_context and emitted
as a runtime_profile_selected SSE event.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class Complexity(str, enum.Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EXPERT = "expert"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Domain(str, enum.Enum):
    TECHNICAL = "technical"
    CREATIVE = "creative"
    ANALYTICAL = "analytical"
    OPERATIONAL = "operational"
    CONVERSATIONAL = "conversational"


class TimeSensitivity(str, enum.Enum):
    REALTIME = "realtime"
    NORMAL = "normal"
    BATCH = "batch"


class KnowledgeState(str, enum.Enum):
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
    reversibility: str = "reversible"          # reversible | irreversible
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
    """Selected agentic patterns for this goal."""
    reasoning: list[str] = field(default_factory=lambda: ["react"])
    rag: list[str] = field(default_factory=lambda: ["hybrid_rag"])
    multi_agent: list[str] = field(default_factory=lambda: ["single_agent"])
    safety: list[str] = field(default_factory=lambda: ["guardrails"])
    max_iterations: int = 15
    persistence_mode: bool = False
    max_persistence_attempts: int = 3
    autonomy_mode: str = "bounded-autonomous"
    selection_reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class RAGStrategyConfig:
    """Selected RAG strategy for this goal."""
    strategy: str = "hybrid_rag"              # naive|hybrid|hyde|multi_hop|graph|corrective|agentic|web_augmented
    sources: list[str] = field(default_factory=lambda: ["knowledge_base"])
    chunking_strategy: str = "semantic"
    embedding_model: str = "default"
    reranker: str = "score"                    # score|rrf|cross_encoder|llm|diversity
    max_context_tokens: int = 6000
    min_relevance_score: float = 0.35
    max_chunks_per_source: int = 5
    citation_required: bool = True
    deduplication_enabled: bool = True
    web_fallback_enabled: bool = False
    graph_strategy: str = "none"              # none|entity|path|community|impact


@dataclass
class ModelPlanConfig:
    """Model assignments per role."""
    planner: str = "default"
    executor: str = "default"
    verifier: str = "default"
    embedder: str = "default"
    classifier: str = "default"
    cost_class: str = "medium"                # low|medium|high
    latency_class: str = "interactive"        # realtime|interactive|batch


@dataclass
class SecurityConfig:
    """Security and governance profile."""
    guardrail_bundle: str = "default"         # default|strict|regulated|developer|rpa
    governance_bundle: str = "free"           # free|enterprise|regulated
    hitl_required: bool = False
    consensus_required: bool = False
    rollback_required: bool = False
    audit_level: str = "standard"             # standard|full|forensic
    compliance_tags: list[str] = field(default_factory=list)
    sandbox_required: bool = False


@dataclass
class MemoryCacheConfig:
    """Memory and cache policy."""
    use_session_memory: bool = True
    use_execution_memory: bool = True
    use_long_term_memory: bool = False
    use_semantic_cache: bool = False
    use_knowledge_graph: bool = False
    reflexion_enabled: bool = False
    cache_ttl_seconds: int = 3600


@dataclass
class EvalConfig:
    """Evaluation and self-improvement profile."""
    enabled: bool = True
    eval_suite: str = "default"               # default|security|rag|coding|ops
    score_threshold: float = 0.72
    reflexion_enabled: bool = True
    prompt_ab_test_enabled: bool = False
    model_ab_test_enabled: bool = False
    creates_regression_case_on_failure: bool = True


@dataclass
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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON embedding in execution_context."""
        import dataclasses
        def _convert(obj: Any) -> Any:
            if isinstance(obj, enum.Enum):
                return obj.value
            if dataclasses.is_dataclass(obj):
                return {k: _convert(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, list):
                return [_convert(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            return obj
        return _convert(self)
```

- [ ] **Step 2.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_runtime_profile.py -v --no-cov
```
Expected: `7 passed`

- [ ] **Step 2.5: Commit**

```bash
cd agent-verse-backend
git add app/orchestration/runtime_profile.py tests/orchestration/test_runtime_profile.py
git commit -m "feat(orchestration): add GoalRuntimeProfile dataclass contracts"
```

---

## Task 3: StrategyRegistry

**Files:**
- Create: `app/orchestration/strategy_registry.py`
- Create: `tests/orchestration/test_strategy_registry.py`

- [ ] **Step 3.1: Write failing tests**

```python
# tests/orchestration/test_strategy_registry.py
"""Tests for StrategyRegistry — canonical pattern catalogue."""
from __future__ import annotations
import pytest
from app.orchestration.strategy_registry import (
    StrategyCapability,
    StrategyState,
    StrategyCategory,
    StrategyRegistry,
    build_default_registry,
)


def test_registry_has_all_required_agent_patterns():
    registry = build_default_registry()
    required = {
        "react", "plan_execute", "reflection", "reflexion", "self_refine",
        "self_consistency", "tree_of_thoughts", "supervisor", "debate",
        "goal_tree", "consensus", "chain_of_thought",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.AGENT)}
    assert required.issubset(registered), f"Missing: {required - registered}"


def test_registry_has_all_required_rag_patterns():
    registry = build_default_registry()
    required = {
        "naive_rag", "hybrid_rag", "hyde", "multi_hop_rag", "graph_rag",
        "corrective_rag", "adaptive_rag", "agentic_rag", "web_augmented_rag",
        "fusion_rag", "self_rag",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.RAG)}
    assert required.issubset(registered), f"Missing: {required - registered}"


def test_registry_has_all_safety_patterns():
    registry = build_default_registry()
    required = {
        "guardrails", "hitl", "consensus_verification", "sandbox",
        "plan_verification", "data_classification", "provenance_verification",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.SAFETY)}
    assert required.issubset(registered), f"Missing: {required - registered}"


def test_every_entry_has_valid_state():
    registry = build_default_registry()
    valid_states = {StrategyState.IMPLEMENTED, StrategyState.PARTIAL,
                    StrategyState.PLANNED, StrategyState.DISABLED}
    for entry in registry.list_all():
        assert entry.state in valid_states, f"{entry.strategy_id} has invalid state"


def test_lookup_by_id():
    registry = build_default_registry()
    cap = registry.get("hybrid_rag")
    assert cap is not None
    assert cap.strategy_id == "hybrid_rag"
    assert cap.category == StrategyCategory.RAG


def test_lookup_nonexistent_returns_none():
    registry = build_default_registry()
    assert registry.get("does_not_exist") is None


def test_filter_by_cost_class():
    registry = build_default_registry()
    low_cost = registry.filter(cost_class="low")
    assert all(s.cost_class == "low" for s in low_cost)


def test_is_available_checks_state_and_deps():
    registry = build_default_registry()
    # hybrid_rag should be available (implemented/partial)
    assert registry.is_available("hybrid_rag", available_deps={"knowledge_store", "embedder"})
    # planned strategy not available
    cap = registry.get("tree_of_thoughts")
    assert cap is not None
    # Even planned ones must be listed
    assert cap.strategy_id == "tree_of_thoughts"
```

- [ ] **Step 3.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_strategy_registry.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 3.3: Implement `app/orchestration/strategy_registry.py`**

```python
"""StrategyRegistry — canonical catalogue of every orchestration strategy.

Every pattern from the architecture docs must be registered here with its state.
Orchestration never selects by name alone — it selects from registry entries.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class StrategyState(str, enum.Enum):
    IMPLEMENTED = "implemented"   # production code exists and is wired
    PARTIAL = "partial"           # code exists but not fully orchestrated
    PLANNED = "planned"           # contract exists but adapter not built
    DISABLED = "disabled"         # not allowed for current env


class StrategyCategory(str, enum.Enum):
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
    required_deps: list[str] = field(default_factory=list)
    optional_deps: list[str] = field(default_factory=list)
    cost_class: str = "medium"     # low|medium|high
    latency_class: str = "interactive"  # realtime|interactive|batch
    risk_class: str = "low"        # low|medium|high
    compatible_goal_properties: dict[str, Any] = field(default_factory=dict)


class StrategyRegistry:
    """Immutable catalogue of all strategies. Thread-safe after construction."""

    def __init__(self, entries: list[StrategyCapability]) -> None:
        self._by_id: dict[str, StrategyCapability] = {e.strategy_id: e for e in entries}

    def get(self, strategy_id: str) -> StrategyCapability | None:
        return self._by_id.get(strategy_id)

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

    def is_available(
        self,
        strategy_id: str,
        *,
        available_deps: set[str] | None = None,
    ) -> bool:
        cap = self._by_id.get(strategy_id)
        if cap is None:
            return False
        if cap.state in (StrategyState.PLANNED, StrategyState.DISABLED):
            return False
        if available_deps is not None:
            missing = set(cap.required_deps) - available_deps
            if missing:
                return False
        return True


def build_default_registry() -> StrategyRegistry:
    """Build the canonical AgentVerse strategy registry."""
    A = StrategyCategory.AGENT
    R = StrategyCategory.RAG
    S = StrategyCategory.SAFETY
    M = StrategyCategory.MEMORY
    O = StrategyCategory.OPTIMISATION
    I = StrategyState.IMPLEMENTED
    P = StrategyState.PARTIAL
    PL = StrategyState.PLANNED

    entries: list[StrategyCapability] = [
        # ── Agent Patterns ─────────────────────────────────────────────────────
        StrategyCapability("react", A, I, "app.agent.graph:AgentGraph",
            "ReAct: reason + act loop", [], [], "medium", "interactive", "low"),
        StrategyCapability("plan_execute", A, I, "app.agent.graph:AgentGraph",
            "Plan then execute steps", [], [], "medium", "interactive", "low"),
        StrategyCapability("chain_of_thought", A, P, "app.agent.graph:AgentGraph",
            "Chain of thought reasoning before planning", [], [], "medium", "interactive", "low"),
        StrategyCapability("zero_shot_cot", A, P, "",
            "Zero-shot chain of thought", [], [], "low", "interactive", "low"),
        StrategyCapability("few_shot_cot", A, PL, "",
            "Few-shot chain of thought with examples", [], [], "medium", "interactive", "low"),
        StrategyCapability("reflection", A, I, "app.agent.graph:AgentGraph",
            "Post-execution reflection", [], [], "medium", "interactive", "low"),
        StrategyCapability("reflexion", A, P, "",
            "Persistent failure lessons via Reflexion", ["reflexion_store"], [], "medium", "batch", "low"),
        StrategyCapability("self_refine", A, P, "",
            "Iterative self-refinement of output", [], [], "medium", "interactive", "low"),
        StrategyCapability("self_consistency", A, PL, "",
            "Multiple reasoning paths + majority vote", [], [], "high", "batch", "low"),
        StrategyCapability("tree_of_thoughts", A, PL, "",
            "Tree-structured search over reasoning paths", [], [], "high", "batch", "low"),
        StrategyCapability("graph_of_thoughts", A, PL, "",
            "Graph-structured thought exploration", [], [], "high", "batch", "low"),
        StrategyCapability("least_to_most", A, PL, "",
            "Decompose into subproblems from least to most complex", [], [], "medium", "interactive", "low"),
        StrategyCapability("rewoo", A, PL, "",
            "Reasoning without observation", [], [], "medium", "interactive", "low"),
        StrategyCapability("program_of_thought", A, PL, "",
            "Generate programs to solve analytical tasks", [], [], "medium", "interactive", "low"),
        StrategyCapability("codeact", A, PL, "",
            "Code execution as primary action modality", ["code_interpreter"], [], "medium", "interactive", "medium"),
        StrategyCapability("goal_tree", A, I, "app.agent.goal_tree:GoalTree",
            "Decompose goal into parallel sub-goals", [], [], "high", "batch", "low"),
        StrategyCapability("supervisor", A, I, "app.agent.supervisor:Supervisor",
            "Supervisor orchestrates sub-agents", [], [], "high", "batch", "low"),
        StrategyCapability("debate", A, I, "app.agent.debate:DebateCoordinator",
            "Multiple agents debate and vote", [], [], "high", "batch", "low"),
        StrategyCapability("mixture_of_agents", A, PL, "",
            "Ensemble of diverse specialized agents", [], [], "high", "batch", "low"),
        StrategyCapability("consensus", A, I, "app.agent.consensus:ConsensusVerifier",
            "Consensus verification across agents", [], [], "high", "batch", "medium"),
        StrategyCapability("peer_review", A, PL, "",
            "Agent output reviewed by peer agent", [], [], "high", "batch", "low"),
        StrategyCapability("camel", A, PL, "",
            "Communicative agents roleplay", [], [], "high", "batch", "low"),
        StrategyCapability("babyagi", A, PL, "",
            "Iterative task creation and prioritization", [], [], "high", "batch", "low"),
        StrategyCapability("autogpt", A, PL, "",
            "Autonomous long-horizon goal pursuit", [], [], "high", "batch", "medium"),
        StrategyCapability("lats", A, PL, "",
            "LLM-powered Monte Carlo tree search", [], [], "high", "batch", "low"),
        StrategyCapability("llm_compiler", A, PL, "",
            "Parallel task compilation and execution", [], [], "medium", "interactive", "low"),

        # ── RAG Patterns ───────────────────────────────────────────────────────
        StrategyCapability("naive_rag", R, I, "app.rag.store:KnowledgeStore",
            "Simple vector retrieval", ["knowledge_store"], ["embedder"], "low", "realtime", "low"),
        StrategyCapability("hybrid_rag", R, I, "app.rag.store:KnowledgeStore",
            "Vector + trigram hybrid", ["knowledge_store"], ["embedder"], "low", "realtime", "low"),
        StrategyCapability("hyde", R, P, "app.rag_platform.retriever:RAGRetriever",
            "Hypothetical document embeddings", ["knowledge_store", "embedder"], [], "medium", "interactive", "low"),
        StrategyCapability("multi_hop_rag", R, P, "app.rag_platform.retriever:RAGRetriever",
            "Multi-hop retrieval over hops", ["knowledge_store", "embedder"], ["kg_store"], "medium", "interactive", "low"),
        StrategyCapability("graph_rag", R, P, "app.rag_platform.retriever:RAGRetriever",
            "Knowledge graph augmented retrieval", ["kg_store"], ["knowledge_store"], "medium", "interactive", "low"),
        StrategyCapability("corrective_rag", R, P, "",
            "Corrective retrieval with gap detection", ["knowledge_store"], [], "medium", "interactive", "low"),
        StrategyCapability("adaptive_rag", R, PL, "",
            "Adapts retrieval strategy to query complexity", ["knowledge_store"], [], "medium", "interactive", "low"),
        StrategyCapability("modular_rag", R, PL, "",
            "Pluggable retrieval modules", [], [], "medium", "interactive", "low"),
        StrategyCapability("speculative_rag", R, PL, "",
            "Speculative retrieval with verification", [], [], "high", "batch", "low"),
        StrategyCapability("agentic_rag", R, P, "app.rag_platform.retriever:RAGRetriever",
            "Agent-owned retrieval as tool call", ["knowledge_store"], [], "medium", "interactive", "low"),
        StrategyCapability("web_augmented_rag", R, I, "app.tools.web_search",
            "Web search augmented retrieval", ["web_search"], [], "medium", "interactive", "low"),
        StrategyCapability("fusion_rag", R, PL, "",
            "Fuse multiple retrieval sources with RRF", [], [], "medium", "interactive", "low"),
        StrategyCapability("self_rag", R, PL, "",
            "Self-reflective retrieval with critique", [], [], "high", "interactive", "low"),
        StrategyCapability("flare", R, PL, "",
            "Forward-looking active retrieval augmentation", [], [], "high", "interactive", "low"),
        StrategyCapability("raptor", R, PL, "",
            "Recursive abstractive processing for tree-organized retrieval", [], [], "high", "batch", "low"),
        StrategyCapability("agentic_chunking", R, PL, "",
            "LLM-driven chunk boundary detection", [], [], "high", "batch", "low"),
        StrategyCapability("colbert_late_interaction", R, PL, "",
            "ColBERT late interaction reranking", [], [], "medium", "interactive", "low"),

        # ── Safety Patterns ────────────────────────────────────────────────────
        StrategyCapability("guardrails", S, I, "app.guardrails_v2.engine:guardrails_engine",
            "Input/output guardrail scanning", [], [], "low", "realtime", "low"),
        StrategyCapability("hitl", S, I, "app.governance.hitl:HITLGateway",
            "Human-in-the-loop approval gate", [], [], "low", "interactive", "high"),
        StrategyCapability("consensus_verification", S, I, "app.agent.consensus:ConsensusVerifier",
            "Multi-agent consensus before execution", [], [], "high", "batch", "medium"),
        StrategyCapability("exfiltration_guard", S, I, "app.agent.exfil_guard",
            "Prevent data exfiltration", [], [], "low", "realtime", "high"),
        StrategyCapability("permission_matrix", S, I, "app.governance.permissions:PermissionMatrix",
            "RBAC permission enforcement", [], [], "low", "realtime", "high"),
        StrategyCapability("policy_compiler", S, PL, "app.policy_runtime.compiler",
            "Compile tenant + org + compliance policies into constraints", [], [], "low", "realtime", "high"),
        StrategyCapability("sandbox", S, PL, "app.sandbox_runtime.executor",
            "Isolated code/browser/shell execution", [], [], "medium", "interactive", "high"),
        StrategyCapability("plan_verification", S, PL, "app.plan_runtime.plan_verifier",
            "Verify plan before execution", [], [], "low", "realtime", "high"),
        StrategyCapability("data_classification", S, PL, "app.data_classification.classifier",
            "Classify every data item before prompt injection", [], [], "low", "realtime", "high"),
        StrategyCapability("provenance_verification", S, PL, "app.provenance.ledger",
            "Claim-level provenance chain", [], [], "medium", "batch", "medium"),
        StrategyCapability("rollback", S, I, "app.reliability.rollback:RollbackEngine",
            "LIFO rollback of executed actions", [], [], "low", "realtime", "high"),

        # ── Memory Patterns ────────────────────────────────────────────────────
        StrategyCapability("working_memory", M, I, "app.agent.state:AgentState",
            "In-context working memory via state", [], [], "low", "realtime", "low"),
        StrategyCapability("session_memory", M, I, "app.memory.execution:ExecutionMemory",
            "Within-session execution memory", [], [], "low", "realtime", "low"),
        StrategyCapability("execution_memory", M, I, "app.memory.execution:ExecutionMemory",
            "Cross-run execution plan memory", [], [], "low", "interactive", "low"),
        StrategyCapability("long_term_memory", M, I, "app.memory.long_term:LongTermMemoryStore",
            "Cross-session learnings store", [], [], "low", "interactive", "low"),
        StrategyCapability("semantic_memory", M, I, "app.rag.semantic_cache:SemanticCache",
            "Semantic similarity cache", ["embedder"], [], "low", "realtime", "low"),
        StrategyCapability("prospective_memory", M, PL, "",
            "Future task scheduling via memory", [], [], "low", "batch", "low"),
        StrategyCapability("reflexion_memory", M, P, "app.state_runtime.reflexion_store",
            "Persistent failure lesson store", [], [], "low", "batch", "low"),
        StrategyCapability("knowledge_graph_memory", M, P, "app.knowledge_graph.store:KnowledgeGraphStore",
            "Entity/relationship graph memory", [], [], "medium", "interactive", "low"),

        # ── Optimisation Patterns ──────────────────────────────────────────────
        StrategyCapability("model_routing", O, I, "app.ai_router.router:AIRouter",
            "Dynamic model selection per task", [], [], "low", "realtime", "low"),
        StrategyCapability("embedding_routing", O, P, "",
            "Dynamic embedding model selection", [], [], "low", "realtime", "low"),
        StrategyCapability("token_optimisation", O, P, "app.agent.prompt_compressor",
            "Prompt compression and token budgeting", [], [], "low", "realtime", "low"),
        StrategyCapability("cost_optimisation", O, P, "app.intelligence.cost_optimizer",
            "Cost-aware model and strategy selection", [], [], "low", "realtime", "low"),
        StrategyCapability("latency_optimisation", O, PL, "",
            "Latency-aware routing and caching", [], [], "low", "realtime", "low"),
        StrategyCapability("semantic_cache", O, I, "app.rag.semantic_cache:SemanticCache",
            "Semantic similarity caching of LLM calls", ["embedder"], [], "low", "realtime", "low"),
        StrategyCapability("llm_response_cache", O, I, "app.rag.llm_response_cache",
            "Deterministic LLM response cache", [], [], "low", "realtime", "low"),
        StrategyCapability("prompt_ab_testing", O, PL, "",
            "A/B test prompt variants gated by eval", [], [], "low", "batch", "low"),
        StrategyCapability("model_ab_testing", O, PL, "",
            "A/B test model variants gated by eval", [], [], "low", "batch", "low"),
        StrategyCapability("prompt_compression", O, P, "app.agent.prompt_compressor",
            "Compress prompts to fit token budgets", [], [], "low", "realtime", "low"),
        StrategyCapability("context_budgeting", O, PL, "app.context.context_budget",
            "Enforce per-role context token budgets", [], [], "low", "realtime", "low"),
    ]

    return StrategyRegistry(entries)


# Module-level singleton
_default_registry: StrategyRegistry | None = None


def get_strategy_registry() -> StrategyRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry
```

- [ ] **Step 3.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_strategy_registry.py -v --no-cov
```
Expected: `8 passed`

- [ ] **Step 3.5: Commit**

```bash
cd agent-verse-backend
git add app/orchestration/strategy_registry.py tests/orchestration/test_strategy_registry.py
git commit -m "feat(orchestration): add StrategyRegistry with 60+ patterns from architecture docs"
```

---

## Task 4: GoalClassifier

**Files:**
- Create: `app/orchestration/goal_classifier.py`
- Create: `tests/orchestration/test_goal_classifier.py`

- [ ] **Step 4.1: Write failing tests for 10 goal archetypes**

```python
# tests/orchestration/test_goal_classifier.py
"""Classifier must correctly classify at least 10 goal archetypes."""
from __future__ import annotations
import pytest
from app.orchestration.goal_classifier import GoalClassifier
from app.orchestration.runtime_profile import (
    Complexity, RiskLevel, Domain, TimeSensitivity,
)


@pytest.fixture
def classifier():
    return GoalClassifier()


# ── Archetype 1: Simple Lookup ────────────────────────────────────────────────
def test_classify_simple_lookup(classifier):
    props = classifier.classify_fast("list all open Jira tickets")
    assert props.complexity == Complexity.SIMPLE
    assert props.risk == RiskLevel.LOW
    assert props.requires_web is False


# ── Archetype 2: Complex Research ─────────────────────────────────────────────
def test_classify_complex_research(classifier):
    props = classifier.classify_fast(
        "Research the latest AI safety techniques, summarize findings "
        "from multiple papers, compare approaches, and write a report"
    )
    assert props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
    assert props.multi_step is True
    assert props.estimated_steps >= 4


# ── Archetype 3: High-Risk Destructive ────────────────────────────────────────
def test_classify_high_risk_delete(classifier):
    props = classifier.classify_fast("delete the production database table users")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert props.reversibility == "irreversible"


def test_classify_critical_deploy(classifier):
    props = classifier.classify_fast("deploy new version to production and charge customers")
    assert props.risk == RiskLevel.CRITICAL


# ── Archetype 4: Coding Task ──────────────────────────────────────────────────
def test_classify_coding_task(classifier):
    props = classifier.classify_fast(
        "Write a Python function to parse JSON logs and extract error counts"
    )
    assert props.domain == Domain.TECHNICAL
    assert props.requires_code is True


# ── Archetype 5: Empty KB / Web Required ──────────────────────────────────────
def test_classify_web_required(classifier):
    props = classifier.classify_fast("What is the current price of Bitcoin today?")
    assert props.requires_web is True
    assert props.time_sensitivity == TimeSensitivity.REALTIME


# ── Archetype 6: Realtime Status ──────────────────────────────────────────────
def test_classify_realtime_status(classifier):
    props = classifier.classify_fast("Get the current status of the Kubernetes pods right now")
    assert props.time_sensitivity in (TimeSensitivity.REALTIME, TimeSensitivity.NORMAL)


# ── Archetype 7: Multi-Agent Research ─────────────────────────────────────────
def test_classify_multi_agent(classifier):
    props = classifier.classify_fast(
        "Analyze competitor products, compare pricing, evaluate technical capabilities, "
        "assess market positioning, and generate a strategic report with recommendations"
    )
    assert props.complexity == Complexity.EXPERT
    assert props.estimated_steps >= 5


# ── Archetype 8: High-Risk Financial ──────────────────────────────────────────
def test_classify_financial_operation(classifier):
    props = classifier.classify_fast("Transfer funds and charge the customer payment method")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert props.reversibility == "irreversible"


# ── Archetype 9: Analytical / Data ────────────────────────────────────────────
def test_classify_analytical(classifier):
    props = classifier.classify_fast(
        "Analyze last quarter sales data, compute trends, and build a forecast model"
    )
    assert props.domain in (Domain.ANALYTICAL, Domain.TECHNICAL)
    assert props.complexity in (Complexity.MEDIUM, Complexity.COMPLEX)


# ── Archetype 10: RPA / Browser ───────────────────────────────────────────────
def test_classify_rpa(classifier):
    props = classifier.classify_fast(
        "Open the admin dashboard, navigate to user settings, and export the user list"
    )
    assert props.domain in (Domain.OPERATIONAL, Domain.TECHNICAL)
    assert props.complexity in (Complexity.MEDIUM, Complexity.COMPLEX)


# ── Classifier confidence ─────────────────────────────────────────────────────
def test_obvious_goals_have_high_confidence(classifier):
    props = classifier.classify_fast("delete all records from users table in production")
    assert props.classifier_confidence >= 0.85


def test_ambiguous_goal_has_lower_confidence(classifier):
    props = classifier.classify_fast("do the thing")
    assert props.classifier_confidence < 0.9  # ambiguous goal
```

- [ ] **Step 4.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_goal_classifier.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 4.3: Implement `app/orchestration/goal_classifier.py`**

```python
"""GoalClassifier — two-tier classification of incoming goals.

Tier 1: Fast keyword-based (<1ms) — always runs.
Tier 2: LLM-based (~200ms) — runs only for MEDIUM complexity when confidence <= 0.85.

The output GoalProperties drives all downstream strategy selection.
"""
from __future__ import annotations

import re
from typing import Any

from app.orchestration.runtime_profile import (
    Complexity,
    Domain,
    GoalProperties,
    KnowledgeState,
    RiskLevel,
    TimeSensitivity,
)

# ── Risk keyword sets ─────────────────────────────────────────────────────────

_CRITICAL_RISK = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge",
    "rm -rf", "production", "prod", "overwrite", "payment",
    "charge", "billing", "transfer funds", "admin", "sudo",
    "root access", "send email blast", "publish release",
})

_HIGH_RISK = frozenset({
    "deploy", "migrate", "alter table", "modify schema", "send email",
    "send sms", "post to", "release", "revoke", "terminate",
    "disable account", "reset password", "grant admin",
})

_IRREVERSIBLE = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge",
    "payment", "transfer", "charge", "send email", "publish", "release",
})

# ── Complexity signals ────────────────────────────────────────────────────────

_COMPLEXITY_EXPERT = frozenset({
    "analyze", "research", "compare", "synthesize", "evaluate",
    "comprehensive", "strategic", "multi-step", "cross-reference",
    "architecture", "design", "forecast", "model",
})

_COMPLEXITY_COMPLEX = frozenset({
    "report", "summary", "breakdown", "multiple", "then", "after",
    "also", "and then", "followed by", "step by step",
})

_STEP_SEPARATORS = re.compile(r"\band\b|\bthen\b|\bafter\b|\bfollowed by\b|\balso\b|\bnext\b",
                               re.IGNORECASE)

# ── Web / realtime signals ────────────────────────────────────────────────────

_WEB_SIGNALS = frozenset({
    "latest", "current", "recent", "today", "news", "price",
    "version", "now", "2025", "2026", "live", "real-time", "right now",
})

# ── Domain signals ────────────────────────────────────────────────────────────

_TECHNICAL_SIGNALS = frozenset({
    "code", "function", "api", "database", "sql", "python", "javascript",
    "docker", "kubernetes", "git", "github", "aws", "gcp", "azure",
    "bug", "error", "deploy", "test", "script", "query", "schema",
})

_ANALYTICAL_SIGNALS = frozenset({
    "analyze", "analysis", "data", "metrics", "statistics", "forecast",
    "trend", "compare", "correlation", "regression", "model", "chart",
    "dashboard", "report", "kpi",
})

_CREATIVE_SIGNALS = frozenset({
    "write", "create", "draft", "generate", "compose", "design",
    "brainstorm", "ideate", "story", "blog", "email", "proposal",
})


class GoalClassifier:
    """Classifies goal text into GoalProperties for runtime profile assembly."""

    def classify_fast(self, goal: str) -> GoalProperties:
        """Keyword-based classification — always < 1ms."""
        lower = goal.lower()
        tokens = set(re.findall(r"\b\w+\b", lower))

        # ── Risk ──────────────────────────────────────────────────────────────
        risk = RiskLevel.LOW
        reversibility = "reversible"

        for phrase in _CRITICAL_RISK:
            if phrase in lower:
                risk = RiskLevel.CRITICAL
                reversibility = "irreversible"
                break

        if risk == RiskLevel.LOW:
            for phrase in _HIGH_RISK:
                if phrase in lower:
                    risk = RiskLevel.HIGH
                    break

        for phrase in _IRREVERSIBLE:
            if phrase in lower:
                reversibility = "irreversible"
                break

        # ── Web / realtime ────────────────────────────────────────────────────
        requires_web = bool(tokens & _WEB_SIGNALS) or any(p in lower for p in _WEB_SIGNALS)
        time_sensitivity = (
            TimeSensitivity.REALTIME if requires_web else TimeSensitivity.NORMAL
        )

        # ── Code ──────────────────────────────────────────────────────────────
        requires_code = bool(tokens & {"code", "function", "script", "python",
                                        "javascript", "sql", "query", "test"})

        # ── Complexity ────────────────────────────────────────────────────────
        step_count = len(_STEP_SEPARATORS.findall(goal)) + 1
        expert_hits = len(tokens & _COMPLEXITY_EXPERT)
        complex_hits = len(tokens & _COMPLEXITY_COMPLEX)

        if expert_hits >= 2 or step_count >= 5:
            complexity = Complexity.EXPERT
            estimated_steps = max(5, step_count)
        elif expert_hits >= 1 or complex_hits >= 2 or step_count >= 3:
            complexity = Complexity.COMPLEX
            estimated_steps = max(4, step_count)
        elif step_count >= 2 or complex_hits >= 1:
            complexity = Complexity.MEDIUM
            estimated_steps = max(2, step_count)
        else:
            complexity = Complexity.SIMPLE
            estimated_steps = 2

        # High-risk always bumps to at least MEDIUM (needs careful planning)
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) and complexity == Complexity.SIMPLE:
            complexity = Complexity.MEDIUM
            estimated_steps = max(3, estimated_steps)

        # ── Domain ────────────────────────────────────────────────────────────
        tech_score = len(tokens & _TECHNICAL_SIGNALS)
        anal_score = len(tokens & _ANALYTICAL_SIGNALS)
        crea_score = len(tokens & _CREATIVE_SIGNALS)
        domain_scores = {
            Domain.TECHNICAL: tech_score,
            Domain.ANALYTICAL: anal_score,
            Domain.CREATIVE: crea_score,
            Domain.OPERATIONAL: 0,
        }
        domain = max(domain_scores, key=lambda d: domain_scores[d])
        if domain_scores[domain] == 0:
            domain = Domain.OPERATIONAL

        # ── Confidence ───────────────────────────────────────────────────────
        # High confidence when strong risk or web signals are present
        confidence = 0.7
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            confidence = 0.95
        elif requires_web:
            confidence = 0.9
        elif complexity == Complexity.SIMPLE and not requires_code:
            confidence = 0.85
        elif expert_hits >= 2:
            confidence = 0.88
        # Low word count = ambiguous
        word_count = len(goal.split())
        if word_count <= 4:
            confidence = min(confidence, 0.65)

        return GoalProperties(
            raw_goal=goal,
            complexity=complexity,
            domain=domain,
            risk=risk,
            time_sensitivity=time_sensitivity,
            kb_state=KnowledgeState.UNKNOWN,
            reversibility=reversibility,
            multi_step=estimated_steps > 1,
            is_generative=bool(tokens & {"write", "generate", "create", "draft", "compose"}),
            requires_web=requires_web,
            requires_code=requires_code,
            requires_vision=bool(tokens & {"image", "photo", "screenshot", "vision", "ocr"}),
            estimated_steps=estimated_steps,
            classifier_confidence=confidence,
        )

    async def classify_with_llm(
        self,
        goal: str,
        provider: Any,
        fast_props: GoalProperties | None = None,
    ) -> GoalProperties:
        """LLM-assisted classification for MEDIUM complexity goals with low confidence.

        Falls back to fast classification if provider is unavailable.
        """
        if provider is None:
            return fast_props or self.classify_fast(goal)

        base = fast_props or self.classify_fast(goal)

        # Only invoke LLM for ambiguous medium-complexity goals
        if base.classifier_confidence > 0.85 or base.complexity != Complexity.MEDIUM:
            return base

        try:
            from app.providers.base import CompletionRequest, Message
            import json

            prompt = (
                "Classify this goal. Return ONLY JSON:\n"
                '{"complexity": "simple|medium|complex|expert", '
                '"domain": "technical|creative|analytical|operational|conversational", '
                '"risk": "low|medium|high|critical", '
                '"requires_web": true|false, '
                '"requires_code": true|false, '
                '"estimated_steps": 1-10, '
                '"confidence": 0.0-1.0}\n\n'
                f"Goal: {goal[:500]}"
            )
            resp = await provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=150,
                temperature=0.0,
            ))
            data = json.loads(resp.content.strip())
            return GoalProperties(
                raw_goal=goal,
                complexity=Complexity(data.get("complexity", base.complexity.value)),
                domain=Domain(data.get("domain", base.domain.value)),
                risk=RiskLevel(data.get("risk", base.risk.value)),
                time_sensitivity=base.time_sensitivity,
                kb_state=base.kb_state,
                reversibility=base.reversibility,
                multi_step=data.get("estimated_steps", base.estimated_steps) > 1,
                is_generative=base.is_generative,
                requires_web=data.get("requires_web", base.requires_web),
                requires_code=data.get("requires_code", base.requires_code),
                requires_vision=base.requires_vision,
                estimated_steps=int(data.get("estimated_steps", base.estimated_steps)),
                classifier_confidence=float(data.get("confidence", 0.8)),
            )
        except Exception:
            return base
```

- [ ] **Step 4.4: Run and confirm all 10 archetypes pass**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_goal_classifier.py -v --no-cov
```
Expected: `14 passed`

- [ ] **Step 4.5: Commit**

```bash
cd agent-verse-backend
git add app/orchestration/goal_classifier.py tests/orchestration/test_goal_classifier.py
git commit -m "feat(orchestration): add GoalClassifier with 10-archetype coverage"
```

---

## Task 5: PatternSelector

**Files:**
- Create: `app/orchestration/pattern_selector.py`
- Create: `tests/orchestration/test_pattern_selector.py`

- [ ] **Step 5.1: Write failing tests**

```python
# tests/orchestration/test_pattern_selector.py
"""PatternSelector must produce correct configs for goal archetypes."""
from __future__ import annotations
import pytest
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import (
    Complexity, RiskLevel, Domain, TimeSensitivity,
    GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig,
)
from app.orchestration.strategy_registry import build_default_registry


@pytest.fixture
def selector():
    return PatternSelector(registry=build_default_registry())


def test_simple_goal_gets_minimal_profile(selector):
    props = GoalProperties(raw_goal="list open tickets", complexity=Complexity.SIMPLE,
                           risk=RiskLevel.LOW)
    agent_cfg = selector.select_agent_patterns(props)
    rag_cfg = selector.select_rag_strategy(props)
    model_cfg = selector.select_model_plan(props)
    assert "react" in agent_cfg.reasoning
    assert rag_cfg.strategy in ("hybrid_rag", "naive_rag")
    assert model_cfg.cost_class == "low"


def test_complex_research_gets_cot_and_multihop(selector):
    props = GoalProperties(
        raw_goal="research AI safety and write comprehensive report",
        complexity=Complexity.EXPERT,
        risk=RiskLevel.LOW,
        requires_web=True,
    )
    agent_cfg = selector.select_agent_patterns(props)
    rag_cfg = selector.select_rag_strategy(props)
    assert "chain_of_thought" in agent_cfg.reasoning or "reflection" in agent_cfg.reasoning
    assert rag_cfg.web_fallback_enabled is True


def test_high_risk_always_gets_hitl(selector):
    props = GoalProperties(
        raw_goal="delete production db",
        complexity=Complexity.MEDIUM,
        risk=RiskLevel.CRITICAL,
        reversibility="irreversible",
    )
    security_cfg = selector.select_security_profile(props)
    assert security_cfg.hitl_required is True
    assert security_cfg.rollback_required is True
    assert security_cfg.audit_level in ("full", "forensic")


def test_critical_risk_cannot_be_downgraded(selector):
    """CRITICAL-priority safety rules must never be removable."""
    props = GoalProperties(
        raw_goal="wipe all data",
        risk=RiskLevel.CRITICAL,
        reversibility="irreversible",
    )
    security_cfg = selector.select_security_profile(props)
    # Even with no extra config, HITL must be required for CRITICAL
    assert security_cfg.hitl_required is True


def test_coding_task_gets_code_rag(selector):
    props = GoalProperties(
        raw_goal="write a Python parser",
        complexity=Complexity.MEDIUM,
        domain=Domain.TECHNICAL,
        requires_code=True,
    )
    rag_cfg = selector.select_rag_strategy(props)
    assert rag_cfg.chunking_strategy in ("semantic", "ast", "code")


def test_web_required_enables_web_fallback(selector):
    props = GoalProperties(
        raw_goal="current Bitcoin price",
        requires_web=True,
        time_sensitivity=TimeSensitivity.REALTIME,
    )
    rag_cfg = selector.select_rag_strategy(props)
    assert rag_cfg.web_fallback_enabled is True


def test_empty_kb_forces_web_fallback(selector):
    from app.orchestration.runtime_profile import KnowledgeState
    props = GoalProperties(
        raw_goal="find info on topic",
        kb_state=KnowledgeState.EMPTY,
    )
    rag_cfg = selector.select_rag_strategy(props)
    assert rag_cfg.web_fallback_enabled is True


def test_realtime_goal_gets_low_latency_model(selector):
    props = GoalProperties(
        raw_goal="get pod status now",
        time_sensitivity=TimeSensitivity.REALTIME,
        complexity=Complexity.SIMPLE,
    )
    model_cfg = selector.select_model_plan(props)
    assert model_cfg.latency_class == "realtime"
    assert model_cfg.cost_class == "low"


def test_expert_goal_gets_full_memory(selector):
    props = GoalProperties(
        raw_goal="comprehensive multi-domain analysis and strategic report",
        complexity=Complexity.EXPERT,
    )
    mem_cfg = selector.select_memory_cache_policy(props)
    assert mem_cfg.use_long_term_memory is True
    assert mem_cfg.use_execution_memory is True


def test_selection_reasons_populated(selector):
    props = GoalProperties(
        raw_goal="delete prod db",
        risk=RiskLevel.CRITICAL,
        reversibility="irreversible",
    )
    agent_cfg = selector.select_agent_patterns(props)
    assert len(agent_cfg.selection_reasons) > 0
```

- [ ] **Step 5.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_pattern_selector.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 5.3: Implement `app/orchestration/pattern_selector.py`**

```python
"""PatternSelector — translates GoalProperties into concrete strategy configs.

Rules are organized by priority:
  CRITICAL  — safety rules, inviolable, cannot be overridden
  HIGH      — strong quality signals
  MEDIUM    — optimization signals
  LOW       — default fill-ins

CRITICAL safety rules can only ADD patterns, never remove.
"""
from __future__ import annotations

from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    Complexity,
    EvalConfig,
    GoalProperties,
    KnowledgeState,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    RiskLevel,
    SecurityConfig,
    TimeSensitivity,
)
from app.orchestration.strategy_registry import StrategyRegistry


class PatternSelector:
    def __init__(self, registry: StrategyRegistry) -> None:
        self._registry = registry

    # ── Agent Patterns ────────────────────────────────────────────────────────

    def select_agent_patterns(self, props: GoalProperties) -> AgentPatternConfig:
        reasoning: list[str] = ["react"]
        multi_agent: list[str] = ["single_agent"]
        safety: list[str] = ["guardrails"]
        reasons: dict[str, str] = {"react": "default reasoning loop"}
        max_iter = 15
        persistence = False
        autonomy = "bounded-autonomous"

        # CRITICAL: High/critical risk always adds HITL + rollback
        if props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            safety = list(dict.fromkeys(safety + ["hitl", "rollback"]))
            reasons["hitl"] = f"risk={props.risk.value}"
            reasons["rollback"] = f"reversibility={props.reversibility}"
            autonomy = "supervised"

        if props.risk == RiskLevel.CRITICAL:
            safety = list(dict.fromkeys(safety + ["consensus_verification"]))
            reasons["consensus_verification"] = "critical risk requires consensus"

        # MEDIUM/HIGH: Reflection for complex goals
        if props.complexity in (Complexity.COMPLEX, Complexity.EXPERT):
            reasoning = list(dict.fromkeys(reasoning + ["chain_of_thought", "reflection"]))
            reasons["chain_of_thought"] = f"complexity={props.complexity.value}"
            reasons["reflection"] = "complex goals benefit from reflection"
            max_iter = 25

        if props.complexity == Complexity.EXPERT:
            max_iter = 50
            persistence = True
            if self._registry.is_available("goal_tree"):
                multi_agent = ["goal_tree"]
                reasons["goal_tree"] = "expert complexity → parallel sub-goals"

        # Code tasks: add self-refine if available
        if props.requires_code and self._registry.is_available("self_refine"):
            reasoning = list(dict.fromkeys(reasoning + ["self_refine"]))
            reasons["self_refine"] = "coding tasks benefit from iterative refinement"

        return AgentPatternConfig(
            reasoning=reasoning,
            rag=["hybrid_rag"],   # RAG selector handles this separately
            multi_agent=multi_agent,
            safety=safety,
            max_iterations=max_iter,
            persistence_mode=persistence,
            max_persistence_attempts=3,
            autonomy_mode=autonomy,
            selection_reasons=reasons,
        )

    # ── RAG Strategy ──────────────────────────────────────────────────────────

    def select_rag_strategy(self, props: GoalProperties) -> RAGStrategyConfig:
        strategy = "hybrid_rag"
        sources = ["knowledge_base"]
        chunking = "semantic"
        embedding = "default"
        reranker = "score"
        graph_strategy = "none"
        web_fallback = False
        max_tokens = 6000
        min_relevance = 0.35

        # Web required
        if props.requires_web or props.time_sensitivity == TimeSensitivity.REALTIME:
            web_fallback = True
            sources = list(dict.fromkeys(sources + ["web_search"]))
            strategy = "web_augmented_rag"

        # Empty / sparse KB
        if props.kb_state in (KnowledgeState.EMPTY, KnowledgeState.SPARSE):
            web_fallback = True
            if "web_search" not in sources:
                sources.append("web_search")

        # Expert complexity: agentic + multi-hop
        if props.complexity == Complexity.EXPERT:
            strategy = "agentic_rag"
            sources = list(dict.fromkeys(sources + ["long_term_memory", "knowledge_graph"]))
            graph_strategy = "entity"
            reranker = "rrf"
            max_tokens = 8000
            min_relevance = 0.25

        elif props.complexity == Complexity.COMPLEX:
            strategy = "hybrid_rag"
            sources = list(dict.fromkeys(sources + ["long_term_memory"]))
            reranker = "rrf"
            max_tokens = 7000

        # Code tasks: AST chunking
        if props.requires_code:
            chunking = "ast"
            embedding = "code"

        # Realtime: minimal retrieval
        if props.time_sensitivity == TimeSensitivity.REALTIME:
            max_tokens = 2000
            strategy = "naive_rag" if not web_fallback else strategy

        return RAGStrategyConfig(
            strategy=strategy,
            sources=sources,
            chunking_strategy=chunking,
            embedding_model=embedding,
            reranker=reranker,
            max_context_tokens=max_tokens,
            min_relevance_score=min_relevance,
            max_chunks_per_source=5,
            citation_required=True,
            deduplication_enabled=True,
            web_fallback_enabled=web_fallback,
            graph_strategy=graph_strategy,
        )

    # ── Model Plan ────────────────────────────────────────────────────────────

    def select_model_plan(self, props: GoalProperties) -> ModelPlanConfig:
        latency = "interactive"
        cost = "medium"

        if props.time_sensitivity == TimeSensitivity.REALTIME:
            latency = "realtime"
            cost = "low"
        elif props.complexity == Complexity.SIMPLE and props.risk == RiskLevel.LOW:
            cost = "low"
        elif props.complexity == Complexity.EXPERT or props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            cost = "high"

        if props.complexity == Complexity.SIMPLE:
            latency = "realtime"

        return ModelPlanConfig(
            planner="default",
            executor="default",
            verifier="default",
            embedder="default",
            classifier="default",
            cost_class=cost,
            latency_class=latency,
        )

    # ── Security Profile ──────────────────────────────────────────────────────

    def select_security_profile(self, props: GoalProperties) -> SecurityConfig:
        hitl = False
        consensus = False
        rollback = False
        audit = "standard"
        guardrail = "default"
        governance = "free"
        sandbox = False
        compliance: list[str] = []

        # CRITICAL inviolable safety rules
        if props.risk == RiskLevel.CRITICAL:
            hitl = True
            consensus = True
            rollback = True
            audit = "forensic"
            guardrail = "strict"
            governance = "enterprise"

        elif props.risk == RiskLevel.HIGH:
            hitl = True
            rollback = True
            audit = "full"
            guardrail = "strict"

        elif props.risk == RiskLevel.MEDIUM:
            audit = "full"
            guardrail = "default"

        if props.reversibility == "irreversible":
            rollback = True  # always register rollback for irreversible

        if props.requires_code:
            sandbox = True

        return SecurityConfig(
            guardrail_bundle=guardrail,
            governance_bundle=governance,
            hitl_required=hitl,
            consensus_required=consensus,
            rollback_required=rollback,
            audit_level=audit,
            compliance_tags=compliance,
            sandbox_required=sandbox,
        )

    # ── Memory / Cache Policy ─────────────────────────────────────────────────

    def select_memory_cache_policy(self, props: GoalProperties) -> MemoryCacheConfig:
        use_ltm = props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
        use_kg = props.complexity == Complexity.EXPERT
        reflexion = props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
        semantic_cache = (
            props.complexity == Complexity.SIMPLE
            and props.risk == RiskLevel.LOW
            and props.time_sensitivity != TimeSensitivity.REALTIME
        )

        return MemoryCacheConfig(
            use_session_memory=True,
            use_execution_memory=True,
            use_long_term_memory=use_ltm,
            use_semantic_cache=semantic_cache,
            use_knowledge_graph=use_kg,
            reflexion_enabled=reflexion,
        )

    # ── Eval Config ───────────────────────────────────────────────────────────

    def select_eval_config(self, props: GoalProperties) -> EvalConfig:
        suite = "default"
        if props.requires_code:
            suite = "coding"
        elif props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            suite = "security"
        elif props.complexity == Complexity.EXPERT:
            suite = "rag"

        return EvalConfig(
            enabled=True,
            eval_suite=suite,
            score_threshold=0.72,
            reflexion_enabled=True,
            creates_regression_case_on_failure=True,
        )
```

- [ ] **Step 5.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_pattern_selector.py -v --no-cov
```
Expected: `10 passed`

- [ ] **Step 5.5: Commit**

```bash
cd agent-verse-backend
git add app/orchestration/pattern_selector.py tests/orchestration/test_pattern_selector.py
git commit -m "feat(orchestration): add PatternSelector with safety-first rules for 10 archetypes"
```

---

## Task 6: DecisionTrace + RuntimeProfileBuilder

**Files:**
- Create: `app/orchestration/decision_trace.py`
- Create: `app/orchestration/runtime_profile_builder.py`
- Create: `tests/orchestration/test_runtime_profile_builder.py`

- [ ] **Step 6.1: Write failing tests**

```python
# tests/orchestration/test_runtime_profile_builder.py
"""RuntimeProfileBuilder integration tests."""
from __future__ import annotations
import time
import pytest
from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, RiskLevel, Complexity,
)
from app.orchestration.strategy_registry import build_default_registry


@pytest.fixture
def builder():
    return RuntimeProfileBuilder(registry=build_default_registry())


async def test_build_profile_simple_lookup(builder):
    profile = await builder.build("list all open Jira tickets", tenant_id="t1", goal_id="g1")
    assert isinstance(profile, GoalRuntimeProfile)
    assert profile.tenant_id == "t1"
    assert profile.goal_id == "g1"
    assert profile.properties.complexity == Complexity.SIMPLE
    assert profile.security.hitl_required is False


async def test_build_profile_critical_risk(builder):
    profile = await builder.build(
        "delete all records from the production database",
        tenant_id="t1", goal_id="g2"
    )
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True
    assert profile.security.audit_level in ("full", "forensic")
    assert profile.properties.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)


async def test_build_records_assembly_latency(builder):
    profile = await builder.build("list users", tenant_id="t1", goal_id="g3")
    assert profile.assembly_latency_ms >= 0.0
    assert profile.assembly_latency_ms < 500.0   # must complete fast


async def test_build_profile_is_json_serializable(builder):
    import json
    profile = await builder.build("research latest ML papers", tenant_id="t1", goal_id="g4")
    data = profile.to_dict()
    json.dumps(data)   # must not raise


async def test_build_web_required_profile(builder):
    profile = await builder.build("what is the current BTC price today?", tenant_id="t1", goal_id="g5")
    assert profile.rag_strategy.web_fallback_enabled is True


async def test_decision_trace_attached(builder):
    from app.orchestration.decision_trace import DecisionTrace
    profile, trace = await builder.build_with_trace(
        "analyze sales data and forecast Q3", tenant_id="t1", goal_id="g6"
    )
    assert isinstance(trace, DecisionTrace)
    assert len(trace.decisions) > 0
    assert trace.goal_id == "g6"
```

- [ ] **Step 6.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_runtime_profile_builder.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 6.3: Implement `app/orchestration/decision_trace.py`**

```python
"""DecisionTrace — serializable record of every strategy selection decision."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SelectionDecision:
    selector: str         # which selector made the decision
    dimension: str        # what dimension was selected (e.g. "rag_strategy")
    selected: Any         # the selected value
    reason: str           # why it was selected
    alternatives: list[Any] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class DecisionTrace:
    goal_id: str
    tenant_id: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    decisions: list[SelectionDecision] = field(default_factory=list)
    total_latency_ms: float = 0.0
    created_at: float = field(default_factory=time.time)

    def add(
        self,
        selector: str,
        dimension: str,
        selected: Any,
        reason: str,
        alternatives: list[Any] | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        self.decisions.append(SelectionDecision(
            selector=selector,
            dimension=dimension,
            selected=selected,
            reason=reason,
            alternatives=alternatives or [],
            latency_ms=latency_ms,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "goal_id": self.goal_id,
            "tenant_id": self.tenant_id,
            "total_latency_ms": self.total_latency_ms,
            "decisions": [
                {
                    "selector": d.selector,
                    "dimension": d.dimension,
                    "selected": str(d.selected),
                    "reason": d.reason,
                    "alternatives": [str(a) for a in d.alternatives],
                    "latency_ms": d.latency_ms,
                }
                for d in self.decisions
            ],
        }

    def to_sse_event(self) -> dict[str, Any]:
        return {
            "type": "runtime_profile_selected",
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "decisions": self.to_dict()["decisions"],
            "total_latency_ms": self.total_latency_ms,
        }
```

- [ ] **Step 6.4: Implement `app/orchestration/runtime_profile_builder.py`**

```python
"""RuntimeProfileBuilder — assembles a complete GoalRuntimeProfile for a goal.

This is the main entry point for the orchestration layer.
Called from GoalService.submit_goal() behind the DYNAMIC_ORCHESTRATION flag.
"""
from __future__ import annotations

import time
from typing import Any

from app.orchestration.decision_trace import DecisionTrace
from app.orchestration.goal_classifier import GoalClassifier
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import GoalRuntimeProfile
from app.orchestration.strategy_registry import StrategyRegistry, build_default_registry


class RuntimeProfileBuilder:
    """Assembles GoalRuntimeProfile by running classifier + all strategy selectors."""

    def __init__(
        self,
        *,
        registry: StrategyRegistry | None = None,
        llm_provider: Any = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._classifier = GoalClassifier()
        self._selector = PatternSelector(registry=self._registry)
        self._provider = llm_provider

    async def build(
        self,
        goal: str,
        *,
        tenant_id: str,
        goal_id: str,
        kb_state: str = "unknown",
        agent_config: dict[str, Any] | None = None,
    ) -> GoalRuntimeProfile:
        """Build the runtime profile. Does not emit SSE events."""
        profile, _ = await self.build_with_trace(
            goal, tenant_id=tenant_id, goal_id=goal_id,
            kb_state=kb_state, agent_config=agent_config,
        )
        return profile

    async def build_with_trace(
        self,
        goal: str,
        *,
        tenant_id: str,
        goal_id: str,
        kb_state: str = "unknown",
        agent_config: dict[str, Any] | None = None,
    ) -> tuple[GoalRuntimeProfile, DecisionTrace]:
        """Build profile and return the full decision trace."""
        t0 = time.perf_counter()
        trace = DecisionTrace(goal_id=goal_id, tenant_id=tenant_id)

        # 1. Classify goal
        t_cls = time.perf_counter()
        props = self._classifier.classify_fast(goal)
        cls_ms = (time.perf_counter() - t_cls) * 1000
        trace.add("GoalClassifier", "properties", props.complexity.value,
                  f"fast classification: confidence={props.classifier_confidence:.2f}",
                  latency_ms=cls_ms)

        # Inject kb_state
        from app.orchestration.runtime_profile import KnowledgeState
        try:
            props.kb_state = KnowledgeState(kb_state)  # type: ignore[attr-defined]
        except ValueError:
            pass

        # 2. Select agent patterns
        agent_cfg = self._selector.select_agent_patterns(props)
        trace.add("PatternSelector", "agent_patterns",
                  agent_cfg.reasoning, str(agent_cfg.selection_reasons))

        # 3. Select RAG strategy
        rag_cfg = self._selector.select_rag_strategy(props)
        trace.add("PatternSelector", "rag_strategy",
                  rag_cfg.strategy, f"sources={rag_cfg.sources}")

        # 4. Select model plan
        model_cfg = self._selector.select_model_plan(props)
        trace.add("PatternSelector", "model_plan",
                  model_cfg.cost_class, f"latency={model_cfg.latency_class}")

        # 5. Select security profile
        security_cfg = self._selector.select_security_profile(props)
        trace.add("PatternSelector", "security",
                  f"hitl={security_cfg.hitl_required}",
                  f"risk={props.risk.value} audit={security_cfg.audit_level}")

        # 6. Select memory/cache policy
        mem_cfg = self._selector.select_memory_cache_policy(props)
        trace.add("PatternSelector", "memory_cache",
                  f"ltm={mem_cfg.use_long_term_memory}", "")

        # 7. Select eval config
        eval_cfg = self._selector.select_eval_config(props)
        trace.add("PatternSelector", "eval_config",
                  eval_cfg.eval_suite, f"threshold={eval_cfg.score_threshold}")

        total_ms = (time.perf_counter() - t0) * 1000
        trace.total_latency_ms = total_ms

        profile = GoalRuntimeProfile(
            goal_id=goal_id,
            tenant_id=tenant_id,
            properties=props,
            agent_patterns=agent_cfg,
            rag_strategy=rag_cfg,
            model_plan=model_cfg,
            security=security_cfg,
            memory_cache=mem_cfg,
            eval_config=eval_cfg,
            assembly_latency_ms=total_ms,
        )

        return profile, trace
```

- [ ] **Step 6.5: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_runtime_profile_builder.py -v --no-cov
```
Expected: `6 passed`

- [ ] **Step 6.6: Run entire orchestration test suite**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/ -v --no-cov
```
Expected: All 35+ tests pass

- [ ] **Step 6.7: Commit**

```bash
cd agent-verse-backend
git add app/orchestration/decision_trace.py app/orchestration/runtime_profile_builder.py \
    tests/orchestration/test_runtime_profile_builder.py
git commit -m "feat(orchestration): add DecisionTrace + RuntimeProfileBuilder — P0 contracts complete"
```
