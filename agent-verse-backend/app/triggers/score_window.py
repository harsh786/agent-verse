"""Rolling-window evaluation for ``goal_score_below`` triggers (B7 live open item 3).

A ``goal_score_below`` trigger compared ONE goal's score with its threshold. Two
of the seven evaluation dimensions — ``accuracy`` and ``coherence`` — are LLM
judgements (``EvalRunner.score_async``): the same correct "ACK" answer scored
accuracy 1.0 in one live run and 0.0 in the next, so a trigger on a judged
dimension fired non-deterministically for a borderline agent. The overall average
includes both judged dimensions (one 1.0 -> 0.0 flip moves it by 1/7), so it
inherits that noise.

Design: a trigger decides on the last ``N`` scores of the goals it watches (the
goals that pass its ``watch_agent_id`` / ``watch_goal_id`` filters), not on one:

* ``score_window`` (``N``): ``0`` = automatic (the safe default):
  ``1`` for the deterministic dimensions (``task_completion``, ``efficiency``,
  ``safety``, ``sla``, ``tool_relevance``) — one goal decides, exactly as
  before — and :data:`DEFAULT_NOISY_WINDOW` (3) for the judged dimensions and
  for ``overall``. ``1`` opts a judged trigger back into single-goal firing;
  at most :data:`MAX_SCORE_WINDOW`.
* ``score_aggregation``: ``"mean"`` (default) fires when the mean of the last
  ``N`` scores is below the threshold (a rolling average: one noisy judgement is
  diluted, a consistently weak agent still fires); ``"all"`` fires only when
  each of the last ``N`` scores is below it (``N`` consecutive breaches).

Nothing fires until ``N`` scores have been observed. After a firing the window is
cleared, so the next firing needs ``N`` fresh goals (no re-fire on every further
goal of the same bad streak). The series is kept in Redis per trigger (shared by
every replica; a relayed or retried event of a goal already in the window is not
counted twice), with a per-process fallback when Redis is unavailable — a short
window only delays a firing, it never fires early.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

_log = logging.getLogger(__name__)

# Dimensions whose score is an LLM judgement (EvalRunner.score_async).
JUDGED_DIMENSIONS = frozenset({"accuracy", "coherence"})
DEFAULT_NOISY_WINDOW = 3
MAX_SCORE_WINDOW = 50
SCORE_AGGREGATIONS = ("mean", "all")
_SERIES_TTL_S = 30 * 86_400  # an idle trigger's window expires after 30 days
_KEY_PREFIX = "trigger:score_window"


def watched_dimension(spec: object) -> str:
    """The trigger's dimension, ``"overall"`` for blank / overall."""
    dimension = str(getattr(spec, "score_dimension", "") or "").strip()
    return "overall" if dimension.lower() in ("", "overall") else dimension


def is_noisy_dimension(dimension: str) -> bool:
    """Judged dimensions and the overall average (which includes them)."""
    return dimension == "overall" or dimension in JUDGED_DIMENSIONS


def effective_window(spec: object) -> int:
    """How many goals' scores the trigger decides on (``score_window``, 0 = auto)."""
    raw = getattr(spec, "score_window", 0)
    size = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
    if size > 0:
        return min(size, MAX_SCORE_WINDOW)
    return DEFAULT_NOISY_WINDOW if is_noisy_dimension(watched_dimension(spec)) else 1


def aggregation(spec: object) -> str:
    value = str(getattr(spec, "score_aggregation", "") or "mean").strip().lower()
    return value if value in SCORE_AGGREGATIONS else "mean"


def window_error(spec: object) -> str | None:
    """Why the trigger's window options are invalid (``None`` = valid)."""
    raw = getattr(spec, "score_window", 0)
    if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw <= MAX_SCORE_WINDOW:
        return (
            f"score_window must be an integer in [0, {MAX_SCORE_WINDOW}] "
            "(0 = automatic: 1 for deterministic dimensions, "
            f"{DEFAULT_NOISY_WINDOW} for LLM-judged dimensions and overall)"
        )
    agg = str(getattr(spec, "score_aggregation", "") or "mean").strip().lower()
    if agg not in SCORE_AGGREGATIONS:
        return f"score_aggregation must be one of: {', '.join(SCORE_AGGREGATIONS)}"
    return None


def breached(scores: Sequence[float], threshold: float, size: int, how: str) -> tuple[bool, float]:
    """Whether the last ``size`` scores (newest first) breach ``threshold``.

    Returns ``(fires, aggregate)``; never fires on fewer than ``size`` scores.
    """
    window = list(scores[:size])
    if not window:
        return False, 0.0
    # "all": every score below <=> the highest one is below.
    aggregate = max(window) if how == "all" else sum(window) / len(window)
    return len(window) >= size and aggregate < threshold, round(aggregate, 6)


def series_key(tenant_id: str, trigger_id: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{trigger_id}"


class ScoreSeries:
    """The recent (goal id, score) observations of each windowed trigger."""

    def __init__(self, redis: Any = None) -> None:
        self._redis = redis
        # Per-process fallback, newest first (used only when Redis is absent/down).
        self._local: dict[str, list[tuple[str, float]]] = {}

    async def observe(self, key: str, goal_id: str, score: float, size: int) -> list[float]:
        """Record ``goal_id``'s score; return the last ``size`` scores, newest first.

        A goal already in the window (a relayed or retried event) is not added
        again.
        """
        if self._redis is not None:
            try:
                return await self._observe_redis(key, goal_id, score, size)
            except Exception as exc:
                _log.warning("score_window_redis_unavailable key=%s: %s", key, exc)
        entries = self._local.setdefault(key, [])
        if all(g != goal_id for g, _ in entries):
            entries.insert(0, (goal_id, score))
            del entries[MAX_SCORE_WINDOW:]
        return [s for _, s in entries[:size]]

    async def reset(self, key: str) -> None:
        """Clear the window (after a firing)."""
        self._local.pop(key, None)
        if self._redis is not None:
            try:
                await self._redis.delete(key)
            except Exception as exc:
                _log.warning("score_window_reset_failed key=%s: %s", key, exc)

    async def _observe_redis(self, key: str, goal_id: str, score: float, size: int) -> list[float]:
        current = [_decode(e) for e in await self._redis.lrange(key, 0, MAX_SCORE_WINDOW - 1)]
        known = [entry for entry in current if entry is not None]
        if any(g == goal_id for g, _ in known):
            return [s for _, s in known[:size]]
        entry = json.dumps({"g": goal_id, "s": score})
        pipe = self._redis.pipeline(transaction=True)
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, MAX_SCORE_WINDOW - 1)
        pipe.expire(key, _SERIES_TTL_S)
        await pipe.execute()
        return [score, *(s for _, s in known)][:size]


def _decode(raw: Any) -> tuple[str, float] | None:
    try:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        data = json.loads(text)
        return str(data["g"]), float(data["s"])
    except Exception:
        return None
