"""Explicit bounded simulation clock."""

from __future__ import annotations

from datetime import datetime, timedelta


class SimulationClock:
    def __init__(self, *, start: datetime, horizon: datetime, maximum_events: int) -> None:
        if start.tzinfo is None or horizon.tzinfo is None:
            raise ValueError("simulation timestamps must be timezone-aware")
        if horizon <= start or maximum_events <= 0:
            raise ValueError("invalid simulation limits")
        self._now = start
        self._horizon = horizon
        self._maximum_events = maximum_events
        self._events: dict[str, datetime] = {}
        self._frozen = False

    @property
    def now(self) -> datetime:
        return self._now

    def freeze(self) -> None:
        self._frozen = True

    def resume(self) -> None:
        self._frozen = False

    def advance(self, delta: timedelta, *, event_id: str) -> datetime:
        prior = self._events.get(event_id)
        if prior is not None:
            return prior
        if self._frozen:
            raise RuntimeError("simulation clock is frozen")
        if delta <= timedelta(0):
            raise ValueError("simulation time must advance monotonically")
        if len(self._events) >= self._maximum_events:
            raise RuntimeError("simulation event limit exceeded")
        next_time = self._now + delta
        if next_time > self._horizon:
            raise RuntimeError("simulation horizon exceeded")
        self._now = next_time
        self._events[event_id] = next_time
        return next_time


__all__ = ["SimulationClock"]
