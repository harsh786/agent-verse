"""A cancelled request must roll back and return its DB connection (real Postgres).

Production leaked connections "idle in transaction" (60 through PgBouncer filled
the pool): Starlette's BaseHTTPMiddleware runs the endpoint in an anyio task
group and cancels it on client disconnect. anyio cancellation is level-triggered
— every await inside the cancelled scope raises again — so the session's own
cleanup (``rollback`` / ``close`` → return the connection) was itself cancelled
mid-await and the connection was abandoned with its transaction open. Only the
server-side idle_in_transaction_session_timeout reclaimed it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import anyio
import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db import session as session_mod

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest_asyncio.fixture
async def factory(postgres_url: str) -> AsyncIterator[Any]:
    sm = session_mod._make_session_factory(postgres_url)
    yield sm
    await sm.kw["bind"].dispose()


async def _idle_in_tx(postgres_url: str) -> int:
    conn = await asyncpg.connect(postgres_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
            )
        )
    finally:
        await conn.close()


async def _settled(factory: Any, postgres_url: str) -> tuple[int, int]:
    """(checked-out pool connections, idle-in-transaction backends) once settled."""
    pool = factory.kw["bind"].pool
    for _ in range(50):
        if pool.checkedout() == 0 and await _idle_in_tx(postgres_url) == 0:
            break
        await asyncio.sleep(0.1)
    return pool.checkedout(), await _idle_in_tx(postgres_url)


async def _cancel_like_basehttpmiddleware(body: Any) -> None:
    """Run ``body(started)`` in an anyio task group and cancel it mid-flight."""
    started = anyio.Event()
    async with anyio.create_task_group() as tg:
        tg.start_soon(body, started)
        await started.wait()
        tg.cancel_scope.cancel()


async def test_cancelled_session_mid_transaction_returns_connection(
    factory: Any, postgres_url: str
) -> None:
    async def handler(started: anyio.Event) -> None:
        async with factory() as s:
            await s.execute(text("SELECT 1"))  # autobegin: transaction now open
            started.set()
            await anyio.sleep(30)

    await _cancel_like_basehttpmiddleware(handler)
    assert await _settled(factory, postgres_url) == (0, 0)


async def test_cancelled_session_begin_block_returns_connection(
    factory: Any, postgres_url: str
) -> None:
    async def handler(started: anyio.Event) -> None:
        async with factory() as s, s.begin():
            await s.execute(text("CREATE TEMP TABLE IF NOT EXISTS t_cancel (x int)"))
            await s.execute(text("INSERT INTO t_cancel VALUES (1)"))
            started.set()
            await anyio.sleep(30)

    await _cancel_like_basehttpmiddleware(handler)
    assert await _settled(factory, postgres_url) == (0, 0)


async def test_cancelled_request_dependency_returns_connection(
    factory: Any, postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: factory)

    async def handler(started: anyio.Event) -> None:
        async for s in session_mod.get_db():
            await s.execute(text("SELECT 1"))
            started.set()
            await anyio.sleep(30)

    await _cancel_like_basehttpmiddleware(handler)
    assert await _settled(factory, postgres_url) == (0, 0)


async def test_repeated_task_cancel_returns_connection(factory: Any, postgres_url: str) -> None:
    """Plain asyncio: a second cancel landing during cleanup (e.g. shutdown)."""
    started = asyncio.Event()

    async def handler() -> None:
        async with factory() as s:
            await s.execute(text("SELECT 1"))
            started.set()
            await asyncio.sleep(30)

    task = asyncio.create_task(handler())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _settled(factory, postgres_url) == (0, 0)


async def test_connection_is_reusable_after_cancellation(factory: Any, postgres_url: str) -> None:
    async def handler(started: anyio.Event) -> None:
        async with factory() as s:
            await s.execute(text("SELECT 1"))
            started.set()
            await anyio.sleep(30)

    for _ in range(3):
        await _cancel_like_basehttpmiddleware(handler)
    async with factory() as s:
        assert (await s.execute(text("SELECT 42"))).scalar() == 42
    assert await _settled(factory, postgres_url) == (0, 0)


# ── Celery task event loops (the leak the stack actually showed) ─────────────
#
# pg_stat_activity on the dev stack showed the leaked backends' last statement
# was "BEGIN;" and the worker log had hundreds of "attached to a different loop"
# / "Event loop is closed" errors: Celery tasks ran coroutines on throw-away
# loops (``new_event_loop`` ... ``loop.close()`` without disposing the engine) or
# on the persistent ``get_event_loop()``, so the module-level engine's pooled
# connections were reused on a different loop. asyncpg wrote BEGIN to the
# socket, then failed awaiting the reply; the connection was dropped with its
# transaction open. Tasks still pending at loop close (fire-and-forget work
# holding a session) leaked the same way.


@pytest.fixture
def global_factory_on_container(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    real_make_engine = session_mod._make_engine
    monkeypatch.setattr(
        session_mod,
        "_make_engine",
        lambda database_url=None: real_make_engine(database_url or postgres_url),
    )
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)
    yield
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(session_mod.dispose_task_engine())
    finally:
        loop.close()


def _idle_in_tx_sync(postgres_url: str) -> int:
    loop = asyncio.new_event_loop()
    n = -1
    try:
        for _ in range(20):
            n = loop.run_until_complete(_idle_in_tx(postgres_url))
            if n == 0:
                return 0
            loop.run_until_complete(asyncio.sleep(0.1))
        return n
    finally:
        loop.close()


def test_fresh_loop_runner_returns_connections_held_by_leftover_tasks(
    global_factory_on_container: None, postgres_url: str
) -> None:
    async def task_body() -> str:
        async def background() -> None:
            async with session_mod.get_session_factory()() as s:
                await s.execute(text("SELECT 1"))  # transaction open
                await asyncio.sleep(30)

        asyncio.get_running_loop().create_task(background())
        await asyncio.sleep(0.5)  # let it open its transaction
        return "done"

    assert session_mod.run_in_fresh_loop(task_body()) == "done"
    assert _idle_in_tx_sync(postgres_url) == 0


def test_consecutive_task_loops_never_reuse_a_connection_across_loops(
    global_factory_on_container: None, postgres_url: str
) -> None:
    captured: dict[str, Any] = {}

    async def first() -> None:
        factory = session_mod.get_session_factory()
        captured["factory"] = factory  # e.g. run_goal keeps it across loops
        async with factory() as s:
            await s.execute(text("SELECT 1"))

    async def second() -> int:
        async with captured["factory"]() as s:
            return int((await s.execute(text("SELECT 7"))).scalar_one())

    session_mod.run_in_fresh_loop(first())
    assert session_mod.run_in_fresh_loop(second()) == 7
    assert _idle_in_tx_sync(postgres_url) == 0


def test_celery_task_loop_helpers_use_the_fresh_loop_runner() -> None:
    """Every scaling-task loop wrapper and the ingestion scheduler go through it."""
    import inspect

    from app.ingestion import repo_tasks, scheduler
    from app.orchestration import evidence_maintenance
    from app.scaling import tasks

    assert "run_in_fresh_loop" in inspect.getsource(tasks._run_async)
    assert "new_event_loop()" not in inspect.getsource(tasks)
    for module in (scheduler, repo_tasks):
        src = inspect.getsource(module)
        assert "get_event_loop().run_until_complete" not in src
        assert "run_in_fresh_loop" in src
    assert "asyncio.run(" not in inspect.getsource(evidence_maintenance)
