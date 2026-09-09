"""
PART 44 — Feature Flag Infrastructure.
PART 43 — Org Celery Cron Tasks.

Feature Flags (PART 44):
  Simple boolean flag system for progressive rollout.
  Feature flags gate every org layer feature.
  PHASE 0: all flags OFF
  Progressive rollout via flag flip.

Cron Tasks (PART 43):
  org-intelligence-cron   — every 15min: detect bottlenecks/insights
  org-digest-cron         — daily 06:00: generate "while you were away"
  org-twin-sync           — event-driven: update digital twin state
"""

from __future__ import annotations

from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── PART 44: Feature Flag Infrastructure ─────────────────────────────────────

# Org OS feature flags — all False by default (PHASE 0 of rollout)
ORG_FEATURE_FLAGS: dict[str, bool] = {
    # Core org OS
    "org_os_enabled": False,  # master switch
    "team_formation_enabled": False,  # TeamFormationEngine
    "meta_orchestrator_enabled": False,  # MetaOrchestrator
    "model_gateway_enabled": False,  # Model Intelligence Gateway
    "context_engine_enabled": False,  # Context Engine
    "quality_gates_enabled": False,  # 6-gate quality system
    "loop_detector_enabled": True,  # OrgLoopDetector (always on for safety)
    # Memory tiers
    "dept_memory_enabled": False,  # Tier 5 dept memory
    "org_memory_enabled": False,  # Tier 6 org memory
    # Gateway channels
    "gateway_telegram_enabled": False,
    "gateway_slack_enabled": False,
    "gateway_teams_enabled": False,
    "gateway_discord_enabled": False,
    "gateway_email_enabled": False,
    "gateway_mcp_enabled": False,
    "gateway_a2a_enabled": False,
    # Advanced features
    "digital_twin_enabled": False,
    "self_improvement_enabled": False,
    "org_learning_enabled": False,
    "plugin_system_enabled": False,
    # Analytics
    "org_analytics_enabled": False,
    "org_digest_enabled": False,
    # UI features
    "command_bar_enabled": True,  # Cmd+K always on
    "org_chart_enabled": True,  # Always on
}


class FeatureFlagService:
    """
    Simple feature flag service backed by in-memory dict.
    In production: backed by Redis or DB for cross-replica consistency.
    """

    def __init__(self, flags: dict[str, bool] | None = None) -> None:
        self._flags: dict[str, bool] = dict(flags or ORG_FEATURE_FLAGS)

    def is_enabled(self, flag: str, tenant_id: str | None = None) -> bool:
        """Check if a feature flag is enabled."""
        # Tenant-specific override would be checked here in production
        return self._flags.get(flag, False)

    def enable(self, flag: str) -> None:
        """Enable a feature flag."""
        self._flags[flag] = True
        _log.info("feature_flag.enabled", flag=flag)

    def disable(self, flag: str) -> None:
        """Disable a feature flag."""
        self._flags[flag] = False
        _log.info("feature_flag.disabled", flag=flag)

    def get_all(self) -> dict[str, bool]:
        return dict(self._flags)

    def enable_phase(self, phase: int) -> list[str]:
        """Enable all flags for a deployment phase. Returns enabled flags."""
        phase_flags = {
            0: [],  # nothing (deploy with flag=OFF)
            1: ["org_os_enabled"],  # internal testing
            2: ["org_os_enabled", "team_formation_enabled", "meta_orchestrator_enabled"],
            3: [
                "org_os_enabled",
                "team_formation_enabled",
                "meta_orchestrator_enabled",
                "model_gateway_enabled",
                "context_engine_enabled",
                "quality_gates_enabled",
                "dept_memory_enabled",
                "org_memory_enabled",
            ],
            4: [
                "gateway_telegram_enabled",
                "gateway_slack_enabled",
                "gateway_mcp_enabled",
                "digital_twin_enabled",
                "org_analytics_enabled",
                "org_digest_enabled",
            ],
            5: [
                "self_improvement_enabled",
                "org_learning_enabled",
                "plugin_system_enabled",
                "gateway_discord_enabled",
                "gateway_email_enabled",
                "gateway_a2a_enabled",
                "gateway_teams_enabled",
            ],
        }
        flags = phase_flags.get(phase, [])
        for flag in flags:
            self.enable(flag)
        _log.info("feature_flag.phase_enabled", phase=phase, flags=flags)
        return flags


