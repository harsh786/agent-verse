"""Tests for app.tool_runtime.tool_trace — per-goal tool-call observability trace."""
from __future__ import annotations

from app.tool_runtime.tool_trace import ToolCallRecord, ToolTrace


def test_tool_call_record_defaults() -> None:
    rec = ToolCallRecord(tool_name="search", success=True, latency_ms=42.0)
    assert rec.error == ""
    assert rec.args_summary == ""


def test_tool_trace_init_sets_goal_id_and_empty_calls() -> None:
    trace = ToolTrace(goal_id="g-1")
    assert trace.goal_id == "g-1"
    assert trace.calls == []


def test_tool_trace_record_appends_call() -> None:
    trace = ToolTrace(goal_id="g-1")
    trace.record("search", success=True, latency_ms=10.0)
    assert len(trace.calls) == 1
    call = trace.calls[0]
    assert call.tool_name == "search"
    assert call.success is True
    assert call.latency_ms == 10.0
    assert call.error == ""
    assert call.args_summary == ""


def test_tool_trace_record_with_error_and_args_summary() -> None:
    trace = ToolTrace(goal_id="g-1")
    trace.record(
        "web_fetch",
        success=False,
        latency_ms=500.0,
        error="timeout",
        args_summary="url=https://example.com",
    )
    call = trace.calls[0]
    assert call.success is False
    assert call.error == "timeout"
    assert call.args_summary == "url=https://example.com"


def test_success_rate_empty_calls_returns_one() -> None:
    trace = ToolTrace(goal_id="g-1")
    assert trace.success_rate == 1.0


def test_success_rate_all_success() -> None:
    trace = ToolTrace(goal_id="g-1")
    trace.record("a", success=True, latency_ms=1.0)
    trace.record("b", success=True, latency_ms=2.0)
    assert trace.success_rate == 1.0


def test_success_rate_mixed() -> None:
    trace = ToolTrace(goal_id="g-1")
    trace.record("a", success=True, latency_ms=1.0)
    trace.record("b", success=False, latency_ms=2.0)
    trace.record("c", success=True, latency_ms=3.0)
    assert trace.success_rate == 2 / 3


def test_success_rate_all_failures() -> None:
    trace = ToolTrace(goal_id="g-1")
    trace.record("a", success=False, latency_ms=1.0)
    assert trace.success_rate == 0.0


def test_to_dict_structure() -> None:
    trace = ToolTrace(goal_id="g-42")
    trace.record("search", success=True, latency_ms=15.5)
    trace.record("write", success=False, latency_ms=99.9, error="denied")

    data = trace.to_dict()
    assert data["goal_id"] == "g-42"
    assert data["call_count"] == 2
    assert data["success_rate"] == 0.5
    assert data["calls"] == [
        {"tool": "search", "success": True, "latency_ms": 15.5},
        {"tool": "write", "success": False, "latency_ms": 99.9},
    ]


def test_to_dict_empty_trace() -> None:
    trace = ToolTrace(goal_id="g-empty")
    data = trace.to_dict()
    assert data["call_count"] == 0
    assert data["success_rate"] == 1.0
    assert data["calls"] == []
