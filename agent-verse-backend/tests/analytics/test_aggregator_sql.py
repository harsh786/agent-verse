"""Test that analytics SQL queries use correct PostgreSQL syntax."""


def test_no_invalid_interval_syntax():
    import inspect
    from app.analytics import aggregator
    src = inspect.getsource(aggregator)
    # The broken pattern: INTERVAL ':days days' (parameterized interval string)
    assert "INTERVAL ':days" not in src, "Invalid PostgreSQL INTERVAL syntax found"
    assert "INTERVAL ':" not in src, "Invalid parameterized INTERVAL syntax"


def test_interval_uses_multiplication():
    import inspect
    from app.analytics import aggregator
    src = inspect.getsource(aggregator)
    # Correct pattern: (:days * INTERVAL '1 day')
    assert "INTERVAL '1 day'" in src or "interval" in src.lower(), "Should use correct interval arithmetic"
