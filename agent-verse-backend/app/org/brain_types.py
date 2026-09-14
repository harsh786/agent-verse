from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrainDecision:
    kind: str            # "reactive" | "proactive"
    rationale: str
    target_goal: str
    est_cost_usd: float
    risk_level: str      # "low" | "medium" | "high"
    signature: str       # stable dedup key for this intended work
