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
async def test_system_session_surfaces_a_failure_to_disable_row_security() -> None:
    """A maintenance session that cannot bypass RLS must fail, not continue.

    This previously asserted a fallback: on `SET LOCAL row_security = off`
    failing, set `app.tenant_id = '__system__'` and carry on. Two problems:

    * it was keyed on the SET *raising*, which is not how the real failure
      presents. Verified against Postgres: for a role without BYPASSRLS the SET
      SUCCEEDS and the subsequent statement raises
      `InsufficientPrivilegeError: query would be affected by row-level
      security`. So the fallback never ran in the case it was written for.
    * had it run, it would have been worse than the error. No table's policy
      matches `__system__`, so every maintenance DELETE would have matched zero
      rows and the task would have reported `{"status": "ok", "deleted": 0}` —
      turning a loud permission error into a silent no-op on a data-retention
      job.

    The fallback is removed. A maintenance job that cannot see the rows it is
    meant to reclaim must fail loudly.
    """
    from app.db.rls import system_session

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute.side_effect = Exception(
        "permission denied to set parameter row_security"
    )

    with pytest.raises(Exception, match="permission denied"):
        async with system_session(mock_session):
            pass  # pragma: no cover - the context manager raises on entry
