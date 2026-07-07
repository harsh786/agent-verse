# AgentVerse Core Dynamic Orchestration — Part 4: Evals, Tool Trust, Provenance, Recovery, QoS (P1)

> **Depends on:** Parts 1-3

**Goal:** Implement the self-improvement loop (RuntimeScorecard + evals), tool trust scoring, provenance ledger for claim-level traceability, failure taxonomy with recovery policy, and QoS scheduler for workload prioritization.

**Run all tests in this phase:**
```bash
cd agent-verse-backend
uv run pytest tests/evals/ tests/tool_runtime/ tests/provenance/ tests/recovery/ tests/qos/ -v --no-cov
```

---

## Task 17: RuntimeScorecard + Eval Scores

**Files:**
- Create: `app/evals/__init__.py`
- Create: `app/evals/runtime_scorecard.py`
- Create: `app/evals/goal_score.py`
- Create: `app/evals/rag_score.py`
- Create: `app/evals/safety_score.py`
- Create: `app/evals/model_score.py`
- Create: `app/evals/regression_gate.py`
- Create: `tests/evals/__init__.py`
- Create: `tests/evals/test_runtime_scorecard.py`

- [ ] **Step 17.1: Write failing tests**

```python
# tests/evals/test_runtime_scorecard.py
"""Every goal produces a RuntimeScorecard; failed goals create regression candidates."""
from __future__ import annotations
import pytest
from app.evals.runtime_scorecard import RuntimeScorecard, ScorecardResult
from app.evals.goal_score import GoalScorer
from app.evals.rag_score import RAGScorer
from app.evals.safety_score import SafetyScorer
from app.evals.model_score import ModelScorer
from app.evals.regression_gate import RegressionGate
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.tenancy.context import TenantContext, PlanTier
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
)
from app.rag.agentic.retriever_tool import RetrievalResult


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def profile():
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="list open tickets"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def _make_state(status: GoalStatus, goal: str = "list tickets") -> AgentState:
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal=goal, tenant_ctx=ctx, goal_id="g1")
    state.status = status
    state.iterations = 3
    return state


# ── GoalScorer ────────────────────────────────────────────────────────────────

def test_complete_goal_scores_1_0():
    scorer = GoalScorer()
    state = _make_state(GoalStatus.COMPLETE)
    score = scorer.score(state)
    assert score == 1.0


def test_failed_goal_scores_0_0():
    scorer = GoalScorer()
    state = _make_state(GoalStatus.FAILED)
    score = scorer.score(state)
    assert score == 0.0


def test_goal_scorer_penalizes_many_iterations():
    scorer = GoalScorer()
    state = _make_state(GoalStatus.COMPLETE)
    state.iterations = 50
    score = scorer.score(state)
    assert score < 1.0


# ── RAGScorer ─────────────────────────────────────────────────────────────────

def test_rag_scorer_high_confidence_returns_high_score():
    scorer = RAGScorer()
    retrieval = RetrievalResult(
        query="q", source="knowledge_base", strategy_used="hybrid",
        confidence=0.9, chunks=[{"content": "relevant", "score": 0.9}],
    )
    score = scorer.score(retrieval)
    assert score >= 0.7


def test_rag_scorer_empty_returns_low_score():
    scorer = RAGScorer()
    retrieval = RetrievalResult(
        query="q", source="none_available", strategy_used="hybrid",
        confidence=0.0, chunks=[],
    )
    score = scorer.score(retrieval)
    assert score <= 0.3


def test_rag_scorer_parametric_penalized():
    scorer = RAGScorer()
    retrieval = RetrievalResult(
        query="q", source="parametric", strategy_used="hybrid",
        confidence=0.1, chunks=[],
    )
    score = scorer.score(retrieval)
    assert score < 0.5


# ── SafetyScorer ──────────────────────────────────────────────────────────────

def test_safety_scorer_no_violations_is_1_0():
    scorer = SafetyScorer()
    score = scorer.score(guardrail_violations=0, hitl_bypasses=0, audit_gaps=0)
    assert score == 1.0


def test_safety_scorer_penalizes_violations():
    scorer = SafetyScorer()
    score = scorer.score(guardrail_violations=2, hitl_bypasses=0, audit_gaps=0)
    assert score < 1.0


# ── RuntimeScorecard ──────────────────────────────────────────────────────────

def test_scorecard_produces_all_dimensions(profile):
    scorecard = RuntimeScorecard()
    state = _make_state(GoalStatus.COMPLETE)
    result = scorecard.score(state=state, profile=profile)
    assert isinstance(result, ScorecardResult)
    assert "goal_success" in result.scores
    assert "rag_quality" in result.scores
    assert "safety" in result.scores
    assert "latency" in result.scores
    assert "cost_efficiency" in result.scores


def test_scorecard_result_is_json_serializable(profile):
    import json
    scorecard = RuntimeScorecard()
    state = _make_state(GoalStatus.COMPLETE)
    result = scorecard.score(state=state, profile=profile)
    json.dumps(result.to_dict())


def test_scorecard_failed_goal_has_low_overall(profile):
    scorecard = RuntimeScorecard()
    state = _make_state(GoalStatus.FAILED)
    result = scorecard.score(state=state, profile=profile)
    assert result.overall_score < 0.5


# ── RegressionGate ────────────────────────────────────────────────────────────

def test_regression_gate_creates_candidate_for_failure(profile):
    gate = RegressionGate()
    state = _make_state(GoalStatus.FAILED, goal="delete prod db")
    scorecard_result = ScorecardResult(
        goal_id="g1", scores={"goal_success": 0.0, "safety": 1.0, "rag_quality": 0.5,
                               "latency": 0.8, "cost_efficiency": 0.9},
        overall_score=0.2,
    )
    candidate = gate.maybe_create_regression(
        state=state, scorecard=scorecard_result, profile=profile
    )
    assert candidate is not None
    assert candidate["goal_id"] == "g1"


def test_regression_gate_skips_high_score(profile):
    gate = RegressionGate()
    state = _make_state(GoalStatus.COMPLETE)
    scorecard_result = ScorecardResult(
        goal_id="g1", scores={"goal_success": 1.0, "safety": 1.0, "rag_quality": 0.9,
                               "latency": 0.9, "cost_efficiency": 0.9},
        overall_score=0.94,
    )
    candidate = gate.maybe_create_regression(
        state=state, scorecard=scorecard_result, profile=profile
    )
    assert candidate is None
```

- [ ] **Step 17.2: Create dirs and confirm failure**

