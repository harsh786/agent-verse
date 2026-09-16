"""Tests for app.triggers.metrics — Prometheus metrics for the trigger framework."""
from __future__ import annotations

import pathlib

import app.triggers.metrics as metrics_mod


def test_metrics_available_flag_matches_prometheus_client_presence() -> None:
    try:
        import prometheus_client  # noqa: F401

        assert metrics_mod.METRICS_AVAILABLE is True
    except ImportError:
        assert metrics_mod.METRICS_AVAILABLE is False


def test_trigger_fired_total_records_without_raising() -> None:
    metrics_mod.TRIGGER_FIRED_TOTAL.labels(
        trigger_type="webhook", tenant_plan="free", result="success"
    ).inc()


def test_trigger_fire_latency_observes_without_raising() -> None:
    metrics_mod.TRIGGER_FIRE_LATENCY.labels(trigger_type="schedule").observe(0.05)


def test_trigger_goal_created_total_increments_without_raising() -> None:
    metrics_mod.TRIGGER_GOAL_CREATED_TOTAL.labels(
        trigger_type="webhook", tenant_plan="professional"
    ).inc()


def test_trigger_circuit_state_gauge_sets_without_raising() -> None:
    metrics_mod.TRIGGER_CIRCUIT_STATE.labels(trigger_id="trig-1", tenant_id="t1").set(1)


def test_trigger_dlq_depth_gauge_sets_without_raising() -> None:
    metrics_mod.TRIGGER_DLQ_DEPTH.labels(trigger_type="rss", tenant_id="t1").set(3)


def test_trigger_rate_limit_drops_total_increments_without_raising() -> None:
    metrics_mod.TRIGGER_RATE_LIMIT_DROPS_TOTAL.labels(
        trigger_type="webhook", tenant_id="t1"
    ).inc()


def test_noop_fallback_behaviour_when_prometheus_client_missing(monkeypatch) -> None:
    """Simulate the ImportError branch and verify the _Noop shim is inert.

    Executes the module source in an isolated namespace (rather than reloading
    the real ``app.triggers.metrics`` module in place) so this does not attempt
    to re-register already-registered Counters/Gauges with prometheus_client's
    global default registry, which would raise a duplicate-timeseries error.
    """
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "prometheus_client":
            raise ImportError("prometheus_client not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    source = pathlib.Path(metrics_mod.__file__).read_text()
    namespace: dict = {"__name__": "app.triggers._metrics_fallback_test"}
    exec(compile(source, metrics_mod.__file__, "exec"), namespace)

    assert namespace["METRICS_AVAILABLE"] is False
    # _Noop methods must be chainable and inert.
    assert namespace["TRIGGER_FIRED_TOTAL"].labels(trigger_type="x").inc() is None
    assert namespace["TRIGGER_FIRE_LATENCY"].labels(trigger_type="x").observe(1.0) is None
    assert namespace["TRIGGER_CIRCUIT_STATE"].labels(trigger_id="x", tenant_id="y").set(2) is None
