"""The single deterministic chokepoint every autonomous action passes.

Ordered checks; the first failure downgrades to propose or block. Fail-closed:
missing counters or an over-budget/high-risk action never auto-executes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.org.autonomy import AutonomyEnforcer
from app.org.brain_settings import AutonomySettings
from app.org.brain_types import BrainDecision
from app.org.loop_detector import OrgLoopDetector

_MIN_AUTONOMY_LEVEL = 3


@dataclass(frozen=True)
class TickCounters:
    day_spend_usd: float
    missions_today: int
    active_autonomous: int
    seconds_since_last_launch: float
    recent_signatures: frozenset[str]
    counters_available: bool


@dataclass(frozen=True)
class Verdict:
    action: str  # "execute" | "propose" | "block"
    reason: str


@dataclass(frozen=True)
class GuardrailCheck:
    """One row of the SENSE→DECIDE→GUARD→ACT trace for the Brain Feed UI.

    ``value``/``limit`` carry the real numbers behind the check (e.g. daily
    cap "2" vs "8", cost "$4.80" vs "$5.00") where a check has a meaningful
    number to show; ``None`` otherwise.
    """

    name: str
    passed: bool
    detail: str
    value: str | None = None
    limit: str | None = None


def _global_kill_switch() -> bool:
    # Environment-sensitive kill switch: ops can disable org autonomy globally.
    # This module intentionally reads AV_ORG_AUTONOMY_DISABLED env var for safety.
    return os.getenv("AV_ORG_AUTONOMY_DISABLED", "").lower() in {"1", "true", "yes"}


def evaluate_guardrails(
    decision: BrainDecision,
    *,
    autonomy_level: int,
    settings: AutonomySettings,
    counters: TickCounters,
    kill_switch: bool,
    enforcer: AutonomyEnforcer,  # Retained for forward risk policy (Task 6 external-send gating).
    loop_detector: OrgLoopDetector,
) -> tuple[Verdict, list[GuardrailCheck]]:
    """Run the 8 ordered checks, returning both the final verdict and the
    full per-check trace (each check that passed before the deciding one,
    plus the deciding check itself) for the Brain Feed UI.

    Semantics are byte-for-byte identical to the pre-trace implementation —
    same early-return order, same action/reason strings — this only adds the
    ``checks`` accumulator alongside it.
    """
    checks: list[GuardrailCheck] = []

    # 1. Kill switch (per-org paused or global env)
    if kill_switch or settings.paused or _global_kill_switch():
        reason = "autonomy paused (kill switch)"
        checks.append(GuardrailCheck("kill_switch", False, reason))
        return Verdict("block", reason), checks
    checks.append(GuardrailCheck("kill_switch", True, "kill switch clear"))

    # 2. Autonomy level — origination requires L3+
    if autonomy_level < _MIN_AUTONOMY_LEVEL:
        reason = f"autonomy level {autonomy_level} below L3"
        checks.append(
            GuardrailCheck(
                "autonomy_level", False, reason,
                value=str(autonomy_level), limit=str(_MIN_AUTONOMY_LEVEL),
            )
        )
        return Verdict("block", reason), checks
    checks.append(
        GuardrailCheck(
            "autonomy_level", True, "autonomy level sufficient",
            value=str(autonomy_level), limit=str(_MIN_AUTONOMY_LEVEL),
        )
    )

    # 3. Cooldown
    if counters.seconds_since_last_launch < settings.min_interval_seconds:
        reason = "cooldown between autonomous launches not elapsed"
        checks.append(
            GuardrailCheck(
                "cooldown", False, reason,
                value=f"{counters.seconds_since_last_launch:.0f}s",
                limit=f"{settings.min_interval_seconds}s",
            )
        )
        return Verdict("block", reason), checks
    checks.append(
        GuardrailCheck(
            "cooldown", True, "cooldown elapsed",
            value=f"{counters.seconds_since_last_launch:.0f}s",
            limit=f"{settings.min_interval_seconds}s",
        )
    )

    # 4. Daily launch cap
    if counters.missions_today >= settings.max_missions_per_day:
        reason = "daily autonomous mission cap reached"
        checks.append(
            GuardrailCheck(
                "daily_cap", False, reason,
                value=str(counters.missions_today), limit=str(settings.max_missions_per_day),
            )
        )
        return Verdict("block", reason), checks
    checks.append(
        GuardrailCheck(
            "daily_cap", True, "under daily cap",
            value=str(counters.missions_today), limit=str(settings.max_missions_per_day),
        )
    )

    # 5. Concurrency cap
    if counters.active_autonomous >= settings.max_concurrent:
        reason = "concurrent autonomous mission cap reached"
        checks.append(
            GuardrailCheck(
                "concurrency", False, reason,
                value=str(counters.active_autonomous), limit=str(settings.max_concurrent),
            )
        )
        return Verdict("block", reason), checks
    checks.append(
        GuardrailCheck(
            "concurrency", True, "under concurrency cap",
            value=str(counters.active_autonomous), limit=str(settings.max_concurrent),
        )
    )

    # 6. Duplicate / loop guard
    if decision.signature in counters.recent_signatures:
        reason = "duplicate of a recent/active autonomous mission"
        checks.append(GuardrailCheck("dedup", False, reason, value=decision.signature))
        return Verdict("block", reason), checks
    checks.append(GuardrailCheck("dedup", True, "no duplicate signature", value=decision.signature))

    # Baseline verdict from level: L3 proposes, L4+ executes.
    baseline = "propose" if autonomy_level == _MIN_AUTONOMY_LEVEL else "execute"

    # 7. Cost — fail closed when counters unavailable or over budget/ceiling.
    if not counters.counters_available:
        reason = "spend counters unavailable — proposing (fail-closed)"
        checks.append(GuardrailCheck("cost", False, reason))
        return Verdict("propose", reason), checks
    remaining = settings.daily_budget_usd - counters.day_spend_usd
    if decision.est_cost_usd > settings.per_mission_cost_ceiling_usd:
        reason = "estimated cost over per-mission ceiling"
        checks.append(
            GuardrailCheck(
                "cost", False, reason,
                value=f"${decision.est_cost_usd:.2f}",
                limit=f"${settings.per_mission_cost_ceiling_usd:.2f}",
            )
        )
        return Verdict("propose", reason), checks
    if decision.est_cost_usd > max(0.0, remaining):
        reason = "estimated cost over remaining daily budget"
        checks.append(
            GuardrailCheck(
                "cost", False, reason,
                value=f"${decision.est_cost_usd:.2f}",
                limit=f"${max(0.0, remaining):.2f}",
            )
        )
        return Verdict("propose", reason), checks
    runaway = loop_detector.check_cost_runaway(counters.day_spend_usd, settings.daily_budget_usd)
    if runaway.detected:
        reason = f"cost runaway: {runaway.details}"
        checks.append(
            GuardrailCheck(
                "cost", False, reason,
                value=f"${counters.day_spend_usd:.2f}",
                limit=f"${settings.daily_budget_usd:.2f}",
            )
        )
        return Verdict("block", reason), checks
    checks.append(
        GuardrailCheck(
            "cost", True, "within cost policy",
            value=f"${decision.est_cost_usd:.2f}",
            limit=f"${settings.per_mission_cost_ceiling_usd:.2f}",
        )
    )

    # 8. Risk gate — high-risk actions require human approval at mission execute time.
    # The DECIDE step tags external-send/destructive/spend actions as high-risk; this gate enforces
    # risk_level == "high" → propose (HITL gating moves to individual step approval at execution).
    if decision.risk_level == "high":
        reason = "high-risk action requires human approval"
        checks.append(
            GuardrailCheck("risk", False, reason, value=decision.risk_level, limit="high")
        )
        return Verdict("propose", reason), checks
    checks.append(
        GuardrailCheck("risk", True, "risk acceptable", value=decision.risk_level, limit="high")
    )

    return Verdict(baseline, "within policy"), checks