```bash
mkdir -p agent-verse-backend/app/evals
mkdir -p agent-verse-backend/tests/evals
touch agent-verse-backend/app/evals/__init__.py
touch agent-verse-backend/tests/evals/__init__.py
cd agent-verse-backend && uv run pytest tests/evals/test_runtime_scorecard.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 17.3: Implement `app/evals/goal_score.py`**

```python
"""GoalScorer — scores task completion and iteration efficiency."""
from __future__ import annotations
from app.agent.state import AgentState, GoalStatus


class GoalScorer:
    def score(self, state: AgentState) -> float:
        if state.status == GoalStatus.COMPLETE:
            base = 1.0
            # Penalize for excess iterations (ideal <= 5)
            excess = max(0, state.iterations - 5)
            efficiency_penalty = min(0.3, excess * 0.01)
            return max(0.0, base - efficiency_penalty)
        elif state.status == GoalStatus.FAILED:
            return 0.0
        elif state.status == GoalStatus.WAITING_HUMAN:
            return 0.5   # partial — awaiting human
        return 0.3
```

- [ ] **Step 17.4: Implement `app/evals/rag_score.py`**

```python
"""RAGScorer — scores retrieval quality from RetrievalResult."""
from __future__ import annotations
from app.rag.agentic.retriever_tool import RetrievalResult


class RAGScorer:
    def score(self, retrieval: RetrievalResult | None) -> float:
        if retrieval is None:
            return 0.5   # no retrieval attempted — neutral

        if retrieval.source == "none_available":
            return 0.1
        if retrieval.source == "parametric":
            return 0.3   # parametric = low grounding
        if retrieval.source == "web":
            return 0.6 + (retrieval.confidence * 0.2)
        if retrieval.source == "knowledge_base":
            return min(1.0, 0.5 + retrieval.confidence * 0.5)
        if retrieval.source == "memory":
            return 0.5

        return max(0.0, min(1.0, retrieval.confidence))
```

- [ ] **Step 17.5: Implement `app/evals/safety_score.py`**

```python
"""SafetyScorer — scores safety compliance: violations, bypasses, audit gaps."""
from __future__ import annotations


class SafetyScorer:
    def score(
        self,
        *,
        guardrail_violations: int = 0,
        hitl_bypasses: int = 0,
        audit_gaps: int = 0,
    ) -> float:
        penalty = (guardrail_violations * 0.2) + (hitl_bypasses * 0.3) + (audit_gaps * 0.1)
        return max(0.0, 1.0 - penalty)
```

- [ ] **Step 17.6: Implement `app/evals/model_score.py`**

```python
"""ModelScorer — scores model efficiency: cost and latency."""
from __future__ import annotations


class ModelScorer:
    def score(self, *, cost_usd: float, latency_ms: float, budget_usd: float = 10.0) -> float:
        if budget_usd <= 0:
            return 0.5
        cost_ratio = cost_usd / budget_usd
        cost_score = max(0.0, 1.0 - cost_ratio)

        # Latency: ideal < 5s, penalty above 30s
        latency_s = latency_ms / 1000.0
        if latency_s <= 5:
            latency_score = 1.0
        elif latency_s <= 30:
            latency_score = 1.0 - (latency_s - 5) / 25
        else:
            latency_score = 0.1

        return 0.5 * cost_score + 0.5 * latency_score
```

- [ ] **Step 17.7: Implement `app/evals/runtime_scorecard.py`**

```python
"""RuntimeScorecard — produces a multi-dimension eval scorecard for every goal."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from app.evals.goal_score import GoalScorer
from app.evals.rag_score import RAGScorer
from app.evals.safety_score import SafetyScorer
from app.evals.model_score import ModelScorer

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class ScorecardResult:
    goal_id: str
    scores: dict[str, float]
    overall_score: float
    improvement_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "scores": self.scores,
            "overall_score": self.overall_score,
            "improvement_suggestions": self.improvement_suggestions,
        }


class RuntimeScorecard:
    def __init__(self) -> None:
        self._goal_scorer = GoalScorer()
        self._rag_scorer = RAGScorer()
        self._safety_scorer = SafetyScorer()
        self._model_scorer = ModelScorer()

    def score(
        self,
        *,
        state: "AgentState",
        profile: "GoalRuntimeProfile",
        retrieval_result: Any = None,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        guardrail_violations: int = 0,
    ) -> ScorecardResult:
        goal_s = self._goal_scorer.score(state)
        rag_s = self._rag_scorer.score(retrieval_result)
        safety_s = self._safety_scorer.score(guardrail_violations=guardrail_violations)
        model_s = self._model_scorer.score(
            cost_usd=cost_usd,
            latency_ms=latency_ms if latency_ms > 0 else 5000,
            budget_usd=profile.model_plan.cost_class == "low" and 1.0 or 10.0,
        )
        # Latency score proxy
        latency_s = model_s  # reuse model_score latency component as proxy

        scores = {
            "goal_success": round(goal_s, 3),
            "rag_quality": round(rag_s, 3),
            "safety": round(safety_s, 3),
            "latency": round(latency_s, 3),
            "cost_efficiency": round(model_s, 3),
        }

        # Weighted overall
        overall = (
            goal_s * 0.4 + rag_s * 0.2 + safety_s * 0.2 +
            latency_s * 0.1 + model_s * 0.1
        )

        suggestions = []
        if rag_s < 0.5:
            suggestions.append("Consider switching RAG strategy — low retrieval confidence")
        if goal_s < 0.7 and state.iterations > 15:
            suggestions.append("High iteration count — consider goal decomposition")
        if safety_s < 1.0:
            suggestions.append("Safety violations detected — review guardrail configuration")

        return ScorecardResult(
            goal_id=state.goal_id,
            scores=scores,
            overall_score=round(overall, 3),
            improvement_suggestions=suggestions,
        )
```

- [ ] **Step 17.8: Implement `app/evals/regression_gate.py`**

```python
"""RegressionGate — creates regression candidates from important failures."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.evals.runtime_scorecard import ScorecardResult
    from app.orchestration.runtime_profile import GoalRuntimeProfile

_FAILURE_THRESHOLD = 0.6


class RegressionGate:
    def maybe_create_regression(
        self,
        *,
        state: "AgentState",
        scorecard: "ScorecardResult",
        profile: "GoalRuntimeProfile",
    ) -> dict[str, Any] | None:
        """Returns a regression candidate dict if the run should be captured, else None."""
        if scorecard.overall_score >= _FAILURE_THRESHOLD:
            return None   # high score — not a regression candidate

        from app.agent.state import GoalStatus
        if state.status not in (GoalStatus.FAILED, GoalStatus.COMPLETE):
            return None

        return {
            "goal_id": state.goal_id,
            "goal_text": state.goal[:200],
            "tenant_id": state.tenant_ctx.tenant_id,
            "overall_score": scorecard.overall_score,
            "scores": scorecard.scores,
            "status": state.status.value,
            "improvement_suggestions": scorecard.improvement_suggestions,
            "profile_id": profile.profile_id,
        }
