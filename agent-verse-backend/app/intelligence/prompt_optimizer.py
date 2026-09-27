"""PromptOptimizer — A/B tests prompt variants and auto-promotes the winner.

Architecture:
  - Prompt variants stored per-tenant in instance-level registries (DB-backed in production).
  - Each goal run is tagged with the active variant_id.
  - After ``min_runs_for_promotion`` runs, a statistical test determines the winner.
  - The winning variant is auto-promoted to is_active=True.
  - Losers are archived (not deleted).

Cross-tenant isolation: variants are scoped to a tenant_id so different tenants
cannot see each other's prompt variants (fixes module-level global leakage).
"""

from __future__ import annotations

import contextlib
import math
import random
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# Upper edges (ms) of the per-variant latency histogram; the last is open-ended.
# p95 is read as the upper edge of the bucket holding the 95th percentile, which
# over- rather than under-states latency — the conservative side for a gate.
LATENCY_BUCKETS_MS: tuple[float, ...] = (
    100, 250, 500, 1_000, 2_500, 5_000, 10_000, 20_000, 30_000, 60_000, 120_000,
    300_000, 600_000, float("inf"),
)


def _latency_bucket(latency_ms: float) -> int:
    for i, edge in enumerate(LATENCY_BUCKETS_MS):
        if latency_ms <= edge:
            return i
    return len(LATENCY_BUCKETS_MS) - 1


def _p95_from_hist(hist: list[int]) -> float:
    total = sum(hist)
    if total == 0:
        return 0.0
    threshold = 0.95 * total
    seen = 0
    for i, count in enumerate(hist):
        seen += count
        if seen >= threshold:
            edge = LATENCY_BUCKETS_MS[i]
            # The open-ended bucket has no finite edge; report its lower edge.
            return LATENCY_BUCKETS_MS[i - 1] if edge == float("inf") else edge
    return LATENCY_BUCKETS_MS[-2]


@dataclass(frozen=True, slots=True)
class VariantStats:
    """Aggregate evidence for one variant (from memory or a ``prompt_variants`` row)."""

    variant_id: str
    run_count: int
    score_sum: float
    score_sq_sum: float
    cost_usd_sum: float
    cost_samples: int
    latency_hist: tuple[int, ...]

    @property
    def mean(self) -> float:
        return self.score_sum / self.run_count if self.run_count else 0.0

    @property
    def variance(self) -> float:
        if self.run_count < 2:
            return 0.0
        return max(0.0, (self.score_sq_sum - self.score_sum**2 / self.run_count)
                   / (self.run_count - 1))

    @property
    def mean_cost_usd(self) -> float:
        return self.cost_usd_sum / self.cost_samples if self.cost_samples else 0.0

    @property
    def p95_latency_ms(self) -> float:
        return _p95_from_hist(list(self.latency_hist))

    @classmethod
    def of(cls, v: PromptVariant) -> VariantStats:
        return cls(v.variant_id, v.run_count, v.score_sum, v.score_sq_sum, v.cost_usd_sum,
                   v.cost_samples, tuple(v.latency_hist))


