"""Regression tests for C3 (maintenance RLS), C5 (engine leak), H12 (lock release)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_session(execute_return: object = None) -> MagicMock:
    """Build a MagicMock session with a properly async-context-manager-compatible begin()."""
    mock_session = MagicMock()
    # session.execute must be awaitable
    mock_session.execute = AsyncMock(return_value=execute_return or MagicMock(
        fetchall=MagicMock(return_value=[]),
        rowcount=0,
    ))
    # session.begin() must return an async context manager (not a coroutine)
    mock_begin = MagicMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)
    return mock_session


def _make_session_factory_patch(mock_session: MagicMock) -> MagicMock:
    """Return a mock_factory whose db()→session is mock_session."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock()
    mock_factory.return_value.return_value = mock_cm  # db() returns mock_cm
    mock_factory.return_value = MagicMock(return_value=mock_cm)  # db = factory()
    return mock_factory


class TestMaintenanceRLS:
    """C3: Maintenance tasks must use system_session to bypass tenant RLS."""

    @pytest.mark.asyncio
    async def test_find_stuck_goals_uses_system_session(self) -> None:
        """system_session must be called before the UPDATE in _find_and_fail_stuck_goals."""
        from app.db import rls as rls_module

        mock_session = _make_mock_session()

        with patch.object(rls_module, "system_session") as mock_sys:
            mock_sys.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sys.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_factory = _make_session_factory_patch(mock_session)

            with patch("app.db.session.get_session_factory", return_value=mock_factory.return_value):
                from app.scaling.tasks import _find_and_fail_stuck_goals
                await _find_and_fail_stuck_goals()

            mock_sys.assert_called()

    def test_system_session_exists_in_rls_module(self) -> None:
        """system_session must be importable from app.db.rls."""
        from app.db.rls import system_session
        assert callable(system_session)

    @pytest.mark.asyncio
    async def test_delete_expired_records_uses_system_session(self) -> None:
        """_delete_expired_records must invoke system_session."""
        from app.db import rls as rls_module

        execute_result = MagicMock()
        execute_result.rowcount = 0
        mock_session = _make_mock_session(execute_result)

        with patch.object(rls_module, "system_session") as mock_sys:
            mock_sys.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sys.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_factory = _make_session_factory_patch(mock_session)

            with patch("app.db.session.get_session_factory", return_value=mock_factory.return_value):
                from app.scaling.tasks import _delete_expired_records
                await _delete_expired_records(90)

            mock_sys.assert_called()

    @pytest.mark.asyncio
    async def test_delete_expired_records_purges_memory_records_by_expires_at(self) -> None:
        """D-18: the retention task must issue a DELETE against memory_records keyed on
        expires_at — expired memory rows were previously never physically removed."""
        from app.db import rls as rls_module

        execute_result = MagicMock()
        execute_result.rowcount = 0
        mock_session = _make_mock_session(execute_result)

        with patch.object(rls_module, "system_session") as mock_sys:
            mock_sys.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sys.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_factory = _make_session_factory_patch(mock_session)

            _sf_target = "app.db.session.get_session_factory"
            with patch(_sf_target, return_value=mock_factory.return_value):
                from app.scaling.tasks import _delete_expired_records
                await _delete_expired_records(90)

        executed_sql = [str(call.args[0]) for call in mock_session.execute.call_args_list]
        memory_deletes = [
            sql for sql in executed_sql if "memory_records" in sql and "expires_at" in sql
        ]
        assert memory_deletes, f"no memory_records expiry purge issued; ran: {executed_sql}"

    @pytest.mark.asyncio
    async def test_expire_db_approvals_uses_system_session(self) -> None:
        """_expire_db_approvals must invoke system_session."""
        from app.db import rls as rls_module

        mock_session = _make_mock_session(MagicMock(fetchall=MagicMock(return_value=[])))

        with patch.object(rls_module, "system_session") as mock_sys:
            mock_sys.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sys.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_factory = _make_session_factory_patch(mock_session)

            with patch("app.db.session.get_session_factory", return_value=mock_factory.return_value):
                from app.scaling.tasks import _expire_db_approvals
                await _expire_db_approvals()

            mock_sys.assert_called()

    @pytest.mark.asyncio
    async def test_update_goal_dlq_uses_system_session(self) -> None:
        """_update_goal_dlq must invoke system_session."""
        from app.db import rls as rls_module

        mock_session = _make_mock_session()

        with patch.object(rls_module, "system_session") as mock_sys:
            mock_sys.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sys.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_factory = _make_session_factory_patch(mock_session)

            with patch("app.db.session.get_session_factory", return_value=mock_factory.return_value):
                from app.scaling.tasks import _update_goal_dlq
                await _update_goal_dlq("goal-1", "tenant-1", "test reason")

            mock_sys.assert_called()


