"""NF-16 follow-up: app pods wait (initContainer) for the migrated schema."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from app.db import wait_for_schema as wfs


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.now += s


def test_expected_heads_match_alembic() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=wfs._BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert wfs.expected_heads() == {line.split()[0] for line in out.splitlines() if line.strip()}


async def test_waits_through_missing_role_and_old_schema_then_starts() -> None:
    clock = _Clock()
    states: list[Any] = [
        ConnectionRefusedError("db starting"),
        RuntimeError('role "agentverse_app" does not exist'),
        {"older"},
        {"head-1"},
    ]
    logs: list[str] = []

    async def read(url: str) -> set[str]:
        state = states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state

    ok = await wfs.wait_for_schema(
        "postgresql+asyncpg://u:secret-pw@db/x",
        expected={"head-1"},
        timeout_s=60,
        interval_s=2,
        read=read,
        sleep=clock.sleep,
        clock=clock,
        log=logs.append,
    )

    assert ok is True
    assert states == []
    assert not any("secret-pw" in line for line in logs)  # the DSN is never logged


async def test_gives_up_after_the_timeout_instead_of_starting() -> None:
    clock = _Clock()

    async def read(url: str) -> set[str]:
        return {"older"}

    logs: list[str] = []
    ok = await wfs.wait_for_schema(
        "x", expected={"head-1"}, timeout_s=10, interval_s=3,
        read=read, sleep=clock.sleep, clock=clock, log=logs.append,
    )
    assert ok is False
    assert "gave up" in logs[-1]


def test_main_refuses_without_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert wfs.main() == 2


@pytest.mark.integration
async def test_real_postgres_ready_only_when_migrated(pg_url: str) -> None:
    """Migrated DB: ready at once. A fresh database (no alembic_version): never ready."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    expected = wfs.expected_heads()
    assert await wfs.wait_for_schema(pg_url, expected=expected, timeout_s=5, log=lambda m: None)

    admin = create_async_engine(pg_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text("DROP DATABASE IF EXISTS nf16_empty"))
            await conn.execute(text("CREATE DATABASE nf16_empty"))
    finally:
        await admin.dispose()
    empty = make_url(pg_url).set(database="nf16_empty").render_as_string(hide_password=False)
    assert not await wfs.wait_for_schema(
        empty, expected=expected, timeout_s=1, interval_s=0.2, log=lambda m: None
    )