# Global singleton
_feature_flags: FeatureFlagService | None = None


def get_feature_flags() -> FeatureFlagService:
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = FeatureFlagService()
    return _feature_flags


def is_feature_enabled(flag: str, tenant_id: str | None = None) -> bool:
    return get_feature_flags().is_enabled(flag, tenant_id)


# ── PART 43: Org Celery Cron Tasks ───────────────────────────────────────────
# These are registered in app/scaling/tasks.py beat_schedule


async def _run_org_intelligence_cron() -> dict[str, Any]:
    """
    org-intelligence-cron: Every 15 minutes.
    Detects bottlenecks, generates insights, updates org health scores.
    """
    from sqlalchemy import select

    from app.db.rls import sqlalchemy_rls_context  # type: ignore[import]
    from app.db.session import db_factory  # type: ignore[import]
    from app.org.analytics import OrgAnalyticsService
    from app.org.models import Organization

    processed = 0
    insights_generated = 0

    try:
        async with db_factory() as session:
            result = await session.execute(
                select(Organization.id, Organization.tenant_id)
                .where(Organization.status == "active")
                .limit(50)
            )
            orgs = result.all()

        for org_id, tenant_id in orgs:
            try:
                async with db_factory() as s2:
                    async with sqlalchemy_rls_context(s2, str(tenant_id)):
                        svc = OrgAnalyticsService(s2, str(tenant_id))
                        await svc.get_org_health_score(str(org_id))
                        bottlenecks = await svc.get_bottlenecks(str(org_id))
                        if bottlenecks:
                            insights_generated += len(bottlenecks)
                processed += 1
            except Exception as exc:
                _log.warning("org_intelligence_cron.org_failed", org_id=str(org_id), error=str(exc))

    except Exception as exc:
        _log.error("org_intelligence_cron.failed", error=str(exc))

    _log.info("org_intelligence_cron.done", processed=processed, insights=insights_generated)
    return {"processed": processed, "insights": insights_generated}


async def _run_org_digest_cron() -> dict[str, Any]:
    """
    org-digest-cron: Daily at 06:00 UTC.
    Generates "While You Were Away" digests for all active orgs.
    """
    from sqlalchemy import select

    from app.db.rls import sqlalchemy_rls_context  # type: ignore[import]
    from app.db.session import db_factory  # type: ignore[import]
    from app.org.digest import OrgDigestService
    from app.org.models import Organization

    processed = 0
    digests_generated = 0

    try:
        async with db_factory() as session:
            result = await session.execute(
                select(Organization.id, Organization.tenant_id)
                .where(Organization.status == "active")
                .limit(100)
            )
            orgs = result.all()

        for org_id, tenant_id in orgs:
            try:
                async with db_factory() as s2:
                    async with sqlalchemy_rls_context(s2, str(tenant_id)):
                        digest_svc = OrgDigestService(s2, str(tenant_id))
                        await digest_svc.generate(str(org_id))
                        digests_generated += 1
                processed += 1
            except Exception as exc:
                _log.warning("org_digest_cron.org_failed", org_id=str(org_id), error=str(exc))

    except Exception as exc:
        _log.error("org_digest_cron.failed", error=str(exc))

    _log.info("org_digest_cron.done", processed=processed, digests=digests_generated)
    return {"processed": processed, "digests": digests_generated}


async def _run_org_twin_sync(event: dict[str, Any]) -> None:
    """
    org-twin-sync: Event-driven, triggered on org events.
    Updates the digital twin state to reflect real-world changes.
    """
    org_id = event.get("org_id", "")
    event_type = event.get("event_type", "")

    if not org_id:
        return

    try:
        from app.org.digital_twin import OrgDigitalTwin

        twin = OrgDigitalTwin()
        await twin.sync(event)
        _log.debug("org_twin_sync.done", org_id=org_id, event=event_type)
    except Exception as exc:
        _log.warning("org_twin_sync.failed", org_id=org_id, error=str(exc))
