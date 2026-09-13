"""Typed, defaulted view of Organization.settings["autonomy"]."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "paused": False,
    "cadence_seconds": 300,
    "min_interval_seconds": 600,
    "max_concurrent": 2,
    "max_missions_per_day": 8,
    "daily_budget_usd": 0.0,
    "per_mission_cost_ceiling_usd": 0.0,
    "blocked_threshold": 5,
    "failed_threshold": 2,
    "idle_threshold": 1,
    "collaboration_enabled": False,
    "collaboration_daily_budget_usd": 1.0,
    "collab_messages_per_tick": 4,
}


@dataclass(frozen=True)
class AutonomySettings:
    paused: bool
    cadence_seconds: int
    min_interval_seconds: int
    max_concurrent: int
    max_missions_per_day: int
    daily_budget_usd: float
    per_mission_cost_ceiling_usd: float
    blocked_threshold: int
    failed_threshold: int
    idle_threshold: int
    collaboration_enabled: bool
    collaboration_daily_budget_usd: float
    collab_messages_per_tick: int


def resolve_autonomy_settings(
    settings: dict[str, Any] | None, monthly_budget_usd: float
) -> AutonomySettings:
    raw = dict(_DEFAULTS)
    if settings and isinstance(settings.get("autonomy"), dict):
        for k, v in settings["autonomy"].items():
            if k in raw and v is not None:
                raw[k] = v
    daily = float(raw["daily_budget_usd"]) or (float(monthly_budget_usd) / 30.0)
    ceiling = float(raw["per_mission_cost_ceiling_usd"]) or (float(monthly_budget_usd) * 0.10)
    return AutonomySettings(
        paused=bool(raw["paused"]),
        cadence_seconds=int(raw["cadence_seconds"]),
        min_interval_seconds=int(raw["min_interval_seconds"]),
        max_concurrent=int(raw["max_concurrent"]),
        max_missions_per_day=int(raw["max_missions_per_day"]),
        daily_budget_usd=daily,
        per_mission_cost_ceiling_usd=ceiling,
        blocked_threshold=int(raw["blocked_threshold"]),
        failed_threshold=int(raw["failed_threshold"]),
        idle_threshold=int(raw["idle_threshold"]),
        collaboration_enabled=bool(raw["collaboration_enabled"]),
        collaboration_daily_budget_usd=float(raw["collaboration_daily_budget_usd"]),
        collab_messages_per_tick=int(raw["collab_messages_per_tick"]),
    )
