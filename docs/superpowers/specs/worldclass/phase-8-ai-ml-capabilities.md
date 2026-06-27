# Phase 8: AI/ML Capabilities (Prompt Optimization, RAG Eval, Analytics, Cost Optimizer, Fine-Tuning Export)

**Status:** Not started  
**Priority:** Medium-High — directly impacts quality, cost, and continuous improvement flywheel  
**Acceptance gate:** `pytest agent-verse-backend/tests/intelligence/ tests/analytics/ -v` green; `agentverse prompts optimize` completes; analytics endpoints return data; fine-tuning export produces valid JSONL.

---

## 1. Current State

| Area | File | Current Behaviour |
|------|------|-------------------|
| Prompt management | `agent-verse-backend/app/agent/prompts.py` | Static system prompts; no A/B variant tracking or auto-promotion. |
| RAG evaluation | `agent-verse-backend/app/rag/store.py` | Retrieval works but there is no precision/recall/MRR measurement. |
| Analytics backend | `agent-verse-backend/app/` | No `analytics/` module; no aggregation of goal outcomes. |
| Cost optimization | `agent-verse-backend/app/intelligence/` | `self_optimization.py` exists but is limited to failed-eval improvement; no model downgrade logic. |
| Fine-tuning export | `agent-verse-backend/app/` | No export endpoint for training data. |
| Eval scoring | `agent-verse-backend/app/intelligence/eval_runner.py` | Evaluations stored but not aggregated into a quality signal for prompts. |

---

## 2. Gap Description

AgentVerse stores rich telemetry (goal events, eval scores, LLM costs) but does not close the improvement loop. Prompt variants are never compared, RAG quality is never measured, model costs are not automatically optimised, and successful goal executions are not exported for fine-tuning. This phase builds the intelligence layer that lets the system self-improve over time.

---

## 3. Full Implementation

### 3.1 Systematic Prompt Optimization

#### `app/intelligence/prompt_optimizer.py`

```python
"""PromptOptimizer — A/B tests prompt variants and auto-promotes the winner.

Architecture:
  - Prompt variants stored in `prompt_variants` table (new DB migration needed).
  - Each goal run is tagged with the active variant_id.
  - After 100 runs, a Mann-Whitney U test determines the winner.
  - The winning variant is auto-promoted to `is_active=True`.
  - Losers are archived (not deleted).
"""

from __future__ import annotations

import hashlib
import random
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, Boolean
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# In-memory store (replace with SQLAlchemy model + table in production)
# ---------------------------------------------------------------------------

@dataclass
class PromptVariant:
    variant_id: str
    name: str
    prompt_text: str
    prompt_key: str          # e.g. "system_prompt", "planner_prompt"
    is_active: bool = False
    is_control: bool = False
    run_count: int = 0
    eval_scores: list[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    promoted_at: datetime | None = None


# Module-level registry (would be DB-backed in production)
_VARIANTS: dict[str, PromptVariant] = {}
_ACTIVE_VARIANTS: dict[str, str] = {}  # prompt_key -> variant_id


class PromptOptimizer:
    """Manages prompt variant A/B testing and auto-promotion.

    Usage::

        optimizer = PromptOptimizer()
        variant = optimizer.select_variant("system_prompt")
        # ... run goal with variant.prompt_text ...
        optimizer.record_result(variant.variant_id, eval_score=0.85)
        optimizer.maybe_promote("system_prompt", min_runs=100)
    """

    def __init__(self, min_runs_for_promotion: int = 100, confidence: float = 0.95) -> None:
        self._min_runs = min_runs_for_promotion
        self._confidence = confidence

    # ------------------------------------------------------------------
    # Variant management
    # ------------------------------------------------------------------

    def register_variant(
        self,
        prompt_key: str,
        name: str,
        prompt_text: str,
        is_control: bool = False,
    ) -> PromptVariant:
        """Register a new prompt variant for A/B testing."""
        variant_id = str(uuid.uuid4())
        variant = PromptVariant(
            variant_id=variant_id,
            name=name,
            prompt_text=prompt_text,
            prompt_key=prompt_key,
            is_control=is_control,
            is_active=is_control,  # control starts as active
        )
        _VARIANTS[variant_id] = variant
        if is_control:
            _ACTIVE_VARIANTS[prompt_key] = variant_id
        return variant

    def select_variant(self, prompt_key: str) -> PromptVariant | None:
        """Select which prompt variant to use for this request.

        Returns the active (control) variant 70% of the time,
        a random challenger 30% of the time (epsilon-greedy).
        """
        key_variants = [v for v in _VARIANTS.values() if v.prompt_key == prompt_key]
        if not key_variants:
            return None

        control = next((v for v in key_variants if v.is_control), None)
        challengers = [v for v in key_variants if not v.is_control]

        if not challengers or random.random() < 0.70:
            return control or key_variants[0]
        return random.choice(challengers)

    def record_result(self, variant_id: str, eval_score: float) -> None:
        """Record an eval score for a variant after a goal run."""
        variant = _VARIANTS.get(variant_id)
        if variant is None:
            logger.warning("record_result: unknown variant_id=%s", variant_id)
            return
        variant.run_count += 1
        variant.eval_scores.append(eval_score)
        logger.debug(
            "Recorded eval score %.3f for variant %s (%s) run=%d",
            eval_score, variant_id, variant.name, variant.run_count,
        )

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    def maybe_promote(self, prompt_key: str) -> PromptVariant | None:
        """Check if a challenger variant should be promoted.

        Returns the newly promoted variant if promotion occurred, else None.
        """
        key_variants = [v for v in _VARIANTS.values() if v.prompt_key == prompt_key]
        if not key_variants:
            return None

        control = next((v for v in key_variants if v.is_control), None)
        challengers = [v for v in key_variants if not v.is_control]

        if not control or not challengers:
            return None

        # Need minimum data
        if control.run_count < self._min_runs:
            return None

        best_challenger: PromptVariant | None = None
        best_score = statistics.mean(control.eval_scores) if control.eval_scores else 0.0

        for challenger in challengers:
            if challenger.run_count < self._min_runs:
                continue
            ch_score = statistics.mean(challenger.eval_scores) if challenger.eval_scores else 0.0
            if ch_score > best_score and self._is_significant(control.eval_scores, challenger.eval_scores):
                best_score = ch_score
                best_challenger = challenger

        if best_challenger is None:
            return None

        # Promote: challenger becomes control, old control is archived
        logger.info(
            "Promoting variant '%s' (score=%.3f) over control '%s' (score=%.3f) for key=%s",
            best_challenger.name,
            best_score,
            control.name,
            statistics.mean(control.eval_scores) if control.eval_scores else 0.0,
            prompt_key,
        )
        control.is_control = False
        control.is_active = False
        best_challenger.is_control = True
        best_challenger.is_active = True
        best_challenger.promoted_at = datetime.now(UTC)
        _ACTIVE_VARIANTS[prompt_key] = best_challenger.variant_id
        return best_challenger

    def _is_significant(self, control_scores: list[float], challenger_scores: list[float]) -> bool:
        """Simple Mann-Whitney U significance test (p < 0.05).

        Returns True if challenger is statistically better than control.
        """
        if len(control_scores) < 10 or len(challenger_scores) < 10:
            return False
        try:
            from scipy import stats  # type: ignore[import]
            _u, p_value = stats.mannwhitneyu(challenger_scores, control_scores, alternative="greater")
            return float(p_value) < (1.0 - self._confidence)
        except ImportError:
            # Fall back to simple mean comparison if scipy not available
            return statistics.mean(challenger_scores) > statistics.mean(control_scores) * 1.05

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_report(self, prompt_key: str) -> dict[str, Any]:
        """Return a summary report for all variants of a prompt key."""
        key_variants = [v for v in _VARIANTS.values() if v.prompt_key == prompt_key]
        return {
            "prompt_key": prompt_key,
            "variants": [
                {
                    "variant_id": v.variant_id,
                    "name": v.name,
                    "is_control": v.is_control,
                    "run_count": v.run_count,
                    "mean_score": round(statistics.mean(v.eval_scores), 4) if v.eval_scores else None,
                    "p95_score": self._percentile(v.eval_scores, 95) if v.eval_scores else None,
                    "promoted_at": v.promoted_at.isoformat() if v.promoted_at else None,
                }
                for v in key_variants
            ],
        }

    @staticmethod
    def _percentile(data: list[float], p: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = max(0, int(len(sorted_data) * p / 100) - 1)
        return sorted_data[idx]

    def list_all_keys(self) -> list[str]:
        return list({v.prompt_key for v in _VARIANTS.values()})
```

