"""Celery application — task queues for goals, schedules, and maintenance."""
from __future__ import annotations

import os

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "agent_verse",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "app.scaling.tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_max_tasks_per_child=100,
    worker_max_memory_per_child=500_000,  # 500 MB in KB
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=30,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.scaling.tasks.run_goal": {"queue": "goals"},
        "app.scaling.tasks.run_goal_dlq": {"queue": "goals_dlq"},
        "app.scaling.tasks.run_scheduled_goal": {"queue": "schedules"},
        "app.scaling.tasks.fire_due_schedules": {"queue": "schedules"},
        "app.scaling.tasks.check_mcp_health": {"queue": "maintenance"},
        "app.scaling.tasks.health_check_mcp": {"queue": "maintenance"},
        "app.scaling.tasks.record_queue_depths": {"queue": "maintenance"},
        "app.scaling.tasks.detect_stuck_goals": {"queue": "maintenance"},
        "app.scaling.tasks.execute_retention_policy": {"queue": "maintenance"},
        "app.scaling.tasks.expire_hitl_approvals": {"queue": "maintenance"},
    },
    beat_schedule={
        "mcp-health-check-every-30s": {
            "task": "app.scaling.tasks.check_mcp_health",
            "schedule": 30.0,
            "options": {"queue": "maintenance"},
        },
        "fire-due-schedules-every-60s": {
            "task": "app.scaling.tasks.fire_due_schedules",
            "schedule": 60.0,
            "options": {"queue": "schedules"},
        },
        "record-queue-depths-every-30s": {
            "task": "app.scaling.tasks.record_queue_depths",
            "schedule": 30.0,
            "options": {"queue": "maintenance"},
        },
        "detect-stuck-goals": {
            "task": "app.scaling.tasks.detect_stuck_goals",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "maintenance"},
        },
        "execute-retention-policy": {
            "task": "app.scaling.tasks.execute_retention_policy",
            "schedule": crontab(hour=3, minute=0),  # 3 AM UTC daily
            "options": {"queue": "maintenance"},
        },
        "expire-hitl-approvals": {
            "task": "app.scaling.tasks.expire_hitl_approvals",
            "schedule": 60.0,  # every 60 seconds
            "options": {"queue": "maintenance"},
        },
    },
)
