"""Regression: ~20 routers were included inside ``try/except: logger.warning``,
so an import error silently dropped an advertised API while the app booted.
Now: logged at ERROR, recorded on ``app.state.failed_routers``, and fatal in
production.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from app.bootstrap.routers import _GUARDED_ROUTERS, _include_guarded


def test_failure_is_recorded_and_logged_at_error_outside_production() -> None:
    app = FastAPI()
    logger = MagicMock()
    _include_guarded(
        app, SimpleNamespace(is_production=False), logger, "ghost_router",
        "app.does_not_exist", "router", None,
    )
    assert app.state.failed_routers == ["ghost_router"]
    logger.error.assert_called_once()
    assert logger.error.call_args.kwargs["router"] == "ghost_router"


def test_failure_aborts_startup_in_production() -> None:
    app = FastAPI()
    with pytest.raises(RuntimeError, match="ghost_router"):
        _include_guarded(
            app, SimpleNamespace(is_production=True), MagicMock(), "ghost_router",
            "app.does_not_exist", "router", None,
        )


def test_every_guarded_router_imports() -> None:
    import importlib

    for name, module, attr, _prefix in _GUARDED_ROUTERS:
        assert getattr(importlib.import_module(module), attr) is not None, name


def test_create_app_registers_every_guarded_router() -> None:
    from app.main import create_app

    app = create_app()
    assert list(getattr(app.state, "failed_routers", []) or []) == []
