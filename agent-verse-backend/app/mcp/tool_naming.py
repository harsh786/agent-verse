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
    "GovernedToolName",
    "connection_slug",
    "governance_names",
    "governed_tool_name",
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


class GovernedToolName(str):
    """A tool name that also knows every name governance may address it by.

    Grants, tool policies, the permission matrix and per-agent permissions are
    written against tool names. For a tool offered as ``orders_db__mongodb_find``
    a rule may name the bare tool (``mongodb_find`` — any connection the agent
    may use), the connection-qualified tool (``orders_db__mongodb_find``) or the
    connection id (``builtin-mongodb:orders-db/mongodb_find``). It compares and
    hashes as the plain name; ``names`` lists those forms, most specific first.
    """

    names: tuple[str, ...]

    def __new__(cls, name: str, names: Iterable[str] = ()) -> GovernedToolName:
        obj = super().__new__(cls, name)
        obj.names = tuple(dict.fromkeys([*[n for n in names if n], str(name)]))
        return obj

    def __reduce__(self) -> tuple[Any, ...]:
        return (GovernedToolName, (str(self), self.names))


def governance_names(tool_name: str) -> tuple[str, ...]:
    """Every name governance may match ``tool_name`` by, most specific first."""
    names = getattr(tool_name, "names", None)
    return tuple(names) if names else (str(tool_name),)


def governed_tool_name(name: str, *, server_id: str = "", server_name: str = "") -> str:
    """``name`` carrying its connection-qualified and connection-id forms."""
    bare = strip_connection_prefix(name, server_name) if server_name else name
    forms: list[str] = []
    if server_id:
        forms.append(f"{server_id}/{bare}")
    if server_name:
        forms.append(qualified_tool_name(server_name, bare))
    forms.append(bare)
    return GovernedToolName(name, forms)


def qualify_colliding_tools[T](tools: Iterable[T]) -> list[T]:
    """Rename tools whose name is exposed by more than one connection (in place).

    Every tool's name also carries its governance forms (see GovernedToolName),
    so a grant / policy / permission naming the bare tool, the qualified tool or
    the connection id is matched correctly, colliding or not.
    """
    items = list(tools)
    servers_by_name: dict[str, set[str]] = {}
    for tool in items:
        name = str(getattr(tool, "name", "") or "")
        servers_by_name.setdefault(name, set()).add(str(getattr(tool, "server_id", "")))
    for tool in items:
        name = str(getattr(tool, "name", "") or "")
        if not name:
            continue
        server_id = str(getattr(tool, "server_id", "") or "")
        label = str(getattr(tool, "server_name", "") or server_id)
        tool_any: Any = tool
        if len(servers_by_name.get(name, ())) > 1:
            name = qualified_tool_name(label, name)
        tool_any.name = governed_tool_name(name, server_id=server_id, server_name=label)
    return items


def strip_connection_prefix(tool_name: str, connection_name: str) -> str:
    """The real tool name for a call dispatched to the connection ``connection_name``."""
    if _SEP not in tool_name:
        return tool_name
    _, _, bare = tool_name.partition(_SEP)
    if bare and tool_name == qualified_tool_name(connection_name, bare):
        return bare
    return tool_name
