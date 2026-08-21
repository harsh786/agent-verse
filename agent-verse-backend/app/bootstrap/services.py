"""Bootstrap: service construction for AgentVerse.

All ``app.state.*`` assignments extracted from ``app/main.py``
so the application factory stays slim.  Receives the FastAPI app
instance and populates app.state with every service.

Note: ``create_app()`` in ``app/main.py`` currently builds all services
inline.  This module preserves the ``build_services`` signature for
future decomposition work and test compatibility, but delegates to
``create_app()`` so there is a single source of truth.  Calling
``build_services`` directly is a no-op that logs a warning.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def build_services(
    app: FastAPI,
    settings: Any,
    *,
    tenant_service: Any = None,
    goal_service: Any = None,
) -> None:
    """Construct all application services and store them on ``app.state``.

    .. deprecated::
        Service construction is performed inline by ``app.main.create_app()``.
        This function preserves the public signature for tests and future
        decomposition but does not duplicate the wiring logic.  It is a
        safe no-op: it only validates that ``app`` and ``settings`` are
        provided and logs a debug message.
    """
    if app is None or settings is None:
        raise ValueError("build_services requires both app and settings")
    logger.debug(
        "build_services_called",
        extra={"has_tenant_service": tenant_service is not None, "has_goal_service": goal_service is not None},  # noqa: E501
    )