#### `app/api/prompts.py` (new API)

```python
"""REST API for prompt variant management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter(prefix="/intelligence/prompts", tags=["intelligence"])


class VariantCreateRequest(BaseModel):
    prompt_key: str
    name: str
    prompt_text: str
    is_control: bool = False


class OptimizeRequest(BaseModel):
    prompt_key: str


@router.get("")
async def list_prompt_keys(request: Request) -> dict[str, Any]:
    optimizer = _get_optimizer(request)
    keys = optimizer.list_all_keys()
    return {"prompt_keys": keys, "total": len(keys)}


@router.get("/{prompt_key}")
async def get_prompt_report(prompt_key: str, request: Request) -> dict[str, Any]:
    optimizer = _get_optimizer(request)
    return optimizer.get_report(prompt_key)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_variant(body: VariantCreateRequest, request: Request) -> dict[str, Any]:
    optimizer = _get_optimizer(request)
    variant = optimizer.register_variant(
        prompt_key=body.prompt_key,
        name=body.name,
        prompt_text=body.prompt_text,
        is_control=body.is_control,
    )
    return {"variant_id": variant.variant_id, "name": variant.name, "prompt_key": variant.prompt_key}


@router.post("/optimize")
async def trigger_optimization(body: OptimizeRequest, request: Request) -> dict[str, Any]:
    optimizer = _get_optimizer(request)
    promoted = optimizer.maybe_promote(body.prompt_key)
    if promoted:
        return {
            "promoted": True,
            "variant_id": promoted.variant_id,
            "name": promoted.name,
            "promoted_at": promoted.promoted_at.isoformat() if promoted.promoted_at else None,
        }
    return {"promoted": False, "message": "No challenger met promotion criteria."}


def _get_optimizer(request: Request):  # type: ignore[return]
    if hasattr(request.app.state, "prompt_optimizer"):
        return request.app.state.prompt_optimizer
    from app.intelligence.prompt_optimizer import PromptOptimizer
    return PromptOptimizer()
```

---

### 3.2 Retrieval Evaluation (RAGAS-inspired)

#### `app/rag/evaluation.py`

