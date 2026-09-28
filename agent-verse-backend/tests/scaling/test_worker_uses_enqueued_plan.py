"""Regression: the Celery worker ran every goal with PROFESSIONAL limits.

run_goal ignored the ``plan`` the API enqueued and read a ``plan`` field from
the LLM-config cache that nothing writes, falling back to "professional" — a
free-tier goal got the 8-hour professional timeout (and other plan limits).
"""

from __future__ import annotations

import inspect


def test_run_goal_builds_its_tenant_context_from_the_enqueued_plan() -> None:
    from app.scaling import tasks

    src = inspect.getsource(tasks.run_goal)
    assert 'get("plan", "professional")' not in src
    assert "plan = PlanTier(plan)" in src
    # An unknown plan must fall back to the most restrictive tier.
    assert "plan = PlanTier.FREE" in src
