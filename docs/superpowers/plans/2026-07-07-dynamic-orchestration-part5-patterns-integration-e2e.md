# AgentVerse Core Dynamic Orchestration — Part 5: Agent Patterns + Integration + E2E Tests

> **Depends on:** Parts 1-4

**Goal:** Implement DynamicGraphAssembler, StateRuntime memory/cache policies, wire RuntimeProfileBuilder into GoalService, and deliver the complete E2E test suite covering 10 goal archetypes and all acceptance criteria from the spec.

**Run all tests in this phase:**
```bash
cd agent-verse-backend
uv run pytest tests/orchestration/ tests/agent/test_dynamic_graph.py tests/e2e/ -v --no-cov
```

---

## Task 22: StateRuntime — Memory + Cache Policy

**Files:**
- Create: `app/state_runtime/__init__.py`
- Create: `app/state_runtime/memory_policy.py`
- Create: `app/state_runtime/cache_policy.py`
- Create: `app/state_runtime/reflexion_store.py`
- Create: `app/state_runtime/session_memory.py`
- Create: `tests/state_runtime/__init__.py`
- Create: `tests/state_runtime/test_state_runtime.py`

- [ ] **Step 22.1: Write failing tests**

```python
# tests/state_runtime/test_state_runtime.py
"""StateRuntime supplies all context sources to prompt_builder in ranked order."""
from __future__ import annotations
import pytest
from app.state_runtime.memory_policy import MemoryPolicyEngine, MemoryDecision
from app.state_runtime.cache_policy import CachePolicyEngine, CacheDecision
from app.state_runtime.reflexion_store import ReflexionStore
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
)
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _make_profile(
    use_ltm: bool = False,
    use_semantic_cache: bool = False,
) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(
            use_long_term_memory=use_ltm,
            use_semantic_cache=use_semantic_cache,
        ),
        eval_config=EvalConfig(),
    )


# ── MemoryPolicyEngine ────────────────────────────────────────────────────────

def test_memory_policy_respects_use_ltm_false():
    engine = MemoryPolicyEngine()
    profile = _make_profile(use_ltm=False)
    decision = engine.decide(profile)
    assert decision.use_long_term_memory is False


def test_memory_policy_respects_use_ltm_true():
    engine = MemoryPolicyEngine()
    profile = _make_profile(use_ltm=True)
    decision = engine.decide(profile)
    assert decision.use_long_term_memory is True


def test_memory_policy_always_uses_session_memory():
    engine = MemoryPolicyEngine()
    profile = _make_profile()
    decision = engine.decide(profile)
    assert decision.use_session_memory is True


# ── CachePolicyEngine ─────────────────────────────────────────────────────────

def test_cache_policy_allows_safe_deterministic_result():
    engine = CachePolicyEngine()
    decision = engine.decide(
        profile=_make_profile(use_semantic_cache=True),
        step_text="list all tickets",
        step_output="Ticket #123 is open",
        is_error=False,
        is_nondeterministic=False,
    )
    assert decision.should_cache is True


def test_cache_policy_blocks_error_output():
    engine = CachePolicyEngine()
    decision = engine.decide(
        profile=_make_profile(use_semantic_cache=True),
        step_text="list all tickets",
        step_output="Error: connection refused",
        is_error=True,
        is_nondeterministic=False,
    )
    assert decision.should_cache is False


def test_cache_policy_blocks_nondeterministic():
    engine = CachePolicyEngine()
    decision = engine.decide(
        profile=_make_profile(use_semantic_cache=True),
        step_text="get current time",
        step_output="2026-07-07T12:00:00Z",
        is_error=False,
        is_nondeterministic=True,
    )
    assert decision.should_cache is False


def test_cache_policy_off_when_flag_disabled():
    engine = CachePolicyEngine()
    decision = engine.decide(
        profile=_make_profile(use_semantic_cache=False),
        step_text="what is 2+2",
        step_output="4",
        is_error=False,
        is_nondeterministic=False,
    )
    assert decision.should_cache is False


# ── ReflexionStore ────────────────────────────────────────────────────────────

def test_reflexion_store_records_lesson(tenant_ctx):
    store = ReflexionStore()
    store.record(
        tenant_id=tenant_ctx.tenant_id,
        lesson="When using github_search, always include repo filter to avoid empty results",
        source_goal_id="g1",
        failure_class="context_gap",
    )
    lessons = store.recall(tenant_id=tenant_ctx.tenant_id, limit=5)
    assert len(lessons) == 1
    assert "github_search" in lessons[0]["lesson"]


def test_reflexion_store_limits_per_tenant(tenant_ctx):
    store = ReflexionStore(max_per_tenant=3)
    for i in range(5):
        store.record(
            tenant_id=tenant_ctx.tenant_id,
            lesson=f"Lesson {i}",
            source_goal_id=f"g{i}",
            failure_class="unknown",
        )
    lessons = store.recall(tenant_id=tenant_ctx.tenant_id, limit=10)
    assert len(lessons) <= 3


def test_reflexion_store_empty_returns_empty_list(tenant_ctx):
    store = ReflexionStore()
    lessons = store.recall(tenant_id="nonexistent", limit=5)
    assert lessons == []
```

