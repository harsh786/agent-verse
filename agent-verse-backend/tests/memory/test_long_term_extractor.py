"""Tests for app.memory.long_term_extractor: typed evidence-only LTM extraction.

Covers the LongTermCandidate model's validation (immutability, extra="forbid",
field bounds) and the validate_extraction() gate that enforces evidence and a
minimum confidence threshold before a candidate is allowed into long-term memory.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.memory.long_term_extractor import LongTermCandidate, validate_extraction


def _candidate(**overrides: object) -> LongTermCandidate:
    defaults: dict[str, object] = {
        "safe_summary": "User prefers dark mode.",
        "evidence_refs": ("msg-1", "msg-2"),
        "confidence": 8_000,
        "classification": "preference",
    }
    defaults.update(overrides)
    return LongTermCandidate(**defaults)  # type: ignore[arg-type]


class TestLongTermCandidateModel:
    def test_valid_candidate_constructs(self):
        candidate = _candidate()
        assert candidate.safe_summary == "User prefers dark mode."
        assert candidate.evidence_refs == ("msg-1", "msg-2")
        assert candidate.confidence == 8_000
        assert candidate.classification == "preference"
        assert candidate.contradiction_keys == ()

    def test_contradiction_keys_default_and_explicit(self):
        assert _candidate().contradiction_keys == ()
        explicit = _candidate(contradiction_keys=("key-a", "key-b"))
        assert explicit.contradiction_keys == ("key-a", "key-b")

    def test_frozen_model_rejects_mutation(self):
        candidate = _candidate()
        with pytest.raises(ValidationError):
            candidate.confidence = 100  # type: ignore[misc]

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            LongTermCandidate(
                safe_summary="x",
                evidence_refs=("e1",),
                confidence=9000,
                classification="fact",
                unexpected="nope",  # type: ignore[call-arg]
            )

    def test_safe_summary_max_length_enforced(self):
        with pytest.raises(ValidationError):
            _candidate(safe_summary="x" * 2_001)

    def test_safe_summary_at_max_length_is_allowed(self):
        candidate = _candidate(safe_summary="x" * 2_000)
        assert len(candidate.safe_summary) == 2_000

    @pytest.mark.parametrize("bad_confidence", [-1, 10_001])
    def test_confidence_out_of_bounds_rejected(self, bad_confidence):
        with pytest.raises(ValidationError):
            _candidate(confidence=bad_confidence)

    @pytest.mark.parametrize("edge_confidence", [0, 10_000])
    def test_confidence_bounds_are_inclusive(self, edge_confidence):
        candidate = _candidate(confidence=edge_confidence)
        assert candidate.confidence == edge_confidence


class TestValidateExtraction:
    def test_accepts_candidate_with_evidence_and_high_confidence(self):
        candidate = _candidate(confidence=5_000)
        result = validate_extraction(candidate)
        assert result is candidate

    def test_rejects_candidate_without_evidence(self):
        candidate = _candidate(evidence_refs=())
        with pytest.raises(ValueError, match="requires evidence"):
            validate_extraction(candidate)

    def test_rejects_candidate_below_confidence_threshold(self):
        candidate = _candidate(confidence=4_999)
        with pytest.raises(ValueError, match="confidence below threshold"):
            validate_extraction(candidate)

    def test_confidence_exactly_at_threshold_passes(self):
        candidate = _candidate(confidence=5_000)
        assert validate_extraction(candidate) is candidate

    def test_evidence_check_runs_before_confidence_check(self):
        # No evidence AND low confidence -> the evidence error must win, since
        # a candidate lacking evidence should never be described by its
        # (irrelevant) confidence score in the error message.
        candidate = _candidate(evidence_refs=(), confidence=0)
        with pytest.raises(ValueError, match="requires evidence"):
            validate_extraction(candidate)
