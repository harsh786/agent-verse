"""Comprehensive tests for app/intelligence/explainability.py — targeting 100% coverage."""
from __future__ import annotations

import pytest

from app.intelligence.explainability import DecisionTrace


class TestDecisionTrace:
    def test_basic_construction(self):
        trace = DecisionTrace(
            action="call_github_api",
            reasoning="Need to list open PRs to assess backlog",
            evidence=["PR count: 12", "Oldest PR: 45 days ago"],
            alternatives=["Skip and estimate", "Use cached data"],
            confidence=0.87,
        )
        assert trace.action == "call_github_api"
        assert trace.reasoning == "Need to list open PRs to assess backlog"
        assert len(trace.evidence) == 2
        assert len(trace.alternatives) == 2
        assert trace.confidence == pytest.approx(0.87)

    def test_trace_id_auto_generated(self):
        trace = DecisionTrace(
            action="read_file",
            reasoning="Need file contents",
            evidence=[],
            alternatives=[],
            confidence=1.0,
        )
        assert trace.trace_id is not None
        assert len(trace.trace_id) == 32  # uuid4().hex is 32 chars

    def test_trace_id_unique_per_instance(self):
        t1 = DecisionTrace(action="a", reasoning="r", evidence=[], alternatives=[], confidence=0.5)
        t2 = DecisionTrace(action="a", reasoning="r", evidence=[], alternatives=[], confidence=0.5)
        assert t1.trace_id != t2.trace_id

    def test_explicit_trace_id_preserved(self):
        trace = DecisionTrace(
            action="deploy",
            reasoning="Deploy to staging",
            evidence=[],
            alternatives=[],
            confidence=0.9,
            trace_id="my-custom-id",
        )
        assert trace.trace_id == "my-custom-id"

    def test_to_dict_contains_all_keys(self):
        trace = DecisionTrace(
            action="run_test_suite",
            reasoning="Verify correctness before deployment",
            evidence=["All tests green", "Coverage > 80%"],
            alternatives=["Deploy without tests", "Defer testing"],
            confidence=0.95,
        )
        d = trace.to_dict()
        assert "trace_id" in d
        assert "action" in d
        assert "reasoning" in d
        assert "evidence" in d
        assert "alternatives" in d
        assert "confidence" in d

    def test_to_dict_values_match_fields(self):
        trace = DecisionTrace(
            action="close_issue",
            reasoning="Issue resolved",
            evidence=["Fix merged", "CI passed"],
            alternatives=["Keep open for monitoring"],
            confidence=0.78,
            trace_id="abc123",
        )
        d = trace.to_dict()
        assert d["trace_id"] == "abc123"
        assert d["action"] == "close_issue"
        assert d["reasoning"] == "Issue resolved"
        assert d["evidence"] == ["Fix merged", "CI passed"]
        assert d["alternatives"] == ["Keep open for monitoring"]
        assert d["confidence"] == pytest.approx(0.78)

    def test_empty_lists_in_to_dict(self):
        trace = DecisionTrace(
            action="noop",
            reasoning="Nothing to do",
            evidence=[],
            alternatives=[],
            confidence=0.0,
        )
        d = trace.to_dict()
        assert d["evidence"] == []
        assert d["alternatives"] == []

    def test_confidence_boundary_zero(self):
        trace = DecisionTrace(
            action="skip", reasoning="uncertain", evidence=[], alternatives=[], confidence=0.0
        )
        assert trace.confidence == 0.0
        assert trace.to_dict()["confidence"] == 0.0

    def test_confidence_boundary_one(self):
        trace = DecisionTrace(
            action="certain", reasoning="definitive", evidence=[], alternatives=[], confidence=1.0
        )
        assert trace.confidence == 1.0
        assert trace.to_dict()["confidence"] == 1.0

    def test_to_dict_is_dict_type(self):
        trace = DecisionTrace(
            action="x", reasoning="y", evidence=[], alternatives=[], confidence=0.5
        )
        assert isinstance(trace.to_dict(), dict)

    def test_multiple_evidence_items(self):
        evidence = [f"fact_{i}" for i in range(10)]
        trace = DecisionTrace(
            action="analyze",
            reasoning="comprehensive analysis",
            evidence=evidence,
            alternatives=[],
            confidence=0.9,
        )
        assert trace.to_dict()["evidence"] == evidence

    def test_multiple_alternatives(self):
        alternatives = ["option_a", "option_b", "option_c", "option_d"]
        trace = DecisionTrace(
            action="choose",
            reasoning="evaluated options",
            evidence=[],
            alternatives=alternatives,
            confidence=0.6,
        )
        assert trace.to_dict()["alternatives"] == alternatives

    def test_action_with_special_characters(self):
        trace = DecisionTrace(
            action="call_api(endpoint='/v2/issues', method='GET')",
            reasoning="Query issues endpoint",
            evidence=[],
            alternatives=[],
            confidence=0.88,
        )
        d = trace.to_dict()
        assert "call_api" in d["action"]