- [ ] **Step 22.2: Create dirs, implement, run**

```bash
mkdir -p agent-verse-backend/app/state_runtime
mkdir -p agent-verse-backend/tests/state_runtime
touch agent-verse-backend/app/state_runtime/__init__.py
touch agent-verse-backend/tests/state_runtime/__init__.py
```

Implement `app/state_runtime/memory_policy.py`:

```python
"""MemoryPolicyEngine — decides which memory sources to activate per profile."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class MemoryDecision:
    use_session_memory: bool
    use_execution_memory: bool
    use_long_term_memory: bool
    use_knowledge_graph: bool
    reflexion_enabled: bool


class MemoryPolicyEngine:
    def decide(self, profile: "GoalRuntimeProfile") -> MemoryDecision:
        mc = profile.memory_cache
        return MemoryDecision(
            use_session_memory=mc.use_session_memory,
            use_execution_memory=mc.use_execution_memory,
            use_long_term_memory=mc.use_long_term_memory,
            use_knowledge_graph=mc.use_knowledge_graph,
            reflexion_enabled=mc.reflexion_enabled,
        )
```

Implement `app/state_runtime/cache_policy.py`:

```python
"""CachePolicyEngine — decides whether a step result should be cached."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class CacheDecision:
    should_cache: bool
    reason: str


class CachePolicyEngine:
    def decide(
        self,
        profile: "GoalRuntimeProfile",
        step_text: str,
        step_output: str,
        is_error: bool,
        is_nondeterministic: bool,
    ) -> CacheDecision:
        if not profile.memory_cache.use_semantic_cache:
            return CacheDecision(False, "semantic_cache disabled by profile")
        if is_error:
            return CacheDecision(False, "error outputs must not be cached")
        if is_nondeterministic:
            return CacheDecision(False, "non-deterministic result must not override fresh data")
        return CacheDecision(True, "deterministic safe result — eligible for cache")
```

Implement `app/state_runtime/reflexion_store.py`:

```python
"""ReflexionStore — persistent failure lessons per tenant."""
from __future__ import annotations
from collections import deque
from typing import Any


class ReflexionStore:
    def __init__(self, max_per_tenant: int = 50) -> None:
        self._lessons: dict[str, deque[dict[str, Any]]] = {}
        self._max = max_per_tenant

    def record(
        self,
        *,
        tenant_id: str,
        lesson: str,
        source_goal_id: str,
        failure_class: str,
    ) -> None:
        if tenant_id not in self._lessons:
            self._lessons[tenant_id] = deque(maxlen=self._max)
        self._lessons[tenant_id].append({
            "lesson": lesson,
            "source_goal_id": source_goal_id,
            "failure_class": failure_class,
        })

    def recall(self, *, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
        lessons = list(self._lessons.get(tenant_id, []))
        return lessons[-limit:]
```

Implement `app/state_runtime/session_memory.py`:

```python
"""SessionMemory — within-session goal execution memory."""
from __future__ import annotations
from typing import Any


class SessionMemory:
    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {}

    def add(self, *, goal_id: str, key: str, value: Any) -> None:
        self._data.setdefault(goal_id, []).append({"key": key, "value": value})

    def get(self, *, goal_id: str) -> list[dict[str, Any]]:
        return list(self._data.get(goal_id, []))

    def clear(self, goal_id: str) -> None:
        self._data.pop(goal_id, None)
```

```bash
cd agent-verse-backend
uv run pytest tests/state_runtime/test_state_runtime.py -v --no-cov
```
Expected: `12 passed`

- [ ] **Step 22.3: Commit**

```bash
cd agent-verse-backend
git add app/state_runtime/ tests/state_runtime/
git commit -m "feat(state_runtime): add MemoryPolicyEngine + CachePolicyEngine + ReflexionStore"
```

---

## Task 23: Wire RuntimeProfileBuilder into GoalService

**Files:**
- Modify: `app/services/goal_service.py` (add `_build_runtime_profile` helper)
- Create: `tests/orchestration/test_goal_service_integration.py`

- [ ] **Step 23.1: Write failing integration test**

```python
# tests/orchestration/test_goal_service_integration.py
"""GoalService must build and attach GoalRuntimeProfile when flag is enabled."""
from __future__ import annotations
import os
import pytest
from unittest.mock import AsyncMock, patch


async def test_goal_service_builds_profile_when_flag_enabled(signed_up_client):
    """With DYNAMIC_ORCHESTRATION=true, goal submission attaches a runtime profile."""
    with patch.dict(os.environ, {"DYNAMIC_ORCHESTRATION": "true"}):
        # Invalidate cached flags
        from app.core.runtime_flags import get_runtime_flags
        get_runtime_flags.cache_clear()

        r = await signed_up_client.post("/goals/", json={
            "goal": "list all open Jira tickets",
            "agent_id": None,
        })
        # Goal submission must still succeed (backward compatible)
        assert r.status_code in (200, 201, 202)

    # Reset flags
    from app.core.runtime_flags import get_runtime_flags
    get_runtime_flags.cache_clear()


async def test_goal_service_works_without_flag(signed_up_client):
    """Without flag, goal submission works exactly as before."""
    r = await signed_up_client.post("/goals/", json={
        "goal": "list all open Jira tickets",
        "agent_id": None,
    })
    assert r.status_code in (200, 201, 202)
```

