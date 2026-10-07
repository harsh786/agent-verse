"""Senders never subscribe to Celery result channels (nothing reads a result).

With results on, apply_async from the API subscribed to the task's Redis result
channel; once that connection dropped, the consumer gave up permanently and every
workflow trigger answered 500 ("Trigger failed") until the API restarted.
"""

from app.scaling.celery_app import celery_app


def test_results_are_ignored_app_wide() -> None:
    assert celery_app.conf.task_ignore_result is True


def test_workflow_and_goal_tasks_ignore_results() -> None:
    import app.scaling.tasks  # noqa: F401 - registers the goal tasks
    import app.workflow.celery_tasks  # noqa: F401 - registers the workflow tasks

    for name in ("workflow.execute_workflow_run",):
        task = celery_app.tasks[name]
        assert task.ignore_result is True, name
