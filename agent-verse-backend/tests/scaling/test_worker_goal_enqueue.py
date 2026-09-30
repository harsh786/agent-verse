"""Regression: goals created inside a Celery task must be enqueued, not orphaned.

``_build_worker_goal_service`` (scheduled triggers) and the IMAP email task built
a GoalService with no task queue, so ``submit_goal`` started the goal as a
background asyncio task inside ``_run_async``'s throwaway event loop — which is
closed as soon as the task body returns, silently killing the goal. These tests
drive the real worker path (real GoalService, real TriggerDispatcher, real
``_run_async`` loop) and assert the goal reached the ``run_goal`` Celery task.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def captured_run_goal():
    from app.scaling import tasks

    fake = MagicMock()
    fake.return_value = MagicMock(id="celery-task-1")
    with patch.object(tasks.run_goal, "apply_async", fake):
        yield fake


def _no_db():
    # No Postgres in unit tests: the worker path must still hand the goal to
    # the queue (persistence failures are covered elsewhere).
    return patch("app.db.session.get_session_factory", return_value=None)


def test_worker_goal_service_carries_task_queue() -> None:
    from app.scaling.tasks import _build_worker_goal_service

    with _no_db():
        goal_service, _ = _build_worker_goal_service()
    assert goal_service is not None
    assert goal_service._task_queue is not None


def test_scheduled_goal_is_enqueued_via_real_worker_path(captured_run_goal) -> None:
    from app.scaling.tasks import _run_async, _run_scheduled_goal_governed
    from app.tenancy.context import PlanTier

    async def _professional(*_a: object, **_k: object) -> PlanTier:
        return PlanTier.PROFESSIONAL

    with (
        _no_db(),
        patch("app.scaling.tasks._worker_async_redis", return_value=None),
        # TRG-06: the plan comes from the tenant record at fire time.
        patch("app.tenancy.plan_resolver.resolve_tenant_plan", new=_professional),
    ):
        event = _run_async(
            _run_scheduled_goal_governed(
                "sched-1",
                "tenant-1",
                "Summarise the nightly report",
                "",
                "2026-09-28T09:00:00Z",
            )
        )

    assert event.goal_created is True, event
    captured_run_goal.assert_called_once()
    kwargs = captured_run_goal.call_args.kwargs
    assert kwargs["kwargs"]["goal_id"] == event.goal_id
    assert kwargs["kwargs"]["tenant_id"] == "tenant-1"
    assert kwargs["kwargs"]["goal_text"] == "Summarise the nightly report"
    assert kwargs["queue"] == "goals.professional"


def test_email_goals_are_enqueued(captured_run_goal) -> None:
    from app.scaling.tasks import _do_check_email_goals, _run_async

    async def _fake_check(goal_service, ctx):
        await goal_service.submit_goal(
            goal="Reply to the customer email",
            priority="normal",
            dry_run=False,
            tenant_ctx=ctx,
        )
        return 1

    with (
        _no_db(),
        patch(
            "app.integrations.email.imap_listener.check_and_process_emails",
            new=_fake_check,
        ),
    ):
        result = _run_async(_do_check_email_goals())

    assert result == {"status": "ok", "processed": 1}
    captured_run_goal.assert_called_once()
    assert captured_run_goal.call_args.kwargs["kwargs"]["goal_text"] == (
        "Reply to the customer email"
    )
