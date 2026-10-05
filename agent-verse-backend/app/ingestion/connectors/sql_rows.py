"""Shared row-sync machinery for the OLTP connectors (PostgreSQL, MySQL) — P1b-7.

* **One cursor per table.** The old connectors kept ONE cursor value for every
  table and advanced it to the newest row of any table: a table that failed (no
  grant yet, a lock timeout) or simply lagged had its rows skipped for good once
  another table moved the cursor past them.
* **Keyset batches with a tie-breaker.** Rows are read ``ORDER BY cursor, pk``
  in bounded batches until the table is exhausted, resuming after
  ``(cursor, pk)``. With ``WHERE cursor > last LIMIT n`` (the old query, one batch
  per sync) rows sharing the last batch's timestamp — every row of a bulk load —
  were skipped, and a big table needed one sync per batch.
* **Typed positions.** The position is stored with its type and restored before
  it is bound, so a ``timestamptz`` column is compared with a datetime, not text
  (asyncpg refuses a str for it; MySQL compared strings).

The cursor is JSON: ``{"v": 2, "tables": {"<table>": {"c": <typed>, "k": [<typed>…]}}}``.
A legacy plain value is the starting position of every table.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# Columns rendered into a row's text, at most (wide tables stay readable).
MAX_RENDERED_COLUMNS = 200
_MAX_VALUE_CHARS = 4000


def encode_value(value: Any) -> dict[str, str] | None:
    """A JSON-safe, type-tagged form of a cursor / key value (None stays None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return {"t": "bool", "v": "1" if value else "0"}
    if isinstance(value, int):
        return {"t": "int", "v": str(value)}
    if isinstance(value, float):
        return {"t": "float", "v": repr(value)}
    if isinstance(value, decimal.Decimal):
        return {"t": "dec", "v": str(value)}
    if isinstance(value, dt.datetime):
        return {"t": "ts", "v": value.isoformat()}
    if isinstance(value, dt.date):
        return {"t": "date", "v": value.isoformat()}
    if isinstance(value, dt.time):
        return {"t": "time", "v": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"t": "uuid", "v": str(value)}
    if isinstance(value, bytes | bytearray | memoryview):
        return {"t": "bytes", "v": base64.b64encode(bytes(value)).decode()}
    return {"t": "str", "v": str(value)}


def decode_value(tagged: Any) -> Any:
    if tagged is None:
        return None
    if not isinstance(tagged, Mapping):
        return tagged
    kind, raw = str(tagged.get("t")), str(tagged.get("v"))
    if kind == "bool":
        return raw == "1"
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "dec":
        return decimal.Decimal(raw)
    if kind == "ts":
        return dt.datetime.fromisoformat(raw)
    if kind == "date":
        return dt.date.fromisoformat(raw)
    if kind == "time":
        return dt.time.fromisoformat(raw)
    if kind == "uuid":
        return uuid.UUID(raw)
    if kind == "bytes":
        return base64.b64decode(raw)
    return raw


def legacy_value(raw: str) -> Any:
    """A pre-v2 plain cursor (``str()`` of the newest value) as a typed value."""
    text = raw.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text


@dataclass
class TablePosition:
    """Where one table's sync stands: after ``(cursor, *key)``; nothing read yet if None."""

    cursor: Any = None
    key: list[Any] = field(default_factory=list)
    started: bool = False


class TableCursors:
    """Per-table positions of one Source."""

    def __init__(self, positions: dict[str, TablePosition], legacy: Any = None) -> None:
        self.positions = positions
        self._legacy = legacy

    @classmethod
    def parse(cls, raw: str | None) -> TableCursors:
        text = (raw or "").strip()
        if not text:
            return cls({})
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                return cls({})
            out: dict[str, TablePosition] = {}
            for table, pos in (data.get("tables") or {}).items():
                out[str(table)] = TablePosition(
                    cursor=decode_value(pos.get("c")),
                    key=[decode_value(k) for k in pos.get("k") or []],
                    started=True,
                )
            return cls(out)
        return cls({}, legacy=legacy_value(text))

    def get(self, table: str) -> TablePosition:
        pos = self.positions.get(table)
        if pos is not None:
            return pos
        if self._legacy is not None:
            # A legacy cursor: rows strictly newer than it (it was exclusive).
            return TablePosition(cursor=self._legacy, key=[], started=True)
        return TablePosition()

    def advance(self, table: str, cursor: Any, key: list[Any]) -> str:
        self.positions[table] = TablePosition(cursor=cursor, key=list(key), started=True)
        return self.dump()

    def dump(self) -> str:
        return json.dumps(
            {
                "v": 2,
                "tables": {
                    t: {"c": encode_value(p.cursor), "k": [encode_value(k) for k in p.key]}
                    for t, p in self.positions.items()
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        )


def render_value(value: Any) -> str:
    if isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False, default=str)
    elif isinstance(value, str) and value[:1] in "{[":
        try:  # JSON returned as text (asyncpg without a codec, PyMySQL)
            text = json.dumps(json.loads(value), ensure_ascii=False)
        except ValueError:
            text = value
    elif isinstance(value, dt.datetime | dt.date | dt.time):
        text = value.isoformat()
    elif isinstance(value, bytes | bytearray | memoryview):
        text = f"<{len(bytes(value))} bytes>"
    else:
        text = str(value)
    return text[:_MAX_VALUE_CHARS]


def row_text(table: str, key_desc: str, row: Mapping[str, Any]) -> str:
    """``<table> record (<key>)`` then one ``column: value`` line per non-NULL column.

    NULLs are left out (they used to read ``notes: None``), JSON is kept as JSON,
    timestamps are ISO-8601.
    """
    lines = [f"{table} record ({key_desc})"]
    shown = 0
    for name, value in row.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        lines.append(f"{name}: {render_value(value)}")
        shown += 1
        if shown >= MAX_RENDERED_COLUMNS:
            lines.append(f"(+{len(row) - shown} more columns)")
            break
    return "\n".join(lines)


def key_desc(pk_cols: Iterable[str], row: Mapping[str, Any]) -> tuple[str, str]:
    """(``id=7``, ``7``) — the human description and the id part of a row's URL."""
    cols = list(pk_cols)
    desc = ", ".join(f"{c}={row.get(c)}" for c in cols)
    ident = "_".join(str(row.get(c, "")) for c in cols)
    return desc, ident
