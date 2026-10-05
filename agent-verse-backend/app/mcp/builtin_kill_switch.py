"""Operator kill switches for built-in MCP connectors (NF-13).

The MongoDB ingestion connector has a kill switch (TG-15,
``INGESTION_CONNECTOR_MONGODB_ENABLED``); the MCP built-in that runs tenant
queries against tenant MongoDB servers had none, so an operator could not turn
the driver path off without a deploy.

``MCP_CONNECTOR_MONGODB_ENABLED=false`` now refuses, for the ``builtin-mongodb``
type, new connections and edits (422), the connector test, and every tool call
(the handler returns ``status: connector_disabled`` before any client exists),
on every replica that reads the setting. Unreadable settings fail closed.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

# builtin type -> Settings attribute (True = enabled)
_KILL_SWITCHES: dict[str, str] = {
    "builtin-mongodb": "mcp_connector_mongodb_enabled",
}

DISABLED_STATUS = "connector_disabled"


class BuiltinConnectorDisabledError(RuntimeError):
    """The operator switched this built-in connector type off."""


def _canonical(builtin_type: str) -> str:
    # A connection id ("builtin-mongodb:orders-db") names its type before ':'.
    return (builtin_type or "").split(":", 1)[0].strip()


def disabled_reason(builtin_type: str) -> str | None:
    """Why ``builtin_type`` is switched off, or ``None`` when it may run."""
    flag = _KILL_SWITCHES.get(_canonical(builtin_type))
    if flag is None:
        return None
    try:
        from app.core.config import get_settings

        enabled = bool(getattr(get_settings(), flag))
    except Exception as exc:  # fail closed: an unreadable switch is "off"
        _log.error("builtin_kill_switch_unreadable flag=%s: %s", flag, exc)
        enabled = False
    if enabled:
        return None
    return (
        f"The {_canonical(builtin_type)} connector is disabled by the operator "
        f"({flag.upper()}=false)."
    )


def assert_builtin_enabled(builtin_type: str) -> None:
    reason = disabled_reason(builtin_type)
    if reason is not None:
        raise BuiltinConnectorDisabledError(reason)
