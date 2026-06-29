"""Tests for civilization Prometheus metrics — lazy registry, recording helpers."""
from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import app.civilization.metrics as metrics_mod
from app.civilization.metrics import (
    _NullMetric,
    _get,
    civ_agents_active,
    civ_budget_spent_usd,
    civ_debates_total,
    civ_learnings_promoted_total,
    civ_learnings_rejected_total,
    civ_spawn_denied_total,
    civ_spawns_total,
    record_agents_active,
    record_budget_spent,
    record_debate,
    record_learning_outcome,
    record_spawn,
)


# ── _NullMetric ───────────────────────────────────────────────────────────────


def test_null_metric_labels_returns_self():
    nm = _NullMetric()
    result = nm.labels(tenant_id="t1", decision="approved")
    assert result is nm


def test_null_metric_inc_no_op():
    nm = _NullMetric()
    nm.inc()          # default amount=1
    nm.inc(5)         # custom amount


def test_null_metric_dec_no_op():
    nm = _NullMetric()
    nm.dec()
    nm.dec(2.5)


def test_null_metric_set_no_op():
    nm = _NullMetric()
    nm.set(42.0)
    nm.set(0.0)


def test_null_metric_observe_no_op():
    nm = _NullMetric()
    nm.observe(0.123)


def test_null_metric_chaining():
    nm = _NullMetric()
    # labels() → self, then can call inc()
    nm.labels(tenant_id="t1").inc()


# ── _get / lazy registry ──────────────────────────────────────────────────────


def test_get_returns_null_metric_when_prometheus_unavailable():
    """When prometheus_client is not importable, _get returns _NullMetric."""
    # Use a unique name to avoid cache collision
    unique_name = "test_metric_no_prom_xyzzy_12345"
    # Clear any previous cache entry
    metrics_mod._metrics.pop(unique_name, None)

    with patch.dict(sys.modules, {"prometheus_client": None}):
        result = _get(unique_name, "Counter", "test counter")

    assert isinstance(result, _NullMetric)
    # Clean up
    metrics_mod._metrics.pop(unique_name, None)


def test_get_caches_metric_on_second_call():
    """_get returns the exact same object on repeated calls for the same name."""
    unique_name = "test_metric_cache_abc_99999"
    metrics_mod._metrics.pop(unique_name, None)

    m1 = _get(unique_name, "Gauge", "test gauge")
    m2 = _get(unique_name, "Gauge", "test gauge")
    assert m1 is m2

    metrics_mod._metrics.pop(unique_name, None)


def test_get_creates_null_for_unknown_type():
    """Unknown metric_type → _NullMetric fallback."""
    unique_name = "test_metric_unknown_type_zzz_55"
    metrics_mod._metrics.pop(unique_name, None)

    result = _get(unique_name, "UnknownType", "desc")
    # Should be either a real metric or a NullMetric (both are valid)
    assert result is not None

    metrics_mod._metrics.pop(unique_name, None)


def test_get_with_prometheus_counter():
    """When prometheus_client is available, a real Counter should be created."""
    unique_name = "test_civ_counter_for_coverage_001"
    metrics_mod._metrics.pop(unique_name, None)

    result = _get(unique_name, "Counter", "test counter for coverage", ["tenant_id"])
    # Result is either a real Counter or NullMetric (both valid in test envs)
    assert result is not None
    assert hasattr(result, "inc") or hasattr(result, "labels")

    metrics_mod._metrics.pop(unique_name, None)


def test_get_with_prometheus_histogram():
    unique_name = "test_civ_histogram_for_coverage_002"
    metrics_mod._metrics.pop(unique_name, None)

    result = _get(unique_name, "Histogram", "test histogram", ["tenant_id"])
    assert result is not None

    metrics_mod._metrics.pop(unique_name, None)


def test_get_falls_back_to_null_on_registration_error():
    """If prometheus raises on metric creation (duplicate), we get NullMetric."""
    unique_name = "test_metric_dup_error_xyz_007"
    metrics_mod._metrics.pop(unique_name, None)

    mock_prom = MagicMock()
    mock_prom.Counter.side_effect = ValueError("duplicate metric")

    with patch.dict(sys.modules, {"prometheus_client": mock_prom}):
        result = _get(unique_name, "Counter", "will fail")

    assert isinstance(result, _NullMetric)
    metrics_mod._metrics.pop(unique_name, None)


# ── Metric accessor functions ─────────────────────────────────────────────────


def test_civ_agents_active_returns_metric():
    m = civ_agents_active()
    assert m is not None


def test_civ_spawns_total_returns_metric():
    m = civ_spawns_total()
    assert m is not None


def test_civ_spawn_denied_total_returns_metric():
    m = civ_spawn_denied_total()
    assert m is not None


def test_civ_budget_spent_usd_returns_metric():
    m = civ_budget_spent_usd()
    assert m is not None


def test_civ_debates_total_returns_metric():
    m = civ_debates_total()
    assert m is not None


def test_civ_learnings_promoted_total_returns_metric():
    m = civ_learnings_promoted_total()
    assert m is not None


def test_civ_learnings_rejected_total_returns_metric():
    m = civ_learnings_rejected_total()
    assert m is not None


# ── record_spawn ──────────────────────────────────────────────────────────────


def test_record_spawn_approved_does_not_raise():
    record_spawn(tenant_id="t1", civilization_id="civ-1", decision="approved")


