# tests/observability/test_orchestration_metrics.py
"""Orchestration metrics must be registered and incrementable."""
from __future__ import annotations
import pytest


def test_orchestration_metrics_registered():
    from app.observability.metrics import (
        orchestration_profile_built_total,
        orchestration_profile_latency_ms,
        orchestration_pattern_selected_total,
        orchestration_rag_strategy_total,
        orchestration_readiness_gate_blocked_total,
    )
    assert orchestration_profile_built_total is not None
    assert orchestration_profile_latency_ms is not None
    assert orchestration_pattern_selected_total is not None
    assert orchestration_rag_strategy_total is not None
    assert orchestration_readiness_gate_blocked_total is not None


def test_orchestration_counter_incrementable():
    from app.observability.metrics import orchestration_profile_built_total
    orchestration_profile_built_total.labels(
        complexity="simple", risk="low", tenant_plan="professional"
    ).inc()


def test_orchestration_histogram_observable():
    from app.observability.metrics import orchestration_profile_latency_ms
    orchestration_profile_latency_ms.observe(1.5)
