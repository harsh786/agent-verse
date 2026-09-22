"""Tests for EmbeddingDriftMonitor (app/embedding/drift_monitor.py).

Previously zero test references existed for this module anywhere in the
repo. `EmbeddingDriftMonitor` is a small, stateless, side-effect-free
calculator — `measure()` buckets an average cosine similarity into a
`DriftSeverity`, and `drift_score()` derives a clamped [0, 1] drift score
from the same similarity. There is no persistence/alerting logic in the
module (no logger, no DB/Redis writes, no state) to exercise, so this file
covers the full actual surface: severity threshold boundaries, drift_score
clamping, and the no-drift baseline.
"""

from __future__ import annotations

import pytest

from app.embedding.drift_monitor import DriftSeverity, EmbeddingDriftMonitor


@pytest.fixture
def monitor() -> EmbeddingDriftMonitor:
    return EmbeddingDriftMonitor()


class TestDriftSeverityEnum:
    def test_severity_values(self) -> None:
        assert DriftSeverity.STABLE == "stable"
        assert DriftSeverity.LOW == "low"
        assert DriftSeverity.MEDIUM == "medium"
        assert DriftSeverity.HIGH == "high"
        assert DriftSeverity.CRITICAL == "critical"

    def test_severity_is_str_enum(self) -> None:
        # StrEnum members compare equal to plain strings and are usable
        # anywhere a str is expected (e.g. as a label/log field).
        assert isinstance(DriftSeverity.STABLE, str)


class TestMeasureNoDriftBaseline:
    def test_perfect_similarity_is_stable(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(1.0) == DriftSeverity.STABLE

    def test_high_similarity_is_stable(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(0.95) == DriftSeverity.STABLE

    def test_default_sample_size_does_not_affect_severity(
        self, monitor: EmbeddingDriftMonitor
    ) -> None:
        assert monitor.measure(0.9, sample_size=5) == monitor.measure(0.9, sample_size=100_000)


class TestMeasureSeverityBoundaries:
    """Boundaries are >= comparisons: 0.85 / 0.70 / 0.55 / 0.40."""

    @pytest.mark.parametrize(
        ("avg_similarity", "expected"),
        [
            (0.85, DriftSeverity.STABLE),
            (0.849999, DriftSeverity.LOW),
            (0.70, DriftSeverity.LOW),
            (0.699999, DriftSeverity.MEDIUM),
            (0.55, DriftSeverity.MEDIUM),
            (0.549999, DriftSeverity.HIGH),
            (0.40, DriftSeverity.HIGH),
            (0.399999, DriftSeverity.CRITICAL),
        ],
    )
    def test_boundary_is_inclusive_on_the_lower_bucket(
        self, monitor: EmbeddingDriftMonitor, avg_similarity: float, expected: DriftSeverity
    ) -> None:
        assert monitor.measure(avg_similarity) == expected

    def test_mid_stable_range(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(0.9) == DriftSeverity.STABLE

    def test_mid_low_range(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(0.77) == DriftSeverity.LOW

    def test_mid_medium_range(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(0.6) == DriftSeverity.MEDIUM

    def test_mid_high_range(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(0.45) == DriftSeverity.HIGH

    def test_zero_similarity_is_critical(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(0.0) == DriftSeverity.CRITICAL

    def test_very_low_similarity_is_critical(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(0.1) == DriftSeverity.CRITICAL


class TestMeasureOutOfRangeInputs:
    """The similarity input is not itself clamped by measure() — only bucketed —
    so out-of-range values (negative, or > 1.0 from a numerically noisy cosine
    computation) must still resolve to a sane severity rather than raising."""

    def test_negative_similarity_is_critical(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(-0.5) == DriftSeverity.CRITICAL

    def test_similarity_above_one_is_stable(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.measure(1.2) == DriftSeverity.STABLE


class TestDriftScoreClamping:
    def test_perfect_similarity_has_zero_drift(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.drift_score(1.0) == 0.0

    def test_zero_similarity_has_full_drift(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.drift_score(0.0) == 1.0

    def test_mid_similarity_drift_score(self, monitor: EmbeddingDriftMonitor) -> None:
        assert monitor.drift_score(0.7) == pytest.approx(0.3)

    def test_negative_similarity_clamps_to_one(self, monitor: EmbeddingDriftMonitor) -> None:
        # 1.0 - (-0.5) = 1.5, must clamp down to the normalized max of 1.0
        assert monitor.drift_score(-0.5) == 1.0

    def test_very_negative_similarity_clamps_to_one(
        self, monitor: EmbeddingDriftMonitor
    ) -> None:
        assert monitor.drift_score(-100.0) == 1.0

    def test_similarity_above_one_clamps_to_zero(self, monitor: EmbeddingDriftMonitor) -> None:
        # 1.0 - 1.2 = -0.2, must clamp up to the normalized min of 0.0
        assert monitor.drift_score(1.2) == 0.0

    def test_similarity_far_above_one_clamps_to_zero(
        self, monitor: EmbeddingDriftMonitor
    ) -> None:
        assert monitor.drift_score(50.0) == 0.0

    def test_drift_score_always_in_unit_interval(self, monitor: EmbeddingDriftMonitor) -> None:
        for value in (-10.0, -1.0, -0.001, 0.0, 0.3, 0.6, 0.999, 1.0, 1.001, 10.0):
            score = monitor.drift_score(value)
            assert 0.0 <= score <= 1.0


class TestMeasureAndDriftScoreAgreement:
    """measure() and drift_score() are both derived from avg_similarity and
    should tell a consistent story: a higher drift_score should never map to
    a *less* severe bucket than a lower drift_score."""

    _SEVERITY_ORDER = {
        DriftSeverity.STABLE: 0,
        DriftSeverity.LOW: 1,
        DriftSeverity.MEDIUM: 2,
        DriftSeverity.HIGH: 3,
        DriftSeverity.CRITICAL: 4,
    }

    @pytest.mark.parametrize(
        "similarities",
        [(1.0, 0.9), (0.9, 0.7), (0.7, 0.5), (0.5, 0.3), (0.3, 0.0)],
    )
    def test_severity_monotonically_increases_as_similarity_drops(
        self, monitor: EmbeddingDriftMonitor, similarities: tuple[float, float]
    ) -> None:
        higher_sim, lower_sim = similarities
        higher_sim_severity = self._SEVERITY_ORDER[monitor.measure(higher_sim)]
        lower_sim_severity = self._SEVERITY_ORDER[monitor.measure(lower_sim)]
        assert lower_sim_severity >= higher_sim_severity

        higher_sim_score = monitor.drift_score(higher_sim)
        lower_sim_score = monitor.drift_score(lower_sim)
        assert lower_sim_score >= higher_sim_score