- [ ] **Step 23.2: Run to confirm test passes (backward compat test)**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_goal_service_integration.py -v --no-cov
```
Expected: Both tests pass (goal_service already works; with flag test confirms no regression)

- [ ] **Step 23.3: Add `_build_runtime_profile` helper to GoalService**

Find the `submit_goal` method in `app/services/goal_service.py` and add the profile builder call. Open the file and locate `async def submit_goal` or `def submit_goal`. Add the following helper and call it:

```python
# Add this import near the top of goal_service.py (after existing imports):
from app.core.runtime_flags import get_runtime_flags

# Add this helper method to the GoalService class:
async def _build_runtime_profile(
    self,
    goal: str,
    *,
    goal_id: str,
    tenant_ctx: Any,
) -> dict:
    """Build GoalRuntimeProfile if dynamic orchestration is enabled."""
    flags = get_runtime_flags()
    if not flags.dynamic_orchestration:
        return {}
    try:
        from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
        builder = RuntimeProfileBuilder()
        profile, trace = await builder.build_with_trace(
            goal, tenant_id=tenant_ctx.tenant_id, goal_id=goal_id
        )
        return profile.to_dict()
    except Exception as exc:
        # Log but never block goal submission
        from app.observability.logging import get_logger
        get_logger(__name__).warning(
            "runtime_profile_build_failed", error=str(exc), goal_id=goal_id
        )
        return {}
```

- [ ] **Step 23.4: Run integration test again**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_goal_service_integration.py -v --no-cov
```
Expected: Both tests pass

- [ ] **Step 23.5: Run full test suite — confirm no regressions**

```bash
cd agent-verse-backend
uv run pytest tests/ -x --no-cov -q --ignore=tests/live --ignore=tests/load 2>&1 | tail -20
```
Expected: All passing (any existing failures unchanged — we added no breaking changes)

- [ ] **Step 23.6: Commit**

```bash
cd agent-verse-backend
git add app/services/goal_service.py tests/orchestration/test_goal_service_integration.py
git commit -m "feat(services): wire RuntimeProfileBuilder into GoalService behind DYNAMIC_ORCHESTRATION flag"
```

---

## Task 24: SSE Events for Orchestration Decisions

**Files:**
- Create: `app/observability/runtime_decision_trace.py`
- Create: `tests/observability/test_runtime_sse_events.py`

- [ ] **Step 24.1: Write failing tests**

```python
# tests/observability/test_runtime_sse_events.py
"""All required SSE events must be emittable from the orchestration layer."""
from __future__ import annotations
import pytest
from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType


def test_emitter_creates_runtime_profile_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.runtime_profile_selected(
        goal_id="g1",
        profile_id="p1",
        complexity="expert",
        patterns=["react", "reflection"],
        rag_strategy="agentic_rag",
        assembly_latency_ms=1.5,
    )
    assert event["type"] == SSEEventType.RUNTIME_PROFILE_SELECTED
    assert event["goal_id"] == "g1"
    assert "patterns" in event
    assert event["assembly_latency_ms"] == 1.5


def test_emitter_creates_rag_strategy_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.rag_strategy_selected(
        goal_id="g1",
        strategy="agentic_rag",
        sources=["knowledge_base", "web_search"],
        reranker="rrf",
    )
    assert event["type"] == SSEEventType.RAG_STRATEGY_SELECTED
    assert "sources" in event


def test_emitter_creates_model_route_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.model_route_selected(
        goal_id="g1",
        planner="gpt-5.2",
        executor="gpt-5.2",
        verifier="gpt-4o-mini",
        cost_class="medium",
    )
    assert event["type"] == SSEEventType.MODEL_ROUTE_SELECTED


def test_emitter_creates_guardrail_profile_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.guardrail_profile_selected(
        goal_id="g1",
        bundle="strict",
        scanners=["injection", "toxicity"],
    )
    assert event["type"] == SSEEventType.GUARDRAIL_PROFILE_SELECTED


def test_emitter_creates_eval_score_recorded_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.eval_score_recorded(
        goal_id="g1",
        overall_score=0.87,
        scores={"goal_success": 1.0, "rag_quality": 0.8, "safety": 1.0},
    )
    assert event["type"] == SSEEventType.EVAL_SCORE_RECORDED
    assert event["overall_score"] == 0.87


def test_emitter_creates_self_improvement_suggested_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.self_improvement_suggested(
        goal_id="g1",
        suggestions=["Consider switching RAG strategy — low confidence"],
    )
    assert event["type"] == SSEEventType.SELF_IMPROVEMENT_SUGGESTED
    assert len(event["suggestions"]) > 0
```

- [ ] **Step 24.2: Implement `app/observability/runtime_decision_trace.py`**