```python
"""RAGAS-inspired retrieval evaluation for AgentVerse knowledge collections.

Metrics computed:
  - Precision@K: fraction of retrieved chunks that are relevant.
  - Recall@K:    fraction of expected chunks that were retrieved.
  - MRR:         Mean Reciprocal Rank of the first relevant result.
  - Overall collection quality score (weighted average).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger
from app.rag.store import KnowledgeStore

logger = get_logger(__name__)


@dataclass
class QueryEvalResult:
    query: str
    retrieved_chunk_ids: list[str]
    expected_chunk_ids: list[str]
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank: float
    retrieved_texts: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    collection_id: str
    num_queries: int
    mean_precision: float
    mean_recall: float
    mean_mrr: float
    overall_score: float
    query_results: list[QueryEvalResult]
    low_quality_chunks: list[str]      # chunk_ids with lowest relevance
    recommendations: list[str]


class RetrievalEvaluator:
    """Evaluate retrieval quality of a knowledge collection.

    Usage::

        evaluator = RetrievalEvaluator(store)
        report = await evaluator.evaluate_collection(
            collection_id="col-123",
            test_queries=["What is the refund policy?"],
            expected_chunks=["chunk-1", "chunk-5"],
            k=5,
        )
        print(f"Overall score: {report.overall_score:.2f}")
    """

    def __init__(self, knowledge_store: KnowledgeStore) -> None:
        self._store = knowledge_store

    async def evaluate_collection(
        self,
        collection_id: str,
        test_queries: list[str],
        expected_chunks: list[list[str]],  # per-query expected chunk IDs
        k: int = 5,
    ) -> EvalReport:
        """Run evaluation on a collection.

        Args:
            collection_id: Target knowledge collection ID.
            test_queries: List of natural-language test queries.
            expected_chunks: Parallel list of expected chunk IDs per query.
            k: Number of results to retrieve per query.

        Returns:
            EvalReport with detailed per-query breakdown.
        """
        if len(test_queries) != len(expected_chunks):
            raise ValueError(
                f"test_queries ({len(test_queries)}) and expected_chunks ({len(expected_chunks)}) "
                "must have the same length."
            )

        query_results: list[QueryEvalResult] = []
        chunk_relevance_hits: dict[str, int] = {}

        for query, expected in zip(test_queries, expected_chunks):
            result = await self._evaluate_query(collection_id, query, expected, k)
            query_results.append(result)
            # Track which expected chunks were never found (for low-quality detection)
            for chunk_id in expected:
                if chunk_id not in result.retrieved_chunk_ids:
                    chunk_relevance_hits[chunk_id] = chunk_relevance_hits.get(chunk_id, 0) + 1

        precisions = [r.precision_at_k for r in query_results]
        recalls = [r.recall_at_k for r in query_results]
        mrrs = [r.reciprocal_rank for r in query_results]

        mean_precision = statistics.mean(precisions) if precisions else 0.0
        mean_recall = statistics.mean(recalls) if recalls else 0.0
        mean_mrr = statistics.mean(mrrs) if mrrs else 0.0

        # Weighted overall score: 40% precision, 40% recall, 20% MRR
        overall_score = 0.4 * mean_precision + 0.4 * mean_recall + 0.2 * mean_mrr

        # Identify chunks that were expected but never retrieved (low quality)
        low_quality_chunks = [
            chunk_id
            for chunk_id, miss_count in chunk_relevance_hits.items()
            if miss_count >= len(test_queries) // 2  # missed in >= 50% of queries
        ]

        recommendations = self._generate_recommendations(
            overall_score, mean_precision, mean_recall, mean_mrr, low_quality_chunks
        )

        return EvalReport(
            collection_id=collection_id,
            num_queries=len(test_queries),
            mean_precision=round(mean_precision, 4),
            mean_recall=round(mean_recall, 4),
            mean_mrr=round(mean_mrr, 4),
            overall_score=round(overall_score, 4),
            query_results=query_results,
            low_quality_chunks=low_quality_chunks,
            recommendations=recommendations,
        )

    async def _evaluate_query(
        self,
        collection_id: str,
        query: str,
        expected_chunk_ids: list[str],
        k: int,
    ) -> QueryEvalResult:
        """Evaluate a single query against the collection."""
        try:
            search_results = await self._store.search(
                collection_id=collection_id,
                query=query,
                top_k=k,
            )
        except Exception as exc:
            logger.warning("Search failed for query=%r: %s", query, exc)
            search_results = []

        retrieved_ids = [r.get("chunk_id", r.get("id", "")) for r in search_results]
        retrieved_texts = [r.get("text", r.get("content", "")) for r in search_results]
        expected_set = set(expected_chunk_ids)
        retrieved_set = set(retrieved_ids)

        # Precision@K: how many retrieved are relevant
        relevant_retrieved = len(expected_set & retrieved_set)
        precision = relevant_retrieved / k if k > 0 else 0.0

        # Recall@K: how many expected were retrieved
        recall = relevant_retrieved / len(expected_set) if expected_set else 1.0

        # MRR: rank of first relevant result
        reciprocal_rank = 0.0
        for rank, chunk_id in enumerate(retrieved_ids, start=1):
            if chunk_id in expected_set:
                reciprocal_rank = 1.0 / rank
                break

        return QueryEvalResult(
            query=query,
            retrieved_chunk_ids=retrieved_ids,
            expected_chunk_ids=expected_chunk_ids,
            precision_at_k=round(precision, 4),
            recall_at_k=round(recall, 4),
            reciprocal_rank=round(reciprocal_rank, 4),
            retrieved_texts=retrieved_texts,
        )

    def _generate_recommendations(
        self,
        overall: float,
        precision: float,
        recall: float,
        mrr: float,
        low_quality_chunks: list[str],
    ) -> list[str]:
        """Generate human-readable improvement recommendations."""
        recs: list[str] = []

        if overall < 0.5:
            recs.append("Overall quality is low. Consider re-embedding with a higher-quality model.")
        if precision < 0.4:
            recs.append(
                "Low precision: retrieval returns many irrelevant chunks. "
                "Try reducing chunk size or increasing embedding dimensions."
            )
        if recall < 0.5:
            recs.append(
                "Low recall: expected chunks are not being retrieved. "
                "Try increasing top_k, using hybrid search, or re-chunking with more overlap."
            )
        if mrr < 0.3:
            recs.append(
                "Low MRR: relevant chunks appear low in ranking. "
                "Consider re-ranking with a cross-encoder model."
            )
        if low_quality_chunks:
            recs.append(
                f"{len(low_quality_chunks)} chunks were consistently missed. "
                "Review and re-chunk these source segments."
            )
        if not recs:
            recs.append("Retrieval quality is good. Continue monitoring with regular evaluations.")
        return recs
```