class TestEngineLeakFix:
    """C5: run_goal must use get_session_factory (singleton), not _make_session_factory."""

    def test_run_goal_does_not_call_make_session_factory(self) -> None:
        """C5: _make_session_factory must not be called directly inside run_goal."""
        import inspect

        from app.scaling import tasks

        source = inspect.getsource(tasks)

        # Find run_goal function body
        run_goal_start = source.find("def run_goal(")
        assert run_goal_start >= 0, "run_goal function not found in tasks.py"

        # Find the next top-level function definition after run_goal
        next_func = source.find("\n@celery_app.task", run_goal_start + 10)
        if next_func < 0:
            next_func = source.find("\ndef ", run_goal_start + 200)
        run_goal_body = source[run_goal_start:next_func] if next_func > 0 else source[run_goal_start:]

        direct_calls = run_goal_body.count("_make_session_factory()")
        assert direct_calls == 0, (
            f"run_goal still calls _make_session_factory() {direct_calls} time(s) — engine leak! "
            "Replace with get_session_factory() (the singleton)."
        )

    def test_run_goal_imports_get_session_factory(self) -> None:
        """C5: run_goal should rely on get_session_factory, not _make_session_factory."""
        import inspect

        from app.scaling import tasks

        source = inspect.getsource(tasks)
        run_goal_start = source.find("def run_goal(")
        next_func = source.find("\n@celery_app.task", run_goal_start + 10)
        if next_func < 0:
            next_func = source.find("\ndef ", run_goal_start + 200)
        run_goal_body = source[run_goal_start:next_func] if next_func > 0 else source[run_goal_start:]

        uses_singleton = "get_session_factory" in run_goal_body
        assert uses_singleton, "run_goal should use get_session_factory() (the singleton)."


class TestLockReleaseFix:
    """H12: Distributed lock must be released successfully after goal completion."""

    def test_sync_lock_class_exists(self) -> None:
        """H12: _SyncGoalLock class must be defined in tasks module."""
        from app.scaling import tasks
        assert hasattr(tasks, "_SyncGoalLock"), (
            "H12: _SyncGoalLock not found in tasks.py. "
            "Add a synchronous Redis-based lock class to avoid event loop mismatch."
        )

    def test_sync_lock_acquire_and_release(self) -> None:
        """H12: _SyncGoalLock.acquire and release must work with a mock sync Redis."""
        from app.scaling.tasks import _SyncGoalLock

        mock_redis = MagicMock()
        mock_redis.set.return_value = True  # acquire succeeds
        mock_redis.eval.return_value = 1  # release succeeds

        lock = _SyncGoalLock(mock_redis, "test-lock-value")

        acquired = lock.acquire("goal-abc", ttl_ms=60_000)
        assert acquired is True
        mock_redis.set.assert_called_once()
        call_kwargs = mock_redis.set.call_args
        assert "nx" in call_kwargs.kwargs or "nx" in str(call_kwargs)

        lock.release("goal-abc")
        mock_redis.eval.assert_called_once()

    def test_run_goal_uses_sync_lock(self) -> None:
        """H12: run_goal must use _SyncGoalLock (or single asyncio.run) instead of async Redis lock."""
        import inspect

        from app.scaling import tasks

        source = inspect.getsource(tasks)
        run_goal_start = source.find("def run_goal(")
        next_func = source.find("\n@celery_app.task", run_goal_start + 10)
        if next_func < 0:
            next_func = source.find("\ndef ", run_goal_start + 200)
        run_goal_body = source[run_goal_start:next_func] if next_func > 0 else source[run_goal_start:]

        has_sync_lock = "_SyncGoalLock" in run_goal_body
        has_single_loop = "asyncio.run(" in run_goal_body

        assert has_sync_lock or has_single_loop, (
            "H12: run_goal must use _SyncGoalLock (sync Redis) or a single asyncio.run() "
            "to avoid event loop mismatch when acquiring/releasing the distributed lock."
        )