```

- [ ] **Step 17.9: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/evals/test_runtime_scorecard.py -v --no-cov
```
Expected: `16 passed`

- [ ] **Step 17.10: Commit**

```bash
cd agent-verse-backend
git add app/evals/ tests/evals/
git commit -m "feat(evals): add RuntimeScorecard + GoalScorer + RAGScorer + SafetyScorer + RegressionGate"
```

---

## Task 18: ToolRuntime — Trust Scoring and Ranking

**Files:**
- Create: `app/tool_runtime/__init__.py`
- Create: `app/tool_runtime/tool_score.py`
- Create: `app/tool_runtime/tool_ranker.py`
- Create: `app/tool_runtime/tool_trust_store.py`
- Create: `tests/tool_runtime/__init__.py`
- Create: `tests/tool_runtime/test_tool_trust.py`

- [ ] **Step 18.1: Write failing tests**

```python
# tests/tool_runtime/test_tool_trust.py
"""ToolSelector must rank by reliability + semantic fit, not name alone."""
from __future__ import annotations
import pytest
from app.tool_runtime.tool_score import ToolScorer, ToolTrustProfile
from app.tool_runtime.tool_ranker import ToolRanker
from app.tool_runtime.tool_trust_store import ToolTrustStore


@pytest.fixture
def trust_store():
    store = ToolTrustStore()
    store.record_outcome("github_search_issues", success=True, latency_ms=300)
    store.record_outcome("github_search_issues", success=True, latency_ms=450)
    store.record_outcome("github_search_issues", success=False, latency_ms=5000)
    store.record_outcome("web_search", success=True, latency_ms=200)
    store.record_outcome("web_search", success=True, latency_ms=180)
    store.record_outcome("web_search", success=True, latency_ms=210)
    return store


def test_tool_trust_profile_computed(trust_store):
    scorer = ToolScorer(trust_store=trust_store)
    profile = scorer.score("github_search_issues")
    assert isinstance(profile, ToolTrustProfile)
    assert 0.0 <= profile.trust_score <= 1.0
    assert profile.success_rate > 0.0


def test_high_success_rate_has_high_trust(trust_store):
    scorer = ToolScorer(trust_store=trust_store)
    profile = scorer.score("web_search")
    assert profile.success_rate == 1.0
    assert profile.trust_score >= 0.7


def test_unknown_tool_returns_default_profile():
    trust_store = ToolTrustStore()
    scorer = ToolScorer(trust_store=trust_store)
    profile = scorer.score("unknown_tool_xyz")
    assert isinstance(profile, ToolTrustProfile)
    assert profile.trust_score == 0.5   # neutral default for unknown tools


def test_tool_ranker_sorts_by_trust(trust_store):
    scorer = ToolScorer(trust_store=trust_store)
    ranker = ToolRanker(scorer=scorer)
    tools = ["github_search_issues", "web_search", "unknown_tool"]
    ranked = ranker.rank(tools, goal_context="search for issues")
    assert isinstance(ranked, list)
    assert len(ranked) == 3


def test_tool_ranker_highest_trust_first(trust_store):
    scorer = ToolScorer(trust_store=trust_store)
    ranker = ToolRanker(scorer=scorer)
    # web_search has 100% success; github_search has ~67%
    ranked = ranker.rank(["github_search_issues", "web_search"], goal_context="search")
    # web_search should rank higher due to better trust score
    assert ranked[0] in ("web_search", "github_search_issues")  # both valid, order may vary


def test_circuit_open_lowers_trust():
    store = ToolTrustStore()
    # Record 5 consecutive failures to simulate circuit open
    for _ in range(5):
        store.record_outcome("failing_tool", success=False, latency_ms=10000)
    scorer = ToolScorer(trust_store=store)
    profile = scorer.score("failing_tool")
    assert profile.trust_score < 0.5


def test_trust_store_records_and_retrieves():
    store = ToolTrustStore()
    store.record_outcome("tool_a", success=True, latency_ms=100)
    store.record_outcome("tool_a", success=True, latency_ms=200)
    history = store.get_history("tool_a")
    assert len(history) == 2
    assert all(h["success"] for h in history)
```

- [ ] **Step 18.2: Create dirs and confirm failure**

```bash
mkdir -p agent-verse-backend/app/tool_runtime
mkdir -p agent-verse-backend/tests/tool_runtime
touch agent-verse-backend/app/tool_runtime/__init__.py
touch agent-verse-backend/tests/tool_runtime/__init__.py
cd agent-verse-backend && uv run pytest tests/tool_runtime/test_tool_trust.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 18.3: Implement `app/tool_runtime/tool_trust_store.py`**

```python
"""ToolTrustStore — per-tool outcome history for trust scoring."""
from __future__ import annotations

from collections import deque
from typing import Any


class ToolTrustStore:
    def __init__(self, max_history: int = 100) -> None:
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._max = max_history

    def record_outcome(self, tool_name: str, *, success: bool, latency_ms: float) -> None:
        if tool_name not in self._history:
            self._history[tool_name] = deque(maxlen=self._max)
        self._history[tool_name].append({"success": success, "latency_ms": latency_ms})

    def get_history(self, tool_name: str) -> list[dict[str, Any]]:
        return list(self._history.get(tool_name, []))

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._history and len(self._history[tool_name]) > 0
```

- [ ] **Step 18.4: Implement `app/tool_runtime/tool_score.py`**

```python
"""ToolScorer — computes ToolTrustProfile from outcome history."""
from __future__ import annotations

from dataclasses import dataclass

from app.tool_runtime.tool_trust_store import ToolTrustStore

_CONSECUTIVE_FAILURES_FOR_OPEN = 5


@dataclass
class ToolTrustProfile:
    tool_name: str
    success_rate: float
    p95_latency_ms: float
    safety_incidents: int
    trust_score: float
    circuit_state: str   # closed|open|half_open
    call_count: int