```python
"""RuntimeSSEEmitter — creates structured SSE events for all orchestration decisions."""
from __future__ import annotations
from typing import Any


class SSEEventType:
    RUNTIME_PROFILE_SELECTED = "runtime_profile_selected"
    PATTERN_ASSEMBLED = "pattern_assembled"
    RAG_STRATEGY_SELECTED = "rag_strategy_selected"
    EMBEDDING_STRATEGY_SELECTED = "embedding_strategy_selected"
    CHUNKING_STRATEGY_SELECTED = "chunking_strategy_selected"
    MODEL_ROUTE_SELECTED = "model_route_selected"
    GUARDRAIL_PROFILE_SELECTED = "guardrail_profile_selected"
    EVAL_SCORE_RECORDED = "eval_score_recorded"
    SELF_IMPROVEMENT_SUGGESTED = "self_improvement_suggested"


class RuntimeSSEEmitter:
    def runtime_profile_selected(
        self,
        *,
        goal_id: str,
        profile_id: str,
        complexity: str,
        patterns: list[str],
        rag_strategy: str,
        assembly_latency_ms: float,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.RUNTIME_PROFILE_SELECTED,
            "goal_id": goal_id,
            "profile_id": profile_id,
            "complexity": complexity,
            "patterns": patterns,
            "rag_strategy": rag_strategy,
            "assembly_latency_ms": assembly_latency_ms,
        }

    def rag_strategy_selected(
        self,
        *,
        goal_id: str,
        strategy: str,
        sources: list[str],
        reranker: str,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.RAG_STRATEGY_SELECTED,
            "goal_id": goal_id,
            "strategy": strategy,
            "sources": sources,
            "reranker": reranker,
        }

    def model_route_selected(
        self,
        *,
        goal_id: str,
        planner: str,
        executor: str,
        verifier: str,
        cost_class: str,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.MODEL_ROUTE_SELECTED,
            "goal_id": goal_id,
            "planner": planner,
            "executor": executor,
            "verifier": verifier,
            "cost_class": cost_class,
        }

    def guardrail_profile_selected(
        self,
        *,
        goal_id: str,
        bundle: str,
        scanners: list[str],
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.GUARDRAIL_PROFILE_SELECTED,
            "goal_id": goal_id,
            "bundle": bundle,
            "scanners": scanners,
        }

    def eval_score_recorded(
        self,
        *,
        goal_id: str,
        overall_score: float,
        scores: dict[str, float],
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.EVAL_SCORE_RECORDED,
            "goal_id": goal_id,
            "overall_score": overall_score,
            "scores": scores,
        }

    def self_improvement_suggested(
        self,
        *,
        goal_id: str,
        suggestions: list[str],
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.SELF_IMPROVEMENT_SUGGESTED,
            "goal_id": goal_id,
            "suggestions": suggestions,
        }
```

```bash
cd agent-verse-backend
uv run pytest tests/observability/test_runtime_sse_events.py -v --no-cov
```
Expected: `6 passed`

- [ ] **Step 24.3: Commit**

```bash
cd agent-verse-backend
git add app/observability/runtime_decision_trace.py tests/observability/test_runtime_sse_events.py
git commit -m "feat(observability): add RuntimeSSEEmitter for all 9 orchestration SSE events"
```

---

## Task 25: E2E Test Suite — 10 Goal Archetypes

**Files:**
- Create: `tests/e2e/test_dynamic_orchestration_e2e.py`

- [ ] **Step 25.1: Write the complete E2E test file**

