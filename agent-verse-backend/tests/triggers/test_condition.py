"""Tests for CEL evaluator, CounterThreshold, WindowAggregate, CompoundTrigger."""
from __future__ import annotations

import time
import pytest

from app.triggers.condition.evaluator import (
    CELEvaluator,
    TemplateRenderer,
    CounterThresholdEvaluator,
    WindowAggregateEvaluator,
    CompoundTriggerEvaluator,
)


# ── CELEvaluator ──────────────────────────────────────────────────────────────

def test_cel_empty_expression_true():
    ev = CELEvaluator()
    assert ev.evaluate("", {}) is True


def test_cel_empty_whitespace_true():
    ev = CELEvaluator()
    assert ev.evaluate("   ", {}) is True


def test_cel_blocked_attributes_excluded():
    ev = CELEvaluator()
    # Should not raise; blocked attr filtered from payload
    result = ev.evaluate("", {"__class__": "bad", "safe": "ok"})
    assert result is True


# ── TemplateRenderer ──────────────────────────────────────────────────────────

def test_template_payload_field():
    r = TemplateRenderer()
    assert r.render("Hello {{payload.name}}", {"name": "World"}) == "Hello World"


def test_template_extra_var():
    r = TemplateRenderer()
    assert r.render("Type: {{trigger_type}}", {}, trigger_type="goal_completed") == "Type: goal_completed"


def test_template_missing_field_empty():
    r = TemplateRenderer()
    assert r.render("{{payload.missing}}", {}) == ""


def test_template_max_length():
    r = TemplateRenderer()
    assert len(r.render("x" * 5000, {})) == 2048


def test_template_direct_payload_field():
    r = TemplateRenderer()
    assert r.render("ID={{invoice_id}}", {"invoice_id": "INV-001"}) == "ID=INV-001"


def test_template_multiple_substitutions():
    r = TemplateRenderer()
    result = r.render("{{payload.a}} and {{payload.b}}", {"a": "foo", "b": "bar"})
    assert result == "foo and bar"


# ── CounterThresholdEvaluator ─────────────────────────────────────────────────

def test_counter_below_threshold_false():
    ev = CounterThresholdEvaluator()
    ev.record("key1")
    ev.record("key1")
    assert ev.check("key1", threshold=5) is False


def test_counter_at_threshold_true():
    ev = CounterThresholdEvaluator()
    for _ in range(3):
        ev.record("key2")
    assert ev.check("key2", threshold=3) is True


def test_counter_above_threshold_true():
    ev = CounterThresholdEvaluator()
    for _ in range(10):
        ev.record("key3")
    assert ev.check("key3", threshold=5) is True


def test_counter_no_events_false():
    ev = CounterThresholdEvaluator()
    assert ev.check("empty_key", threshold=1) is False


def test_counter_reset_clears():
    ev = CounterThresholdEvaluator()
    for _ in range(5):
        ev.record("resetkey")
    ev.reset("resetkey")
    assert ev.check("resetkey", threshold=1) is False


def test_counter_different_keys_isolated():
    ev = CounterThresholdEvaluator()
    for _ in range(5):
        ev.record("key_a")
    assert ev.check("key_b", threshold=1) is False


# ── WindowAggregateEvaluator ──────────────────────────────────────────────────

def test_window_avg_above_threshold():
    ev = WindowAggregateEvaluator()
    for v in [80, 90, 95, 85]:
        ev.record("cpu", v)
    assert ev.check("cpu", threshold=85.0, aggregation="avg", comparison=">=") is True


def test_window_avg_below_threshold_false():
    ev = WindowAggregateEvaluator()
    for v in [10, 20, 30]:
        ev.record("load", v)
    assert ev.check("load", threshold=50.0, aggregation="avg", comparison=">") is False


def test_window_max_check():
    ev = WindowAggregateEvaluator()
    for v in [1, 2, 99, 3]:
        ev.record("temp", v)
    assert ev.check("temp", threshold=90.0, aggregation="max", comparison=">") is True


def test_window_sum_check():
    ev = WindowAggregateEvaluator()
    for v in [10, 20, 30]:
        ev.record("cost", v)
    assert ev.check("cost", threshold=59.0, aggregation="sum", comparison=">") is True


def test_window_no_data_false():
    ev = WindowAggregateEvaluator()
    assert ev.check("no_data", threshold=50.0) is False


def test_window_count_aggregation():
    ev = WindowAggregateEvaluator()
    for _ in range(5):
        ev.record("events", 1.0)
    assert ev.check("events", threshold=3.0, aggregation="count", comparison=">") is True


# ── CompoundTriggerEvaluator ──────────────────────────────────────────────────

def test_compound_and_all_true():
    ev = CompoundTriggerEvaluator()
    assert ev.evaluate_compound("AND", {"a": True, "b": True, "c": True}) is True


def test_compound_and_one_false():
    ev = CompoundTriggerEvaluator()
    assert ev.evaluate_compound("AND", {"a": True, "b": False}) is False


def test_compound_or_one_true():
    ev = CompoundTriggerEvaluator()
    assert ev.evaluate_compound("OR", {"a": False, "b": True}) is True


def test_compound_or_all_false():
    ev = CompoundTriggerEvaluator()
    assert ev.evaluate_compound("OR", {"a": False, "b": False}) is False


def test_compound_not_first():
    ev = CompoundTriggerEvaluator()
    assert ev.evaluate_compound("NOT", {"a": True}) is False
    assert ev.evaluate_compound("NOT", {"a": False}) is True


def test_compound_empty_false():
    ev = CompoundTriggerEvaluator()
    assert ev.evaluate_compound("AND", {}) is False


def test_compound_default_and():
    ev = CompoundTriggerEvaluator()
    # Unknown logic defaults to AND
    assert ev.evaluate_compound("UNKNOWN", {"a": True, "b": True}) is True
