"""Auto-instrumentation wiring (FastAPI server spans + library instrumentors).

Verifies the previously-absent instrumentation the tracing docstring promised is
now actually applied, idempotently and fail-safe.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.observability import tracing


def test_fastapi_instrumentation_is_off_by_default() -> None:
    # OPT-IN: the FastAPI ASGI instrumentation is fragile on this app's routers
    # (crashes the CORS preflight), so instrument_app must NOT wire it by default.
    app = FastAPI()
    tracing.instrument_app(app)
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is False


def test_instrument_app_wires_fastapi_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as config_mod

    original = config_mod.get_settings
    monkeypatch.setattr(
        config_mod,
        "get_settings",
        lambda: original().model_copy(update={"otel_instrument_fastapi": True}),
    )
    app = FastAPI()
    tracing.instrument_app(app)
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True


def test_instrument_libraries_is_idempotent() -> None:
    # Must be safe to call repeatedly (create_app can run more than once in tests).
    tracing.instrument_libraries()
    tracing.instrument_libraries()  # second call must not raise


def test_instrument_app_never_raises_on_bad_input() -> None:
    # Fail-safe: a bad app object must not break startup.
    tracing.instrument_app(object())  # type: ignore[arg-type]