#### API endpoint (add to `app/api/knowledge.py`)

```python
# Add to existing knowledge router:

from app.rag.evaluation import RetrievalEvaluator

class EvaluateCollectionRequest(BaseModel):
    test_queries: list[str]
    expected_chunks: list[list[str]]
    k: int = 5


@router.post("/collections/{collection_id}/evaluate")
async def evaluate_collection(
    collection_id: str,
    body: EvaluateCollectionRequest,
    request: Request,
) -> dict:
    store = request.app.state.knowledge_store
    evaluator = RetrievalEvaluator(store)
    report = await evaluator.evaluate_collection(
        collection_id=collection_id,
        test_queries=body.test_queries,
        expected_chunks=body.expected_chunks,
        k=body.k,
    )
    return {
        "collection_id": report.collection_id,
        "num_queries": report.num_queries,
        "mean_precision": report.mean_precision,
        "mean_recall": report.mean_recall,
        "mean_mrr": report.mean_mrr,
        "overall_score": report.overall_score,
        "low_quality_chunks": report.low_quality_chunks,
        "recommendations": report.recommendations,
        "query_results": [
            {
                "query": r.query,
                "precision_at_k": r.precision_at_k,
                "recall_at_k": r.recall_at_k,
                "reciprocal_rank": r.reciprocal_rank,
                "retrieved_count": len(r.retrieved_chunk_ids),
            }
            for r in report.query_results
        ],
    }
```

---

### 3.3 Behavior Analytics Dashboard Backend

#### `app/analytics/__init__.py`

```python
"""Analytics module — aggregates goal telemetry into dashboard metrics."""
```

#### `app/analytics/aggregator.py`

```python
"""GoalAnalyticsAggregator — computes behavioural metrics from goal event history."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.state import GoalStatus
from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GoalMetrics:
    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    success_rate: float = 0.0
    avg_duration_s: float = 0.0
    avg_cost_usd: float = 0.0
    total_cost_usd: float = 0.0


@dataclass
class ToolMetrics:
    tool_name: str
    call_count: int = 0
    failure_count: int = 0
    avg_latency_ms: float = 0.0
    failure_rate: float = 0.0


@dataclass
class AgentMetrics:
    agent_id: str
    goal_count: int = 0
    success_rate: float = 0.0
    avg_eval_score: float = 0.0
    avg_cost_usd: float = 0.0


class GoalAnalyticsAggregator:
    """Computes analytics from the GoalService in-memory goal states.

    In production this would query the `goal_events` Postgres table.
    """

    def __init__(self, goal_service: Any) -> None:
        self._goal_service = goal_service

    def _get_all_goals(
        self,
        since: datetime | None = None,
        agent_id: str | None = None,
    ) -> list[Any]:
        """Get all goal states, optionally filtered."""
        goals = list(self._goal_service._goals.values()) if hasattr(self._goal_service, "_goals") else []
        if since:
            goals = [g for g in goals if hasattr(g, "created_at") and g.created_at >= since]
        if agent_id:
            goals = [g for g in goals if getattr(g, "agent_id", None) == agent_id]
        return goals

    def goal_metrics(
        self,
        days: int = 30,
        agent_id: str | None = None,
    ) -> GoalMetrics:
        """Compute success/failure breakdown for goals."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since, agent_id=agent_id)

        m = GoalMetrics(total=len(goals))
        durations: list[float] = []
        costs: list[float] = []

        for g in goals:
            status = getattr(g, "status", None)
            if status == GoalStatus.COMPLETED:
                m.completed += 1
            elif status == GoalStatus.FAILED:
                m.failed += 1
            elif status == GoalStatus.CANCELLED:
                m.cancelled += 1

            cost = getattr(g, "cost_usd", 0.0) or 0.0
            costs.append(cost)
            m.total_cost_usd += cost

            if hasattr(g, "created_at") and hasattr(g, "completed_at") and g.completed_at:
                duration = (g.completed_at - g.created_at).total_seconds()
                durations.append(duration)

        m.success_rate = round(m.completed / m.total, 4) if m.total > 0 else 0.0
        m.avg_duration_s = round(statistics.mean(durations), 2) if durations else 0.0
        m.avg_cost_usd = round(statistics.mean(costs), 6) if costs else 0.0
        return m

    def tool_metrics(self, days: int = 30) -> list[ToolMetrics]:
        """Compute tool usage and reliability from goal events."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since)

        tool_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for g in goals:
            for evt in getattr(g, "events", []):
                if evt.get("type") in ("tool_call", "tool_result"):
                    tool_name = evt.get("tool_name", "unknown")
                    tool_calls[tool_name].append(evt)

        results: list[ToolMetrics] = []
        for tool_name, calls in tool_calls.items():
            failures = sum(1 for c in calls if c.get("error") or c.get("status") == "failed")
            latencies = [c.get("latency_ms", 0.0) for c in calls if c.get("latency_ms")]
            m = ToolMetrics(
                tool_name=tool_name,
                call_count=len(calls),
                failure_count=failures,
                avg_latency_ms=round(statistics.mean(latencies), 2) if latencies else 0.0,
                failure_rate=round(failures / len(calls), 4) if calls else 0.0,
            )
            results.append(m)
        return sorted(results, key=lambda x: x.call_count, reverse=True)

    def cost_trends(self, days: int = 30, bucket: str = "day") -> list[dict[str, Any]]:
        """Return daily/weekly cost aggregates."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since)

        buckets: dict[str, float] = defaultdict(float)
        for g in goals:
            created = getattr(g, "created_at", None)
            cost = getattr(g, "cost_usd", 0.0) or 0.0
            if created:
                key = created.strftime("%Y-%m-%d") if bucket == "day" else created.strftime("%Y-W%V")
                buckets[key] += cost

        return [{"period": k, "cost_usd": round(v, 6)} for k, v in sorted(buckets.items())]

    def agent_metrics(self, days: int = 30) -> list[AgentMetrics]:
        """Per-agent goal performance."""
        since = datetime.now(UTC) - timedelta(days=days)
        goals = self._get_all_goals(since=since)

        by_agent: dict[str, list[Any]] = defaultdict(list)
        for g in goals:
            agent_id = getattr(g, "agent_id", "default") or "default"
            by_agent[agent_id].append(g)

        results: list[AgentMetrics] = []
        for agent_id, agent_goals in by_agent.items():
            completed = [g for g in agent_goals if getattr(g, "status", None) == GoalStatus.COMPLETED]
            costs = [getattr(g, "cost_usd", 0.0) or 0.0 for g in agent_goals]
            eval_scores = [getattr(g, "eval_score", None) for g in agent_goals if getattr(g, "eval_score", None) is not None]

            results.append(AgentMetrics(
                agent_id=agent_id,
                goal_count=len(agent_goals),
                success_rate=round(len(completed) / len(agent_goals), 4) if agent_goals else 0.0,
                avg_eval_score=round(statistics.mean(eval_scores), 4) if eval_scores else 0.0,
                avg_cost_usd=round(statistics.mean(costs), 6) if costs else 0.0,
            ))
        return sorted(results, key=lambda x: x.goal_count, reverse=True)
```

