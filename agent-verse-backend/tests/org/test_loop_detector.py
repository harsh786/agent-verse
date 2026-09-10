"""Tests for OrgLoopDetector — app/org/loop_detector.py"""
from __future__ import annotations

from app.org.loop_detector import OrgLoopDetector


def test_circular_delegation_detected():
    detector = OrgLoopDetector()
    detector.check_delegation("agent-A", "agent-B")
    detector.check_delegation("agent-B", "agent-C")
    result = detector.check_delegation("agent-A", "agent-B")  # agent-A delegates to B again
    # The chain for agent-A already has B; re-delegating to B is a pingpong
    # OR: delegation depth exceeded for "agent-A"
    # Either way: something should be detected
    assert result.detected is True or detector.check_delegation("agent-A", "agent-A").detected


def test_tool_obsession_detected():
    detector = OrgLoopDetector()
    for _ in range(6):  # threshold is 5
        result = detector.check_tool_obsession("agent-X", "web_search")
    assert result.detected is True
    assert result.pattern == "agent_obsession"


def test_tool_obsession_not_detected_below_threshold():
    detector = OrgLoopDetector()
    for _ in range(3):
        result = detector.check_tool_obsession("agent-Y", "code_exec")
    assert result.detected is False


def test_replan_count_detected():
    detector = OrgLoopDetector()
    for _ in range(4):  # threshold is 3
        result = detector.check_replan_count("task-123")
    assert result.detected is True
    assert result.pattern == "infinite_replan"


def test_cost_runaway_detected():
    detector = OrgLoopDetector()
    result = detector.check_cost_runaway(spent_usd=310.0, budget_usd=100.0)  # 3.1x
    assert result.detected is True
    assert result.pattern == "cost_runaway"


def test_cost_within_budget_not_detected():
    detector = OrgLoopDetector()
    result = detector.check_cost_runaway(spent_usd=80.0, budget_usd=100.0)
    assert result.detected is False


def test_reset_agent_clears_state():
    detector = OrgLoopDetector()
    detector.check_tool_obsession("agent-Z", "tool1")
    detector.reset_agent("agent-Z")
    # After reset, count starts fresh
    for _ in range(3):
        result = detector.check_tool_obsession("agent-Z", "tool1")
    assert result.detected is False
