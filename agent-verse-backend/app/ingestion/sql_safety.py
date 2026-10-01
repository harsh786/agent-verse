"""SQL building helpers for the warehouse connectors (ClickHouse, Snowflake, BigQuery).

Those connectors used to paste ``table``, ``cursor_column``, ``stream_name`` and
the sync cursor straight into SQL. The cursor is a value read back from the
source's own rows, so a row containing ``'`` broke the next sync — or rewrote its
query. Values now travel as bound parameters, and identifiers must match a strict
pattern before they are (dialect-) quoted into the statement.
"""

from __future__ import annotations

import re

__all__ = [
    "CURSOR_PARAM",
    "UnsafeIdentifierError",
    "bind_cursor_placeholder",
    "checked_identifier",
]

# Plain SQL identifier part; dotted names (db.schema.table) are split first.
_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")
# BigQuery project ids may contain hyphens (my-project.dataset.table).
_BQ_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")

CURSOR_PARAM = "cursor"

# ``{cursor}`` placeholders in a tenant-written query, quoted or not.
_PLACEHOLDER = re.compile(r"""(['"]?)\{cursor\}\1""")


class UnsafeIdentifierError(ValueError):
    """A table / column / stream name is not a plain SQL identifier."""


def checked_identifier(
    name: object, *, what: str, max_parts: int = 3, bigquery_project: bool = False
) -> list[str]:
    """Split and validate a (possibly dotted) identifier; raise if anything is off.

    Every part must be ``[A-Za-z_][A-Za-z0-9_]*`` — no quotes, spaces, comments,
    semicolons or backticks — so it can be quoted (or used bare) safely. With
    ``bigquery_project`` the first of three parts may be a BigQuery project id.
    """
    text = str(name or "").strip()
    parts = text.split(".") if text else []
    if not parts or len(parts) > max_parts:
        raise UnsafeIdentifierError(f"invalid {what} {text!r}: expected 1-{max_parts} name parts")
    for i, part in enumerate(parts):
        if bigquery_project and i == 0 and len(parts) == 3 and _BQ_PROJECT.match(part):
            continue
        if not _PART.match(part):
            raise UnsafeIdentifierError(
                f"invalid {what} {text!r}: {part!r} is not a plain SQL identifier"
            )
    return parts


def bind_cursor_placeholder(query: str, placeholder: str) -> tuple[str, bool]:
    """Replace ``{cursor}`` / ``'{cursor}'`` in a tenant query with a bind placeholder.

    Returns the rewritten query and whether a placeholder was present. The cursor
    value itself is then passed separately as a bound parameter.
    """
    rewritten, count = _PLACEHOLDER.subn(lambda _m: placeholder, query)
    return rewritten, count > 0