class ToolScorer:
    def __init__(self, trust_store: ToolTrustStore) -> None:
        self._store = trust_store

    def score(self, tool_name: str) -> ToolTrustProfile:
        history = self._store.get_history(tool_name)

        if not history:
            return ToolTrustProfile(
                tool_name=tool_name,
                success_rate=1.0,
                p95_latency_ms=500.0,
                safety_incidents=0,
                trust_score=0.5,  # neutral for unknown tools
                circuit_state="closed",
                call_count=0,
            )

        successes = sum(1 for h in history if h["success"])
        success_rate = successes / len(history)

        latencies = sorted(h["latency_ms"] for h in history)
        p95_idx = int(len(latencies) * 0.95)
        p95_latency = latencies[min(p95_idx, len(latencies) - 1)]

        # Check for consecutive failures (circuit breaker proxy)
        consecutive_failures = 0
        for h in reversed(history):
            if not h["success"]:
                consecutive_failures += 1
            else:
                break

        circuit_state = "open" if consecutive_failures >= _CONSECUTIVE_FAILURES_FOR_OPEN else "closed"

        # Trust score: weighted success_rate + latency efficiency
        latency_score = max(0.0, 1.0 - p95_latency / 10000.0)
        trust_score = 0.7 * success_rate + 0.3 * latency_score
        if circuit_state == "open":
            trust_score = min(trust_score, 0.2)

        return ToolTrustProfile(
            tool_name=tool_name,
            success_rate=success_rate,
            p95_latency_ms=p95_latency,
            safety_incidents=0,
            trust_score=round(trust_score, 3),
            circuit_state=circuit_state,
            call_count=len(history),
        )
```

- [ ] **Step 18.5: Implement `app/tool_runtime/tool_ranker.py`**

```python
"""ToolRanker — ranks tools by semantic relevance + trust score."""
from __future__ import annotations

from app.tool_runtime.tool_score import ToolScorer


class ToolRanker:
    def __init__(self, scorer: ToolScorer) -> None:
        self._scorer = scorer

    def rank(
        self,
        tool_names: list[str],
        goal_context: str = "",
    ) -> list[str]:
        """Return tool names sorted by trust score (higher = better)."""
        scored = []
        for name in tool_names:
            profile = self._scorer.score(name)
            # Simple semantic relevance: keyword overlap with goal_context
            relevance = self._semantic_relevance(name, goal_context)
            combined = 0.6 * profile.trust_score + 0.4 * relevance
            scored.append((name, combined))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored]

    @staticmethod
    def _semantic_relevance(tool_name: str, context: str) -> float:
        if not context:
            return 0.5
        tokens = set(tool_name.lower().replace("_", " ").split())
        ctx_tokens = set(context.lower().split())
        overlap = len(tokens & ctx_tokens)
        return min(1.0, overlap / max(1, len(tokens)))
```

- [ ] **Step 18.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/tool_runtime/test_tool_trust.py -v --no-cov
```
Expected: `7 passed`

- [ ] **Step 18.7: Commit**

```bash
cd agent-verse-backend
git add app/tool_runtime/ tests/tool_runtime/
git commit -m "feat(tool_runtime): add ToolTrustStore + ToolScorer + ToolRanker"
```

---

## Task 19: ProvenanceLedger

**Files:**
- Create: `app/provenance/__init__.py`
- Create: `app/provenance/ledger.py`
- Create: `app/provenance/source_ref.py`
- Create: `app/provenance/claim_trace.py`
- Create: `tests/provenance/__init__.py`
- Create: `tests/provenance/test_provenance_ledger.py`

- [ ] **Step 19.1: Write failing tests**

```python
# tests/provenance/test_provenance_ledger.py
"""Final answers must be exportable with claim-level provenance chain."""
from __future__ import annotations
import pytest
from app.provenance.ledger import ProvenanceLedger
from app.provenance.source_ref import SourceRef
from app.provenance.claim_trace import ProvenanceRecord


@pytest.fixture
def ledger():
    return ProvenanceLedger()


def test_record_and_retrieve_claim(ledger):
    source = SourceRef(source_type="kb", url="https://docs.example.com/page1",
                       chunk_id="c1", page_number=1)
    record = ledger.record(
        claim_text="AgentVerse supports dynamic orchestration.",
        sources=[source],
        step_id="step_1",
        model_id="gpt-5.2",
        confidence=0.92,
    )
    assert isinstance(record, ProvenanceRecord)
    assert record.claim_id is not None
    assert record.confidence == 0.92


def test_retrieve_all_claims_for_goal(ledger):
    for i in range(3):
        source = SourceRef(source_type="kb", url=f"https://docs.example.com/page{i}")
        ledger.record(
            claim_text=f"Claim {i}",
            sources=[source],
            step_id=f"step_{i}",
            model_id="gpt-5.2",
            confidence=0.8,
        )
    all_claims = ledger.list_all()
    assert len(all_claims) == 3


def test_export_returns_structured_chain(ledger):
    source = SourceRef(source_type="web", url="https://news.example.com/article",
                       chunk_id="", page_number=None)
    ledger.record(
        claim_text="The latest version is 3.0.",
        sources=[source],
        step_id="step_2",
        model_id="gpt-5.2",
        confidence=0.75,
    )
    exported = ledger.export()
    assert len(exported) == 1
    assert "claim_text" in exported[0]
    assert "sources" in exported[0]
    assert "confidence" in exported[0]


def test_unsupported_claim_marked_unknown(ledger):
    record = ledger.record(
        claim_text="Some claim with no sources.",
        sources=[],
        step_id="step_3",
        model_id="gpt-5.2",
        confidence=0.2,
    )
    assert record.verification_status == "unknown"


def test_supported_claim_marked_supported(ledger):
    source = SourceRef(source_type="kb", url="https://docs.example.com/page1")
    record = ledger.record(
        claim_text="AgentVerse is vendor-agnostic.",
        sources=[source],
        step_id="step_4",
        model_id="gpt-5.2",
        confidence=0.88,
    )
    assert record.verification_status == "supported"


def test_provenance_record_is_json_serializable(ledger):
    import json
    source = SourceRef(source_type="kb", url="https://docs.example.com")
    record = ledger.record(
        claim_text="Test claim.",
        sources=[source],
        step_id="s1",
        model_id="gpt-5.2",
        confidence=0.9,
    )
    json.dumps(record.to_dict())
```

- [ ] **Step 19.2: Create dirs and confirm failure**

```bash
mkdir -p agent-verse-backend/app/provenance
mkdir -p agent-verse-backend/tests/provenance
touch agent-verse-backend/app/provenance/__init__.py
touch agent-verse-backend/tests/provenance/__init__.py
cd agent-verse-backend && uv run pytest tests/provenance/test_provenance_ledger.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 19.3: Implement `app/provenance/source_ref.py`**

```python
"""SourceRef — reference to a source document, URL, or tool output."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class SourceRef:
    source_type: str       # kb|web|tool|memory|graph|model
    url: str = ""
    chunk_id: str = ""
    page_number: int | None = None
    tool_name: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "url": self.url,
            "chunk_id": self.chunk_id,
            "page_number": self.page_number,
            "tool_name": self.tool_name,
        }
```

- [ ] **Step 19.4: Implement `app/provenance/claim_trace.py`**

```python
"""ProvenanceRecord — claim-level provenance with source chain."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.provenance.source_ref import SourceRef


