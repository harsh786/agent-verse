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

import random
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


# ---------------------------------------------------------------------------
# Data classes
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

    # ------------------------------------------------------------------
    # Variant management
    # ------------------------------------------------------------------

    def register_variant(
        self,
        prompt_key: str,
        name: str,
        prompt_text: str,
        is_control: bool = False,
        *,
        tenant_id: str = "global",
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
        self._variants.setdefault(tenant_id, {})[variant_id] = variant
        if is_control:
            self._active.setdefault(tenant_id, {})[prompt_key] = variant_id
        return variant

    def select_variant(
        self, prompt_key: str, *, tenant_id: str = "global"
    ) -> PromptVariant | None:
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

    def record_result(self, variant_id: str, eval_score: float) -> None:
        """Record an eval score for a variant after a goal run.

        Searches all tenant scopes since variant_ids are globally unique UUIDs.
        """
        for tenant_variants in self._variants.values():
            variant = tenant_variants.get(variant_id)
            if variant is not None:
                variant.run_count += 1
                variant.eval_scores.append(eval_score)
                return

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    def maybe_promote(
        self, prompt_key: str, *, tenant_id: str = "global"
    ) -> PromptVariant | None:
        """Check if a challenger variant should be promoted.

        Returns the newly promoted variant if promotion occurred, else None.
        """
        tenant_variants = self._variants.get(tenant_id, {})
        key_variants = [v for v in tenant_variants.values() if v.prompt_key == prompt_key]
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
            ch_score = (
                statistics.mean(challenger.eval_scores) if challenger.eval_scores else 0.0
            )
            if ch_score > best_score and self._is_significant(
                control.eval_scores, challenger.eval_scores
            ):
                best_score = ch_score
                best_challenger = challenger

        if best_challenger is None:
            return None

        # Promote: challenger becomes control, old control is archived
        control.is_control = False
        control.is_active = False
        best_challenger.is_control = True
        best_challenger.is_active = True
        best_challenger.promoted_at = datetime.now(UTC)
        self._active.setdefault(tenant_id, {})[prompt_key] = best_challenger.variant_id
        return best_challenger

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
    # Reporting
    # ------------------------------------------------------------------

    def get_report(
        self, prompt_key: str, *, tenant_id: str = "global"
    ) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Module-level backward-compat aliases (point to the "global" scope of a
# default instance so existing code that imports _VARIANTS / _ACTIVE_VARIANTS
# directly still works).
# ---------------------------------------------------------------------------
_default_optimizer = PromptOptimizer()
_VARIANTS: dict[str, PromptVariant] = _default_optimizer._variants.setdefault("global", {})
_ACTIVE_VARIANTS: dict[str, str] = _default_optimizer._active.setdefault("global", {})
