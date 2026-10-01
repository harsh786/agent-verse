"""Distinct tool names when several connections expose the same tool.

A tenant may hold several connections of one built-in type ("orders-db" and
"analytics-db", both MongoDB). Each exposes ``mongodb_find``. Offered to a model
under the bare name, the two collide: duplicate function names (which provider
APIs reject), and lookups that silently pick whichever was discovered first —
i.e. a call meant for one database runs against the other.

:func:`qualify_colliding_tools` renames ONLY the colliding tools to
``<connection_slug>__<tool>`` (a valid function name, <= 64 chars); unique tools
keep their bare names. :func:`strip_connection_prefix` maps the qualified name
back to the tool's real name when the call is dispatched to that connection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

__all__ = [
    "connection_slug",
    "qualified_tool_name",
    "qualify_colliding_tools",
    "strip_connection_prefix",
]

_SEP = "__"
_MAX = 64


def connection_slug(name: str) -> str:
    """Function-name-safe slug of a connection's display name."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "connection"


def qualified_tool_name(connection_name: str, tool_name: str) -> str:
    room = max(1, _MAX - len(_SEP) - len(tool_name))
    prefix = connection_slug(connection_name)[:room].rstrip("_") or "c"
    return f"{prefix}{_SEP}{tool_name}"


def qualify_colliding_tools[T](tools: Iterable[T]) -> list[T]:
    """Rename tools whose name is exposed by more than one connection (in place)."""
    items = list(tools)
    servers_by_name: dict[str, set[str]] = {}
    for tool in items:
        name = str(getattr(tool, "name", "") or "")
        servers_by_name.setdefault(name, set()).add(str(getattr(tool, "server_id", "")))
    for tool in items:
        name = str(getattr(tool, "name", "") or "")
        if name and len(servers_by_name.get(name, ())) > 1:
            label = str(getattr(tool, "server_name", "") or getattr(tool, "server_id", ""))
            tool_any: Any = tool
            tool_any.name = qualified_tool_name(label, name)
    return items


def strip_connection_prefix(tool_name: str, connection_name: str) -> str:
    """The real tool name for a call dispatched to the connection ``connection_name``."""
    if _SEP not in tool_name:
        return tool_name
    _, _, bare = tool_name.partition(_SEP)
    if bare and tool_name == qualified_tool_name(connection_name, bare):
        return bare
    return tool_name
