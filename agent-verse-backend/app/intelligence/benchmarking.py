"""Agent performance benchmarking — aggregate eval trends across runs."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext


@dataclass
class BenchmarkRun:
    """A single benchmark run record."""
    suite_name: str
    score: float
    tenant_id: str = "global"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


@dataclass
class AgentBenchmark:
    agent_id: str
    tenant_id: str
    run_count: int = 0
    avg_scores: dict[str, float] = field(default_factory=dict)
    trend: str = "stable"  # "improving" | "declining" | "stable"
    last_run_at: str = ""
    best_score: float = 0.0
    worst_score: float = 1.0
    scorecards: list[EvalScorecard] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "run_count": self.run_count,
            "avg_scores": self.avg_scores,
            "trend": self.trend,
            "last_run_at": self.last_run_at,
            "best_score": self.best_score,
            "worst_score": self.worst_score,
        }


class BenchmarkStore:
    """Benchmark store with DB persistence and in-memory cache."""

    def __init__(self, db_session_factory: Any = None) -> None:
        self._db = db_session_factory
        # {tenant_id: {agent_id: AgentBenchmark}}
        self._benchmarks: dict[str, dict[str, AgentBenchmark]] = defaultdict(dict)
        # In-memory cache for BenchmarkRun records
        self._runs: dict[str, list[BenchmarkRun]] = {}

    def record_eval(
        self,
        *,
        agent_id: str,
        scorecard: EvalScorecard,
        tenant_ctx: TenantContext,
    ) -> AgentBenchmark:
        """Record an eval scorecard and update the agent's benchmark."""
        tid = tenant_ctx.tenant_id
        bench = self._benchmarks[tid].setdefault(
            agent_id,
            AgentBenchmark(agent_id=agent_id, tenant_id=tid),
        )

        bench.scorecards.append(scorecard)
        bench.run_count += 1
        bench.last_run_at = datetime.now(UTC).isoformat()

        # Compute avg scores across all runs
        all_scores: dict[str, list[float]] = defaultdict(list)
        for sc in bench.scorecards:
            for dim, val in sc.scores.items():
                all_scores[dim].append(val)
        bench.avg_scores = {
            dim: round(sum(vals) / len(vals), 3) for dim, vals in all_scores.items()
        }

        # Compute overall best/worst
        overall = [sc.average_score() for sc in bench.scorecards]
        bench.best_score = round(max(overall), 3)
        bench.worst_score = round(min(overall), 3)

        # Trend: compare last 3 runs vs previous 3 runs
        if len(overall) >= 6:
            recent_avg = sum(overall[-3:]) / 3
            previous_avg = sum(overall[-6:-3]) / 3
            if recent_avg > previous_avg + 0.05:
                bench.trend = "improving"
            elif recent_avg < previous_avg - 0.05:
                bench.trend = "declining"
            else:
                bench.trend = "stable"

        return bench

    def get_benchmark(
        self, *, agent_id: str, tenant_ctx: TenantContext
    ) -> AgentBenchmark | None:
        return self._benchmarks.get(tenant_ctx.tenant_id, {}).get(agent_id)

    def list_benchmarks(self, *, tenant_ctx: TenantContext) -> list[AgentBenchmark]:
        return list(self._benchmarks.get(tenant_ctx.tenant_id, {}).values())

    def compare_agents(
        self, *, agent_ids: list[str], tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        """Return comparison dict for multiple agents, sorted by avg score."""
        results = []
        for aid in agent_ids:
            bench = self.get_benchmark(agent_id=aid, tenant_ctx=tenant_ctx)
            if bench:
                overall = sum(bench.avg_scores.values()) / max(len(bench.avg_scores), 1)
                results.append({**bench.to_dict(), "overall_avg": round(overall, 3)})
        return sorted(results, key=lambda x: x.get("overall_avg", 0), reverse=True)

    async def record_run_async(self, run: BenchmarkRun) -> None:
        """Persist benchmark run to DB and update in-memory cache."""
        if self._db is not None:
            try:
                import json
                import uuid
                from sqlalchemy import text
                async with self._db() as session, session.begin():
                    await session.execute(text("""
                        INSERT INTO benchmark_runs
                            (id, tenant_id, suite_name, score, metadata, created_at)
                        VALUES (:id, :tid, :suite, :score, :meta::jsonb, NOW())
                    """), {
                        "id": uuid.uuid4().hex,
                        "tid": getattr(run, "tenant_id", "global"),
                        "suite": run.suite_name,
                        "score": run.score,
                        "meta": json.dumps(getattr(run, "metadata", {})),
                    })
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("benchmark_persist_failed: %s", exc)

        # Also keep in-memory cache
        self._runs.setdefault(run.suite_name, []).append(run)

    async def load_history_from_db(self, suite_name: str, limit: int = 100) -> list[dict]:
        """Load historical benchmark runs from DB."""
        if self._db is None:
            return []
        try:
            from sqlalchemy import text
            async with self._db() as session:
                rows = (await session.execute(text("""
                    SELECT id, suite_name, score, metadata, created_at
                    FROM benchmark_runs
                    WHERE suite_name = :suite
                    ORDER BY created_at DESC LIMIT :lim
                """), {"suite": suite_name, "lim": limit})).fetchall()
            return [
                {"id": r[0], "suite_name": r[1], "score": float(r[2]),
                 "metadata": r[3] or {}, "created_at": r[4].isoformat() if r[4] else ""}
                for r in rows
            ]
        except Exception:
            return []