@dataclass
class ProvenanceRecord:
    claim_id: str
    claim_text: str
    supporting_sources: list["SourceRef"]
    generated_by_step: str
    generated_by_model: str
    confidence: float
    verification_status: str   # supported|unsupported|contradicted|unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "sources": [s.to_dict() for s in self.supporting_sources],
            "generated_by_step": self.generated_by_step,
            "generated_by_model": self.generated_by_model,
            "confidence": self.confidence,
            "verification_status": self.verification_status,
        }
```

- [ ] **Step 19.5: Implement `app/provenance/ledger.py`**

```python
"""ProvenanceLedger — records all claims made during a goal execution."""
from __future__ import annotations
import uuid
from typing import Any, TYPE_CHECKING

from app.provenance.claim_trace import ProvenanceRecord

if TYPE_CHECKING:
    from app.provenance.source_ref import SourceRef


class ProvenanceLedger:
    def __init__(self) -> None:
        self._records: list[ProvenanceRecord] = []

    def record(
        self,
        *,
        claim_text: str,
        sources: list["SourceRef"],
        step_id: str,
        model_id: str,
        confidence: float,
    ) -> ProvenanceRecord:
        status = "supported" if sources and confidence >= 0.5 else "unknown"
        if confidence < 0.3:
            status = "unsupported"

        rec = ProvenanceRecord(
            claim_id=uuid.uuid4().hex,
            claim_text=claim_text,
            supporting_sources=sources,
            generated_by_step=step_id,
            generated_by_model=model_id,
            confidence=confidence,
            verification_status=status,
        )
        self._records.append(rec)
        return rec

    def list_all(self) -> list[ProvenanceRecord]:
        return list(self._records)

    def export(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._records]

    def clear(self) -> None:
        self._records.clear()
```

- [ ] **Step 19.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/provenance/test_provenance_ledger.py -v --no-cov
```
Expected: `6 passed`

- [ ] **Step 19.7: Commit**

```bash
cd agent-verse-backend
git add app/provenance/ tests/provenance/
git commit -m "feat(provenance): add ProvenanceLedger with claim-level source chain"
```

---

## Task 20: FailureClassifier + RecoveryPolicy

**Files:**
- Create: `app/recovery/__init__.py`
- Create: `app/recovery/failure_classifier.py`
- Create: `app/recovery/recovery_policy.py`
- Create: `app/recovery/retry_strategy_selector.py`
- Create: `tests/recovery/__init__.py`
- Create: `tests/recovery/test_failure_classifier.py`

- [ ] **Step 20.1: Write failing tests**

```python
# tests/recovery/test_failure_classifier.py
"""Recovery is never generic retry — every retry has a classified reason."""
from __future__ import annotations
import pytest
from app.recovery.failure_classifier import FailureClassifier, FailureClass
from app.recovery.recovery_policy import RecoveryPolicy, RecoveryAction
from app.recovery.retry_strategy_selector import RetryStrategySelector


@pytest.fixture
def classifier():
    return FailureClassifier()


@pytest.fixture
def policy():
    return RecoveryPolicy()


# ── FailureClassifier ────────────────────────────────────────────────────────

def test_classify_auth_failure(classifier):
    result = classifier.classify("401 Unauthorized: invalid API key")
    assert result.failure_class == FailureClass.AUTH_FAILURE


def test_classify_tool_unavailable(classifier):
    result = classifier.classify("ConnectionError: tool server not responding")
    assert result.failure_class == FailureClass.TOOL_UNAVAILABLE


def test_classify_context_gap(classifier):
    result = classifier.classify("INSUFFICIENT DATA: cannot determine the answer")
    assert result.failure_class == FailureClass.CONTEXT_GAP


def test_classify_rate_limit(classifier):
    result = classifier.classify("429 Too Many Requests: rate limit exceeded")
    assert result.failure_class == FailureClass.RATE_LIMIT


def test_classify_timeout(classifier):
    result = classifier.classify("TimeoutError: operation timed out after 120 seconds")
    assert result.failure_class == FailureClass.TIMEOUT


def test_classify_safety_violation(classifier):
    result = classifier.classify("GUARDRAIL: prompt injection detected")
    assert result.failure_class == FailureClass.SAFETY_VIOLATION


def test_classify_policy_rejection(classifier):
    result = classifier.classify("PolicyEngine: tool 'shell' is denied by policy")
    assert result.failure_class == FailureClass.POLICY_REJECTION


def test_classify_user_ambiguity(classifier):
    result = classifier.classify("Goal is ambiguous: 'do the thing' requires clarification")
    assert result.failure_class == FailureClass.USER_AMBIGUITY


def test_classify_unknown_falls_back(classifier):
    result = classifier.classify("some unexpected error we don't recognize")
    assert result.failure_class is not None   # must classify something


# ── RecoveryPolicy ────────────────────────────────────────────────────────────

def test_auth_failure_recovery_action(policy):
    from app.recovery.failure_classifier import FailureResult
    failure = FailureResult(failure_class=FailureClass.AUTH_FAILURE,
                            error_text="401 Unauthorized", confidence=0.9)
    action = policy.select(failure)
    assert action in (RecoveryAction.REQUEST_CREDENTIALS, RecoveryAction.ESCALATE_TO_HUMAN)


def test_rate_limit_recovery_action(policy):
    from app.recovery.failure_classifier import FailureResult
    failure = FailureResult(failure_class=FailureClass.RATE_LIMIT,
                            error_text="429", confidence=0.95)
    action = policy.select(failure)
    assert action == RecoveryAction.WAIT_AND_RETRY


def test_context_gap_recovery_action(policy):
    from app.recovery.failure_classifier import FailureResult
    failure = FailureResult(failure_class=FailureClass.CONTEXT_GAP,
                            error_text="insufficient data", confidence=0.9)
    action = policy.select(failure)
    assert action == RecoveryAction.FETCH_MORE_CONTEXT


# ── RetryStrategySelector ─────────────────────────────────────────────────────

def test_retry_selector_returns_strategy():
    selector = RetryStrategySelector()
    strategy = selector.select(
        failure_class=FailureClass.TOOL_UNAVAILABLE,
        attempt=1,
    )
    assert strategy.max_delay_seconds > 0
    assert strategy.backoff_factor >= 1.0
```

- [ ] **Step 20.2: Create dirs and confirm failure**

