"""Regression: worker-run goals ignored the agent's pinned model (model_override).

Only the API path applied it (GoalService -> ModelRouter.with_override); the
Celery worker's agent lookup didn't even select the column.
"""

from __future__ import annotations

import inspect


def test_worker_reads_and_applies_the_agent_model_override() -> None:
    from app.scaling import tasks

    src = inspect.getsource(tasks.run_goal)
    assert "model_override FROM agents" in src
    assert "with_override(_agent_model_override)" in src


def test_with_override_pins_every_role() -> None:
    from app.agent.model_router import ModelRouter

    router = ModelRouter().with_override("pinned-model")
    for task in ("planning", "execution", "verification"):
        assert router.model_for(task) == "pinned-model"