@dataclass(frozen=True, slots=True)
class PromotionVerdict:
    promoted_variant_id: str | None
    # challenger variant_id -> reasons it was held back (empty list = eligible)
    held: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class PromptVariant:
    variant_id: str
    name: str
    prompt_text: str
    prompt_key: str  # e.g. "system_prompt", "planner_prompt"
    is_active: bool = False
    is_control: bool = False
    run_count: int = 0
    eval_scores: list[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    promoted_at: datetime | None = None
    # Running aggregates — what promotion is decided on (identical in memory
    # and in ``prompt_variants``), so no per-run sample list has to be kept.
    score_sum: float = 0.0
    score_sq_sum: float = 0.0
    cost_usd_sum: float = 0.0
    cost_samples: int = 0
    latency_hist: list[int] = field(default_factory=lambda: [0] * len(LATENCY_BUCKETS_MS))


class PromptOptimizer:
    """Manages prompt variant A/B testing and auto-promotion.

    Variants are scoped per tenant_id to prevent cross-tenant prompt leakage.

    Usage::

        optimizer = PromptOptimizer()
        variant = optimizer.register_variant("system_prompt", "Control",
                                             "You are a helpful assistant.",
                                             is_control=True)
        # ... run goal with variant.prompt_text ...
        optimizer.record_result(variant.variant_id, eval_score=0.85)
        optimizer.maybe_promote("system_prompt")
    """

    def __init__(self, min_runs_for_promotion: int = 100, confidence: float = 0.95) -> None:
        self._min_runs = min_runs_for_promotion
        self._confidence = confidence
        # Per-tenant variant registries: tenant_id → {variant_id: PromptVariant}
        self._variants: dict[str, dict[str, PromptVariant]] = {}
        # Per-tenant active variant map: tenant_id → {prompt_key: variant_id}
        self._active: dict[str, dict[str, str]] = {}
        # Pending DB-persist tasks — strong references prevent task GC before completion.
        self._pending_tasks: set = set()

    # ------------------------------------------------------------------
    # Variant management
    # ------------------------------------------------------------------

    def add_variant(self, variant: PromptVariant, tenant_id: str, db: Any = None) -> None:
        """Register a pre-built PromptVariant for the given tenant.

        If *db* is None, the variant is stored in-memory only and WILL BE LOST
        on process restart.  Always pass *db* in production so the variant is
        persisted to the ``prompt_variants`` table.
        """
        if db is None:
            import logging as _log

            _log.getLogger(__name__).warning(
                "prompt_variant_no_db_in_memory_only variant_id=%s tenant_id=%s "
                "will_be_lost_on_restart=True",
                variant.variant_id,
                tenant_id,
            )
        self._variants.setdefault(tenant_id, {})[variant.variant_id] = variant
        if variant.is_control:
            self._active.setdefault(tenant_id, {})[variant.prompt_key] = variant.variant_id
        if db is not None:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self.persist_variant(variant, tenant_id, db))
                # Hold a strong reference so the GC does not collect the task early.
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)
            except RuntimeError:
                pass  # Not in an async context — caller must persist separately.

    def register_variant(
        self,
        prompt_key: str,
        name: str,
        prompt_text: str,
        *,
        tenant_id: str = "global",
        is_control: bool = False,
        db: Any = None,
    ) -> PromptVariant:
        """Register a new prompt variant for A/B testing.

        If *db* is provided (an async session factory), the variant is also
        persisted to ``prompt_variants`` as a fire-and-forget task.
        """
        variant_id = str(uuid.uuid4())
        variant = PromptVariant(
            variant_id=variant_id,
            name=name,
            prompt_text=prompt_text,
            prompt_key=prompt_key,
            is_control=is_control,
            is_active=is_control,  # control starts as active
        )
        self._variants.setdefault(tenant_id, {})[variant_id] = variant
        if is_control:
            self._active.setdefault(tenant_id, {})[prompt_key] = variant_id

        # Persist to DB if available (fire-and-forget)
        if db is not None:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.persist_variant(variant, tenant_id, db))  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
            except RuntimeError:
                pass  # Not in async context — caller can persist separately

        return variant

    # ------------------------------------------------------------------
    # DB / Redis persistence
    # ------------------------------------------------------------------

    def set_redis(self, redis: Any) -> None:
        """Set Redis client for cache invalidation between replicas."""
        self._redis = redis

    async def invalidate_cache(self) -> None:
        """Publish cache invalidation to other replicas."""
        if getattr(self, "_redis", None) is None:
            return
        with contextlib.suppress(Exception):
            await self._redis.publish("prompt_variant_invalidate", "reload")

    async def persist_variant(self, variant: PromptVariant, tenant_id: str, db: Any) -> None:
        """Persist a variant to the prompt_variants table."""
        if db is None:
            return
        try:
            from sqlalchemy import text

            async with db() as session, session.begin():
                await session.execute(
                    text("""
                    INSERT INTO prompt_variants
                        (id, tenant_id, prompt_key, variant_name, prompt_text, is_control,
                         win_count, loss_count, is_active, created_at, updated_at)
                    VALUES
                        (:id, :tid, :key, :name, :text, :ctrl, 0, 0, TRUE, NOW(), NOW())
                    ON CONFLICT (tenant_id, prompt_key, variant_name)
                    DO UPDATE SET
                        prompt_text = EXCLUDED.prompt_text,
                        is_control  = EXCLUDED.is_control,
                        is_active   = TRUE,
                        updated_at  = NOW()
                """),
                    {
                        "id": variant.variant_id,
                        "tid": tenant_id,
                        "key": variant.prompt_key,
                        "name": variant.name,
                        "text": variant.prompt_text,
                        "ctrl": variant.is_control,
                    },
                )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("prompt_variant_persist_failed: %s", exc)

    async def persist_outcome(self, variant_id: str, won: bool, db: Any) -> None:
        """Update win/loss counts in DB after A/B test result."""
        if db is None:
            return
        try:
            from sqlalchemy import text

            col = "win_count" if won else "loss_count"
            async with db() as session, session.begin():
                await session.execute(
                    text(
                        f"UPDATE prompt_variants SET {col} = {col} + 1, updated_at = NOW() WHERE id = :id"  # noqa: E501
                    ),
                    {"id": variant_id},
                )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("prompt_outcome_persist_failed: %s", exc)

    async def load_from_db(self, db: Any) -> int:
        """Load all active variants from DB into in-process cache.

        Call at startup and after Redis invalidation.
        Returns count of variants loaded.
        """
        if db is None:
            return 0
        try:
            from sqlalchemy import text

            async with db() as session:
                rows = (
                    await session.execute(
                        text("""
                    SELECT id, tenant_id, prompt_key, variant_name, prompt_text,
                           is_control, win_count, loss_count
                    FROM prompt_variants
                    WHERE is_active = TRUE
                    ORDER BY tenant_id, prompt_key, is_control DESC
                """)
                    )
                ).fetchall()

            # Clear and rebuild from DB
            self._variants.clear()
            for row in rows:
                vid, tid, key, name, text_val, ctrl, wins, losses = row
                v = PromptVariant(
                    variant_id=vid,
                    prompt_key=key,
                    name=name,
                    prompt_text=text_val or "",
                    is_control=bool(ctrl),
                )
                # Store win/loss on the variant if the dataclass supports it
                if hasattr(v, "win_count"):
                    v.win_count = wins or 0
                if hasattr(v, "loss_count"):
                    v.loss_count = losses or 0
                self._variants.setdefault(tid, {})[vid] = v

            from app.observability.logging import get_logger

            get_logger(__name__).info("prompt_variants_loaded", count=len(rows))
            return len(rows)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("prompt_variants_load_failed: %s", exc)
            return 0

    def select_variant(self, prompt_key: str, *, tenant_id: str = "global") -> PromptVariant | None:
        """Select which prompt variant to use for this request.

        Returns the active (control) variant 70% of the time,
        a random challenger 30% of the time (epsilon-greedy).
        Checks tenant-specific scope first, then falls back to "global".
        """
        for scope in (tenant_id, "global") if tenant_id != "global" else ("global",):
            tenant_variants = self._variants.get(scope, {})
            key_variants = [v for v in tenant_variants.values() if v.prompt_key == prompt_key]
            if not key_variants:
                continue

            control = next((v for v in key_variants if v.is_control), None)
            challengers = [v for v in key_variants if not v.is_control]

            if not challengers or random.random() < 0.70:
                return control or key_variants[0]
            return random.choice(challengers)
        return None

    def get_variant(
        self, variant_id: str, *, tenant_id: str = "global"
    ) -> PromptVariant | None:
        """Return a registered variant by id for a tenant, or None if absent.

        Public accessor used by the improvement-action handlers to apply prompt
        updates idempotently (register only when the deterministic variant id is
        not already present).
        """
        return self._variants.get(tenant_id, {}).get(variant_id)

    def record_result(
        self,
        variant_id: str,
        eval_score: float,
        *,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
    ) -> None:
        """Record an eval score (and, when known, cost/latency) for a variant after a run.

        In-memory path. Searches all tenant scopes since variant_ids are
        globally unique UUIDs. With a database use :meth:`arecord_result`.
        """
        for tenant_variants in self._variants.values():
            variant = tenant_variants.get(variant_id)
            if variant is not None:
                variant.run_count += 1
                variant.eval_scores.append(eval_score)
                variant.score_sum += eval_score
                variant.score_sq_sum += eval_score * eval_score
                if cost_usd is not None:
                    variant.cost_usd_sum += cost_usd
                    variant.cost_samples += 1
                if latency_ms is not None:
                    variant.latency_hist[_latency_bucket(latency_ms)] += 1
                return

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    def _significantly_better(self, control: VariantStats, challenger: VariantStats) -> bool:
        """One-sided Welch z-test on the aggregates: is the challenger's mean higher?"""
        if control.run_count < 10 or challenger.run_count < 10:
            return False
        se = math.sqrt(control.variance / control.run_count
                       + challenger.variance / challenger.run_count)
        diff = challenger.mean - control.mean
        if se == 0.0:
            return diff > 0
        z = diff / se
        p_value = 0.5 * math.erfc(z / math.sqrt(2))
        return p_value < (1.0 - self._confidence)

    def decide_promotion(
        self, control: VariantStats, challengers: list[VariantStats], *, tenant_id: str,
        prompt_key: str,
    ) -> PromotionVerdict:
        """Pick the challenger to promote, if any.

        A challenger must be (1) significantly better on quality AND (2) pass the
        ``RegressionGate`` against the control on cost and p95 latency. Quality
        alone used to decide, so a challenger that scored a little higher while
        tripling cost or latency was promoted automatically.
        """
        from app.evals.regression_baseline import (
            AggregateMetrics,
            BaselineKey,
            RegressionBaseline,
        )
        from app.evals.regression_gate import RegressionGate

        held: dict[str, list[str]] = {}
        if control.run_count < self._min_runs:
            return PromotionVerdict(None, {c.variant_id: ["control_insufficient_samples"]
                                           for c in challengers})
        key = BaselineKey(tenant_id, f"prompt:{prompt_key}", "prompt_variant", "1", 1, "v1",
                          "v1", "v1")

        def _metrics(v: VariantStats) -> AggregateMetrics:
            return AggregateMetrics(
                quality=v.mean, safety=1.0, mean_cost_usd=v.mean_cost_usd,
                p95_latency_ms=v.p95_latency_ms, coverage=1.0, sample_size=v.run_count,
                policy_passed=True, tenant_isolation_passed=True,
            )

        gate = RegressionGate(minimum_samples=self._min_runs)
        baseline = RegressionBaseline(key=key, revision=1, metrics=_metrics(control))
        best: VariantStats | None = None
        for ch in challengers:
            reasons: list[str] = []
            if ch.run_count < self._min_runs:
                reasons.append("insufficient_samples")
            elif not self._significantly_better(control, ch):
                reasons.append("not_significantly_better")
            decision = gate.evaluate_promotion(
                baseline=baseline, candidate_key=key, candidate=_metrics(ch)
            )
            reasons.extend(r for r in decision.reasons if r not in reasons)
            held[ch.variant_id] = reasons
            if not reasons and (best is None or ch.mean > best.mean):
                best = ch
        return PromotionVerdict(best.variant_id if best else None, held)

    def maybe_promote(self, prompt_key: str, *, tenant_id: str = "global") -> PromptVariant | None:
        """Check if a challenger variant should be promoted (in-memory path).

        Returns the newly promoted variant if promotion occurred, else None.
        """
        tenant_variants = self._variants.get(tenant_id, {})
        key_variants = [v for v in tenant_variants.values() if v.prompt_key == prompt_key]
        control = next((v for v in key_variants if v.is_control), None)
        challengers = [v for v in key_variants if not v.is_control]
        if not control or not challengers:
            return None
        verdict = self.decide_promotion(
            VariantStats.of(control), [VariantStats.of(c) for c in challengers],
            tenant_id=tenant_id, prompt_key=prompt_key,
        )
        if verdict.promoted_variant_id is None:
            return None
        winner = tenant_variants[verdict.promoted_variant_id]
        # Promote: challenger becomes control, old control is archived
        control.is_control = False
        control.is_active = False
        winner.is_control = True
        winner.is_active = True
        winner.promoted_at = datetime.now(UTC)
        self._active.setdefault(tenant_id, {})[prompt_key] = winner.variant_id
        return winner

    def _is_significant(self, control_scores: list[float], challenger_scores: list[float]) -> bool:
        """Simple significance test — True if challenger is statistically better."""
        if len(control_scores) < 10 or len(challenger_scores) < 10:
            return False
        try:
            from scipy import stats  # type: ignore[import]

            _u, p_value = stats.mannwhitneyu(
                challenger_scores, control_scores, alternative="greater"
            )
            return float(p_value) < (1.0 - self._confidence)
        except ImportError:
            # Fall back to simple mean comparison if scipy not available
            return statistics.mean(challenger_scores) > statistics.mean(control_scores) * 1.05

    # ------------------------------------------------------------------
    # Database mode — Postgres is the source of truth
    # ------------------------------------------------------------------
    #
    # With ``set_db`` the optimizer reads and writes ``prompt_variants`` per
    # tenant, under RLS, instead of a process dict hydrated at startup with
    # every tenant's variants. That dict diverged across replicas (a variant
    # registered or promoted on one was unknown to the others until restart),
    # grew with the whole fleet, and — once RLS is enforced — would have loaded
    # only the "global" rows. Tenants may READ "global" variants; they may only
    # write, promote, delete and record outcomes on their own.

    _SELECT_TTL_S = 15.0
    _SELECT_CACHE_MAX = 10_000

    def set_db(self, session_factory: Any) -> None:
        self._db = session_factory
        self._select_cache: dict[tuple[str, str], tuple[float, list[PromptVariant]]] = {}

    @property
    def db_mode(self) -> bool:
        return getattr(self, "_db", None) is not None

    @contextlib.asynccontextmanager
    async def _tx(self, tenant_id: str) -> Any:
        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            yield session

    _COLUMNS = (
        "id, tenant_id, prompt_key, variant_name, prompt_text, is_control, is_active, "
        "run_count, score_sum, score_sq_sum, cost_usd_sum, cost_samples, latency_hist, "
        "promoted_at, created_at"
    )

    @staticmethod
    def _from_row(row: Any) -> tuple[str, PromptVariant]:
        hist = list(row["latency_hist"] or [])
        hist += [0] * (len(LATENCY_BUCKETS_MS) - len(hist))
        v = PromptVariant(
            variant_id=str(row["id"]), name=row["variant_name"],
            prompt_text=row["prompt_text"] or "", prompt_key=row["prompt_key"],
            is_active=bool(row["is_active"]), is_control=bool(row["is_control"]),
            run_count=int(row["run_count"] or 0), promoted_at=row["promoted_at"],
            created_at=row["created_at"] or datetime.now(UTC),
            score_sum=float(row["score_sum"] or 0), score_sq_sum=float(row["score_sq_sum"] or 0),
            cost_usd_sum=float(row["cost_usd_sum"] or 0),
            cost_samples=int(row["cost_samples"] or 0), latency_hist=hist,
        )
        return str(row["tenant_id"]), v

    async def alist(self, tenant_id: str, prompt_key: str = "") -> list[PromptVariant]:
        """The tenant's active variants; the "global" ones when it has none."""
        from sqlalchemy import text

        where = "is_active AND tenant_id = :scope"
        if prompt_key:
            where += " AND prompt_key = :key"
        for scope in (tenant_id, "global"):
            async with self._tx(tenant_id) as session:
                rows = (await session.execute(
                    text(f"SELECT {self._COLUMNS} FROM prompt_variants WHERE {where} "
                         "ORDER BY prompt_key, is_control DESC, created_at LIMIT 500"),
                    {"scope": scope, "key": prompt_key},
                )).mappings().all()
            if rows:
                return [self._from_row(r)[1] for r in rows]
        return []

    async def aget(self, variant_id: str, tenant_id: str) -> tuple[str, PromptVariant] | None:
        """``(owner_scope, variant)`` for a variant the tenant may read, else None."""
        from sqlalchemy import text

        async with self._tx(tenant_id) as session:
            row = (await session.execute(
                text(f"SELECT {self._COLUMNS} FROM prompt_variants "
                     "WHERE id = :id AND tenant_id IN (:tid, 'global')"),
                {"id": variant_id, "tid": tenant_id},
            )).mappings().first()
        return self._from_row(row) if row is not None else None

    async def aregister(
        self, prompt_key: str, name: str, prompt_text: str, *, tenant_id: str,
        is_control: bool = False,
    ) -> PromptVariant | None:
        """Insert a variant for the tenant; None if it already has one of that name."""
        from sqlalchemy import text

        variant = PromptVariant(
            variant_id=str(uuid.uuid4()), name=name, prompt_text=prompt_text,
            prompt_key=prompt_key, is_control=is_control, is_active=True,
        )
        async with self._tx(tenant_id) as session:
            inserted = (await session.execute(
                text("INSERT INTO prompt_variants (id, tenant_id, prompt_key, variant_name, "
                     " prompt_text, is_control, is_active) "
                     "VALUES (:id, :tid, :key, :name, :text, :ctrl, TRUE) "
                     "ON CONFLICT (tenant_id, prompt_key, variant_name) DO NOTHING RETURNING id"),
                {"id": variant.variant_id, "tid": tenant_id, "key": prompt_key, "name": name,
                 "text": prompt_text, "ctrl": is_control},
            )).first()
        self._forget(tenant_id, prompt_key)
        return variant if inserted is not None else None

    async def apromote(self, variant_id: str, tenant_id: str) -> PromptVariant | None:
        """Make one of the TENANT'S OWN variants the control for its key."""
        from sqlalchemy import text

        async with self._tx(tenant_id) as session:
            row = (await session.execute(
                text(f"SELECT {self._COLUMNS} FROM prompt_variants "
                     "WHERE id = :id AND tenant_id = :tid FOR UPDATE"),
                {"id": variant_id, "tid": tenant_id},
            )).mappings().first()
            if row is None:
                return None
            _, variant = self._from_row(row)
            await self._swap_control(session, tenant_id, variant.prompt_key, variant_id)
        self._forget(tenant_id, variant.prompt_key)
        variant.is_control = variant.is_active = True
        variant.promoted_at = datetime.now(UTC)
        return variant

    @staticmethod
    async def _swap_control(session: Any, tenant_id: str, prompt_key: str, winner: str) -> None:
        from sqlalchemy import text

        # The previous control is archived (inactive), never deleted.
        await session.execute(
            text("UPDATE prompt_variants SET "
                 " is_active = CASE WHEN id = :win THEN TRUE ELSE is_active AND NOT is_control END,"
                 " promoted_at = CASE WHEN id = :win THEN NOW() ELSE promoted_at END, "
                 " is_control = (id = :win), updated_at = NOW() "
                 "WHERE tenant_id = :tid AND prompt_key = :key AND (is_control OR id = :win)"),
            {"win": winner, "tid": tenant_id, "key": prompt_key},
        )

    async def adelete(self, variant_id: str, tenant_id: str) -> bool:
        from sqlalchemy import text

        async with self._tx(tenant_id) as session:
            row = (await session.execute(
                text("DELETE FROM prompt_variants WHERE id = :id AND tenant_id = :tid "
                     "RETURNING prompt_key"),
                {"id": variant_id, "tid": tenant_id},
            )).first()
        if row is None:
            return False
        self._forget(tenant_id, row[0])
        return True

    def _forget(self, tenant_id: str, prompt_key: str) -> None:
        cache = getattr(self, "_select_cache", None)
        if cache is not None:
            cache.pop((tenant_id, prompt_key), None)

    async def aselect_variant(self, prompt_key: str, *, tenant_id: str) -> PromptVariant | None:
        """DB-mode :meth:`select_variant` (control 70% / random challenger 30%).

        Called once per plan/execute step, so candidates are cached per
        ``(tenant, key)`` for a few seconds — bounded staleness across replicas.
        """
        import time as _time

        cache = self._select_cache
        hit = cache.get((tenant_id, prompt_key))
        now = _time.monotonic()
        if hit is not None and now - hit[0] < self._SELECT_TTL_S:
            variants = hit[1]
        else:
            variants = await self.alist(tenant_id, prompt_key)
            if len(cache) >= self._SELECT_CACHE_MAX:
                cache.clear()
            cache[(tenant_id, prompt_key)] = (now, variants)
        if not variants:
            return None
        control = next((v for v in variants if v.is_control), None)
        challengers = [v for v in variants if not v.is_control]
        if not challengers or random.random() < 0.70:
            return control or variants[0]
        return random.choice(challengers)

    async def arecord_result(
        self,
        variant_id: str,
        *,
        tenant_id: str,
        eval_score: float,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
    ) -> PromotionVerdict | None:
        """Atomically add one run's outcome to the tenant's variant, then re-decide.

        Only the tenant's own variants accumulate evidence: a run that used a
        shared "global" variant must not move statistics every tenant relies on.
        Returns the promotion verdict for the variant's key (None if not owned).
        """
        from sqlalchemy import text

        bucket = _latency_bucket(latency_ms) + 1 if latency_ms is not None else None
        async with self._tx(tenant_id) as session:
            row = (await session.execute(
                text("UPDATE prompt_variants SET run_count = run_count + 1, "
                     " score_sum = score_sum + CAST(:s AS double precision), "
                     " score_sq_sum = score_sq_sum + CAST(:s AS double precision) "
                     "   * CAST(:s AS double precision), "
                     " cost_usd_sum = cost_usd_sum + COALESCE(CAST(:c AS double precision), 0), "
                     " cost_samples = cost_samples "
                     "   + CASE WHEN CAST(:c AS double precision) IS NULL THEN 0 ELSE 1 END, "
                     " latency_hist = CASE WHEN CAST(:b AS int) IS NULL THEN latency_hist "
                     "   ELSE latency_hist[1:CAST(:b AS int) - 1] "
                     "     || (latency_hist[CAST(:b AS int)] + 1) "
                     "     || latency_hist[CAST(:b AS int) + 1:] END, "
                     " win_count = win_count "
                     "   + CASE WHEN CAST(:s AS double precision) >= 0.7 THEN 1 ELSE 0 END, "
                     " loss_count = loss_count "
                     "   + CASE WHEN CAST(:s AS double precision) >= 0.7 THEN 0 ELSE 1 END, "
                     " updated_at = NOW() "
                     "WHERE id = :id AND tenant_id = :tid AND is_active RETURNING prompt_key"),
                {"s": float(eval_score), "c": cost_usd, "b": bucket, "id": variant_id,
                 "tid": tenant_id},
            )).first()
        if row is None:
            return None
        return await self.amaybe_promote(row[0], tenant_id=tenant_id)

    async def amaybe_promote(self, prompt_key: str, *, tenant_id: str) -> PromotionVerdict:
        """Decide (and, if warranted, apply) a promotion for the tenant's key.

        Rows are locked FOR UPDATE, so concurrent outcomes on several replicas
        cannot promote two different winners.
        """
        from sqlalchemy import text

        async with self._tx(tenant_id) as session:
            rows = (await session.execute(
                text(f"SELECT {self._COLUMNS} FROM prompt_variants "
                     "WHERE tenant_id = :tid AND prompt_key = :key AND is_active "
                     "ORDER BY id FOR UPDATE"),
                {"tid": tenant_id, "key": prompt_key},
            )).mappings().all()
            variants = [self._from_row(r)[1] for r in rows]
            control = next((v for v in variants if v.is_control), None)
            challengers = [v for v in variants if not v.is_control]
            if control is None or not challengers:
                return PromotionVerdict(None)
            verdict = self.decide_promotion(
                VariantStats.of(control), [VariantStats.of(c) for c in challengers],
                tenant_id=tenant_id, prompt_key=prompt_key,
            )
            if verdict.promoted_variant_id is not None:
                await self._swap_control(session, tenant_id, prompt_key,
                                         verdict.promoted_variant_id)
        if verdict.promoted_variant_id is not None:
            self._forget(tenant_id, prompt_key)
            from app.observability.logging import get_logger

            get_logger(__name__).info(
                "prompt_variant_auto_promoted", tenant_id=tenant_id, prompt_key=prompt_key,
                variant_id=verdict.promoted_variant_id,
            )
        return verdict

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_report(self, prompt_key: str, *, tenant_id: str = "global") -> dict[str, Any]:
        """Return a summary report for all variants of a prompt key."""
        tenant_variants = self._variants.get(tenant_id, {})
        key_variants = [v for v in tenant_variants.values() if v.prompt_key == prompt_key]
        return {
            "prompt_key": prompt_key,
            "variants": [
                {
                    "variant_id": v.variant_id,
                    "name": v.name,
                    "is_control": v.is_control,
                    "run_count": v.run_count,
                    "mean_score": (
                        round(statistics.mean(v.eval_scores), 4) if v.eval_scores else None
                    ),
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

    def list_all_keys(self, *, tenant_id: str = "global") -> list[str]:
        tenant_variants = self._variants.get(tenant_id, {})
        return list({v.prompt_key for v in tenant_variants.values()})

    def get_active_variant(self, variant_set_id: str) -> str | None:
        """Get the currently active prompt variant ID for A/B testing via PromptVariantSelector.

        Uses PromptVariantSelector for deterministic hash-based selection across
        all registered variants for this prompt key.
        """
        try:
            from app.context.prompt_variant_selector import PromptVariantSelector

            # Collect all variant IDs registered under this key (across all tenants)
            pool: list[str] = []
            for tenant_variants in self._variants.values():
                for v in tenant_variants.values():
                    if v.prompt_key == variant_set_id and v.variant_id not in pool:
                        pool.append(v.variant_id)
            if not pool:
                return None
            selector = PromptVariantSelector()
            selected = selector.select(goal_id=variant_set_id, variant_pool=pool)
            return selected.variant_id
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Module-level backward-compat aliases (point to the "global" scope of a
# default instance so existing code that imports _VARIANTS / _ACTIVE_VARIANTS
# directly still works).
# ---------------------------------------------------------------------------
_default_optimizer = PromptOptimizer()
_VARIANTS: dict[str, PromptVariant] = _default_optimizer._variants.setdefault("global", {})
_ACTIVE_VARIANTS: dict[str, str] = _default_optimizer._active.setdefault("global", {})
