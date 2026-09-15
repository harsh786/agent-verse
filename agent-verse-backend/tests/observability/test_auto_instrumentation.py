"""Auto-instrumentation wiring (FastAPI server spans + library instrumentors).

Verifies the previously-absent instrumentation the tracing docstring promised is
now actually applied, idempotently and fail-safe.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.observability import tracing


def test_instrument_app_marks_fastapi_instrumented() -> None:
    app = FastAPI()
    tracing.instrument_app(app)
    # FastAPIInstrumentor stamps this attribute when it wires the ASGI middleware.
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True


def test_instrument_libraries_is_idempotent() -> None:
    # Must be safe to call repeatedly (create_app can run more than once in tests).
    tracing.instrument_libraries()
    tracing.instrument_libraries()  # second call must not raise


def test_instrument_app_never_raises_on_bad_input() -> None:
    # Fail-safe: a bad app object must not break startup.
    tracing.instrument_app(object())  # type: ignore[arg-type]