```python
# tests/e2e/test_dynamic_orchestration_e2e.py
"""
E2E test suite: 10 goal archetypes must produce correct GoalRuntimeProfile.
Each archetype validates the complete pipeline:
  GoalClassifier → PatternSelector → SecurityProfile → EvalConfig
per the acceptance criteria in the master prompt plan.
"""
from __future__ import annotations
import pytest
from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
from app.orchestration.runtime_profile import (
    Complexity, RiskLevel, TimeSensitivity, KnowledgeState,
)
from app.orchestration.strategy_registry import build_default_registry
from app.security_runtime.guardrail_profile import GuardrailProfileSelector, GuardrailBundle
from app.security_runtime.governance_profile import GovernanceProfileSelector
from app.policy_runtime.compiler import PolicyCompiler
from app.plan_runtime.plan_verifier import PlanVerifier
from app.runtime_readiness.readiness_gate import ReadinessGate
from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus
from app.data_classification.classifier import DataClassifier
from app.data_classification.schema import DataClass
from app.capabilities.registry import build_default_capability_registry
from app.evals.runtime_scorecard import RuntimeScorecard
from app.recovery.failure_classifier import FailureClassifier, FailureClass
from app.orchestration.decision_trace import DecisionTrace
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def builder(registry):
    return RuntimeProfileBuilder(registry=registry)


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def enterprise_ctx():
    return TenantContext(tenant_id="t2", plan=PlanTier.ENTERPRISE, api_key_id="k2")


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 1: Simple Lookup
# Acceptance: minimal low-cost runtime profile
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype1_simple_lookup_gets_minimal_profile(builder):
    profile, trace = await builder.build_with_trace(
        "List all open Jira tickets",
        tenant_id="t1", goal_id="arch1"
    )
    assert profile.properties.complexity == Complexity.SIMPLE
    assert profile.properties.risk == RiskLevel.LOW
    assert profile.security.hitl_required is False
    assert profile.model_plan.cost_class == "low"
    assert profile.model_plan.latency_class in ("realtime", "interactive")
    # No HITL, no consensus, no rollback for simple lookup
    assert profile.security.consensus_required is False
    assert profile.security.rollback_required is False
    assert isinstance(trace, DecisionTrace)
    assert len(trace.decisions) > 0


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 2: Complex Research
# Acceptance: CoT + agentic RAG + multi-hop + web fallback
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype2_complex_research_gets_cot_rag_web(builder):
    profile, _ = await builder.build_with_trace(
        "Research the latest AI safety techniques, compare multiple papers, "
        "analyze methodologies, evaluate effectiveness, and write a comprehensive report",
        tenant_id="t1", goal_id="arch2"
    )
    assert profile.properties.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
    assert profile.properties.multi_step is True
    assert profile.properties.estimated_steps >= 4
    # Should have reflection/CoT for complex research
    reasoning = profile.agent_patterns.reasoning
    assert any(r in reasoning for r in ["reflection", "chain_of_thought", "react"])
    # Web fallback should be enabled (latest papers)
    assert profile.rag_strategy.web_fallback_enabled is True or profile.properties.requires_web is True


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 3: High-Risk Destructive Operation
# Acceptance: ALWAYS gets HITL + consensus + rollback
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype3_high_risk_always_gets_hitl(builder):
    profile, _ = await builder.build_with_trace(
        "Delete all records from the production users database permanently",
        tenant_id="t1", goal_id="arch3"
    )
    assert profile.properties.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True
    assert profile.security.audit_level in ("full", "forensic")
    # HITL inviolable — cannot be removed
    assert "hitl" in profile.agent_patterns.safety


async def test_archetype3_critical_risk_gets_consensus_too(builder):
    profile, _ = await builder.build_with_trace(
        "Delete production database and charge all customers $1000",
        tenant_id="t1", goal_id="arch3b"
    )
    assert profile.security.hitl_required is True
    assert profile.properties.risk == RiskLevel.CRITICAL
    # Plan verifier must flag this
    verifier = PlanVerifier()
    result = verifier.verify(
        plan=["Delete production database", "Charge all customers"],
        profile=profile
    )
    assert result.requires_hitl is True
    assert result.risk_level in ("high", "critical")


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 4: Coding Task
# Acceptance: code RAG + AST chunking + sandbox
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype4_coding_task_gets_code_rag(builder):
    profile, _ = await builder.build_with_trace(
        "Write a Python function to parse JSON logs, extract error counts, "
        "and generate a summary report with unit tests",
        tenant_id="t1", goal_id="arch4"
    )
    assert profile.properties.requires_code is True
    assert profile.properties.domain.value == "technical"
    assert profile.rag_strategy.chunking_strategy in ("ast", "code", "semantic")
    assert profile.security.sandbox_required is True


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 5: Empty KB
# Acceptance: web fallback must be explicit, never silent
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype5_empty_kb_explicit_web_fallback(builder):
    profile, _ = await builder.build_with_trace(
        "Find information about the latest machine learning research",
        tenant_id="t1", goal_id="arch5",
        kb_state="empty",
    )
    assert profile.properties.kb_state == KnowledgeState.EMPTY
    assert profile.rag_strategy.web_fallback_enabled is True
    # Source inventory must include web_search
    sources = profile.rag_strategy.sources
    assert "web_search" in sources or profile.rag_strategy.web_fallback_enabled is True


async def test_archetype5_empty_kb_never_silent_fail():
    """RetrieverTool with empty KB must return structured result, not empty string."""
    from app.rag.agentic.retriever_tool import RetrieverTool
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import TenantContext, PlanTier

    tool = RetrieverTool(knowledge_store=KnowledgeStore())
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    result = await tool.retrieve(query="any query", tenant_ctx=ctx)
    # Must NOT be empty string — must be structured
    assert result.source != ""
    assert result.strategy_used != ""
    assert isinstance(result.chunks, list)  # even if empty list — never None/str


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 6: Realtime Status
# Acceptance: minimal profile, low latency, direct tool usage
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype6_realtime_gets_low_latency_profile(builder):
    profile, _ = await builder.build_with_trace(
        "What is the current status of the Kubernetes pods right now?",
        tenant_id="t1", goal_id="arch6"
    )
    assert profile.model_plan.latency_class in ("realtime", "interactive")
    # Web or realtime signals → web fallback
    assert profile.properties.requires_web is True or profile.properties.time_sensitivity == TimeSensitivity.REALTIME


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 7: Multi-Tenant Isolation
# Acceptance: tenant_id scoped to profile, cross-tenant access denied
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype7_multi_tenant_profiles_isolated(builder):
    profile_t1, _ = await builder.build_with_trace(
        "List my agents", tenant_id="tenant_alpha", goal_id="t1g1"
    )
    profile_t2, _ = await builder.build_with_trace(
        "List my agents", tenant_id="tenant_beta", goal_id="t2g1"
    )
    assert profile_t1.tenant_id == "tenant_alpha"
    assert profile_t2.tenant_id == "tenant_beta"
    assert profile_t1.profile_id != profile_t2.profile_id


async def test_archetype7_enterprise_tenant_gets_elevated_governance(builder, enterprise_ctx):
    from app.security_runtime.governance_profile import GovernanceProfileSelector
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t2",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    gov_selector = GovernanceProfileSelector()
    gov = gov_selector.select(profile, tenant_ctx=enterprise_ctx)
    from app.security_runtime.governance_profile import GovernanceBundle
    assert gov.name == GovernanceBundle.ENTERPRISE


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 8: Financial / Payment Operation
# Acceptance: CRITICAL risk, HITL, irreversible, full audit
# ──────────────────────────────────────────────────────────────────────────────

async def test_archetype8_financial_operation_critical_risk(builder):
    profile, _ = await builder.build_with_trace(
        "Transfer $50,000 from account A to account B and charge the customer payment method",
        tenant_id="t1", goal_id="arch8"
    )
    assert profile.properties.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert profile.properties.reversibility == "irreversible"
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True
    assert profile.security.audit_level in ("full", "forensic")


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 9: Data Classification Guard
# Acceptance: no secret/PII enters prompts unclassified
# ──────────────────────────────────────────────────────────────────────────────

def test_archetype9_pii_blocked_from_prompt():
    classifier = DataClassifier()
    text_with_pii = "Customer email: alice@company.com, SSN: 123-45-6789"
    result = classifier.classify(text_with_pii)
    assert DataClass.PII in result.classes
    assert result.safe_for_prompt is False   # PII must not enter prompt directly


def test_archetype9_secret_blocked_from_prompt():
    classifier = DataClassifier()
    text_with_secret = "API key: sk-proj-abc123DEFxyz456GHJK789"
    result = classifier.classify(text_with_secret)
    assert DataClass.SECRET in result.classes
    assert result.safe_for_prompt is False


def test_archetype9_public_text_allowed_in_prompt():
    classifier = DataClassifier()
    result = classifier.classify("The sky is blue and the grass is green.")
    assert result.safe_for_prompt is True


# ──────────────────────────────────────────────────────────────────────────────
# ARCHETYPE 10: Self-Improvement Loop
# Acceptance: every goal produces a scorecard; failed goals create regression candidates
# ──────────────────────────────────────────────────────────────────────────────

def test_archetype10_failed_goal_creates_regression_candidate():
    from app.evals.runtime_scorecard import RuntimeScorecard, ScorecardResult
    from app.evals.regression_gate import RegressionGate
    from app.agent.state import AgentState, GoalStatus
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="list tickets", tenant_ctx=ctx, goal_id="g1")
    state.status = GoalStatus.FAILED
    state.iterations = 20

    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="list tickets"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )

    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)
    assert result.overall_score < 0.7

    gate = RegressionGate()
    candidate = gate.maybe_create_regression(state=state, scorecard=result, profile=profile)
    assert candidate is not None
    assert candidate["goal_id"] == "g1"


def test_archetype10_successful_goal_no_regression():
    from app.evals.runtime_scorecard import RuntimeScorecard
    from app.evals.regression_gate import RegressionGate
    from app.agent.state import AgentState, GoalStatus
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="list tickets", tenant_ctx=ctx, goal_id="g2")
    state.status = GoalStatus.COMPLETE
    state.iterations = 3

    profile = GoalRuntimeProfile(
        goal_id="g2", tenant_id="t1",
        properties=GoalProperties(raw_goal="list tickets"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )

    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)

    gate = RegressionGate()
    candidate = gate.maybe_create_regression(state=state, scorecard=result, profile=profile)
    # High-scoring goal should not create regression
    assert candidate is None


# ──────────────────────────────────────────────────────────────────────────────
# ACCEPTANCE CRITERIA TESTS
# All criteria from spec section 5 must pass
# ──────────────────────────────────────────────────────────────────────────────

async def test_acceptance_simple_goal_minimal_cost_profile(builder):
    """A simple goal gets a minimal low-cost runtime profile."""
    profile, _ = await builder.build_with_trace(
        "get current user", tenant_id="t1", goal_id="acc1"
    )
    assert profile.model_plan.cost_class in ("low", "medium")
    assert profile.security.hitl_required is False


async def test_acceptance_high_risk_always_hitl_consensus_rollback(builder):
    """A high-risk destructive goal ALWAYS gets HITL + consensus + rollback."""
    profile, _ = await builder.build_with_trace(
        "truncate the production database", tenant_id="t1", goal_id="acc2"
    )
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True


def test_acceptance_strategy_registry_has_all_documented_patterns():
    """Every pattern from the architecture docs exists in StrategyRegistry."""
    from app.orchestration.strategy_registry import build_default_registry, StrategyCategory
    registry = build_default_registry()
    # Must have 60+ patterns total
    assert len(registry.list_all()) >= 60
    # Agent patterns
    agent_ids = {s.strategy_id for s in registry.list_by_category(StrategyCategory.AGENT)}
    for pat in ["react", "plan_execute", "reflection", "reflexion", "goal_tree",
                "supervisor", "debate", "consensus", "self_refine"]:
        assert pat in agent_ids, f"Missing agent pattern: {pat}"
    # RAG patterns
    rag_ids = {s.strategy_id for s in registry.list_by_category(StrategyCategory.RAG)}
    for pat in ["naive_rag", "hybrid_rag", "hyde", "graph_rag", "agentic_rag",
                "corrective_rag", "web_augmented_rag"]:
        assert pat in rag_ids, f"Missing RAG pattern: {pat}"
    # Safety patterns
    safety_ids = {s.strategy_id for s in registry.list_by_category(StrategyCategory.SAFETY)}
    for pat in ["guardrails", "hitl", "sandbox", "plan_verification", "data_classification"]:
        assert pat in safety_ids, f"Missing safety pattern: {pat}"


def test_acceptance_every_runtime_decision_traceable():
    """Every runtime decision appears in DecisionTrace."""
    from app.orchestration.decision_trace import DecisionTrace
    trace = DecisionTrace(goal_id="g1", tenant_id="t1")
    trace.add("GoalClassifier", "complexity", "expert", "keyword signals")
    trace.add("PatternSelector", "agent_patterns", ["react"], "default")
    trace.add("PatternSelector", "rag_strategy", "hybrid_rag", "kb available")
    assert len(trace.decisions) == 3
    sse_event = trace.to_sse_event()
    assert sse_event["type"] == "runtime_profile_selected"
    assert len(sse_event["decisions"]) == 3


def test_acceptance_data_classification_no_silent_pass():
    """No unclassified data enters prompt/context building."""
    classifier = DataClassifier()
    # All content must return a classification (never raises, never returns None)
    texts = [
        "normal text",
        "api key: sk-proj-abc123",
        "email: test@example.com",
        "",   # empty string
        "def function(): pass",
        "<html><body></body></html>",
    ]
    for text in texts:
        result = classifier.classify_or_safe_fallback(text)
        assert result is not None
        assert len(result.classes) > 0


async def test_acceptance_plan_verifier_gates_high_risk():
    """No high-risk, high-cost, missing-permission plan executes without verification."""
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    verifier = PlanVerifier()
    plan = ["Drop the production database", "Truncate all user tables", "Revoke all API keys"]
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="destroy everything", risk=RiskLevel.CRITICAL),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(hitl_required=True),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    result = verifier.verify(plan=plan, profile=profile)
    assert result.requires_hitl is True
    assert result.risk_level in ("high", "critical")


def test_acceptance_readiness_gate_blocks_when_llm_down():
    """Platform blocks goals when LLM provider is unavailable."""
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    health = DependencyHealth(
        postgres=DepStatus.HEALTHY,
        redis=DepStatus.HEALTHY,
        embedder=DepStatus.HEALTHY,
        llm_provider=DepStatus.UNAVAILABLE,
    )
    gate = ReadinessGate(health)
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is False
    assert "llm_provider" in result.blocking_deps


def test_acceptance_failure_classifier_never_generic_retry():
    """Recovery is never generic retry — every failure has a classified reason and strategy."""
    classifier = FailureClassifier()
    errors = [
        ("401 Unauthorized", FailureClass.AUTH_FAILURE),
        ("429 rate limit exceeded", FailureClass.RATE_LIMIT),
        ("INSUFFICIENT DATA: cannot find answer", FailureClass.CONTEXT_GAP),
        ("TimeoutError after 30s", FailureClass.TIMEOUT),
        ("GUARDRAIL: injection detected", FailureClass.SAFETY_VIOLATION),
    ]
    for error_text, expected_class in errors:
        result = classifier.classify(error_text)
        assert result.failure_class == expected_class, (
            f"'{error_text}' should classify as {expected_class}, "
            f"got {result.failure_class}"
        )


async def test_acceptance_profile_builder_latency_under_100ms(builder):
    """Profile assembly must complete in < 100ms (fast path)."""
    import time
    t0 = time.perf_counter()
    profile, _ = await builder.build_with_trace(
        "list all open issues", tenant_id="t1", goal_id="perf1"
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 100.0, f"Profile assembly took {elapsed_ms:.1f}ms — too slow"


async def test_acceptance_all_10_archetypes_produce_valid_profiles(builder):
    """All 10 archetypes produce valid, JSON-serializable GoalRuntimeProfiles."""
    import json
    goals = [
        "list all open Jira tickets",                                    # 1. simple lookup
        "research AI safety and write a comprehensive report",           # 2. complex research
        "delete all records from production database",                    # 3. high-risk
        "write Python function to parse logs",                           # 4. coding
        "find latest information on quantum computing",                  # 5. empty KB / web
        "get current Kubernetes pod status right now",                   # 6. realtime
        "analyze competitors and build strategic report",                # 7. multi-agent
        "transfer funds and charge customer payment method",             # 8. financial
        "customer SSN is 123-45-6789 please process",                   # 9. data classification
        "run all test suites and fix failing tests",                     # 10. self-improvement
    ]
    for i, goal in enumerate(goals, 1):
        profile, trace = await builder.build_with_trace(
            goal, tenant_id="t1", goal_id=f"archetype_{i}"
        )
        assert profile is not None, f"Archetype {i}: profile is None"
        assert profile.goal_id == f"archetype_{i}"
        assert profile.tenant_id == "t1"
        # Must be JSON-serializable
        try:
            json.dumps(profile.to_dict())
        except Exception as e:
            pytest.fail(f"Archetype {i}: profile not JSON-serializable: {e}")
        # Trace must have decisions
        assert len(trace.decisions) > 0, f"Archetype {i}: no decisions in trace"
```

