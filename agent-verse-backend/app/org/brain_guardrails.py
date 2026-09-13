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


def _global_kill_switch() -> bool:
    return os.getenv("AV_ORG_AUTONOMY_DISABLED", "").lower() in {"1", "true", "yes"}


def evaluate_guardrails(
    decision: BrainDecision,
    *,
    autonomy_level: int,
    settings: AutonomySettings,
    counters: TickCounters,
    kill_switch: bool,
    enforcer: AutonomyEnforcer,
    loop_detector: OrgLoopDetector,
) -> Verdict:
    # 1. Kill switch (per-org paused or global env)
    if kill_switch or settings.paused or _global_kill_switch():
        return Verdict("block", "autonomy paused (kill switch)")
    # 2. Autonomy level — origination requires L3+
    if autonomy_level < _MIN_AUTONOMY_LEVEL:
        return Verdict("block", f"autonomy level {autonomy_level} below L3")
    # 3. Cooldown
    if counters.seconds_since_last_launch < settings.min_interval_seconds:
        return Verdict("block", "cooldown between autonomous launches not elapsed")
    # 4. Daily launch cap
    if counters.missions_today >= settings.max_missions_per_day:
        return Verdict("block", "daily autonomous mission cap reached")
    # 5. Concurrency cap
    if counters.active_autonomous >= settings.max_concurrent:
        return Verdict("block", "concurrent autonomous mission cap reached")
    # 6. Duplicate / loop guard
    if decision.signature in counters.recent_signatures:
        return Verdict("block", "duplicate of a recent/active autonomous mission")
    # Baseline verdict from level: L3 proposes, L4+ executes.
    baseline = "propose" if autonomy_level == _MIN_AUTONOMY_LEVEL else "execute"
    # 7. Cost — fail closed when counters unavailable or over budget/ceiling.
    if not counters.counters_available:
        return Verdict("propose", "spend counters unavailable — proposing (fail-closed)")
    remaining = settings.daily_budget_usd - counters.day_spend_usd
    if decision.est_cost_usd > settings.per_mission_cost_ceiling_usd:
        return Verdict("propose", "estimated cost over per-mission ceiling")
    if decision.est_cost_usd > max(0.0, remaining):
        return Verdict("propose", "estimated cost over remaining daily budget")
    runaway = loop_detector.check_cost_runaway(counters.day_spend_usd, settings.daily_budget_usd)
    if runaway.detected:
        return Verdict("block", f"cost runaway: {runaway.details}")
    # 8. Risk gate — high-risk / external-send always needs approval.
    if decision.risk_level == "high":
        return Verdict("propose", "high-risk action requires human approval")
    return Verdict(baseline, "within policy")
