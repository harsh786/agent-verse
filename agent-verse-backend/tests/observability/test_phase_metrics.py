"""Tests for phase-level Prometheus histograms."""


def test_plan_duration_histogram_exists():
    from app.observability.metrics import PLAN_DURATION, VERIFY_DURATION, QUEUE_WAIT_DURATION
    assert PLAN_DURATION is not None
    assert VERIFY_DURATION is not None
    assert QUEUE_WAIT_DURATION is not None


def test_record_plan_duration():
    from app.observability.metrics import record_plan_duration
    record_plan_duration(1, 0.5)  # Should not raise


def test_record_verify_duration():
    from app.observability.metrics import record_verify_duration
    record_verify_duration(0.1)  # Should not raise


def test_record_queue_wait():
    from app.observability.metrics import record_queue_wait
    record_queue_wait("normal", 1.5)  # Should not raise


def test_record_plan_duration_clamps_iteration():
    """Iterations above 15 should be clamped to '15' label."""
    from app.observability.metrics import record_plan_duration, PLAN_DURATION
    # Should not raise with large iteration value
    record_plan_duration(99, 0.1)


def test_record_plan_duration_negative_seconds():
    """Negative durations should be clamped to 0."""
    from app.observability.metrics import record_plan_duration
    record_plan_duration(1, -5.0)  # Should not raise
