"""Celery application — task queues for goals, schedules, and maintenance."""

from __future__ import annotations

import itertools
import os
from datetime import timedelta
from typing import Any

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]
from celery.signals import (  # type: ignore[import-untyped]
    beat_init,
    worker_init,
    worker_process_init,
)

from app.observability.log_redaction import install_log_redaction

# OI-3: workers and beat never call configure_logging(); mask credentials in
# every stdlib and structlog line (messages, exception text, tracebacks).
install_log_redaction()

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
# The shipped workers (infra/docker-compose*.yml, infra/k8s/worker-deployment.yaml,
# infra/helm/agentverse/values.yaml) must together consume EVERY queue a task is
# routed to — tests/scaling/test_worker_queue_coverage.py enforces this:
#   celery,goals,goals.free,goals.starter,goals.professional,goals.enterprise,
#   goals_dlq,schedules,maintenance,governance,ingestion,
#   workflows.free,workflows.starter,workflows.professional,workflows.enterprise,
#   workflows.maintenance,
#   goals.subgoals.free,goals.subgoals.starter,goals.subgoals.professional,
#   goals.subgoals.enterprise  (dedicated sub-goal pool only, see below)
PLAN_QUEUE_MAP = {
    "free": "goals.free",
    "starter": "goals.starter",
    "professional": "goals.professional",
    "enterprise": "goals.enterprise",
}

# ── Supervisor sub-goal queues (CORE-09) ───────────────────────────────────────
# A worker-run supervisor parent holds its Celery slot while it waits for its
# sub-goals. Sub-goals used to share the parent's goals.{plan} queue, so a pool
# whose slots were all held by waiting parents never ran their children (the
# parent starved, or deadlocked, its own sub-goals). Sub-goals therefore go to
# their own per-plan queue family, consumed ONLY by a dedicated sub-goal worker
# pool (the ``subgoal-worker`` service / deployment). The main goal worker must
# never consume these queues (tests/scaling/test_worker_queue_coverage.py). A
# sub-goal never runs the supervisor itself (SUBGOAL_MARKER), so the sub-goal
# pool cannot be starved the same way.
SUBGOAL_QUEUE_MAP = {plan: f"goals.subgoals.{plan}" for plan in PLAN_QUEUE_MAP}


def goal_queue_for(plan: str, *, subgoal: bool = False) -> str:
    """The Celery queue a goal of *plan* is dispatched to (unknown plan: free)."""
    if subgoal:
        return SUBGOAL_QUEUE_MAP.get(plan, "goals.subgoals.free")
    return PLAN_QUEUE_MAP.get(plan, "goals.free")