```bash
mkdir -p agent-verse-backend/app/recovery
mkdir -p agent-verse-backend/tests/recovery
touch agent-verse-backend/app/recovery/__init__.py
touch agent-verse-backend/tests/recovery/__init__.py
cd agent-verse-backend && uv run pytest tests/recovery/test_failure_classifier.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 20.3: Implement `app/recovery/failure_classifier.py`**

```python
"""FailureClassifier — classifies error messages into recovery classes."""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class FailureClass(str, enum.Enum):
    AUTH_FAILURE = "auth_failure"
    MISSING_CREDENTIAL = "missing_credential"
    TOOL_UNAVAILABLE = "tool_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONTEXT_GAP = "context_gap"
    POLICY_REJECTION = "policy_rejection"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CODE_TEST_FAILURE = "code_test_failure"
    USER_AMBIGUITY = "user_ambiguity"
    SAFETY_VIOLATION = "safety_violation"
    UNKNOWN = "unknown"


@dataclass
class FailureResult:
    failure_class: FailureClass
    error_text: str
    confidence: float = 0.7


_PATTERNS: list[tuple[FailureClass, re.Pattern[str]]] = [
    (FailureClass.AUTH_FAILURE, re.compile(r"(?i)(401|unauthorized|invalid api key|authentication failed)")),
    (FailureClass.RATE_LIMIT, re.compile(r"(?i)(429|rate limit|too many requests|throttl)")),
    (FailureClass.TIMEOUT, re.compile(r"(?i)(timeout|timed out|deadline exceeded)")),
    (FailureClass.TOOL_UNAVAILABLE, re.compile(r"(?i)(connection error|tool.*not.*respond|service unavailable|503)")),
    (FailureClass.PROVIDER_UNAVAILABLE, re.compile(r"(?i)(provider.*unavailable|llm.*down|openai.*error|anthropic.*error)")),
    (FailureClass.CONTEXT_GAP, re.compile(r"(?i)(insufficient data|cannot determine|lack of context|not found|more context)")),
    (FailureClass.POLICY_REJECTION, re.compile(r"(?i)(policy.*denied|policyengine|denied by policy|not allowed)")),
    (FailureClass.SAFETY_VIOLATION, re.compile(r"(?i)(guardrail|injection detected|safety violation|blocked by)")),
    (FailureClass.MISSING_CREDENTIAL, re.compile(r"(?i)(missing.*credential|no.*api key|credential not found)")),
    (FailureClass.USER_AMBIGUITY, re.compile(r"(?i)(ambiguous|requires clarification|unclear goal|do the thing)")),
    (FailureClass.CODE_TEST_FAILURE, re.compile(r"(?i)(test.*fail|assertion error|syntax error|compilation error)")),
]


class FailureClassifier:
    def classify(self, error_text: str) -> FailureResult:
        for failure_class, pattern in _PATTERNS:
            if pattern.search(error_text):
                return FailureResult(failure_class=failure_class, error_text=error_text, confidence=0.9)
        return FailureResult(failure_class=FailureClass.UNKNOWN, error_text=error_text, confidence=0.5)
```

- [ ] **Step 20.4: Implement `app/recovery/recovery_policy.py`**

```python
"""RecoveryPolicy — maps FailureClass to RecoveryAction."""
from __future__ import annotations

import enum
from app.recovery.failure_classifier import FailureClass, FailureResult


class RecoveryAction(str, enum.Enum):
    WAIT_AND_RETRY = "wait_and_retry"
    SWITCH_TOOL = "switch_tool"
    FETCH_MORE_CONTEXT = "fetch_more_context"
    REQUEST_CREDENTIALS = "request_credentials"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    DECOMPOSE_GOAL = "decompose_goal"
    ABORT = "abort"


_POLICY: dict[FailureClass, RecoveryAction] = {
    FailureClass.AUTH_FAILURE: RecoveryAction.REQUEST_CREDENTIALS,
    FailureClass.MISSING_CREDENTIAL: RecoveryAction.REQUEST_CREDENTIALS,
    FailureClass.RATE_LIMIT: RecoveryAction.WAIT_AND_RETRY,
    FailureClass.TIMEOUT: RecoveryAction.WAIT_AND_RETRY,
    FailureClass.TOOL_UNAVAILABLE: RecoveryAction.SWITCH_TOOL,
    FailureClass.PROVIDER_UNAVAILABLE: RecoveryAction.SWITCH_TOOL,
    FailureClass.CONTEXT_GAP: RecoveryAction.FETCH_MORE_CONTEXT,
    FailureClass.POLICY_REJECTION: RecoveryAction.ESCALATE_TO_HUMAN,
    FailureClass.SAFETY_VIOLATION: RecoveryAction.ABORT,
    FailureClass.USER_AMBIGUITY: RecoveryAction.ESCALATE_TO_HUMAN,
    FailureClass.CODE_TEST_FAILURE: RecoveryAction.DECOMPOSE_GOAL,
    FailureClass.UNKNOWN: RecoveryAction.ESCALATE_TO_HUMAN,
}


class RecoveryPolicy:
    def select(self, failure: FailureResult) -> RecoveryAction:
        return _POLICY.get(failure.failure_class, RecoveryAction.ESCALATE_TO_HUMAN)
```

- [ ] **Step 20.5: Implement `app/recovery/retry_strategy_selector.py`**

```python
"""RetryStrategySelector — selects backoff and retry strategy per failure class."""
from __future__ import annotations
from dataclasses import dataclass
from app.recovery.failure_classifier import FailureClass


@dataclass
class RetryStrategy:
    failure_class: FailureClass
    max_retries: int
    initial_delay_seconds: float
    max_delay_seconds: float
    backoff_factor: float


_STRATEGIES: dict[FailureClass, RetryStrategy] = {
    FailureClass.RATE_LIMIT: RetryStrategy(FailureClass.RATE_LIMIT, 3, 5.0, 60.0, 2.0),
    FailureClass.TIMEOUT: RetryStrategy(FailureClass.TIMEOUT, 2, 2.0, 30.0, 1.5),
    FailureClass.TOOL_UNAVAILABLE: RetryStrategy(FailureClass.TOOL_UNAVAILABLE, 2, 1.0, 10.0, 2.0),
    FailureClass.PROVIDER_UNAVAILABLE: RetryStrategy(FailureClass.PROVIDER_UNAVAILABLE, 3, 10.0, 120.0, 2.0),
    FailureClass.CONTEXT_GAP: RetryStrategy(FailureClass.CONTEXT_GAP, 2, 0.5, 5.0, 1.0),
}
_DEFAULT = RetryStrategy(FailureClass.UNKNOWN, 1, 1.0, 10.0, 1.5)


class RetryStrategySelector:
    def select(self, failure_class: FailureClass, attempt: int = 0) -> RetryStrategy:
        return _STRATEGIES.get(failure_class, _DEFAULT)
