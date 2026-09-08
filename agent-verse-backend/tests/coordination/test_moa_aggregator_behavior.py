"""Behavioral tests for MoA aggregation-input building and unique-deployment quorum."""

from __future__ import annotations

import pytest

from app.coordination.moa.aggregator import build_aggregation_input
from app.coordination.moa.quorum import evaluate_quorum
from tests.coordination.test_moa_quorum import _proposal


def test_aggregator_flattens_evidence_and_excludes_all_invalid() -> None:
    good_one = _proposal("one", "d1").model_copy(
        update={"evidence_references": ("ev://a", "ev://b")}
    )
    good_two = _proposal("two", "d2").model_copy(update={"evidence_references": ("ev://c",)})
    result = build_aggregation_input((good_one, good_two), maximum_characters=4_000)
    assert result.included_proposal_ids == ("one", "two")
    # Evidence from every included proposal is flattened, preserving order.
    assert result.evidence_references == ("ev://a", "ev://b", "ev://c")
    assert "proposal=one" in result.prompt and "proposal=two" in result.prompt


def test_aggregator_excludes_invalid_and_records_reasons() -> None:
    bad = _proposal("bad", "d1", valid=False).model_copy(
        update={"rejection_reason": "schema_invalid"}
    )
    unlabelled = _proposal("noreason", "d2", valid=False)
    result = build_aggregation_input((bad, unlabelled), maximum_characters=2_000)
    assert result.included_proposal_ids == ()
    assert result.prompt == ""
    assert ("bad", "schema_invalid") in result.excluded
    # A rejected proposal without a stated reason still gets a placeholder reason.
    assert ("noreason", "invalid") in result.excluded


def test_aggregator_enforces_positive_bound_and_context_ceiling() -> None:
    with pytest.raises(ValueError, match="bound must be positive"):
        build_aggregation_input((), maximum_characters=0)
    big = _proposal("big", "d1").model_copy(update={"safe_excerpt": "x" * 500})
    with pytest.raises(ValueError, match="exceeds bound"):
        build_aggregation_input((big,), maximum_characters=50)


def test_quorum_rejects_non_positive_requirement() -> None:
    with pytest.raises(ValueError, match="quorum must be positive"):
        evaluate_quorum((_proposal("a", "d1"),), required=0)


def test_quorum_met_exactly_at_boundary_ignores_invalid_duplicates() -> None:
    result = evaluate_quorum(
        (
            _proposal("a", "d1"),
            _proposal("b", "d2"),
            _proposal("c", "d2"),  # duplicate deployment, does not add to the count
            _proposal("bad", "d3", valid=False),  # invalid, excluded entirely
        ),
        required=2,
    )
    assert result.met
    assert result.valid_unique_deployments == ("d1", "d2")
    # d3 came only from an invalid proposal, so it is not counted.
    assert "d3" not in result.valid_unique_deployments
