"""Tests for per-plan autoscale desired-worker gauge."""


class TestAutoscaleGauge:
    def test_record_desired_workers_importable(self):
        from app.observability.metrics import record_desired_workers
        assert callable(record_desired_workers)

    def test_record_desired_workers_does_not_raise(self):
        from app.observability.metrics import record_desired_workers
        record_desired_workers(plan="free", count=2)
        record_desired_workers(plan="enterprise", count=10)

    def test_record_desired_workers_sets_gauge(self):
        """DESIRED_WORKERS gauge reflects the last set value."""
        from app.observability.metrics import DESIRED_WORKERS, record_desired_workers
        record_desired_workers(plan="starter", count=5)
        sample = DESIRED_WORKERS.labels(plan="starter")
        # Gauge stores current value — accessing _value is implementation detail
        # but acceptable in a unit test context
        assert sample._value.get() == 5.0

    def test_record_desired_workers_all_plans(self):
        """All four standard plan names can be recorded without error."""
        from app.observability.metrics import record_desired_workers
        for plan in ("free", "starter", "professional", "enterprise"):
            record_desired_workers(plan=plan, count=1)


class TestDesiredWorkersGaugeExists:
    def test_desired_workers_gauge_in_registry(self):
        """DESIRED_WORKERS Gauge is registered and exportable."""
        from app.observability.metrics import DESIRED_WORKERS, render_metrics
        body, ct = render_metrics()
        assert b"agentverse_desired_workers" in body
        assert DESIRED_WORKERS is not None
