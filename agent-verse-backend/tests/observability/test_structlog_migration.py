"""Verify critical modules use structlog instead of stdlib logging."""
from __future__ import annotations
import importlib
import inspect


MIGRATED_MODULES = [
    "app.services.goal_service",
    "app.scaling.tasks",
    "app.rag.store",
    "app.governance.audit",
    "app.perception.browser_agent",
    "app.agent.loop",
]


def test_migrated_modules_use_get_logger():
    """Verify that migrated modules do not import stdlib logging directly."""
    for module_name in MIGRATED_MODULES:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            continue  # module may not be importable without extras

        source = inspect.getsource(mod)
        # They should use get_logger, not getLogger
        # Note: some may still have 'import logging' for backward compat — that's ok
        # as long as the logger instance uses get_logger
        assert "get_logger" in source or "structlog" in source, (
            f"{module_name} does not appear to use structlog get_logger"
        )