class _AsyncNullCM:
    """Trivial async context manager (stands in for ``session.begin()``)."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _FakeSavepoint:
    """Stands in for ``session.begin_nested()`` with real SAVEPOINT semantics.

    Entering increments the session's savepoint depth so a failure inside is
    known to be *local*; on exit (whether clean or via exception) it decrements
    the depth again -- mirroring Postgres's ROLLBACK TO SAVEPOINT, which undoes
    only that subtransaction and leaves the *enclosing* transaction usable.
    Critically it does NOT suppress the exception: the caller's own
    ``try/except`` around ``async with session.begin_nested():`` is what
    records the per-item failure, exactly as in the real code.
    """

    def __init__(self, session: _FakePoisonableSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSavepoint:
        self._session.savepoint_depth += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self._session.savepoint_depth -= 1
        return False  # never suppress -- let the surrounding try/except handle it


class _FakePoisonableSession:
    """Fake asyncpg/SQLAlchemy session that reproduces the exact Postgres
    behaviour the real bug depended on: once a statement fails *outside of a
    savepoint*, the whole transaction is aborted and every later statement on
    the same session raises "current transaction is aborted" -- until a
    savepoint (``begin_nested``) isolates the failure instead.
    """

    def __init__(self, fail_substrings: set[str]) -> None:
        self.fail_substrings = fail_substrings
        self.poisoned = False
        self.savepoint_depth = 0
        self.executed_sql: list[str] = []

    async def execute(self, stmt: object, params: object = None) -> MagicMock:
        sql = str(stmt)
        self.executed_sql.append(sql)
        if self.poisoned:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until end of "
                "transaction block"
            )
        if any(needle in sql for needle in self.fail_substrings):
            if self.savepoint_depth == 0:
                # Not inside a SAVEPOINT: the abort is permanent for the rest
                # of this transaction, exactly like real Postgres.
                self.poisoned = True
            raise RuntimeError("simulated permission denied for this statement")
        result = MagicMock()
        result.rowcount = 1
        return result

    def begin(self) -> _AsyncNullCM:
        return _AsyncNullCM()

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint(self)


class TestDeleteExpiredRecordsSavepointIsolation:
    """Regression test: one table's DELETE failing must not poison the
    later tables' DELETEs in the same ``_delete_expired_records`` transaction.

    Mirrors the bug fixed in ``ComplianceController.execute_data_deletion_async``
    (see tests/enterprise/test_gdpr_erasure_rls.py): a per-iteration
    ``try/except`` around a raw DELETE, all sharing one
    ``session.begin()`` transaction, looks like independent per-table
    outcomes -- but without a SAVEPOINT, Postgres aborts the whole
    transaction on the first error and every subsequent DELETE fails too
    with "current transaction is aborted", silently masquerading as its own
    independent failure.
    """

    @pytest.mark.asyncio
    async def test_goal_events_failure_does_not_poison_decision_traces_delete(self) -> None:
        from app.db import rls as rls_module

        # Only the FIRST table's DELETE ("goal_events") is made to fail.
        # "decision_traces" and "memory_records" would succeed on a fresh
        # transaction -- the whole point of the regression test is proving
        # they still do, instead of failing with the poisoned-transaction error.
        session = _FakePoisonableSession(fail_substrings={"goal_events"})

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        db_factory = MagicMock(return_value=cm)  # db() -> cm

        with patch.object(rls_module, "system_session") as mock_sys:
            mock_sys.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sys.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.db.session.get_session_factory", return_value=db_factory):
                from app.scaling.tasks import _delete_expired_records

                result = await _delete_expired_records(90)

        deleted = result["deleted"]

        # goal_events genuinely failed.
        assert isinstance(deleted["goal_events"], str)
        assert "error" in deleted["goal_events"]

        # The load-bearing assertions: decision_traces and memory_records were
        # NOT poisoned by goal_events' failure and actually ran (rowcount==1),
        # rather than each also failing with "current transaction is aborted".
        assert deleted["decision_traces"] == 1, (
            f"decision_traces DELETE was poisoned by goal_events' failure: {deleted}"
        )
        assert deleted["memory_records"] == 1, (
            f"memory_records DELETE was poisoned by an earlier failure: {deleted}"
        )

    @pytest.mark.asyncio
    async def test_all_tables_isolated_when_middle_table_fails(self) -> None:
        """Failing table need not be first -- decision_traces failing must not
        poison the later memory_records DELETE either."""
        from app.db import rls as rls_module

        session = _FakePoisonableSession(fail_substrings={"decision_traces"})

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        db_factory = MagicMock(return_value=cm)

        with patch.object(rls_module, "system_session") as mock_sys:
            mock_sys.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sys.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.db.session.get_session_factory", return_value=db_factory):
                from app.scaling.tasks import _delete_expired_records

                result = await _delete_expired_records(90)

        deleted = result["deleted"]
        assert deleted["goal_events"] == 1
        assert isinstance(deleted["decision_traces"], str)
        assert "error" in deleted["decision_traces"]
        assert deleted["memory_records"] == 1, (
            f"memory_records DELETE was poisoned by decision_traces' failure: {deleted}"
        )