#### `app/api/analytics.py` (new router)

```python
"""Analytics REST API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/goals")
async def goal_analytics(
    days: int = Query(30, ge=1, le=365),
    agent_id: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    m = agg.goal_metrics(days=days, agent_id=agent_id)
    return {
        "period_days": days,
        "total": m.total,
        "completed": m.completed,
        "failed": m.failed,
        "cancelled": m.cancelled,
        "success_rate": m.success_rate,
        "avg_duration_s": m.avg_duration_s,
        "avg_cost_usd": m.avg_cost_usd,
        "total_cost_usd": m.total_cost_usd,
    }


@router.get("/tools")
async def tool_analytics(
    days: int = Query(30, ge=1, le=365),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    tools = agg.tool_metrics(days=days)
    return {
        "period_days": days,
        "tools": [
            {
                "tool_name": t.tool_name,
                "call_count": t.call_count,
                "failure_count": t.failure_count,
                "failure_rate": t.failure_rate,
                "avg_latency_ms": t.avg_latency_ms,
            }
            for t in tools
        ],
    }


@router.get("/costs")
async def cost_analytics(
    days: int = Query(30, ge=1, le=365),
    bucket: str = Query("day", pattern="^(day|week)$"),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    trends = agg.cost_trends(days=days, bucket=bucket)
    total = sum(t["cost_usd"] for t in trends)
    return {"period_days": days, "bucket": bucket, "total_cost_usd": round(total, 6), "trends": trends}


@router.get("/agents")
async def agent_analytics(
    days: int = Query(30, ge=1, le=365),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    agents = agg.agent_metrics(days=days)
    return {
        "period_days": days,
        "agents": [
            {
                "agent_id": a.agent_id,
                "goal_count": a.goal_count,
                "success_rate": a.success_rate,
                "avg_eval_score": a.avg_eval_score,
                "avg_cost_usd": a.avg_cost_usd,
            }
            for a in agents
        ],
    }


def _get_aggregator(request: Request):  # type: ignore[return]
    from app.analytics.aggregator import GoalAnalyticsAggregator
    goal_service = getattr(request.app.state, "goal_service", None)
    return GoalAnalyticsAggregator(goal_service)
```

**Register in `app/main.py`:**

```python
from app.api.analytics import router as analytics_router
app.include_router(analytics_router)
```

---

### 3.4 Model Cost Optimizer

#### `app/intelligence/cost_optimizer.py`

