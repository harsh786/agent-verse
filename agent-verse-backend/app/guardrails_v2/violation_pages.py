"""Keyset pages over a tenant's durable guardrail violations (P8b-3).

``GET /guardrails-v2/violations`` and the legacy ``GET /guardrails/violations``
both read the durable ``guardrail_violations`` store through this helper (the
legacy one used to read a per-process dict nothing wrote to). Pages are newest
first and keyset-paginated by ``(created_at, id)`` with an opaque cursor — no
OFFSET, no total count, so a page costs the same at any depth.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import json
from dataclasses import dataclass
from typing import Any

from app.guardrails_v2.models import GuardrailViolation


class InvalidCursorError(ValueError):
    """The pagination cursor is malformed."""


def encode_cursor(violation: GuardrailViolation) -> str:
    raw = json.dumps({"ts": violation.created_at, "id": violation.violation_id})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime.datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        ts = datetime.datetime.fromisoformat(str(data["ts"]))
        vid = str(data["id"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid cursor") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    return ts, vid


@dataclass
class ViolationPage:
    violations: list[GuardrailViolation]
    next_cursor: str | None


async def violation_page(
    engine: Any,
    tenant_id: str,
    *,
    limit: int,
    severity: str | None = None,
    layer: str | None = None,
    goal_id: str | None = None,
    cursor: str | None = None,
) -> ViolationPage:
    """One page of *tenant_id*'s violations. Raises InvalidCursorError on a bad
    cursor and whatever the store raises when it cannot be read."""
    before = decode_cursor(cursor) if cursor else None
    rows: list[GuardrailViolation] = await engine.aget_violations(
        tenant_id, limit + 1, severity, layer=layer, goal_id=goal_id, before=before
    )
    page = rows[:limit]
    more = len(rows) > limit and bool(page)
    return ViolationPage(page, encode_cursor(page[-1]) if more else None)


__all__ = [
    "InvalidCursorError",
    "ViolationPage",
    "decode_cursor",
    "encode_cursor",
    "violation_page",
]