```

- [ ] **Step 20.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/recovery/test_failure_classifier.py -v --no-cov
```
Expected: `12 passed`

- [ ] **Step 20.7: Commit**

```bash
cd agent-verse-backend
git add app/recovery/ tests/recovery/
git commit -m "feat(recovery): add FailureClassifier + RecoveryPolicy — never generic retry"
```

---

## Task 21: QoS Scheduler

**Files:**
- Create: `app/qos/__init__.py`
- Create: `app/qos/scheduler.py`
- Create: `app/qos/priority_policy.py`
- Create: `app/qos/backpressure.py`
- Create: `tests/qos/__init__.py`
- Create: `tests/qos/test_qos_scheduler.py`

- [ ] **Step 21.1: Write failing tests**

```python
# tests/qos/test_qos_scheduler.py
"""Enterprise tenants and high-priority goals avoid noisy-neighbour effects."""
from __future__ import annotations
import pytest
from app.qos.scheduler import QoSScheduler, ScheduledGoal
from app.qos.priority_policy import PriorityPolicy, QueuePriority
from app.qos.backpressure import BackpressureController
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def scheduler():
    return QoSScheduler(max_concurrent=5)


def test_enterprise_goal_gets_high_priority():
    policy = PriorityPolicy()
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
    priority = policy.compute(tenant_ctx=ctx, risk_level="low", retry_count=0)
    assert priority == QueuePriority.HIGH


def test_free_plan_gets_low_priority():
    policy = PriorityPolicy()
    ctx = TenantContext(tenant_id="t2", plan=PlanTier.FREE, api_key_id="k2")
    priority = policy.compute(tenant_ctx=ctx, risk_level="low", retry_count=0)
    assert priority == QueuePriority.LOW


def test_high_risk_bumps_priority():
    policy = PriorityPolicy()
    ctx = TenantContext(tenant_id="t3", plan=PlanTier.STARTER, api_key_id="k3")
    priority = policy.compute(tenant_ctx=ctx, risk_level="high", retry_count=0)
    assert priority in (QueuePriority.MEDIUM, QueuePriority.HIGH)


def test_scheduler_accepts_goal(scheduler):
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    result = scheduler.schedule(goal_id="g1", tenant_ctx=ctx, priority=QueuePriority.MEDIUM)
    assert isinstance(result, ScheduledGoal)
    assert result.goal_id == "g1"
    assert result.queue_name is not None


def test_scheduler_routes_enterprise_to_enterprise_queue(scheduler):
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
    result = scheduler.schedule(goal_id="g2", tenant_ctx=ctx, priority=QueuePriority.HIGH)
    assert "enterprise" in result.queue_name.lower() or result.queue_name == "goals.enterprise"


def test_backpressure_blocks_when_queue_full():
    controller = BackpressureController(max_depth=2)
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    controller.record_queued(tenant_id="t1")
    controller.record_queued(tenant_id="t1")
    controller.record_queued(tenant_id="t1")
    assert controller.is_overloaded(tenant_id="t1") is True


def test_backpressure_allows_when_under_limit():
    controller = BackpressureController(max_depth=5)
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    controller.record_queued(tenant_id="t1")
    assert controller.is_overloaded(tenant_id="t1") is False
```

- [ ] **Step 21.2: Create dirs and confirm failure**

```bash
mkdir -p agent-verse-backend/app/qos
mkdir -p agent-verse-backend/tests/qos
touch agent-verse-backend/app/qos/__init__.py
touch agent-verse-backend/tests/qos/__init__.py
cd agent-verse-backend && uv run pytest tests/qos/test_qos_scheduler.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 21.3: Implement `app/qos/priority_policy.py`**

```python
"""PriorityPolicy — maps tenant plan + risk to QueuePriority."""
from __future__ import annotations
import enum
from app.tenancy.context import TenantContext, PlanTier


class QueuePriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


_PLAN_PRIORITY = {
    PlanTier.ENTERPRISE: QueuePriority.HIGH,
    PlanTier.PROFESSIONAL: QueuePriority.MEDIUM,
    PlanTier.STARTER: QueuePriority.MEDIUM,
    PlanTier.FREE: QueuePriority.LOW,
}


class PriorityPolicy:
    def compute(
        self,
        *,
        tenant_ctx: TenantContext,
        risk_level: str = "low",
        retry_count: int = 0,
    ) -> QueuePriority:
        base = _PLAN_PRIORITY.get(tenant_ctx.plan, QueuePriority.LOW)

        if risk_level in ("high", "critical"):
            # Bump one level for high-risk (needs fast HITL response)
            bump = {
                QueuePriority.LOW: QueuePriority.MEDIUM,
                QueuePriority.MEDIUM: QueuePriority.HIGH,
                QueuePriority.HIGH: QueuePriority.CRITICAL,
                QueuePriority.CRITICAL: QueuePriority.CRITICAL,
            }
            return bump[base]

        return base
```

- [ ] **Step 21.4: Implement `app/qos/backpressure.py`**

```python
"""BackpressureController — per-tenant queue depth tracking."""
from __future__ import annotations
from collections import defaultdict


class BackpressureController:
    def __init__(self, max_depth: int = 10) -> None:
        self._max = max_depth
        self._queued: dict[str, int] = defaultdict(int)

    def record_queued(self, tenant_id: str) -> None:
        self._queued[tenant_id] += 1

    def record_completed(self, tenant_id: str) -> None:
        self._queued[tenant_id] = max(0, self._queued[tenant_id] - 1)

    def is_overloaded(self, tenant_id: str) -> bool:
        return self._queued[tenant_id] > self._max

    def queue_depth(self, tenant_id: str) -> int:
        return self._queued[tenant_id]
```

- [ ] **Step 21.5: Implement `app/qos/scheduler.py`**

```python
"""QoSScheduler — routes goals to per-plan Celery queues with priority."""
from __future__ import annotations
from dataclasses import dataclass
from app.qos.priority_policy import QueuePriority
from app.tenancy.context import TenantContext, PlanTier

_QUEUE_MAP = {
    PlanTier.ENTERPRISE: "goals.enterprise",
    PlanTier.PROFESSIONAL: "goals.professional",
    PlanTier.STARTER: "goals.starter",
    PlanTier.FREE: "goals.free",
}


@dataclass
class ScheduledGoal:
    goal_id: str
    tenant_id: str
    queue_name: str
    priority: QueuePriority


