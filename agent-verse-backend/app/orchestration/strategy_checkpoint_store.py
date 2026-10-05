"""Postgres-backed checkpoint store for StrategyRunner strategies (CORE-18).

The distributed supervisor / goal_tree / debate / voyager runs checkpointed into
a per-process LRU (``InMemoryPatternCheckpointStore``), so a worker crash or a
redelivered goal restarted the run from scratch and repeated its LLM spend and
side effects. ``strategy_run_checkpoints`` (tenant RLS, rows cascade with the
goal) holds the adapter's latest typed state for (tenant, goal, strategy) plus
the sub-task answers the state references, so a redelivered run resumes where
it stopped.

It implements the pattern checkpoint-store protocol the adapters use:
``save(state)`` and ``load(session_id, execution_id)``. Loads are re-validated
into an allow-listed state model; an unknown stored type fails loudly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from pydantic import BaseModel

# Answers kept per run (bounded: a run has a handful of sub-tasks).
_MAX_ANSWERS = 64
_MAX_ANSWER_CHARS = 20_000


def _state_types() -> dict[str, type[BaseModel]]:
    from app.agent.patterns.voyager import VoyagerState
    from app.coordination.patterns.common import DurablePatternState
    from app.coordination.patterns.debate_adapter import DurableDebateState

    return {
        "DurablePatternState": DurablePatternState,
        "DurableDebateState": DurableDebateState,
        "VoyagerState": VoyagerState,
    }


def _retryable(state: BaseModel) -> BaseModel:
    """A redelivered run retries what did not finish and keeps what did.

    A supervisor/goal_tree state that stopped ``failed``/``cancelled`` would
    otherwise be loaded as-is and fail again forever (the adapter only runs
    ``pending`` items): its unfinished items go back to ``pending`` while
    completed items — whose side effects already happened — stay completed.
    A voyager state does the same: its ``task_index`` / ``evidence_refs`` keep
    the finished tasks, and the run continues from the first unfinished one.
    """
    from app.agent.patterns.voyager import VoyagerState
    from app.coordination.patterns.common import DurablePatternState

    if isinstance(state, VoyagerState) and state.phase in ("failed", "cancelled"):
        return state.model_copy(update={"phase": "executing", "terminal_reason": None})
    if not isinstance(state, DurablePatternState) or state.phase not in ("failed", "cancelled"):
        return state
    return state.model_copy(
        update={
            "phase": "executing",
            "terminal_reason": None,
            "work_items": tuple(
                item
                if item.state == "completed"
                else item.model_copy(update={"state": "pending"})
                for item in state.work_items
            ),
        }
    )


class PostgresStrategyCheckpointStore:
    """One strategy run's durable checkpoint (keyed tenant + goal + strategy)."""

    def __init__(
        self, db_factory: Any, *, tenant_id: str, goal_id: str, strategy_id: str
    ) -> None:
        self._db = db_factory
        self._keys = {"tid": tenant_id, "gid": goal_id, "sid": strategy_id}

    def _session(self) -> Any:
        from app.db.rls import sqlalchemy_rls_context

        tenant_id = self._keys["tid"]

        @asynccontextmanager
        async def _cm() -> Any:
            async with AsyncExitStack() as stack:
                session = await stack.enter_async_context(self._db())
                await stack.enter_async_context(session.begin())
                await stack.enter_async_context(sqlalchemy_rls_context(session, tenant_id))
                yield session

        return _cm()

    async def save(self, state: Any) -> None:
        """Upsert the adapter's latest state (awaited; raises on failure)."""
        from sqlalchemy import text

        payload = state.model_dump(mode="json")
        async with self._session() as session:
            await session.execute(
                text(
                    "INSERT INTO strategy_run_checkpoints "
                    "(tenant_id, goal_id, strategy_id, session_id, execution_id, "
                    " state_type, state) "
                    "VALUES (:tid, :gid, :sid, :sess, :exe, :typ, CAST(:state AS JSONB)) "
                    "ON CONFLICT (tenant_id, goal_id, strategy_id) DO UPDATE SET "
                    "session_id = EXCLUDED.session_id, execution_id = EXCLUDED.execution_id, "
                    "state_type = EXCLUDED.state_type, state = EXCLUDED.state, "
                    "updated_at = now()"
                ),
                {
                    **self._keys,
                    "sess": str(getattr(state, "session_id", "")),
                    "exe": str(getattr(state, "execution_id", "")),
                    "typ": type(state).__name__,
                    "state": json.dumps(payload, default=str),
                },
            )

    async def load(self, session_id: str, execution_id: str) -> BaseModel | None:
        from sqlalchemy import text

        async with self._session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT session_id, execution_id, state_type, state "
                        "FROM strategy_run_checkpoints "
                        "WHERE tenant_id = :tid AND goal_id = :gid AND strategy_id = :sid"
                    ),
                    self._keys,
                )
            ).mappings().first()
        if row is None or (row["session_id"], row["execution_id"]) != (session_id, execution_id):
            return None
        model = _state_types().get(str(row["state_type"]))
        if model is None:
            raise RuntimeError(f"unknown strategy checkpoint type: {row['state_type']!r}")
        raw = row["state"]
        state = model.model_validate(json.loads(raw) if isinstance(raw, str) else raw)
        return _retryable(state)

    async def put_answer(self, ref: str, answer: str) -> None:
        """Record a sub-task's answer under the reference its work item stores.

        Raises when the answer was not stored (the per-run cap is reached): a
        work item pointing at an answer that is not there would later be
        synthesized from nothing.
        """
        from sqlalchemy import text

        async with self._session() as session:
            stored = await session.execute(
                text(
                    "INSERT INTO strategy_run_checkpoints "
                    "(tenant_id, goal_id, strategy_id, answers) "
                    "VALUES (:tid, :gid, :sid, "
                    "jsonb_build_object(CAST(:ref AS TEXT), CAST(:ans AS TEXT))) "
                    "ON CONFLICT (tenant_id, goal_id, strategy_id) DO UPDATE SET "
                    "answers = CASE WHEN (SELECT count(*) FROM jsonb_object_keys("
                    "strategy_run_checkpoints.answers)) >= CAST(:cap AS INTEGER) "
                    "THEN strategy_run_checkpoints.answers "
                    "ELSE strategy_run_checkpoints.answers || "
                    "jsonb_build_object(CAST(:ref AS TEXT), CAST(:ans AS TEXT)) END, "
                    "updated_at = now() "
                    "RETURNING answers ? CAST(:ref AS TEXT)"
                ),
                {
                    **self._keys,
                    "ref": ref,
                    "ans": answer[:_MAX_ANSWER_CHARS],
                    "cap": _MAX_ANSWERS,
                },
            )
            if stored.scalar() is not True:
                raise RuntimeError(
                    f"strategy checkpoint answer cap ({_MAX_ANSWERS}) reached; answer not stored"
                )

    async def get_answer(self, ref: str) -> str | None:
        from sqlalchemy import text

        async with self._session() as session:
            value = (
                await session.execute(
                    text(
                        "SELECT answers ->> :ref FROM strategy_run_checkpoints "
                        "WHERE tenant_id = :tid AND goal_id = :gid AND strategy_id = :sid"
                    ),
                    {**self._keys, "ref": ref},
                )
            ).scalar()
        return str(value) if value is not None else None


def postgres_store_factory(
    db_getter: Callable[[], Any] | None,
) -> Callable[[Any], PostgresStrategyCheckpointStore | None]:
    """``request -> store`` when a DB is wired (via the zero-arg getter), else None."""

    def _for(request: Any) -> PostgresStrategyCheckpointStore | None:
        db = db_getter() if db_getter is not None else None
        if db is None:
            return None
        return PostgresStrategyCheckpointStore(
            db,
            tenant_id=str(request.tenant_id),
            goal_id=str(request.goal_id),
            strategy_id=str(request.strategy_id),
        )

    return _for


__all__ = ["PostgresStrategyCheckpointStore", "postgres_store_factory"]
