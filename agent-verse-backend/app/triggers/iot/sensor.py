"""Sensor threshold trigger evaluator."""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

# Unit conversion factors to SI
_UNIT_TO_SI = {
    "celsius": 1.0,
    "fahrenheit": None,  # handled specially
    "kelvin": None,
    "psi": 6894.76,  # to Pascal
    "bar": 100000.0,
    "kpa": 1000.0,
    "mpa": 1_000_000.0,
    "%": 1.0,
    "rpm": 1.0,
    "m/s": 1.0,
    "km/h": 1 / 3.6,
    "mph": 0.44704,
    "v": 1.0,
    "mv": 0.001,
    "a": 1.0,
    "ma": 0.001,
}


def convert_to_base(value: float, unit: str) -> float:
    """Convert sensor reading to base unit."""
    u = unit.lower().strip()
    if u == "fahrenheit":
        return (value - 32) * 5 / 9  # to Celsius
    if u == "kelvin":
        return value - 273.15  # to Celsius
    factor = _UNIT_TO_SI.get(u, 1.0)
    return value * (factor or 1.0)


class SensorThresholdEvaluator:
    """Evaluate sensor readings against per-trigger thresholds."""

    COMPARISON_OPS: dict = {  # noqa: RUF012
        ">": lambda v, t: v > t,
        ">=": lambda v, t: v >= t,
        "<": lambda v, t: v < t,
        "<=": lambda v, t: v <= t,
        "==": lambda v, t: v == t,
        "!=": lambda v, t: v != t,
    }

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher

    def check_threshold(
        self,
        value: float,
        threshold: float,
        comparison: str = ">",
        *,
        value_unit: str = "",
        threshold_unit: str = "",
    ) -> bool:
        """Return True if the sensor value crosses the threshold."""
        if value_unit and threshold_unit and value_unit != threshold_unit:
            value = convert_to_base(value, value_unit)
            threshold = convert_to_base(threshold, threshold_unit)
        op = self.COMPARISON_OPS.get(comparison, self.COMPARISON_OPS[">"])
        return op(value, threshold)

    async def evaluate(
        self,
        device_id: str,
        metric: str,
        value: float,
        unit: str = "",
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Check device sensor reading and dispatch matching triggers."""
        if self._store is None or self._dispatcher is None:
            return []

        triggers = await self._store.find_by_type_async("sensor_threshold", tenant_id=tenant_id)
        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        fired = []
        payload = {
            "device_id": device_id,
            "metric": metric,
            "value": value,
            "unit": unit,
        }

        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            watch_device = getattr(spec, "sensor_device_id", "") or ""
            watch_metric = getattr(spec, "sensor_metric", "") or ""
            threshold = getattr(spec, "sensor_threshold", None)
            comparison = getattr(spec, "sensor_comparison", ">") or ">"

            if watch_device and watch_device != device_id:
                continue
            if watch_metric and watch_metric != metric:
                continue
            if threshold is None:
                continue

            crossed = self.check_threshold(
                value,
                threshold,
                comparison,
                value_unit=unit,
            )
            if not crossed:
                continue

            payload_enriched = {**payload, "threshold": threshold, "comparison": comparison}
            try:
                r = await self._dispatcher.dispatch(spec, payload_enriched, tenant_ctx)
                fired.append(r)
            except Exception as exc:
                _log.warning("sensor_threshold_dispatch_error: %s", exc)

        return fired
