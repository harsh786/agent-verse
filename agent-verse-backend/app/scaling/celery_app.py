"""Celery application — task queues for goals, schedules, and maintenance."""

from __future__ import annotations

import os

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_SENTINEL_URLS = os.getenv("REDIS_SENTINEL_URLS", "")
_SENTINEL_MASTER = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")


def _build_celery_broker_url() -> str:
    """Build the Celery broker URL, adding Sentinel support when configured.

    Celery's Redis Sentinel transport uses the ``sentinel://`` scheme::

        sentinel://[:password@]host1:port1;host2:port2/db?master_name=master

    Multiple Sentinel nodes are separated by ``;`` (semicolons) in Celery's
    format.  The ``master_name`` is passed separately via
    ``broker_transport_options`` rather than in the URL query-string, because
    Celery only reads it from the transport options dict.
    """
    if _SENTINEL_URLS:
        password = os.getenv("REDIS_SENTINEL_PASSWORD") or os.getenv("REDIS_PASSWORD", "")
        auth = f":{password}@" if password else ""
        nodes = ";".join(entry.strip() for entry in _SENTINEL_URLS.split(",") if entry.strip())
        db = os.getenv("REDIS_SENTINEL_DB", "0")
        return f"sentinel://{auth}{nodes}/{db}"
    return REDIS_URL


_BROKER_URL = _build_celery_broker_url()

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
    broker=_BROKER_URL,
    # Result backend stays single-node — cross-shard atomic ops not needed.
    backend=REDIS_URL,
    include=[
        "app.scaling.tasks",
        "app.workflow.celery_tasks",  # Workflow Automation Engine tasks
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
        # Default goal queue — falls back to goals.free when no queue is specified.
        # At dispatch time CeleryGoalTaskQueue overrides this via apply_async(queue=).
        "app.scaling.tasks.run_goal": {"queue": "goals.free"},
        # Org mission team-formation + dispatch — worker subscribes to "goals".
        "app.scaling.tasks.execute_org_mission": {"queue": "goals"},
        "app.scaling.tasks.resweep_stuck_missions": {"queue": "maintenance"},
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
        # ── Workflow Automation Engine ─────────────────────────────────────────
        # The tasks in app/workflow/celery_tasks.py register under explicit
        # ``name="workflow.*"`` (not their dotted module path), so route on those
        # registered names. WorkflowRunner dispatch also passes an explicit
        # ``queue=workflows.{plan_tier}`` which overrides these at apply_async
        # time; the routes here are the fallback when no queue is supplied (e.g.
        # a bare ``.delay()`` or a beat entry) and keep every workflow task on a
        # ``workflows.*`` queue.
        "workflow.execute_workflow_run": {"queue": "workflows.free"},
        "workflow.check_hitl_escalations": {"queue": "workflows.maintenance"},
        "workflow.retry_dead_letter_webhooks": {"queue": "workflows.maintenance"},
        "workflow.cleanup_expired_runs": {"queue": "workflows.maintenance"},
        "workflow.fire_due_workflow_schedules": {"queue": "workflows.maintenance"},
        # Legacy dotted-path keys (kept for backwards-compat; do not match the
        # registered task names above, but harmless).
        "app.workflow.celery_tasks.execute_workflow_run": {"queue": "workflows.free"},
        "app.workflow.celery_tasks.check_hitl_escalations": {"queue": "workflows.maintenance"},
        "app.workflow.celery_tasks.retry_dead_letter_webhooks": {"queue": "workflows.maintenance"},
        "app.workflow.celery_tasks.cleanup_expired_runs": {"queue": "workflows.maintenance"},
        "agentverse.workflows.run_free": {"queue": "workflows.free"},
        "agentverse.workflows.run_starter": {"queue": "workflows.starter"},
        "agentverse.workflows.run_professional": {"queue": "workflows.professional"},
        "agentverse.workflows.run_enterprise": {"queue": "workflows.enterprise"},
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
        "resweep-stuck-missions": {
            "task": "app.scaling.tasks.resweep_stuck_missions",
            "schedule": 120.0,  # every 2 minutes — recover missions whose dispatch task was lost
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
        # Ingestion: dispatch due sources every 60s, retry DLQ every 5min
        "ingestion-dispatch-due-sources": {
            "task": "ingestion.dispatch_due_sources",
            "schedule": 60.0,
            "options": {"queue": "ingestion"},
        },
        "ingestion-retry-dlq": {
            "task": "ingestion.retry_dlq_entries",
            "schedule": 300.0,
            "options": {"queue": "ingestion"},
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
        # ── N8: Org Autonomous Loop — runs every 5 minutes ─────────────────
        "org-brain-autonomous-loop": {
            "task": "app.scaling.tasks.org_brain_loop",
            "schedule": 300.0,  # every 5 minutes
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
        # ── Workflow Automation Engine beat tasks ─────────────────────────────
        # NOTE: these tasks register under their explicit ``workflow.*`` names
        # (see @celery_app.task(name=...) in app/workflow/celery_tasks.py), NOT
        # their dotted module path — beat entries must reference the registered
        # name or the schedule fires an unregistered-task error.
        "workflow-check-hitl-escalations": {
            "task": "workflow.check_hitl_escalations",
            "schedule": 900.0,  # every 15 minutes
            "options": {"queue": "workflows.maintenance"},
        },
        "workflow-retry-dead-letter-webhooks": {
            "task": "workflow.retry_dead_letter_webhooks",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "workflows.maintenance"},
        },
        "workflow-cleanup-expired-runs": {
            "task": "workflow.cleanup_expired_runs",
            "schedule": crontab(hour=2, minute=0),  # 2 AM UTC daily
            "options": {"queue": "workflows.maintenance"},
        },
        # Item 4: fire published workflows whose cron schedule trigger is due.
        "workflow-fire-due-schedules": {
            "task": "workflow.fire_due_workflow_schedules",
            "schedule": 60.0,  # every 60 seconds
            "options": {"queue": "workflows.maintenance"},
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

# ── Redis Sentinel transport options ──────────────────────────────────────────
# When Sentinel is active, tell Celery which master name to watch and point the
# result backend at the same Sentinel topology.
if _SENTINEL_URLS:
    celery_app.conf.broker_transport_options = {
        "master_name": _SENTINEL_MASTER,
        "sentinel_kwargs": {},
    }
    # Result backend mirrors the broker topology so failover works end-to-end.
    celery_app.conf.result_backend = _BROKER_URL
    celery_app.conf.redis_backend_use_ssl = REDIS_URL.startswith("rediss://")

    # RedBeat also needs the Sentinel URL so the lock key survives failover.
    if getattr(celery_app.conf, "beat_scheduler", "").endswith("RedBeatScheduler"):
        celery_app.conf.redbeat_redis_url = _BROKER_URL

# Backwards-compatible alias used by some imports
app = celery_app