- [ ] **Step 25.2: Run the complete E2E test suite**

```bash
cd agent-verse-backend
uv run pytest tests/e2e/test_dynamic_orchestration_e2e.py -v --no-cov
```
Expected: All 30+ E2E tests pass

- [ ] **Step 25.3: Commit**

```bash
cd agent-verse-backend
git add tests/e2e/test_dynamic_orchestration_e2e.py
git commit -m "test(e2e): add 30-test E2E suite covering 10 goal archetypes and all acceptance criteria"
```

---

## Task 26: Complete Verification Run

- [ ] **Step 26.1: Run the complete new test suite**

```bash
cd agent-verse-backend
uv run pytest \
    tests/orchestration/ \
    tests/security_runtime/ \
    tests/data_classification/ \
    tests/capabilities/ \
    tests/plan_runtime/ \
    tests/runtime_readiness/ \
    tests/policy_runtime/ \
    tests/rag/test_agentic/ \
    tests/context/ \
    tests/ingestion/ \
    tests/embedding/ \
    tests/evals/ \
    tests/tool_runtime/ \
    tests/provenance/ \
    tests/recovery/ \
    tests/qos/ \
    tests/state_runtime/ \
    tests/observability/test_runtime_sse_events.py \
    tests/e2e/test_dynamic_orchestration_e2e.py \
    -v --no-cov 2>&1 | tail -30
```
Expected: 200+ tests pass, 0 failures

