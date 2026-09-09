"""Tests for system_session RLS bypass in app/db/rls.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


def test_system_session_is_importable() -> None:
    """system_session must be importable from app.db.rls."""
    from app.db.rls import system_session
    assert callable(system_session)


@pytest.mark.asyncio
async def test_system_session_sets_row_security_off() -> None:
    """system_session must issue SET LOCAL row_security = off (or equivalent)."""
    from app.db.rls import system_session

    mock_session = AsyncMock(spec=AsyncSession)

    async with system_session(mock_session):
        pass

    assert mock_session.execute.called, "system_session did not call session.execute at all"

    # Extract the actual SQL text from the TextClause arguments.
    # str(TextClause("SET LOCAL ...")) returns the SQL string; str(call_object)
    # only shows the repr of the TextClause object, so we inspect the args directly.
    calls_text = []
    for c in mock_session.execute.call_args_list:
        for arg in c.args:
            calls_text.append(str(arg))  # str(TextClause) returns the SQL text
        for v in c.kwargs.values():
            calls_text.append(str(v))

    security_set = any(
        "row_security" in ct.lower() or "__system__" in ct
        for ct in calls_text
    )
    assert security_set, (
        f"system_session did not disable RLS. SQL calls seen: {calls_text}"
    )


@pytest.mark.asyncio
async def test_system_session_yields_the_same_session() -> None:
    """system_session must yield the same session object it receives."""
    from app.db.rls import system_session

    mock_session = AsyncMock(spec=AsyncSession)

    async with system_session(mock_session) as yielded:
        assert yielded is mock_session, (
            "system_session should yield the same session object it was given"
        )


@pytest.mark.asyncio
async def test_system_session_fallback_on_permission_error() -> None:
    """When SET LOCAL row_security = off fails, fall back to setting system GUC."""
    from app.db.rls import system_session

    mock_session = AsyncMock(spec=AsyncSession)

    call_count = 0

    async def side_effect(stmt: object, *args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("permission denied to set parameter row_security")
        return AsyncMock()

    mock_session.execute.side_effect = side_effect

    # Must not raise — fallback should handle the error
    async with system_session(mock_session) as yielded:
        assert yielded is mock_session

    # Both the failing call + the fallback call happened
    assert call_count >= 2, (
        "Expected at least 2 execute calls (primary SET + fallback), "
        f"got {call_count}"
    )