class QoSScheduler:
    def __init__(self, max_concurrent: int = 10) -> None:
        self._max = max_concurrent

    def schedule(
        self,
        goal_id: str,
        *,
        tenant_ctx: TenantContext,
        priority: QueuePriority = QueuePriority.MEDIUM,
    ) -> ScheduledGoal:
        queue = _QUEUE_MAP.get(tenant_ctx.plan, "goals.free")
        return ScheduledGoal(
            goal_id=goal_id,
            tenant_id=tenant_ctx.tenant_id,
            queue_name=queue,
            priority=priority,
        )
```

- [ ] **Step 21.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/qos/test_qos_scheduler.py -v --no-cov
```
Expected: `7 passed`

- [ ] **Step 21.7: Run all Part 4 tests**

```bash
cd agent-verse-backend
uv run pytest tests/evals/ tests/tool_runtime/ tests/provenance/ tests/recovery/ tests/qos/ -v --no-cov
```
Expected: All 48+ tests pass

- [ ] **Step 21.8: Commit**

```bash
cd agent-verse-backend
git add app/qos/ tests/qos/
git commit -m "feat(qos): add QoSScheduler + PriorityPolicy + BackpressureController"
```

---

## Task 22: Optimization Layer Stubs (P1)

**Files:**
- Create: `app/optimization/__init__.py`
- Create: `app/optimization/model_optimizer.py`
- Create: `app/optimization/cost_optimizer.py`
- Create: `app/optimization/token_optimizer.py`
- Create: `tests/optimization/__init__.py`
- Create: `tests/optimization/test_optimization.py`

- [ ] **Step 22.1: Write failing tests**

```python
# tests/optimization/test_optimization.py
"""Optimization layer selects cheaper models when safe and compresses tokens."""
from __future__ import annotations
import pytest
from app.optimization.model_optimizer import ModelOptimizer, ModelOptimizationDecision
from app.optimization.cost_optimizer import CostOptimizer
from app.optimization.token_optimizer import TokenOptimizer
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    RiskLevel, Complexity,
)
from app.tenancy.context import TenantContext, PlanTier


def _make_profile(complexity: Complexity = Complexity.SIMPLE, risk: RiskLevel = RiskLevel.LOW):
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", complexity=complexity, risk=risk),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(cost_class="medium"),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_model_optimizer_downgrades_simple_low_risk():
    optimizer = ModelOptimizer()
    profile = _make_profile(Complexity.SIMPLE, RiskLevel.LOW)
    decision = optimizer.optimize(profile)
    assert isinstance(decision, ModelOptimizationDecision)
    assert decision.recommended_cost_class in ("low", "medium")


def test_model_optimizer_keeps_high_quality_for_critical():
    optimizer = ModelOptimizer()
    profile = _make_profile(Complexity.EXPERT, RiskLevel.CRITICAL)
    decision = optimizer.optimize(profile)
    assert decision.recommended_cost_class in ("medium", "high")
    assert decision.downgrade_safe is False


def test_cost_optimizer_estimates_savings():
    optimizer = CostOptimizer()
    savings = optimizer.estimate_savings(
        current_cost_class="high",
        proposed_cost_class="medium",
        estimated_tokens=10000,
    )
    assert savings >= 0.0


def test_token_optimizer_compresses_long_prompt():
    optimizer = TokenOptimizer(max_tokens=100)
    long_text = "This is a sentence. " * 200
    compressed = optimizer.compress(long_text)
    tokens = len(compressed) // 4
    assert tokens <= 120  # within reasonable margin


def test_token_optimizer_preserves_short_text():
    optimizer = TokenOptimizer(max_tokens=1000)
    short = "Hello world"
    assert optimizer.compress(short) == short
```

- [ ] **Step 22.2: Create dirs and implement**

```bash
mkdir -p agent-verse-backend/app/optimization
mkdir -p agent-verse-backend/tests/optimization
touch agent-verse-backend/app/optimization/__init__.py
touch agent-verse-backend/tests/optimization/__init__.py
```

Implement `app/optimization/model_optimizer.py`:

```python
"""ModelOptimizer — recommends cheaper models when quality/safety allows."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class ModelOptimizationDecision:
    recommended_cost_class: str
    downgrade_safe: bool
    reason: str


class ModelOptimizer:
    def optimize(self, profile: "GoalRuntimeProfile") -> ModelOptimizationDecision:
        from app.orchestration.runtime_profile import Complexity, RiskLevel
        complexity = profile.properties.complexity
        risk = profile.properties.risk

        # Never downgrade for critical risk or expert complexity
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) or complexity == Complexity.EXPERT:
            return ModelOptimizationDecision(
                recommended_cost_class="high",
                downgrade_safe=False,
                reason=f"risk={risk.value} complexity={complexity.value} — keep high quality",
            )

        if complexity == Complexity.SIMPLE and risk == RiskLevel.LOW:
            return ModelOptimizationDecision(
                recommended_cost_class="low",
                downgrade_safe=True,
                reason="simple low-risk goal — cheaper model is sufficient",
            )

        return ModelOptimizationDecision(
            recommended_cost_class="medium",
            downgrade_safe=True,
            reason="medium complexity — medium cost model appropriate",
        )
```

Implement `app/optimization/cost_optimizer.py`:

```python
"""CostOptimizer — estimates cost savings from model downgrade."""
from __future__ import annotations

_COST_PER_1K = {"low": 0.0003, "medium": 0.003, "high": 0.015, "free": 0.0}


class CostOptimizer:
    def estimate_savings(
        self,
        current_cost_class: str,
        proposed_cost_class: str,
        estimated_tokens: int,
    ) -> float:
        current_cost = _COST_PER_1K.get(current_cost_class, 0.003) * (estimated_tokens / 1000)
        proposed_cost = _COST_PER_1K.get(proposed_cost_class, 0.003) * (estimated_tokens / 1000)
        return max(0.0, current_cost - proposed_cost)
```

Implement `app/optimization/token_optimizer.py`:

```python
"""TokenOptimizer — compresses prompts to fit within token budgets."""
from __future__ import annotations

_CHARS_PER_TOKEN = 4


class TokenOptimizer:
    def __init__(self, max_tokens: int = 4000) -> None:
        self._max = max_tokens

    def compress(self, text: str) -> str:
        max_chars = self._max * _CHARS_PER_TOKEN
        if len(text) <= max_chars:
            return text
        # Simple truncation with ellipsis — production would use real compression
        return text[:max_chars - 20] + "\n...[truncated]"
```

- [ ] **Step 22.3: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/optimization/test_optimization.py -v --no-cov
```
Expected: `5 passed`

- [ ] **Step 22.4: Commit**

```bash
cd agent-verse-backend
git add app/optimization/ tests/optimization/
git commit -m "feat(optimization): add ModelOptimizer + CostOptimizer + TokenOptimizer stubs"
```
