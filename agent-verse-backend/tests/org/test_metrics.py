"""Tests for org-level Prometheus metrics helpers — app/org/metrics.py"""
from __future__ import annotations

import builtins
import importlib
import sys

import app.org.metrics as metrics


def test_record_mission_completed_does_not_raise():
    metrics.record_mission_completed("org1", "engineering", "completed", 123.4)
    metrics.record_mission_completed("org1", "engineering", "failed", 1.0, risk_level="high")


def test_record_model_cost_does_not_raise():
    metrics.record_model_cost("org1", "smart", "engineering", 0.42)


def test_record_model_quality_does_not_raise():
    metrics.record_model_quality("org1", "smart", "verifier", 0.91)


def test_record_approval_wait_does_not_raise():
    metrics.record_approval_wait("org1", "deploy", 120.0)


def test_record_knowledge_search_hit_and_miss():
    metrics.record_knowledge_search("org1", "coll-1", True)
    metrics.record_knowledge_search("org1", "coll-1", False)


def test_record_cross_dept_message_does_not_raise():
    metrics.record_cross_dept_message("org1", "sales", "finance")


def test_set_blocked_tasks_does_not_raise():
    metrics.set_blocked_tasks("org1", "engineering", 3)


def test_set_health_score_does_not_raise():
    metrics.set_health_score("org1", 87.5)


def test_set_budget_used_does_not_raise():
    metrics.set_budget_used("org1", "engineering", 0.65)


def test_metrics_objects_are_importable():
    # Whether or not prometheus_client is installed, module-level metric handles
    # must exist and support labels()/inc()/observe()/set() without raising.
    assert metrics.ORG_MISSION_TOTAL is not None
    assert metrics.ORG_HEALTH_SCORE is not None


def test_metrics_available_flag_is_bool():
    assert isinstance(metrics._METRICS_AVAILABLE, bool)


def test_metrics_stub_fallback_when_prometheus_client_missing(monkeypatch):
    """When prometheus_client can't be imported, metrics.py must fall back to
    no-op stub objects instead of raising at import time."""
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "prometheus_client" or name.startswith("prometheus_client."):
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "prometheus_client", raising=False)
    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.delitem(sys.modules, "app.org.metrics", raising=False)
    try:
        reloaded = importlib.import_module("app.org.metrics")
        assert reloaded._METRICS_AVAILABLE is False
        # Stub objects must tolerate the same call surface as the real ones.
        reloaded.record_mission_completed("org1", "engineering", "completed", 10.0)
        reloaded.set_health_score("org1", 99.0)
    finally:
        monkeypatch.undo()
        sys.modules.pop("app.org.metrics", None)
        importlib.import_module("app.org.metrics")