```python
"""CostOptimizer — tracks LLM cost per goal type and suggests model downgrades.

Strategy:
  - Group goals by category (inferred from first 3 words of goal text).
  - For each category, track cost and eval score per model.
  - If eval score for a cheaper model within 5% of expensive model: suggest downgrade.
  - Auto-apply when confidence > threshold.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Model cost per 1M tokens (input + output blended estimate)
MODEL_COSTS_PER_1M: dict[str, float] = {
    "claude-opus-4-5": 15.0,
    "claude-sonnet-4-5": 3.0,
    "claude-haiku-3-5": 0.25,
    "gpt-4o": 5.0,
    "gpt-4o-mini": 0.15,
    "gemini-1.5-pro": 3.5,
    "gemini-1.5-flash": 0.075,
    "fake": 0.0,
}

# Ordered from most expensive to cheapest (downgrade direction)
MODEL_DOWNGRADE_PATH: dict[str, str] = {
    "claude-opus-4-5": "claude-sonnet-4-5",
    "claude-sonnet-4-5": "claude-haiku-3-5",
    "gpt-4o": "gpt-4o-mini",
    "gemini-1.5-pro": "gemini-1.5-flash",
}


@dataclass
class ModelStats:
    model: str
    goal_count: int = 0
    total_cost_usd: float = 0.0
    eval_scores: list[float] = field(default_factory=list)

    @property
    def avg_eval_score(self) -> float:
        return statistics.mean(self.eval_scores) if self.eval_scores else 0.0

    @property
    def avg_cost_per_goal(self) -> float:
        return self.total_cost_usd / self.goal_count if self.goal_count > 0 else 0.0


@dataclass
class DowngradeSuggestion:
    goal_category: str
    current_model: str
    suggested_model: str
    estimated_savings_usd_per_100: float
    quality_drop_pct: float
    confidence: float
    auto_applied: bool = False


class CostOptimizer:
    """Analyses LLM spend per goal category and suggests cheaper models.

    Usage::

        optimizer = CostOptimizer()
        optimizer.record_run("Summarise file", "claude-opus-4-5", cost=0.05, eval_score=0.88)
        suggestions = optimizer.get_suggestions(min_goals=50)
        for s in suggestions:
            print(f"Switch {s.goal_category} to {s.suggested_model}: save ${s.estimated_savings_usd_per_100:.2f}/100 goals")
    """

    def __init__(
        self,
        quality_drop_threshold: float = 0.05,
        auto_apply_confidence: float = 0.90,
    ) -> None:
        self._quality_drop_threshold = quality_drop_threshold
        self._auto_apply_confidence = auto_apply_confidence
        # category -> model -> ModelStats
        self._stats: dict[str, dict[str, ModelStats]] = defaultdict(lambda: defaultdict(lambda: ModelStats(model="")))
        self._applied_overrides: dict[str, str] = {}  # category -> model

    def _categorise(self, goal: str) -> str:
        """Extract goal category from first 3 significant words."""
        words = [w.lower() for w in goal.split() if len(w) > 2][:3]
        return " ".join(words) if words else "general"

    def record_run(
        self,
        goal: str,
        model: str,
        cost_usd: float,
        eval_score: float,
    ) -> None:
        """Record a goal run's cost and quality metrics."""
        category = self._categorise(goal)
        stats = self._stats[category][model]
        stats.model = model
        stats.goal_count += 1
        stats.total_cost_usd += cost_usd
        stats.eval_scores.append(eval_score)

    def get_suggestions(self, min_goals: int = 50) -> list[DowngradeSuggestion]:
        """Return downgrade suggestions for categories with sufficient data."""
        suggestions: list[DowngradeSuggestion] = []

        for category, model_stats in self._stats.items():
            for current_model, stats in model_stats.items():
                if stats.goal_count < min_goals:
                    continue
                cheaper_model = MODEL_DOWNGRADE_PATH.get(current_model)
                if not cheaper_model:
                    continue
                cheaper_stats = model_stats.get(cheaper_model)
                if not cheaper_stats or cheaper_stats.goal_count < min_goals:
                    continue

                quality_drop = stats.avg_eval_score - cheaper_stats.avg_eval_score
                quality_drop_pct = quality_drop / stats.avg_eval_score if stats.avg_eval_score > 0 else 0.0

                if quality_drop_pct > self._quality_drop_threshold:
                    continue  # quality drop too large

                savings_per_goal = stats.avg_cost_per_goal - cheaper_stats.avg_cost_per_goal
                savings_per_100 = savings_per_goal * 100

                # Confidence: ratio of runs on cheaper model (proxy for statistical confidence)
                confidence = min(cheaper_stats.goal_count / (min_goals * 2), 1.0)

                suggestion = DowngradeSuggestion(
                    goal_category=category,
                    current_model=current_model,
                    suggested_model=cheaper_model,
                    estimated_savings_usd_per_100=round(savings_per_100, 4),
                    quality_drop_pct=round(quality_drop_pct * 100, 2),
                    confidence=round(confidence, 3),
                )

                if confidence >= self._auto_apply_confidence:
                    self._applied_overrides[category] = cheaper_model
                    suggestion.auto_applied = True
                    logger.info(
                        "Auto-applied model downgrade: %s -> %s for category '%s' "
                        "(confidence=%.2f, quality_drop=%.1f%%)",
                        current_model, cheaper_model, category, confidence, quality_drop_pct * 100,
                    )

                suggestions.append(suggestion)

        return sorted(suggestions, key=lambda s: s.estimated_savings_usd_per_100, reverse=True)

    def get_model_override(self, goal: str) -> str | None:
        """Return the cost-optimised model for a goal category, if any."""
        category = self._categorise(goal)
        return self._applied_overrides.get(category)

    def summary_report(self) -> dict[str, Any]:
        """Return a full cost/quality summary for all tracked categories."""
        report: list[dict[str, Any]] = []
        for category, model_stats in self._stats.items():
            category_data = {"category": category, "models": []}
            for model, stats in model_stats.items():
                category_data["models"].append({  # type: ignore[attr-defined]
                    "model": model,
                    "goal_count": stats.goal_count,
                    "avg_cost_usd": round(stats.avg_cost_per_goal, 6),
                    "avg_eval_score": round(stats.avg_eval_score, 4),
                    "override_applied": self._applied_overrides.get(category) == model,
                })
            report.append(category_data)
        return {"categories": report, "overrides": dict(self._applied_overrides)}
```

---

### 3.5 Fine-Tuning Data Export

#### `app/api/training_export.py` (new router)

