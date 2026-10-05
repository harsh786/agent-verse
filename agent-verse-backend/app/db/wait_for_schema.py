"""Block until the database schema is at this image's alembic head (NF-16).

App pods connect as the least-privilege application role, which the migrate
Job creates (and grants) after it has migrated the schema. Started before that
Job finished, a pod used to crash-loop: the role did not exist yet, or the
tables it needs were missing. Run as an initContainer
(``python -m app.db.wait_for_schema``) it waits instead — the pod sits in
``Init`` until ``alembic_version`` (read AS the app role, through the same
``DATABASE_URL``) equals the head of the migrations shipped in this image, then
the app container starts.

Fails closed: an unreachable database, a missing role or a missing / older
``alembic_version`` is "not ready", never "go ahead". After
``SCHEMA_WAIT_TIMEOUT_SECONDS`` (default 1800) it exits 1 so the stall shows up
as a failing init container with the last reason logged — not as a running app
on a half-migrated schema. The DSN is never logged.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_INTERVAL_SECONDS = 3.0


def expected_heads() -> set[str]:
    """The head revision(s) of the migrations shipped with this code."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "app" / "db" / "migrations"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


async def current_heads(database_url: str) -> set[str]:
    """``alembic_version`` as the connecting role sees it (raises when unreadable)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT version_num FROM alembic_version"))).all()
    finally:
        await engine.dispose()
    return {str(r[0]) for r in rows}


async def wait_for_schema(
    database_url: str,
    *,
    expected: set[str],
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    interval_s: float = DEFAULT_INTERVAL_SECONDS,
    read: Callable[[str], Awaitable[set[str]]] = current_heads,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr, flush=True),
) -> bool:
    """True once the schema is at ``expected``; False when ``timeout_s`` ran out."""
    deadline = clock() + timeout_s
    last = ""
    while True:
        try:
            heads = await read(database_url)
        except Exception as exc:  # role / table / server not there yet
            reason = f"database not ready ({type(exc).__name__})"
        else:
            if heads == expected:
                log(f"schema at head {sorted(expected)}; starting")
                return True
            reason = f"schema at {sorted(heads) or 'no revision'}, waiting for {sorted(expected)}"
        if reason != last:
            log(f"wait-for-schema: {reason}")
            last = reason
        if clock() >= deadline:
            log(f"wait-for-schema: gave up after {timeout_s:g}s: {reason}")
            return False
        await sleep(interval_s)


def main() -> int:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("wait-for-schema: DATABASE_URL is not set", file=sys.stderr)
        return 2
    timeout = float(os.environ.get("SCHEMA_WAIT_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    from app.db.session import run_in_fresh_loop

    ok = run_in_fresh_loop(wait_for_schema(url, expected=expected_heads(), timeout_s=timeout))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    sys.exit(main())