celery_app = Celery(
    "agent_verse",
    broker=_BROKER_URL,
    # Result backend stays single-node — cross-shard atomic ops not needed.
    backend=REDIS_URL,
    include=[
        "app.scaling.tasks",
        "app.workflow.celery_tasks",  # Workflow Automation Engine tasks
        # Ingestion scheduler (shared_tasks): beat schedules
        # ingestion.dispatch_due_sources / retry_dlq_entries, which were never
        # registered on the worker without this import.
        "app.ingestion.scheduler",
        # Durable repository (git clone) ingestion (POST /knowledge/ingest/repo).
        "app.ingestion.repo_tasks",
        # RAFT fine-tune status poller (beat: poll-raft-fine-tune-jobs).
        "app.scaling.raft_tasks",
        "app.scaling.event_outbox_tasks",
        # Inbound A2A task outcomes + callbacks (beat: reconcile-a2a-tasks, A2A-01).
        "app.scaling.a2a_tasks",
        # Expired strategy-evidence purge (beat: purge-expired-strategy-evidence).
        "app.orchestration.evidence_maintenance",
        # Coordination outbox delivery (beat: dispatch-coordination-outbox).
        "app.coordination.outbox_tasks",
        # Coordination pattern runs admitted by the REST route (ORG-39).
        "app.coordination.pattern_runs.tasks",
        # Durable training-data export jobs (POST /intelligence/export-training-data/jobs).
        "app.training_export.tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_max_tasks_per_child=100,
    # L-03: a child that has loaded the reranker (torch + sentence-transformers +
    # model) sits at ~600 MB; a 500 MB cap recycled it after every search. Size
    # each pool's memory limit as parent + concurrency x (cap + one task's
    # growth) — tests/scaling/test_worker_memory_budget.py checks the manifests.
    worker_max_memory_per_child=700_000,  # 700 MB in KB
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
        "app.scaling.tasks.fire_due_org_mission_schedules": {"queue": "schedules"},
        # Scheduled-mission deliverable publishing (event-driven, no beat entry).
        "app.scaling.tasks.publish_mission_deliverable": {"queue": "goals"},
        "app.scaling.tasks.run_goal_dlq": {"queue": "goals_dlq"},
        "app.scaling.tasks.run_scheduled_goal": {"queue": "schedules"},
        "app.scaling.tasks.fire_due_schedules": {"queue": "schedules"},
        # TRG-54: one task per polling trigger, off the beat, on its own queue.
        "app.scaling.tasks.poll_trigger": {"queue": "triggers.poll"},
        "app.scaling.tasks.check_mcp_health": {"queue": "maintenance"},
        "app.scaling.tasks.health_check_mcp": {"queue": "maintenance"},
        "app.scaling.tasks.record_queue_depths": {"queue": "maintenance"},
        "app.scaling.tasks.detect_stuck_goals": {"queue": "maintenance"},
        "app.scaling.tasks.reap_stale_goal_runners": {"queue": "maintenance"},
        "app.scaling.a2a_tasks.reconcile_a2a_tasks": {"queue": "maintenance"},
        "app.scaling.a2a_tasks.deliver_a2a_callback": {"queue": "maintenance"},
        "app.scaling.tasks.execute_retention_policy": {"queue": "maintenance"},
        "app.scaling.tasks.expire_hitl_approvals": {"queue": "maintenance"},
        "app.scaling.tasks.check_email_goals": {"queue": "maintenance"},
        # AI-Ops dataset runs (durable, resumable per case) — MEM-25. Short,
        # non-blocking steps that poll their case goals (P7-1).
        "app.scaling.tasks.run_ai_ops_dataset": {"queue": "maintenance"},
        "app.scaling.tasks.resume_stalled_ai_ops_runs": {"queue": "maintenance"},
        "app.scaling.tasks.run_eval_suite_worker": {"queue": "maintenance"},
        "app.scaling.tasks.resume_stalled_eval_suite_runs": {"queue": "maintenance"},
        "app.scaling.raft_tasks.poll_raft_fine_tune_jobs": {"queue": "maintenance"},
        "app.scaling.event_outbox_tasks.drain_goal_event_outbox": {"queue": "maintenance"},
        # GDPR export — runs in background, long-running
        "agentverse.compliance.run_gdpr_export": {"queue": "maintenance"},
        # Training-data export jobs (OPS-37) — long-running, streamed to object storage.
        "agentverse.training_export.run": {"queue": "maintenance"},
        "agentverse.training_export.expire": {"queue": "maintenance"},
        # Per-plan routing aliases (workers can subscribe to these specific queues)
        "agentverse.goals.run_goal_free": {"queue": "goals.free"},
        "agentverse.goals.run_goal_starter": {"queue": "goals.starter"},
        "agentverse.goals.run_goal_professional": {"queue": "goals.professional"},
        "agentverse.goals.run_goal_enterprise": {"queue": "goals.enterprise"},
        "agentverse.goals.run_goal_dlq": {"queue": "goals_dlq"},
        "agentverse.schedules.*": {"queue": "schedules"},
        # Ingestion tasks (sync_source is enqueued by dispatch_due_sources with no
        # explicit queue) share the beat entries' ``ingestion`` queue.
        "ingestion.*": {"queue": "ingestion"},
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
        "workflow.wake_due_timer_waits": {"queue": "workflows.maintenance"},
        "workflow.redispatch_stuck_runs": {"queue": "workflows.maintenance"},
        "workflow.deliver_workflow_callback": {"queue": "workflows.maintenance"},
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
        # NF-17: finished training-export files are deleted after their retention.
        "expire-training-exports-hourly": {
            "task": "agentverse.training_export.expire",
            "schedule": 3600.0,
            "options": {"queue": "maintenance"},
        },
        # MEM-53: re-dispatch eval-suite runs whose workers died.
        "resume-stalled-eval-suite-runs-every-60s": {
            "task": "app.scaling.tasks.resume_stalled_eval_suite_runs",
            "schedule": 60.0,
            "options": {"queue": "maintenance"},
        },
        # P7-1: re-dispatch AI-Ops dataset runs whose step chain died.
        "resume-stalled-ai-ops-runs-every-60s": {
            "task": "app.scaling.tasks.resume_stalled_ai_ops_runs",
            "schedule": 60.0,
            "options": {"queue": "maintenance"},
        },
        "mcp-health-check-every-30s": {
            "task": "app.scaling.tasks.check_mcp_health",
            "schedule": 30.0,
            "options": {"queue": "maintenance"},
        },
        # B1-7 / B1-16: every 15 s, so a slot fires at most ~15 s late whatever
        # second the beat started on (a 60 s interval counted from the beat's
        # start fired up to 59 s late, and RedBeat keeps a crontab entry on the
        # second of its first run). Each tick is one indexed claim of due rows;
        # the beat guard keeps ticks from overlapping. A tick no worker took
        # in time expires: the next one covers it (slots come from last_fired_at).
        "fire-due-schedules-every-60s": {
            "task": "app.scaling.tasks.fire_due_schedules",
            "schedule": 15.0,
            "options": {"queue": "schedules", "expires": 14},
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
        # GOAL-STALL: a goal whose runner stopped heart-beating (dead / wedged
        # worker) is requeued or failed within ~goal_heartbeat_stale_seconds.
        "reap-stale-goal-runners": {
            "task": "app.scaling.tasks.reap_stale_goal_runners",
            "schedule": 60.0,
            "options": {"queue": "maintenance"},
        },
        # A2A-01: A2A task outcomes + callback delivery/retries (durable).
        "reconcile-a2a-tasks": {
            "task": "app.scaling.a2a_tasks.reconcile_a2a_tasks",
            "schedule": 30.0,
            "options": {"queue": "maintenance"},
        },
        "resweep-stuck-missions": {
            "task": "app.scaling.tasks.resweep_stuck_missions",
            "schedule": 120.0,  # every 2 minutes — recover missions whose dispatch task was lost
            "options": {"queue": "maintenance"},
        },
        "fire-due-org-mission-schedules": {
            "task": "app.scaling.tasks.fire_due_org_mission_schedules",
            "schedule": 60.0,  # every 60s — launch autonomous missions for due schedules
            "options": {"queue": "schedules"},
        },
        # Data-subject rights. These existed as tasks but were never scheduled,
        # so recorded erasure requests were never executed.
        "process-dpdp-erasures": {
            "task": "agentverse.process_dpdp_erasures",
            "schedule": crontab(minute=20),  # hourly
            "options": {"queue": "maintenance"},
        },
        "process-tenant-erasures": {
            "task": "agentverse.maintenance.process_tenant_erasures",
            "schedule": crontab(minute=40),  # hourly (jobs are due after a 30-day grace)
            "options": {"queue": "maintenance"},
        },
        "execute-retention-policy": {
            "task": "app.scaling.tasks.execute_retention_policy",
            "schedule": crontab(hour=3, minute=0),  # 3 AM UTC daily
            "options": {"queue": "maintenance"},
        },
        # Coordination outbox -> Redis Streams. Rows were written in the same
        # transaction as each coordination state change but never delivered.
        # Exclusive per-row claims (SKIP LOCKED) make overlapping ticks safe.
        "dispatch-coordination-outbox": {
            "task": "agentverse.coordination.dispatch_outbox",
            "schedule": 5.0,
            "options": {"queue": "maintenance"},
        },
        # ORG-38: expire handoffs whose target crashed (past deadline) and re-run
        # parent resumes that failed after the committed transition.
        "sweep-coordination-handoffs": {
            "task": "agentverse.coordination.sweep_handoffs",
            "schedule": 60.0,
            "options": {"queue": "maintenance"},
        },
        "purge-expired-strategy-evidence": {
            "task": "agentverse.maintenance.purge_expired_strategy_evidence",
            # Hourly: one row per strategy per finished goal must be purged at
            # write rate (CORE-36); each run drains the backlog (time-boxed).
            "schedule": crontab(minute=15),
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
        # RAFT: advance submitted/running fine-tune jobs (bounded batch per tick)
        # so a finished model becomes deployable without a manual refresh.
        # Goal events whose durable append failed are parked in a Redis outbox
        # (SVC-08); replay them so the event history has no holes.
        "drain-goal-event-outbox": {
            "task": "app.scaling.event_outbox_tasks.drain_goal_event_outbox",
            "schedule": 30.0,
            "options": {"queue": "maintenance"},
        },
        "poll-raft-fine-tune-jobs": {
            "task": "app.scaling.raft_tasks.poll_raft_fine_tune_jobs",
            "schedule": 120.0,  # every 2 minutes
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
        # Fail jobs a lost worker left "running" (older than the lock TTL).
        "ingestion-reap-stale-jobs": {
            "task": "ingestion.reap_stale_jobs",
            "schedule": 600.0,
            "options": {"queue": "ingestion"},
        },
        # (reindex-stale-knowledge removed: it marked a legacy table nothing
        # reads — see app.scaling.tasks.reindex_stale_knowledge. Freshness comes
        # from per-Source re-sync via ingestion-dispatch-due-sources.)
        "purge-expired-artifacts-daily": {
            "task": "agentverse.maintenance.purge_expired_artifacts",
            "schedule": 86400,  # daily
            "options": {"queue": "maintenance"},
        },
        # HEALTH-06: connector health snapshots past CONNECTOR_HEALTH_RETENTION_DAYS.
        "prune-connector-health-snapshots-hourly": {
            "task": "agentverse.maintenance.prune_connector_health_snapshots",
            "schedule": 3600,
            "options": {"queue": "maintenance"},
        },
        # SAML-01: SSO user sessions expired for over a week.
        "prune-user-sessions-hourly": {
            "task": "agentverse.maintenance.prune_user_sessions",
            "schedule": 3600,
            "options": {"queue": "maintenance"},
        },
        # ORG-42: generated chat documents past their retention window.
        "purge-expired-chat-artifacts-hourly": {
            "task": "agentverse.maintenance.purge_expired_chat_artifacts",
            "schedule": 3600,
            "options": {"queue": "maintenance"},
        },
        # CHAT-SEC-3: chat sessions past their ttl_days (pinned and held: kept).
        "purge-expired-chat-sessions-hourly": {
            "task": "agentverse.maintenance.purge_expired_chat_sessions",
            "schedule": 3600,
            "options": {"queue": "maintenance"},
        },
        # a08-F177-01: mission attachments past their retention window.
        "purge-expired-org-attachments-hourly": {
            "task": "agentverse.maintenance.purge_expired_org_attachments",
            "schedule": 3600,
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
        # ── Task 9: Ambient Collaboration Tick — runs every 15 minutes ─────
        "org-collaboration-ambient-loop": {
            "task": "app.scaling.tasks.org_collaboration_loop",
            "schedule": 900.0,  # every 15 minutes
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
            # minute=0: without it the job fired every minute of 02:00-02:59.
            "schedule": crontab(day_of_month="1", hour="2", minute="0"),
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
        # Durable audit → SIEM forwarding (AUDIT-06); a no-op without SIEM_TYPE.
        "forward-siem-outbox": {
            "task": "app.scaling.tasks.forward_siem_outbox",
            "schedule": 10.0,
            "options": {"queue": "maintenance"},
        },
        "scan-cost-anomalies": {
            "task": "app.scaling.tasks.scan_cost_anomalies",
            "schedule": crontab(minute="0"),
            "options": {"queue": "maintenance"},
        },
        # a10-F246-05: "embed-marketplace-templates" removed — it only counted
        # rows WHERE embedding IS NULL every 15 min; nothing embeds templates or
        # reads marketplace_templates.embedding (search is full-text).
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
            "schedule": crontab(minute="*"),  # B1-7: second 0 of every minute
            "options": {"queue": "workflows.maintenance", "expires": 55},
        },
        # Durable timer waits: re-dispatch runs whose ``wait`` step wake time has
        # passed (the wait no longer sleeps inside a worker slot).
        "workflow-wake-due-timer-waits": {
            "task": "workflow.wake_due_timer_waits",
            "schedule": 30.0,  # every 30 seconds
            "options": {"queue": "workflows.maintenance"},
        },
        # WF-22: re-dispatch runs abandoned by a dead worker (no live lease)
        # instead of waiting out the 25h broker visibility timeout.
        "workflow-redispatch-stuck-runs": {
            "task": "workflow.redispatch_stuck_runs",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "workflows.maintenance"},
        },
    },
)


# ── Beat ticks expire before the next one (GAP-WORKER) ────────────────────────
# Live (2026-10-06): the goal worker's two slots were held by goals for ~2 h and
# the beat kept enqueueing its periodic ticks (~2,500/h, every 5-60 s), so
# ``maintenance`` grew to ~4,700 stale ticks that then ran back to back — the
# same no-op sweep hundreds of times. A periodic tick is only useful until the
# next one is sent: every beat entry now carries an ``expires`` shorter than its
# period, so a tick no worker took in time is discarded (cheaply, on receipt)
# instead of queueing behind its successors. An explicit ``expires`` is kept.
_BEAT_EXPIRES_FRACTION = 0.9
_BEAT_MIN_EXPIRES_S = 1.0


def _crontab_min_gap_seconds(schedule: Any) -> float | None:
    """The shortest gap between two consecutive runs of a celery crontab."""
    try:
        from datetime import UTC, datetime

        from croniter import croniter

        expr = " ".join(
            str(getattr(schedule, attr))
            for attr in (
                "_orig_minute",
                "_orig_hour",
                "_orig_day_of_month",
                "_orig_month_of_year",
                "_orig_day_of_week",
            )
        )
        it = croniter(expr, datetime(2026, 1, 1, tzinfo=UTC))
        runs = [it.get_next(datetime) for _ in range(64)]
    except Exception:
        return None
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(runs)]
    return min(gaps) if gaps else None


def beat_period_seconds(schedule: Any) -> float | None:
    """A beat entry's period in seconds (``None`` when it cannot be derived)."""
    if isinstance(schedule, bool):
        return None
    if isinstance(schedule, int | float):
        return float(schedule)
    if isinstance(schedule, timedelta):
        return schedule.total_seconds()
    if isinstance(schedule, crontab):
        return _crontab_min_gap_seconds(schedule)
    run_every = getattr(schedule, "run_every", None)  # celery.schedules.schedule
    if isinstance(run_every, timedelta):
        return run_every.total_seconds()
    return None


def beat_tick_expires(schedule: Any) -> float | None:
    """The ``expires`` (seconds after sending) for a tick of *schedule*."""
    period = beat_period_seconds(schedule)
    if period is None or period <= 0:
        return None
    return max(_BEAT_MIN_EXPIRES_S, round(period * _BEAT_EXPIRES_FRACTION, 1))


def apply_beat_tick_expiry(beat_schedule: dict[str, Any]) -> None:
    """Give every beat entry without an explicit ``expires`` one (in place)."""
    for entry in beat_schedule.values():
        options = dict(entry.get("options") or {})
        if options.get("expires") is not None:
            continue
        expires = beat_tick_expires(entry.get("schedule"))
        if expires is not None:
            options["expires"] = expires
            entry["options"] = options


apply_beat_tick_expiry(celery_app.conf.beat_schedule)


# ── Redelivery window for long goals ───────────────────────────────────────────
# Tasks are acks_late (a crashed worker's goal is redelivered, not lost). On the
# Redis transport an unacked message is redelivered once the visibility timeout
# (default 1h) passes — far shorter than a goal may legitimately run (24h for
# enterprise), so a healthy long goal was handed to a second worker mid-run. The
# window must outlast the longest plan goal timeout. run_goal additionally takes
# a per-goal lock and atomically claims the goal row, so a redelivery that still
# arrives never runs a goal twice.
def _goal_visibility_timeout_s() -> int:
    from app.tenancy.context import PLAN_LIMITS

    longest = max(limits.goal_timeout_seconds for limits in PLAN_LIMITS.values())
    return int(longest) + 3_600  # + 1h headroom for setup / teardown


_BROKER_TRANSPORT_OPTIONS: dict[str, object] = {
    "visibility_timeout": _goal_visibility_timeout_s(),
    # B1-15: a dead TCP connection to Redis must fail fast, not block a send
    # (the beat's publish) until the kernel gives up (~15 min).
    "socket_timeout": 30,
    "socket_connect_timeout": 10,
    "socket_keepalive": True,
    "health_check_interval": 25,
}

# B1-15: query options redis-py applies to RedBeat's own client (RedBeat builds
# it with StrictRedis.from_url and ignores redbeat_redis_options there).
_REDBEAT_CLIENT_QUERY = (
    "socket_timeout=15&socket_connect_timeout=5&socket_keepalive=true"
    "&health_check_interval=25&retry_on_timeout=true"
)


def _with_client_timeouts(url: str) -> str:
    if url.startswith("sentinel://"):
        return url
    return f"{url}{'&' if '?' in url else '?'}{_REDBEAT_CLIENT_QUERY}"
celery_app.conf.broker_transport_options = dict(_BROKER_TRANSPORT_OPTIONS)

# ── RedBeat HA Beat Scheduler ──────────────────────────────────────────────────
# Allows multiple beat replicas — only one acquires the Redis lock at a time.
# Requires: pip install celery-redbeat
try:
    import redbeat  # type: ignore[import]  # noqa: F401

    # B1-14: RedBeat that never subscribes the beat to task results.
    celery_app.conf.beat_scheduler = "app.scaling.beat_scheduler:AgentVerseRedBeatScheduler"
    celery_app.conf.redbeat_redis_url = _with_client_timeouts(REDIS_URL)
    celery_app.conf.redbeat_lock_key = "agentverse:beat:lock"
    celery_app.conf.redbeat_lock_timeout = 300  # 5 minutes
    # B1-14: wake at least every 30 s, well inside the 300 s lock (the default
    # 300 s sleep equalled the lock timeout, so a late wake-up lost the lock).
    celery_app.conf.beat_max_loop_interval = 30
except ImportError:
    # redbeat not installed — falls back to default file-based beat scheduler
    pass

# ── Redis Sentinel transport options ──────────────────────────────────────────
# When Sentinel is active, tell Celery which master name to watch and point the
# result backend at the same Sentinel topology.
if _SENTINEL_URLS:
    celery_app.conf.broker_transport_options = {
        **_BROKER_TRANSPORT_OPTIONS,
        "master_name": _SENTINEL_MASTER,
        "sentinel_kwargs": {},
    }
    # Result backend mirrors the broker topology so failover works end-to-end.
    # The Sentinel result backend reads master_name from its OWN transport
    # options - without them every result store failed.
    celery_app.conf.result_backend = _BROKER_URL
    celery_app.conf.result_backend_transport_options = {"master_name": _SENTINEL_MASTER}
    celery_app.conf.redis_backend_use_ssl = REDIS_URL.startswith("rediss://")

    # RedBeat also needs the Sentinel URL so the lock key survives failover.
    if "RedBeatScheduler" in str(getattr(celery_app.conf, "beat_scheduler", "")):
        celery_app.conf.redbeat_redis_url = _BROKER_URL

# Backwards-compatible alias used by some imports
app = celery_app


# ── Retrieval model warm-up (RERANK-PRELOAD, L-03) ───────────────────────────
# A pool that opts in (WORKER_PRELOAD_RETRIEVAL_MODELS=true) warms the
# cross-encoder in each prefork child on a background thread, so the first
# knowledge search does not pay the model load inside its retrieval deadline.
# worker_process_init must return quickly, hence the background load. Off by
# default: every warm child holds ~450 MB of torch + model, and the workflow /
# sub-goal pools were OOM-killed by it. They load lazily on first use (a search
# meanwhile skips the cross-encoder after rag_rerank_warmup_wait_seconds).


def _preload_retrieval_models() -> None:
    try:
        from app.rag import colbert_model, cross_encoder

        cross_encoder.preload_default_cross_encoder()
        colbert_model.prefetch_checkpoint()
    except Exception as exc:  # never fail worker start over a warm-up
        import logging

        logging.getLogger(__name__).warning("cross_encoder_preload_failed: %s", exc)


@worker_process_init.connect  # type: ignore[untyped-decorator]
def _on_worker_process_init(**_kwargs: object) -> None:
    from app.core.config import get_settings

    if bool(getattr(get_settings(), "worker_preload_retrieval_models", False)):
        _preload_retrieval_models()


# ── Vault key startup checks (BYOK-2) ────────────────────────────────────────
# Workers decrypt the tenant BYOK keys the API encrypted. A worker started
# without VAULT_MASTER_KEY (the helm worker had none) used to come up normally
# and fail every BYOK goal with "Tenant LLM API key could not be decrypted".
# Outside development/test it now refuses to start, and it refuses when its key
# cannot open the API's vault canary. Celery swallows Exceptions raised by signal
# handlers (logged, start continues), so the refusal is a SystemExit.


def _vault_startup_refuse(role: str, reason: str) -> None:
    import logging

    message = f"agentverse {role} refuses to start: {reason}"
    logging.getLogger(__name__).critical("vault_startup_check_failed role=%s: %s", role, reason)
    raise SystemExit(message)


def _vault_startup_check(role: str, *, canary: bool) -> None:
    import logging

    from app.providers import vault as vault_mod
    from app.providers import vault_canary

    log = logging.getLogger(__name__)
    try:
        vault = vault_mod.assert_vault_key_configured(role)
    except RuntimeError as exc:
        _vault_startup_refuse(role, f"{exc} (VAULT_MASTER_KEY must be set on every process)")
        return
    log.info("vault_key_configured role=%s fingerprint=%s", role, vault.fingerprint())
    if not canary:
        return
    try:
        result = vault_canary.run_vault_self_check(role)
    except Exception as exc:  # the check itself broke: not evidence of a wrong key
        log.warning("vault_canary_check_failed role=%s: %s", role, type(exc).__name__)
        return
    if result.status == "mismatch":
        if vault_mod._dev_key_allowed():
            log.error("vault_canary_mismatch role=%s: %s", role, result.message)
            return
        _vault_startup_refuse(role, result.message)
    elif result.ok:
        log.info("vault_canary_ok role=%s fingerprint=%s", role, result.local_fingerprint)
    else:
        log.warning("vault_canary_unverified role=%s: %s", role, result.message)


def _vault_startup_check_worker() -> None:
    _vault_startup_check("worker", canary=True)


def _vault_startup_check_beat() -> None:
    # beat only schedules; it decrypts nothing itself, so the key check suffices.
    _vault_startup_check("beat", canary=False)


@worker_init.connect  # type: ignore[untyped-decorator]
def _on_worker_init_vault_check(**_kwargs: object) -> None:
    _vault_startup_check_worker()


# ── OCR pool sizing (OCR-PAR) ─────────────────────────────────────────────────
# Each prefork child runs its own process-wide OCR pool. Sized to all the CPUs,
# `--concurrency=N` children would start N x CPUs tesseracts. worker_init runs in
# the parent before the fork: record the pool size so every child sizes its pool
# to its share (OCR_MAX_CONCURRENCY, when set, still wins).


@worker_init.connect  # type: ignore[untyped-decorator]
def _on_worker_init_ocr_share(sender: object = None, **_kwargs: object) -> None:
    try:
        from app.ocr.concurrency import available_cpus, note_worker_processes

        pool_cls = getattr(sender, "pool_cls", None)
        if isinstance(pool_cls, str):  # not resolved yet when worker_init fires
            from celery.concurrency import get_implementation  # type: ignore[import-untyped]

            pool_cls = get_implementation(pool_cls)
        module = str(getattr(pool_cls, "__module__", "") or "")
        if not module.endswith(".prefork"):
            return  # threads / solo / gevent: one process, one shared OCR pool
        # No --concurrency: celery starts one child per CPU.
        note_worker_processes(int(getattr(sender, "concurrency", 0) or 0) or available_cpus())
    except Exception as exc:  # never fail worker start over OCR sizing
        import logging

        logging.getLogger(__name__).warning("ocr_worker_share_failed: %s", exc)


@beat_init.connect  # type: ignore[untyped-decorator]
def _on_beat_init_vault_check(**_kwargs: object) -> None:
    _vault_startup_check_beat()