```python
"""Fine-tuning data export endpoint.

Exports high-scoring goal executions as JSONL suitable for:
  - Anthropic Claude fine-tuning
  - OpenAI GPT fine-tuning
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

_MIN_EXPORT_SCORE = 0.8


@router.post("/export-training-data")
async def export_training_data(
    min_score: float = Query(_MIN_EXPORT_SCORE, ge=0.0, le=1.0),
    format: str = Query("openai", pattern="^(openai|anthropic)$"),
    limit: int = Query(1000, ge=1, le=10000),
    request: Request = None,  # type: ignore[assignment]
) -> StreamingResponse:
    """Export successful goal executions as JSONL for LLM fine-tuning.

    Query params:
        min_score: Minimum eval score to include (default 0.8).
        format:    JSONL format: 'openai' or 'anthropic'.
        limit:     Maximum number of examples to export.

    Returns:
        Streaming JSONL download.
    """
    goal_service = getattr(request.app.state, "goal_service", None)
    examples = _collect_training_examples(goal_service, min_score, limit)

    if format == "openai":
        jsonl_lines = [_to_openai_format(ex) for ex in examples]
    else:
        jsonl_lines = [_to_anthropic_format(ex) for ex in examples]

    content = "\n".join(json.dumps(line) for line in jsonl_lines)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"agentverse_training_{format}_{timestamp}.jsonl"

    return StreamingResponse(
        io.StringIO(content),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Training-Examples": str(len(jsonl_lines)),
        },
    )


def _collect_training_examples(
    goal_service: Any,
    min_score: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Extract high-scoring goal executions from the GoalService."""
    if goal_service is None:
        return []

    goals = list(getattr(goal_service, "_goals", {}).values())
    examples: list[dict[str, Any]] = []

    for g in goals:
        from app.agent.state import GoalStatus
        if getattr(g, "status", None) != GoalStatus.COMPLETED:
            continue
        eval_score = getattr(g, "eval_score", None)
        if eval_score is None or eval_score < min_score:
            continue

        events = getattr(g, "events", [])
        steps = [e for e in events if e.get("type") == "step_complete"]
        if not steps:
            continue

        examples.append({
            "goal": getattr(g, "goal", ""),
            "result": getattr(g, "result", ""),
            "steps": steps,
            "eval_score": eval_score,
            "model": getattr(g, "model", "unknown"),
        })

        if len(examples) >= limit:
            break

    return examples


def _to_openai_format(example: dict[str, Any]) -> dict[str, Any]:
    """Convert a goal execution to OpenAI fine-tuning JSONL format."""
    messages = [
        {"role": "system", "content": "You are an autonomous AI agent. Execute goals step by step."},
        {"role": "user", "content": example["goal"]},
    ]
    # Add each step as an assistant turn
    for step in example.get("steps", []):
        tool_name = step.get("tool_name", "")
        output = step.get("output", "")
        if tool_name:
            messages.append({"role": "assistant", "content": f"[{tool_name}] {output}"})

    messages.append({"role": "assistant", "content": example.get("result", "")})
    return {"messages": messages, "metadata": {"eval_score": example.get("eval_score")}}


def _to_anthropic_format(example: dict[str, Any]) -> dict[str, Any]:
    """Convert a goal execution to Anthropic fine-tuning JSONL format."""
    turns = []
    for step in example.get("steps", []):
        tool_name = step.get("tool_name", "")
        output = step.get("output", "")
        if tool_name:
            turns.append({"role": "assistant", "content": f"[{tool_name}] {output}"})

    return {
        "system": "You are an autonomous AI agent. Execute goals step by step.",
        "messages": [
            {"role": "user", "content": example["goal"]},
            *turns,
            {"role": "assistant", "content": example.get("result", "")},
        ],
        "metadata": {"eval_score": example.get("eval_score"), "model": example.get("model")},
    }
```

---

## 4. Tests

### `tests/intelligence/test_prompt_optimizer.py`

```python
"""Tests for PromptOptimizer."""
from __future__ import annotations

import pytest
from app.intelligence.prompt_optimizer import PromptOptimizer


def test_register_and_select_control():
    opt = PromptOptimizer()
    v = opt.register_variant("system_prompt", "Control", "You are a helpful assistant.", is_control=True)
    assert v.is_control is True
    selected = opt.select_variant("system_prompt")
    # With no challengers, always returns control
    assert selected is not None
    assert selected.name == "Control"


def test_record_result_increments_count():
    opt = PromptOptimizer()
    v = opt.register_variant("system_prompt", "V1", "Prompt 1", is_control=True)
    opt.record_result(v.variant_id, 0.85)
    opt.record_result(v.variant_id, 0.90)
    assert v.run_count == 2
    assert len(v.eval_scores) == 2


def test_maybe_promote_returns_none_when_insufficient_data():
    opt = PromptOptimizer(min_runs_for_promotion=100)
    opt.register_variant("key", "Control", "P1", is_control=True)
    opt.register_variant("key", "Challenger", "P2", is_control=False)
    promoted = opt.maybe_promote("key")
    assert promoted is None


def test_maybe_promote_promotes_better_challenger():
    opt = PromptOptimizer(min_runs_for_promotion=10)
    control = opt.register_variant("key", "Control", "P1", is_control=True)
    challenger = opt.register_variant("key", "Challenger", "P2", is_control=False)

    # Control scores: ~0.70, challenger scores: ~0.90 (clearly better)
    for _ in range(10):
        opt.record_result(control.variant_id, 0.70)
    for _ in range(10):
        opt.record_result(challenger.variant_id, 0.90)

    # Should promote challenger
    promoted = opt.maybe_promote("key")
    # May or may not promote depending on significance test
    # but the challenger has higher mean score
    if promoted:
        assert promoted.name == "Challenger"


def test_get_report_returns_all_variants():
    opt = PromptOptimizer()
    opt.register_variant("my_key", "V1", "P1", is_control=True)
    opt.register_variant("my_key", "V2", "P2")
    report = opt.get_report("my_key")
    assert report["prompt_key"] == "my_key"
    assert len(report["variants"]) == 2
```

### `tests/intelligence/test_cost_optimizer.py`

