"""Model-capability-aware execution strategy.

The execution strategy is chosen *per model* from that model's capability
profile — no single strategy is applied to every model. A model that reliably
emits dependency-graph plans gets structured/parallel-wave execution; a weak
model gets the safe sequential strategy; a fast cheap model is routed to
latency-sensitive roles. Every strategy carries an always-safe fallback
(``SEQUENTIAL`` + ``SINGLE``), and the profile can be refined at runtime by the
adaptivity tracker (see :mod:`app.agent.strategy_adaptivity`).

This module is deliberately pure and dependency-free so it is trivially
testable: ``resolve()`` maps per-role capability profiles to an
:class:`ExecutionStrategy`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class PlanMode(StrEnum):
    STRUCTURED = "structured"  # Strategy A — dependency graph -> parallel waves
    SEQUENTIAL = "sequential"  # safe default — one step after another


class ToolMode(StrEnum):
    PARALLEL = "parallel"  # Strategy B — all tool calls in a turn run concurrently
    SINGLE = "single"  # safe default — one tool call per turn


class LatencyTier(StrEnum):
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


class JsonReliability(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ModelCapabilityProfile:
    """What a model *reliably* does — the input to the strategy resolver."""

    model_id: str
    structured_planning: bool = False
    parallel_tool_calls: bool = False
    json_reliability: str = JsonReliability.LOW.value
    latency_tier: str = LatencyTier.MEDIUM.value
    strict_schema_enforced: bool = False


@dataclass(frozen=True)
class ExecutionStrategy:
    """The resolved, per-goal execution strategy. Always has a safe fallback."""

    plan_mode: PlanMode = PlanMode.SEQUENTIAL
    tool_mode: ToolMode = ToolMode.SINGLE
    wave_width_cap: int = 5
    # Strategy C — role -> model routing. Empty string means "keep the role's
    # default model" (no override).
    planner_model: str = ""
    executor_model: str = ""
    verifier_model: str = ""
    reason: str = ""

    @classmethod
    def safe_default(cls, reason: str = "unknown model — safe default") -> ExecutionStrategy:
        return cls(reason=reason)


# ── Static seed registry ─────────────────────────────────────────────────────
# Keyed by a lowercase substring matched against the model id. First match wins,
# so order from most-specific to least. Unknown models fall back to a
# conservative SEQUENTIAL/SINGLE profile — never a risky default.
_STATIC_PROFILES: list[tuple[str, ModelCapabilityProfile]] = [
    # Weak/self-hosted: emits malformed structured JSON, no reliable parallel
    # tool calls, slow — verified live against the NVIDIA endpoint.
    (
        "gpt-oss",
        ModelCapabilityProfile(
            model_id="gpt-oss",
            structured_planning=False,
            parallel_tool_calls=False,
            json_reliability=JsonReliability.MEDIUM.value,
            latency_tier=LatencyTier.SLOW.value,
        ),
    ),
    # Frontier instruct families — reliable structured output + parallel tools.
    (
        "claude-opus",
        ModelCapabilityProfile(
            model_id="claude-opus",
            structured_planning=True,
            parallel_tool_calls=True,
            json_reliability=JsonReliability.HIGH.value,
            latency_tier=LatencyTier.MEDIUM.value,
            strict_schema_enforced=True,
        ),
    ),
    (
        "claude-sonnet",
        ModelCapabilityProfile(
            model_id="claude-sonnet",
            structured_planning=True,
            parallel_tool_calls=True,
            json_reliability=JsonReliability.HIGH.value,
            latency_tier=LatencyTier.FAST.value,
            strict_schema_enforced=True,
        ),
    ),
    (
        "claude-haiku",
        ModelCapabilityProfile(
            model_id="claude-haiku",
            structured_planning=False,
            parallel_tool_calls=True,
            json_reliability=JsonReliability.MEDIUM.value,
            latency_tier=LatencyTier.FAST.value,
        ),
    ),
    (
        "gpt-5",
        ModelCapabilityProfile(
            model_id="gpt-5",
            structured_planning=True,
            parallel_tool_calls=True,
            json_reliability=JsonReliability.HIGH.value,
            latency_tier=LatencyTier.MEDIUM.value,
            strict_schema_enforced=True,
        ),
    ),
    (
        "gpt-4o-mini",
        ModelCapabilityProfile(
            model_id="gpt-4o-mini",
            structured_planning=True,
            parallel_tool_calls=True,
            json_reliability=JsonReliability.HIGH.value,
            latency_tier=LatencyTier.FAST.value,
            strict_schema_enforced=True,
        ),
    ),
    (
        "gpt-4",
        ModelCapabilityProfile(
            model_id="gpt-4",
            structured_planning=True,
            parallel_tool_calls=True,
            json_reliability=JsonReliability.HIGH.value,
            latency_tier=LatencyTier.MEDIUM.value,
            strict_schema_enforced=True,
        ),
    ),
]


_TIER_RANK = {LatencyTier.FAST.value: 0, LatencyTier.MEDIUM.value: 1, LatencyTier.SLOW.value: 2}


def latency_tier_for_ms(avg_latency_ms: float) -> str:
    """Derive a latency tier from an observed average latency."""
    if avg_latency_ms <= 0:
        return LatencyTier.MEDIUM.value
    if avg_latency_ms < 2000:
        return LatencyTier.FAST.value
    if avg_latency_ms < 8000:
        return LatencyTier.MEDIUM.value
    return LatencyTier.SLOW.value


def is_seeded(model_id: str | None) -> bool:
    """True when the model matches a family in the static seed registry.

    Used by the adaptivity layer to distinguish a model we have a prior for
    (explore rarely) from a genuinely unknown model (explore to learn it).
    """
    mid = (model_id or "").lower().strip()
    return bool(mid) and any(needle in mid for needle, _ in _STATIC_PROFILES)


def profile_for(model_id: str | None) -> ModelCapabilityProfile:
    """Return the static capability profile for a model id.

    Matching is a lowercase substring against the seed registry (first match
    wins). Unknown or empty models get the conservative default profile so the
    resolver produces the safe SEQUENTIAL/SINGLE strategy.
    """
    mid = (model_id or "").lower().strip()
    if mid:
        for needle, profile in _STATIC_PROFILES:
            if needle in mid:
                return replace(profile, model_id=model_id or profile.model_id)
    return ModelCapabilityProfile(model_id=model_id or "unknown")


def resolve(
    *,
    planner: ModelCapabilityProfile,
    executor: ModelCapabilityProfile,
    verifier: ModelCapabilityProfile | None = None,
    fast_model_id: str | None = None,
) -> ExecutionStrategy:
    """Map per-role capability profiles to an :class:`ExecutionStrategy`.

    Rules (each independently defaults to the safe option):

    * **plan_mode = STRUCTURED** iff the planner ``structured_planning`` *and*
      (``strict_schema_enforced`` or ``json_reliability == "high"``). This is the
      gate for Strategy A (dependency-graph -> parallel waves).
    * **tool_mode = PARALLEL** iff the executor ``parallel_tool_calls`` (Strategy B).
    * **wave_width_cap** is smaller when the planner's JSON is not high-reliability,
      to bound the blast radius of a bad dependency graph.
    * **verifier_model** is routed to ``fast_model_id`` when a fast model exists and
      the verifier's own model is not already fast (Strategy C). Quality roles
      (planner/executor) keep their configured models.
    """
    plan_mode = PlanMode.SEQUENTIAL
    reasons: list[str] = []
    if planner.structured_planning and (
        planner.strict_schema_enforced or planner.json_reliability == JsonReliability.HIGH.value
    ):
        plan_mode = PlanMode.STRUCTURED
        reasons.append(f"A:structured(planner={planner.model_id})")
    else:
        reasons.append(f"A:sequential(planner={planner.model_id})")

    tool_mode = ToolMode.PARALLEL if executor.parallel_tool_calls else ToolMode.SINGLE
    reasons.append(f"B:{tool_mode.value}(executor={executor.model_id})")

    wave_width_cap = 5 if planner.json_reliability == JsonReliability.HIGH.value else 3

    # Strategy C — route verification (latency-sensitive, quality-tolerant) to the
    # fastest available role model. Derived automatically from the role profiles'
    # latency tiers (learned from observation or seeded), so no manual fast-model
    # flag is needed. An explicit fast_model_id hint still wins when provided.
    v_profile = verifier or executor
    verifier_model = ""
    _candidates = [p for p in (planner, executor, verifier) if p is not None]
    _fastest = min(
        _candidates, key=lambda p: _TIER_RANK.get(p.latency_tier, 1), default=None
    )
    if fast_model_id and fast_model_id != v_profile.model_id:
        verifier_model = fast_model_id
        reasons.append(f"C:verifier->{fast_model_id}")
    elif (
        _fastest is not None
        and _TIER_RANK.get(_fastest.latency_tier, 1) < _TIER_RANK.get(v_profile.latency_tier, 1)
        and _fastest.model_id != v_profile.model_id
    ):
        verifier_model = _fastest.model_id
        reasons.append(f"C:verifier->{_fastest.model_id}(fastest)")

    return ExecutionStrategy(
        plan_mode=plan_mode,
        tool_mode=tool_mode,
        wave_width_cap=wave_width_cap,
        verifier_model=verifier_model,
        reason="; ".join(reasons),
    )
