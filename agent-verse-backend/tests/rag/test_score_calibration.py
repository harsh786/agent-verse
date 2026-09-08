"""Probability calibration for retrieval / rerank scores (Coverage-Matrix row 6)."""

from __future__ import annotations

import math

import pytest

from app.rag.score_calibration import calibrate_scores, retrieval_confidence


@pytest.mark.parametrize("method", ["minmax", "softmax", "logistic"])
def test_calibration_is_bounded_unit_interval(method: str) -> None:
    raw = [3.2, -1.0, 0.4, 9.7, -8.1]
    calibrated = calibrate_scores(raw, method=method)  # type: ignore[arg-type]
    assert len(calibrated) == len(raw)
    assert all(0.0 <= value <= 1.0 for value in calibrated)
    assert all(math.isfinite(value) for value in calibrated)


@pytest.mark.parametrize("method", ["minmax", "softmax", "logistic"])
def test_calibration_is_monotonic(method: str) -> None:
    # A descending raw ordering must stay descending after calibration.
    raw = [0.95, 0.80, 0.55, 0.30, 0.05]
    calibrated = calibrate_scores(raw, method=method)  # type: ignore[arg-type]
    assert calibrated == sorted(calibrated, reverse=True)
    # Strictly separated inputs must not collapse to identical confidences.
    assert calibrated[0] > calibrated[-1]


def test_minmax_maps_extremes_to_endpoints() -> None:
    calibrated = calibrate_scores([0.2, 0.8, 0.5], method="minmax")
    assert calibrated[1] == pytest.approx(1.0)
    assert calibrated[0] == pytest.approx(0.0)


def test_softmax_normalises_to_distribution() -> None:
    calibrated = calibrate_scores([1.0, 2.0, 3.0], method="softmax")
    assert sum(calibrated) == pytest.approx(1.0)
    assert calibrated[2] > calibrated[1] > calibrated[0]


def test_logistic_is_absolute_not_merely_relative() -> None:
    # Logistic calibration keys off absolute magnitude, so a strong set lands
    # near 1 and a weak set near 0 even though both are internally ordered.
    strong = calibrate_scores([0.95, 0.9], method="logistic")
    weak = calibrate_scores([0.12, 0.05], method="logistic")
    assert all(value > 0.8 for value in strong)
    assert all(value < 0.2 for value in weak)


def test_empty_and_degenerate_inputs() -> None:
    assert calibrate_scores([], method="minmax") == []
    # All-equal inputs cannot be spread — return a neutral confidence.
    calibrated = calibrate_scores([0.5, 0.5, 0.5], method="minmax")
    assert calibrated == [0.5, 0.5, 0.5]


def test_calibration_rejects_non_finite() -> None:
    with pytest.raises(ValueError):
        calibrate_scores([float("nan"), 0.1], method="minmax")


def test_retrieval_confidence_discriminates_relevant_from_irrelevant() -> None:
    relevant = retrieval_confidence([0.92, 0.88, 0.81])
    irrelevant = retrieval_confidence([0.12, 0.08, 0.05])
    assert 0.0 <= irrelevant <= 1.0
    assert 0.0 <= relevant <= 1.0
    assert relevant > 0.7
    assert irrelevant < 0.3
    assert relevant > irrelevant


def test_retrieval_confidence_empty_is_zero() -> None:
    assert retrieval_confidence([]) == 0.0


def test_retrieval_confidence_uses_top_k_only() -> None:
    # A couple of strong hits should not be drowned out by a long weak tail.
    scores = [0.95, 0.93] + [0.02] * 20
    assert retrieval_confidence(scores, top_k=2) > 0.7