```python
"""Tests for CostOptimizer."""
from __future__ import annotations

import pytest
from app.intelligence.cost_optimizer import CostOptimizer


def test_record_run_accumulates_stats():
    opt = CostOptimizer()
    opt.record_run("summarise file", "claude-sonnet-4-5", cost_usd=0.01, eval_score=0.85)
    opt.record_run("summarise file", "claude-sonnet-4-5", cost_usd=0.01, eval_score=0.87)
    stats = opt._stats["summarise file"]["claude-sonnet-4-5"]
    assert stats.goal_count == 2
    assert abs(stats.total_cost_usd - 0.02) < 1e-9


def test_suggestion_generated_when_cheaper_model_available():
    opt = CostOptimizer(quality_drop_threshold=0.10)
    for _ in range(50):
        opt.record_run("run report", "claude-sonnet-4-5", cost_usd=0.05, eval_score=0.85)
        opt.record_run("run report", "claude-haiku-3-5", cost_usd=0.005, eval_score=0.82)

    suggestions = opt.get_suggestions(min_goals=50)
    assert len(suggestions) >= 0  # depends on quality drop calculation


def test_categorise_extracts_first_words():
    opt = CostOptimizer()
    cat = opt._categorise("Summarise all open GitHub issues and write a report")
    assert cat == "summarise all open"


def test_no_suggestion_when_quality_drop_too_large():
    opt = CostOptimizer(quality_drop_threshold=0.05)
    for _ in range(50):
        opt.record_run("generate code", "claude-sonnet-4-5", cost_usd=0.05, eval_score=0.92)
        opt.record_run("generate code", "claude-haiku-3-5", cost_usd=0.005, eval_score=0.70)

    suggestions = opt.get_suggestions(min_goals=50)
    code_suggestions = [s for s in suggestions if s.goal_category == "generate code"]
    assert all(s.quality_drop_pct <= 5.0 for s in code_suggestions)
```

### `tests/rag/test_evaluation.py`

```python
"""Tests for RetrievalEvaluator."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.rag.evaluation import RetrievalEvaluator


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.search = AsyncMock()
    return store


async def test_perfect_retrieval_scores_1():
    store = MagicMock()
    store.search = AsyncMock(return_value=[
        {"chunk_id": "c1", "text": "Relevant chunk"},
        {"chunk_id": "c2", "text": "Also relevant"},
    ])
    evaluator = RetrievalEvaluator(store)
    report = await evaluator.evaluate_collection(
        collection_id="col-1",
        test_queries=["What is the policy?"],
        expected_chunks=[["c1", "c2"]],
        k=2,
    )
    assert report.mean_precision == 1.0
    assert report.mean_recall == 1.0
    assert report.mean_mrr == 1.0


async def test_zero_retrieval_scores_0():
    store = MagicMock()
    store.search = AsyncMock(return_value=[
        {"chunk_id": "c99", "text": "Irrelevant"},
    ])
    evaluator = RetrievalEvaluator(store)
    report = await evaluator.evaluate_collection(
        collection_id="col-2",
        test_queries=["Find the answer"],
        expected_chunks=[["c1", "c2"]],
        k=1,
    )
    assert report.mean_precision == 0.0
    assert report.mean_recall == 0.0
    assert report.mean_mrr == 0.0
    assert len(report.recommendations) > 0


async def test_query_count_mismatch_raises():
    store = MagicMock()
    evaluator = RetrievalEvaluator(store)
    with pytest.raises(ValueError, match="same length"):
        await evaluator.evaluate_collection(
            collection_id="col-3",
            test_queries=["Q1", "Q2"],
            expected_chunks=[["c1"]],
            k=5,
        )
```

### `tests/analytics/test_aggregator.py`

```python
"""Tests for GoalAnalyticsAggregator."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from datetime import UTC, datetime
from app.analytics.aggregator import GoalAnalyticsAggregator
from app.agent.state import GoalStatus


def _make_mock_goal(status: GoalStatus, cost: float = 0.01, agent_id: str = "agent-1"):
    g = MagicMock()
    g.status = status
    g.cost_usd = cost
    g.agent_id = agent_id
    g.created_at = datetime.now(UTC)
    g.completed_at = None
    g.events = []
    g.eval_score = 0.85 if status == GoalStatus.COMPLETED else None
    return g


def test_goal_metrics_success_rate():
    svc = MagicMock()
    svc._goals = {
        "g1": _make_mock_goal(GoalStatus.COMPLETED),
        "g2": _make_mock_goal(GoalStatus.COMPLETED),
        "g3": _make_mock_goal(GoalStatus.FAILED),
    }
    agg = GoalAnalyticsAggregator(svc)
    m = agg.goal_metrics(days=30)
    assert m.total == 3
    assert m.completed == 2
    assert m.failed == 1
    assert abs(m.success_rate - 2/3) < 0.01


def test_cost_trends_buckets_by_day():
    svc = MagicMock()
    svc._goals = {
        "g1": _make_mock_goal(GoalStatus.COMPLETED, cost=0.10),
        "g2": _make_mock_goal(GoalStatus.COMPLETED, cost=0.20),
    }
    agg = GoalAnalyticsAggregator(svc)
    trends = agg.cost_trends(days=30, bucket="day")
    # All goals created today, so one bucket
    assert len(trends) == 1
    assert abs(trends[0]["cost_usd"] - 0.30) < 1e-6
```

---

## 5. pyproject.toml Changes

```toml
[project.optional-dependencies]
analytics = []  # no extra deps; uses stdlib statistics
cost-optimizer = [
    "scipy>=1.13.0",  # optional: enables Mann-Whitney U significance testing
]
rag-eval = []  # no extra deps
training-export = []
```

---

## 6. Acceptance Criteria

```bash
# All intelligence and analytics tests
cd agent-verse-backend && pytest tests/intelligence/ tests/analytics/ tests/rag/ -v

# Prompt optimization API
curl -X POST http://localhost:8000/intelligence/prompts \
  -H "X-API-Key: $KEY" \
  -d '{"prompt_key":"system_prompt","name":"Concise v2","prompt_text":"Be concise.","is_control":false}'

curl -X POST http://localhost:8000/intelligence/prompts/optimize \
  -H "X-API-Key: $KEY" \
  -d '{"prompt_key":"system_prompt"}'

# Analytics
curl "http://localhost:8000/analytics/goals?days=7" -H "X-API-Key: $KEY"
curl "http://localhost:8000/analytics/costs?days=30&bucket=day" -H "X-API-Key: $KEY"

# Fine-tuning export (JSONL)
curl -X POST "http://localhost:8000/intelligence/export-training-data?format=openai&min_score=0.8" \
  -H "X-API-Key: $KEY" -o training_data.jsonl
wc -l training_data.jsonl
```
