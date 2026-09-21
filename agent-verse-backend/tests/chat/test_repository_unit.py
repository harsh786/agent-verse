"""Pure-logic unit tests for app/chat/repository.py that need no database.

Covers ``_decode_cursor``'s malformed-input branches (empty/garbled cursors must
fall back to ``None`` rather than raising on untrusted client input) and
``update_session``'s short-circuit when no updatable field is supplied (it must
return False *without* opening a session/transaction).
"""

from __future__ import annotations

from datetime import UTC

import pytest

from app.chat.repository import PostgresChatRepository, _decode_cursor


# ── _decode_cursor ──────────────────────────────────────────────────────────


def test_decode_cursor_none_and_empty() -> None:
    assert _decode_cursor(None) is None
    assert _decode_cursor("") is None


def test_decode_cursor_missing_separator() -> None:
    assert _decode_cursor("no-pipe-here") is None


def test_decode_cursor_empty_timestamp_or_id() -> None:
    assert _decode_cursor("|row-id") is None
    assert _decode_cursor("2024-01-01T00:00:00+00:00|") is None


def test_decode_cursor_invalid_timestamp() -> None:
    assert _decode_cursor("not-a-timestamp|row-id") is None


def test_decode_cursor_naive_timestamp_gets_utc() -> None:
    result = _decode_cursor("2024-01-01T00:00:00|row-id")
    assert result is not None
    ts, row_id = result
    assert row_id == "row-id"
    assert ts.tzinfo is UTC


def test_decode_cursor_tz_aware_timestamp_preserved() -> None:
    result = _decode_cursor("2024-01-01T00:00:00+05:00|row-id")
    assert result is not None
    ts, row_id = result
    assert row_id == "row-id"
    assert ts.utcoffset() is not None and ts.utcoffset().total_seconds() == 5 * 3600


# ── update_session short-circuit ────────────────────────────────────────────


class _ExplodingSessionFactory:
    """Raises if ever called — proves update_session never opens a session
    when there are no updatable fields (pure allowlist filter, no I/O)."""

    def __call__(self) -> None:  # pragma: no cover - should never run
        raise AssertionError("session factory must not be invoked")


@pytest.mark.asyncio
async def test_update_session_no_valid_fields_short_circuits() -> None:
    repo = PostgresChatRepository(_ExplodingSessionFactory())  # type: ignore[arg-type]
    result = await repo.update_session("sid", "tenant", not_a_real_column="x")
    assert result is False


@pytest.mark.asyncio
async def test_update_session_empty_kwargs_short_circuits() -> None:
    repo = PostgresChatRepository(_ExplodingSessionFactory())  # type: ignore[arg-type]
    assert await repo.update_session("sid", "tenant") is False
