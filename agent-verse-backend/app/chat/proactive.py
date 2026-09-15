"""Proactive-outreach consent + rate gate (Part B, Phase 9).

The assistant may initiate messages (reminders, follow-ups, "want me to handle
X?"), but ONLY within per-principal consent: enabled, allowed channel, outside
quiet hours, under the daily rate limit. This module is the pure decision gate;
the signal bus + delivery wire into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProactivePreferences:
    enabled: bool = True
    # (start_hour, end_hour) in the principal's local time; supports wrap-around
    # (e.g. (22, 7) = 10pm-7am). None = no quiet hours.
    quiet_hours: tuple[int, int] | None = None
    max_per_day: int = 5
    channels: frozenset[str] = field(default_factory=lambda: frozenset({"web"}))


@dataclass(frozen=True)
class ProactiveDecision:
    allow: bool
    reason: str


def _in_quiet_hours(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wrap-around window (e.g. 22-7).
    return hour >= start or hour < end


def evaluate_proactive(
    prefs: ProactivePreferences,
    *,
    now_hour: int,
    sent_today: int,
    channel: str,
) -> ProactiveDecision:
    """Decide whether a proactive message may be sent right now."""
    if not prefs.enabled:
        return ProactiveDecision(False, "disabled")
    if channel not in prefs.channels:
        return ProactiveDecision(False, "channel_not_allowed")
    if prefs.quiet_hours is not None and _in_quiet_hours(now_hour, prefs.quiet_hours):
        return ProactiveDecision(False, "quiet_hours")
    if sent_today >= prefs.max_per_day:
        return ProactiveDecision(False, "rate_limited")
    return ProactiveDecision(True, "ok")
