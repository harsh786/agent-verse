"""Celery application — task queues for goals, schedules, and maintenance."""
from __future__ import annotations

import os

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Per-plan queue routing ─────────────────────────────────────────────────────
# Enterprise tenants get dedicated queues to prevent noisy-neighbour effects.
# Worker -Q flag must include all plan queues:
#   -Q goals,goals.free,goals.starter,goals.professional,goals.enterprise,
#      goals_dlq,schedules,maintenance
PLAN_QUEUE_MAP = {
    "free": "goals.free",
    "starter": "goals.starter",
    "professional": "goals.professional",
    "enterprise": "goals.enterprise",
}

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
        # Default goal queue — workers pick up based on tenant plan at dispatch time.
        # Per-plan queues allow enterprise tenants to get dedicated worker pools.
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
        "app.scaling.tasks.check_email_goals": {"queue": "maintenance"},
        # GDPR export — runs in background, long-running
        "agentverse.compliance.run_gdpr_export": {"queue": "maintenance"},
        # Per-plan routing aliases (workers can subscribe to these specific queues)
        "agentverse.goals.run_goal_free": {"queue": "goals.free"},
        "agentverse.goals.run_goal_starter": {"queue": "goals.starter"},
        "agentverse.goals.run_goal_professional": {"queue": "goals.professional"},
        "agentverse.goals.run_goal_enterprise": {"queue": "goals.enterprise"},
        "agentverse.goals.run_goal_dlq": {"queue": "goals_dlq"},
        "agentverse.schedules.*": {"queue": "schedules"},
        "agentverse.maintenance.*": {"queue": "maintenance"},
        # Civilization tasks
        "app.scaling.tasks.civilization_tick": {"queue": "maintenance"},
        "app.scaling.tasks.civilization_learning_step": {"queue": "maintenance"},
        "app.scaling.tasks.discover_and_tick_civilizations": {"queue": "maintenance"},
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
        "check-email-goals": {
            "task": "app.scaling.tasks.check_email_goals",
            "schedule": 60.0,  # every 60 seconds
            "options": {"queue": "maintenance"},
        },
        "drain-goals-dlq-every-5min": {
            "task": "app.scaling.tasks.run_goal_dlq",
            "schedule": 300.0,  # every 5 minutes — drain Dead Letter Queue
            "options": {"queue": "goals_dlq"},
        },
        # Freshness reindex: mark stale knowledge chunks hourly
        "reindex-stale-knowledge": {
            "task": "agentverse.maintenance.reindex_stale_knowledge",
            "schedule": 3600,
            "options": {"queue": "maintenance"},
        },
        "purge-expired-artifacts-daily": {
            "task": "agentverse.maintenance.purge_expired_artifacts",
            "schedule": 86400,  # daily
            "options": {"queue": "maintenance"},
        },
        "civilization-discovery-every-30s": {
            "task": "app.scaling.tasks.discover_and_tick_civilizations",
            "schedule": 30,
            "options": {"queue": "maintenance"},
        },
        # ── M-1: Eight new maintenance tasks ──────────────────────────────
        "warm-jwks-cache": {
            "task": "app.scaling.tasks.warm_jwks_cache",
            "schedule": crontab(minute="*/9"),
            "options": {"queue": "maintenance"},
        },
        "create-guardrail-partitions": {
            "task": "app.scaling.tasks.create_guardrail_partitions",
            "schedule": crontab(day_of_month="1", hour="2"),
            "options": {"queue": "maintenance"},
        },
        "enforce-hitl-sla": {
            "task": "app.scaling.tasks.enforce_hitl_sla",
            "schedule": crontab(minute="*/5"),
            "options": {"queue": "maintenance"},
        },
        "flush-audit-wal": {
            "task": "app.scaling.tasks.flush_audit_wal",
            "schedule": 10.0,
            "options": {"queue": "maintenance"},
        },
        "scan-cost-anomalies": {
            "task": "app.scaling.tasks.scan_cost_anomalies",
            "schedule": crontab(minute="0"),
            "options": {"queue": "maintenance"},
        },
        "embed-marketplace-templates": {
            "task": "app.scaling.tasks.embed_marketplace_templates",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "maintenance"},
        },
        "conclude-stale-experiments": {
            "task": "app.scaling.tasks.conclude_stale_experiments",
            "schedule": crontab(hour="3", minute="0"),
            "options": {"queue": "maintenance"},
        },
        "expire-stale-documents": {
            "task": "app.scaling.tasks.expire_stale_documents",
            "schedule": crontab(hour="1", minute="0"),
            "options": {"queue": "maintenance"},
        },
    },
)

# ── RedBeat HA Beat Scheduler ──────────────────────────────────────────────────
# Allows multiple beat replicas — only one acquires the Redis lock at a time.
# Requires: pip install celery-redbeat
try:
    import redbeat  # type: ignore[import]  # noqa: F401
    celery_app.conf.beat_scheduler = "redbeat.RedBeatScheduler"
    celery_app.conf.redbeat_redis_url = REDIS_URL
    celery_app.conf.redbeat_lock_key = "agentverse:beat:lock"
    celery_app.conf.redbeat_lock_timeout = 300  # 5 minutes
except ImportError:
    # redbeat not installed — falls back to default file-based beat scheduler
    pass

# Backwards-compatible alias used by some imports
app = celery_app

