"""L-01: every sync -> async bridge in ``app/`` goes through ``run_in_fresh_loop``.

``asyncio.run`` / ``new_event_loop`` / ``run_until_complete`` close a loop
without disposing the module-level engines, so pooled asyncpg connections (and
async Redis clients) outlive their loop and break the next Celery task on the
worker ("Event loop is closed", "attached to a different loop"). The coordination
outbox task did exactly that on the live stack.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# Each exception owns its loop and touches no shared engine / client.
_ALLOWED = {
    # The one sanctioned implementation.
    "db/session.py": "run_in_fresh_loop itself",
    # Alembic: its own short-lived engine, process-local.
    "db/migrations/env.py": "alembic runs on its own engine in its own process",
    # Heartbeat thread: its own loop and its own raw asyncpg connection, closed
    # in the thread's finally; never the pooled engines.
    "scaling/goal_watchdog.py": "dedicated thread loop with its own raw connection",
    # Sandboxed subprocess entrypoint: one loop per process, exits afterwards.
    "execution_environment/worker_entrypoint.py": "one-shot sandbox subprocess",
}

_FORBIDDEN_CALLS = {"run", "new_event_loop", "run_until_complete"}


def _offending_calls(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        owner = node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else ""
        if attr == "run_until_complete" or (
            attr in _FORBIDDEN_CALLS and owner_name in {"asyncio", "_asyncio", "anyio"}
        ):
            found.append((node.lineno, f"{owner_name or '<expr>'}.{attr}"))
    return found


def test_no_app_module_drives_async_code_outside_run_in_fresh_loop() -> None:
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel in _ALLOWED:
            continue
        for lineno, call in _offending_calls(path.read_text(encoding="utf-8")):
            offenders.append(f"app/{rel}:{lineno} {call}")
    assert offenders == [], (
        "drive async code from sync code with app.db.session.run_in_fresh_loop:\n"
        + "\n".join(offenders)
    )


def test_outbox_task_runs_on_a_fresh_torn_down_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.coordination.outbox_tasks as outbox_tasks
    import app.db.session as sess

    calls: list[Any] = []
    real = sess.run_in_fresh_loop

    def spy(coro: Any) -> Any:
        calls.append(coro)
        return real(coro)

    async def body() -> dict[str, Any]:
        return {"status": "ok", "tenants": 0, "delivered": 0}

    monkeypatch.setattr(sess, "run_in_fresh_loop", spy)
    monkeypatch.setattr(outbox_tasks, "dispatch_coordination_outbox_once", body)
    assert outbox_tasks.dispatch_coordination_outbox()["status"] == "ok"
    assert len(calls) == 1


async def test_captured_system_factory_engine_is_disposed_at_every_loop_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A captured maintenance factory (IngestionJobTracker) leaks like the app one did."""
    from unittest.mock import AsyncMock, MagicMock, patch

    import app.db.session as sess

    first, second = MagicMock(dispose=AsyncMock()), MagicMock(dispose=AsyncMock())
    settings = MagicMock(maintenance_database_url="postgresql+asyncpg://m@h/db")
    monkeypatch.setattr(sess, "get_settings", lambda: settings)
    monkeypatch.setattr(sess, "_system_engine", None)
    monkeypatch.setattr(sess, "_system_session_factory", None)
    with patch("app.db.session._make_engine", side_effect=[first, second]):
        captured = sess.get_system_session_factory()  # held across loops
        await sess.dispose_task_engine()  # end of loop A
        sess.get_system_session_factory()  # loop B builds a fresh engine
        await sess.dispose_task_engine()  # end of loop B
    assert captured is not None
    assert first.dispose.await_count == 2, "captured system engine leaked a loop"
    assert second.dispose.await_count == 1


def test_loop_teardown_closers_run_before_the_loop_closes() -> None:
    import app.db.session as sess

    closed: list[bool] = []

    async def body() -> None:
        loop = asyncio.get_running_loop()

        async def closer() -> None:
            closed.append(not loop.is_closed())

        sess.on_loop_teardown(closer)

    sess.run_in_fresh_loop(body())
    assert closed == [True]


def test_env_configured_usage_redis_is_per_loop_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker's usage counter client never crosses loops and is closed with its loop."""
    import app.db.session as sess
    import app.embedding.usage as usage

    built: list[Any] = []

    class _Client:
        def __init__(self) -> None:
            self.loop = asyncio.get_running_loop()
            self.closed = False
            self.calls: list[tuple[str, int]] = []
            built.append(self)

        async def hincrby(self, key: str, field: str, amount: int) -> None:
            assert asyncio.get_running_loop() is self.loop
            self.calls.append((field, amount))

        async def expire(self, key: str, ttl: int) -> None:
            return None

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379/0")
    monkeypatch.setattr(usage, "_redis", None)
    monkeypatch.setattr(usage, "_make_env_client", _Client)
    usage.configure_usage_redis_from_env()
    try:
        for _ in range(2):
            sess.run_in_fresh_loop(usage.record_embedding_usage("t1", "m", 3))
    finally:
        usage.configure_usage_redis(None)
    assert len(built) == 2
    assert built[0].loop is not built[1].loop
    assert all(c.closed for c in built)
    assert all(c.calls == [("m", 3)] for c in built)
