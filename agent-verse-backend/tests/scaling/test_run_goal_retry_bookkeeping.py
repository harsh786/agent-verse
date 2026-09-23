"""Regression tests for run_goal's exception/retry bookkeeping.

Two related distributed-execution bugs in ``app.scaling.tasks.run_goal``:

1. On ANY exception raised while running the agent (including a purely
   transient one, e.g. a rate limit or network blip that Celery will retry),
   the task unconditionally called ``mark_worker_failed(exc)`` *before*
   deciding whether a retry was even going to happen. ``mark_worker_failed``
   sets the goal's DB status to "failed" and (via ``_finalize_owning_mission``)
   permanently finalizes any owning org mission — closing subtasks, disbanding
   the team, emitting ``mission.failed``. ``finalize_mission`` only
   reconciles missions that are still "active", so once a mission is
   incorrectly finalized "failed" by a transient error, a *later successful*
   retry's own ``mark_worker_complete -> _finalize_owning_mission`` call finds
   the mission already terminal and silently no-ops — the mission stays
   wrongly "failed" forever even though the goal went on to succeed.

2. The DLQ / max-retries-exhausted handling
   (``except self.MaxRetriesExceededError:``) was unreachable dead code.
   Celery's ``Task.retry(exc=exc, ...)`` re-raises the *original* exception
   once retries are exhausted whenever ``exc`` is passed while an exception is
   active (``celery.app.task.raise_with_context`` does a bare ``raise`` when
   ``sys.exc_info()[1] is exc``), instead of raising
   ``MaxRetriesExceededError``. So a goal that truly exhausts its retries
   (a poison-pill goal, or a permanently broken tenant) never actually reached
   ``run_goal_dlq.delay(...)``, never got its terminal DB status update, and —
   worst of all — never decremented the per-tenant concurrent-goal counter,
   which would leak forever and eventually block all future goal submissions
   for that tenant.

Both scenarios are exercised end-to-end through ``run_goal.run(...)`` (not
mocked at the exception-handling level) using a fake ``AgentGraph`` whose
``run()`` always raises, so the real exception-handling code in
``app.scaling.tasks.run_goal`` is what's under test. Celery's own retry/eta
scheduling is bypassed by manipulating ``self.request`` directly via
``push_request``/``pop_request`` (``called_directly`` decides whether
``self.retry()`` bare-re-raises immediately, matching real worker semantics
for the "not yet exhausted" vs a controlled "exhausted" check on
``self.request.retries``), so the test runs in milliseconds instead of
sleeping through Celery's real exponential backoff.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


class _RaisingAgentGraph:
    """Fake AgentGraph whose run() always raises — simulates a failing step."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        raise RuntimeError("transient LLM provider error")


@pytest.fixture
def _mission_and_dlq_spies():
    """Patch the three side effects a terminal failure must (and must only
    exhaustively) trigger, and hand back lists recording each call.
    """
    from app.scaling import tasks

    finalize_calls: list[tuple[str, str]] = []
    dlq_calls: list[dict[str, Any]] = []
    decrement_calls: list[str] = []

    async def fake_finalize(goal_id: str, tenant_id: str) -> None:
        finalize_calls.append((goal_id, tenant_id))

    def fake_dlq_delay(**kwargs: Any) -> None:
        dlq_calls.append(kwargs)

    async def fake_decrement(tenant_id: str, redis_url: str) -> None:
        decrement_calls.append(tenant_id)

    with (
        patch.object(tasks, "_finalize_owning_mission", fake_finalize),
        patch.object(tasks.run_goal_dlq, "delay", fake_dlq_delay),
        patch.object(tasks, "_decrement_after_completion", fake_decrement),
    ):
        yield {
            "finalize_calls": finalize_calls,
            "dlq_calls": dlq_calls,
            "decrement_calls": decrement_calls,
        }


def test_transient_failure_does_not_finalize_mission_or_touch_dlq(
    monkeypatch: pytest.MonkeyPatch, _mission_and_dlq_spies: dict[str, list[Any]]
) -> None:
    """An error on a retryable attempt must NOT finalize the owning mission.

    Before the fix: `mark_worker_failed(exc)` ran unconditionally on every
    exception, finalizing any owning org mission as permanently "failed" even
    though Celery was about to retry the goal and it might still succeed.
    """
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    monkeypatch.setattr(_graph_mod, "AgentGraph", _RaisingAgentGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    # Attempt 0 of 3 max_retries, called directly like a real (non-eager)
    # worker invocation — Celery bare-re-raises the original exception via
    # `raise_with_context` regardless of retries remaining when
    # `called_directly` is True, exactly like the exception propagating out
    # of a real task body that the worker will reschedule.
    tasks.run_goal.push_request(retries=0, called_directly=True)
    try:
        with pytest.raises(RuntimeError, match="transient LLM provider error"):
            tasks.run_goal.run("goal-transient-1", "tenant-1", "do the thing")
    finally:
        tasks.run_goal.pop_request()

    assert _mission_and_dlq_spies["finalize_calls"] == [], (
        "a retryable (non-exhausted) failure must not finalize the owning "
        "mission — it can still succeed on retry"
    )
    assert _mission_and_dlq_spies["dlq_calls"] == [], (
        "a retryable failure must not be routed to the DLQ"
    )
    assert _mission_and_dlq_spies["decrement_calls"] == [], (
        "the concurrent-goal counter must stay held while the goal may still "
        "retry and complete"
    )


def test_retries_exhausted_reaches_dlq_and_releases_counter(
    monkeypatch: pytest.MonkeyPatch, _mission_and_dlq_spies: dict[str, list[Any]]
) -> None:
    """Once retries are exhausted, the terminal bookkeeping must actually run.

    Before the fix, this branch was unreachable: Celery's
    ``Task.retry(exc=exc, ...)`` re-raises the original exception (not
    ``MaxRetriesExceededError``) once retries are exhausted, so
    ``run_goal_dlq.delay`` was never called, the goal's DB status was never
    durably updated with the DLQ-routed message, and the per-tenant
    concurrent-goal counter was never decremented — a permanent leak.
    """
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    monkeypatch.setattr(_graph_mod, "AgentGraph", _RaisingAgentGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    # Simulate the final attempt: retries already equals max_retries (3).
    tasks.run_goal.push_request(retries=3, called_directly=False)
    try:
        result = tasks.run_goal.run("goal-exhausted-1", "tenant-1", "do the thing")
    finally:
        tasks.run_goal.pop_request()

    assert result == {"status": "dead_lettered", "goal_id": "goal-exhausted-1"}
    assert _mission_and_dlq_spies["dlq_calls"] == [
        {
            "goal_id": "goal-exhausted-1",
            "tenant_id": "tenant-1",
            "goal_text": "do the thing",
            "reason": "max_retries_exceeded",
        }
    ], "an exhausted goal must be routed to the dead-letter queue"
    assert _mission_and_dlq_spies["decrement_calls"] == ["tenant-1"], (
        "the concurrent-goal counter must be released once the goal is "
        "permanently done, or it leaks forever"
    )
    # Now that the goal is genuinely terminal, finalizing any owning mission
    # is correct (and must happen exactly once).
    assert _mission_and_dlq_spies["finalize_calls"] == [("goal-exhausted-1", "tenant-1")]