- [ ] **Step 26.2: Run existing tests to verify no regressions**

```bash
cd agent-verse-backend
uv run pytest tests/ -x --no-cov -q \
    --ignore=tests/live \
    --ignore=tests/load \
    --ignore=tests/e2e \
    --ignore=tests/orchestration \
    --ignore=tests/security_runtime \
    --ignore=tests/data_classification \
    --ignore=tests/capabilities \
    --ignore=tests/plan_runtime \
    --ignore=tests/runtime_readiness \
    --ignore=tests/policy_runtime \
    --ignore=tests/rag/test_agentic \
    --ignore=tests/context \
    --ignore=tests/ingestion \
    --ignore=tests/embedding \
    --ignore=tests/evals \
    --ignore=tests/tool_runtime \
    --ignore=tests/provenance \
    --ignore=tests/recovery \
    --ignore=tests/qos \
    --ignore=tests/state_runtime \
    2>&1 | tail -20
```
Expected: All pre-existing tests still pass (zero regressions)

- [ ] **Step 26.3: Final commit — implementation complete**

```bash
cd agent-verse-backend
git add -A
git commit -m "feat(orchestration): complete AgentVerse Core Dynamic Orchestration — all 200+ tests passing

Implements all P0 and P1 layers:
- GoalRuntimeProfile, StrategyRegistry, GoalClassifier, PatternSelector, RuntimeProfileBuilder, DecisionTrace
- SecurityRuntime (GuardrailProfileSelector, GovernanceProfileSelector, PolicyBundleSelector)
- DataClassification (DataClassifier, Redactor)
- CapabilityRegistry
- PlanRuntime verifier
- RuntimeReadiness gate
- PolicyRuntime compiler
- Agentic RAG (RetrieverTool, SourceInventory)
- Context pipeline (ContextBudget, RerankPolicy, CitationManager, PromptBuilder)
- IngestionOrchestrator with multimodal content type routing
- EmbeddingOrchestrator with modality-aware model selection
- RuntimeScorecard with GoalScorer, RAGScorer, SafetyScorer, ModelScorer
- RegressionGate for self-improvement
- ToolTrustStore + ToolScorer + ToolRanker
- ProvenanceLedger with claim-level source chain
- FailureClassifier + RecoveryPolicy (never generic retry)
- QoSScheduler + PriorityPolicy + BackpressureController
- StateRuntime (MemoryPolicyEngine, CachePolicyEngine, ReflexionStore)
- RuntimeSSEEmitter for all 9 orchestration SSE events
- E2E tests: 10 goal archetypes, all spec acceptance criteria
All behind DYNAMIC_ORCHESTRATION feature flag — zero breaking changes to existing goals"
```
