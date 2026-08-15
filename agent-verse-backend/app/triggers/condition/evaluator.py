"""CEL condition evaluator and Jinja2 sandboxed template renderer."""
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)


class CELEvaluator:
    """Evaluate CEL expressions against a payload dict.

    Falls back to a permissive mode when cel-python is not installed.
    """

    TIMEOUT_SECONDS: float = 0.5
    BLOCKED_ATTRIBUTES = frozenset([
        "__class__", "__import__", "eval", "exec", "open",
        "__builtins__", "globals", "locals",
    ])

    def evaluate(self, expression: str, payload: dict) -> bool:
        """Return True if expression evaluates truthy, False otherwise."""
        if not expression.strip():
            return True
        try:
            import celpy  # type: ignore[import]
            import celpy.celtypes as ct  # type: ignore[import]
            env = celpy.Environment()
            ast = env.compile(expression)
            prog = env.program(ast)
            activation = {
                "payload": ct.MapType({
                    ct.StringType(k): self._to_cel(v, ct)
                    for k, v in payload.items()
                    if k not in self.BLOCKED_ATTRIBUTES
                })
            }
            return bool(prog.evaluate(activation))
        except ImportError:
            _log.debug("cel-python not installed, condition assumed True")
            return True
        except Exception as exc:
            _log.warning("cel_eval_error expr='%s': %s", expression, exc)
            raise

    def _to_cel(self, value: object, ct: object) -> object:
        """Convert Python value to CEL type."""
        if isinstance(value, bool):
            return ct.BoolType(value)  # type: ignore[attr-defined]
        if isinstance(value, int):
            return ct.IntType(value)  # type: ignore[attr-defined]
        if isinstance(value, float):
            return ct.DoubleType(value)  # type: ignore[attr-defined]
        return ct.StringType(str(value))  # type: ignore[attr-defined]


class TemplateRenderer:
    """Render Jinja2 sandboxed goal templates.

    Supports {{payload.field}} and {{trigger_type}} interpolation.
    Maximum output: 2048 characters.
    """

    MAX_LENGTH = 2048
    _PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")

    def render(self, template: str, payload: dict, **extra: str) -> str:
        """Render the template with the given payload and extra variables."""

        def replace(match: re.Match) -> str:
            key = match.group(1).strip()
            # Support {{payload.field}} notation
            if key.startswith("payload."):
                field = key[len("payload."):]
                return str(payload.get(field, ""))
            # Support direct {{field}} that matches extra kwargs
            if key in extra:
                return str(extra[key])
            # Support direct {{field}} from payload
            if key in payload:
                return str(payload[key])
            return ""

        result = self._PLACEHOLDER_RE.sub(replace, template)
        return result[: self.MAX_LENGTH]


class CounterThresholdEvaluator:
    """Sliding-window counter backed by a dict (production: use Redis INCR+EXPIRE).

    Fires when a counter hits or exceeds a threshold within a time window.
    """

    def __init__(self) -> None:
        import time as _time
        self._time = _time
        # key → list of timestamps
        self._events: dict[str, list[float]] = {}

    def record(self, key: str) -> int:
        """Record an event occurrence and return the current window count."""
        now = self._time.monotonic()
        self._events.setdefault(key, []).append(now)
        return self._count(key, window_secs=3600)

    def check(
        self,
        key: str,
        threshold: int,
        window_secs: int = 3600,
    ) -> bool:
        """Return True if count in window >= threshold."""
        now = self._time.monotonic()
        events = self._events.get(key, [])
        # Keep only events within window
        cutoff = now - window_secs
        recent = [t for t in events if t >= cutoff]
        self._events[key] = recent
        return len(recent) >= threshold

    def _count(self, key: str, window_secs: int = 3600) -> int:
        import time as _time
        now = _time.monotonic()
        return sum(1 for t in self._events.get(key, []) if t >= now - window_secs)

    def reset(self, key: str) -> None:
        self._events.pop(key, None)


class WindowAggregateEvaluator:
    """Time-window aggregation evaluator (sum/avg/max/min) against a threshold.

    Production implementation would use Redis sorted sets.
    """

    AGGREGATIONS = {
        "sum": sum,
        "avg": lambda vals: sum(vals) / len(vals) if vals else 0.0,
        "max": max,
        "min": min,
        "count": len,
    }

    def __init__(self) -> None:
        import time as _time
        self._time = _time
        # key → list of (timestamp, value)
        self._values: dict[str, list[tuple[float, float]]] = {}

    def record(self, key: str, value: float) -> None:
        """Record a metric sample."""
        now = self._time.monotonic()
        self._values.setdefault(key, []).append((now, value))

    def check(
        self,
        key: str,
        threshold: float,
        aggregation: str = "avg",
        window_secs: int = 300,
        comparison: str = ">",
    ) -> bool:
        """Return True if the aggregate of values in window meets the threshold condition."""
        now = self._time.monotonic()
        cutoff = now - window_secs
        pairs = [(t, v) for t, v in self._values.get(key, []) if t >= cutoff]
        self._values[key] = pairs

        if not pairs:
            return False

        values = [v for _, v in pairs]
        agg_fn = self.AGGREGATIONS.get(aggregation, self.AGGREGATIONS["avg"])
        aggregate = agg_fn(values)  # type: ignore[operator]

        ops = {">": aggregate > threshold, ">=": aggregate >= threshold,
               "<": aggregate < threshold, "<=": aggregate <= threshold,
               "==": aggregate == threshold}
        return ops.get(comparison, False)


class CompoundTriggerEvaluator:
    """Evaluate AND/OR/NOT logic over multiple condition results.

    Each condition is identified by a string key and a boolean current state.
    """

    def evaluate_compound(
        self,
        logic: str,
        states: dict[str, bool],
    ) -> bool:
        """
        Evaluate compound logic.

        logic: 'AND' | 'OR' | 'NOT'
        states: mapping of condition_id → bool (current state)
        """
        values = list(states.values())
        if not values:
            return False

        if logic.upper() == "AND":
            return all(values)
        if logic.upper() == "OR":
            return any(values)
        if logic.upper() == "NOT":
            # NOT applies to first condition
            return not values[0]
        # Default: AND
        return all(values)