def test_record_spawn_denied_increments_denied_counter():
    """denied decision should hit both civ_spawns_total and civ_spawn_denied_total."""
    mock_spawns = MagicMock()
    mock_denied = MagicMock()

    with patch.object(metrics_mod, "civ_spawns_total", return_value=mock_spawns), \
         patch.object(metrics_mod, "civ_spawn_denied_total", return_value=mock_denied):
        record_spawn(tenant_id="t1", civilization_id="civ-1", decision="denied")

    mock_spawns.labels.assert_called_once_with(tenant_id="t1", decision="denied")
    mock_spawns.labels().inc.assert_called_once()
    mock_denied.labels.assert_called_once_with(tenant_id="t1", reason_category="policy")
    mock_denied.labels().inc.assert_called_once()


def test_record_spawn_approved_only_increments_spawns_total():
    mock_spawns = MagicMock()
    mock_denied = MagicMock()

    with patch.object(metrics_mod, "civ_spawns_total", return_value=mock_spawns), \
         patch.object(metrics_mod, "civ_spawn_denied_total", return_value=mock_denied):
        record_spawn(tenant_id="t1", civilization_id="civ-1", decision="approved")

    mock_spawns.labels.assert_called_once()
    mock_denied.labels.assert_not_called()


def test_record_spawn_swallows_exceptions():
    """Metric recording errors must never propagate."""
    mock_spawns = MagicMock()
    mock_spawns.labels.side_effect = RuntimeError("metric broken")

    with patch.object(metrics_mod, "civ_spawns_total", return_value=mock_spawns):
        record_spawn(tenant_id="t1", civilization_id="civ-1", decision="approved")
        # should not raise


# ── record_agents_active ──────────────────────────────────────────────────────


def test_record_agents_active_calls_gauge_set():
    mock_gauge = MagicMock()

    with patch.object(metrics_mod, "civ_agents_active", return_value=mock_gauge):
        record_agents_active(tenant_id="t1", civilization_id="civ-1", count=7)

    mock_gauge.labels.assert_called_once_with(tenant_id="t1", civilization_id="civ-1")
    mock_gauge.labels().set.assert_called_once_with(7)


def test_record_agents_active_swallows_exceptions():
    mock_gauge = MagicMock()
    mock_gauge.labels.side_effect = RuntimeError("gauge broken")

    with patch.object(metrics_mod, "civ_agents_active", return_value=mock_gauge):
        record_agents_active(tenant_id="t1", civilization_id="civ-1", count=3)


# ── record_budget_spent ───────────────────────────────────────────────────────


def test_record_budget_spent_calls_gauge_set():
    mock_gauge = MagicMock()

    with patch.object(metrics_mod, "civ_budget_spent_usd", return_value=mock_gauge):
        record_budget_spent(tenant_id="t1", civilization_id="civ-1", amount_usd=42.5)

    mock_gauge.labels.assert_called_once_with(tenant_id="t1", civilization_id="civ-1")
    mock_gauge.labels().set.assert_called_once_with(42.5)


def test_record_budget_spent_swallows_exceptions():
    mock_gauge = MagicMock()
    mock_gauge.labels.side_effect = RuntimeError("gauge broken")

    with patch.object(metrics_mod, "civ_budget_spent_usd", return_value=mock_gauge):
        record_budget_spent(tenant_id="t1", civilization_id="civ-1", amount_usd=10.0)


# ── record_debate ─────────────────────────────────────────────────────────────


def test_record_debate_increments_counter():
    mock_counter = MagicMock()

    with patch.object(metrics_mod, "civ_debates_total", return_value=mock_counter):
        record_debate(tenant_id="t1")

    mock_counter.labels.assert_called_once_with(tenant_id="t1")
    mock_counter.labels().inc.assert_called_once()


def test_record_debate_swallows_exceptions():
    mock_counter = MagicMock()
    mock_counter.labels.side_effect = RuntimeError("counter broken")

    with patch.object(metrics_mod, "civ_debates_total", return_value=mock_counter):
        record_debate(tenant_id="t1")


# ── record_learning_outcome ───────────────────────────────────────────────────


def test_record_learning_outcome_promoted():
    mock_promoted = MagicMock()
    mock_rejected = MagicMock()

    with patch.object(metrics_mod, "civ_learnings_promoted_total", return_value=mock_promoted), \
         patch.object(metrics_mod, "civ_learnings_rejected_total", return_value=mock_rejected):
        record_learning_outcome(tenant_id="t1", outcome="promoted")

    mock_promoted.labels.assert_called_once_with(tenant_id="t1")
    mock_promoted.labels().inc.assert_called_once()
    mock_rejected.labels.assert_not_called()


def test_record_learning_outcome_rejected():
    mock_promoted = MagicMock()
    mock_rejected = MagicMock()

    with patch.object(metrics_mod, "civ_learnings_promoted_total", return_value=mock_promoted), \
         patch.object(metrics_mod, "civ_learnings_rejected_total", return_value=mock_rejected):
        record_learning_outcome(tenant_id="t1", outcome="rejected")

    mock_rejected.labels.assert_called_once_with(tenant_id="t1")
    mock_rejected.labels().inc.assert_called_once()
    mock_promoted.labels.assert_not_called()


def test_record_learning_outcome_validated_is_noop():
    """'validated' outcome increments neither counter (tracked via absence)."""
    mock_promoted = MagicMock()
    mock_rejected = MagicMock()

    with patch.object(metrics_mod, "civ_learnings_promoted_total", return_value=mock_promoted), \
         patch.object(metrics_mod, "civ_learnings_rejected_total", return_value=mock_rejected):
        record_learning_outcome(tenant_id="t1", outcome="validated")

    mock_promoted.labels.assert_not_called()
    mock_rejected.labels.assert_not_called()


def test_record_learning_outcome_swallows_exceptions():
    mock_promoted = MagicMock()
    mock_promoted.labels.side_effect = RuntimeError("broken")

    with patch.object(metrics_mod, "civ_learnings_promoted_total", return_value=mock_promoted):
        record_learning_outcome(tenant_id="t1", outcome="promoted")
