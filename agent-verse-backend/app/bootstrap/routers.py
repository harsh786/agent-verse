"""Bootstrap: router registration for the AgentVerse FastAPI application.

All ``app.include_router()`` calls extracted from ``app/main.py`` so the
factory function stays slim.  Import paths mirror the originals exactly.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI

from app.api.a2a import router as a2a_router
from app.api.admin import router as admin_router
from app.api.agent_credentials_api import router as agent_credentials_router
from app.api.agent_directory import router as agent_directory_router
from app.api.agents import router as agents_router
from app.api.analytics import router as analytics_router
from app.api.artifacts import router as artifacts_router
from app.api.auth import router as auth_router

# ── Router imports (verbatim from app/main.py) ───────────────────────────────
from app.api.billing import router as billing_router
from app.api.builder import router as builder_router
from app.api.channels.ingestion import router as channels_router  # Phase 2
from app.api.civilization import router as civilization_router
from app.api.collab import router as collab_router
from app.api.connectors import router as connectors_router
from app.api.coordination import router as coordination_router
from app.api.coordination_auction import router as coordination_auction_router
from app.api.coordination_camel import router as coordination_camel_router
from app.api.coordination_generative import router as coordination_generative_router
from app.api.coordination_group_chat import router as coordination_group_chat_router
from app.api.coordination_handoffs import router as coordination_handoffs_router
from app.api.coordination_magentic import router as coordination_magentic_router
from app.api.coordination_moa import router as coordination_moa_router
from app.api.coordination_swarm import router as coordination_swarm_router
from app.api.coordination_transcript import router as coordination_transcript_router
from app.api.costs import router as costs_router

# ── Enterprise sub-routers ────────────────────────────────────────────────────
from app.api.enterprise import (
    compliance_router,
    intelligence_router,
    marketplace_router,
    scim_router,
)
from app.api.enterprise import router as enterprise_router
from app.api.goals import router as goals_router
from app.api.golden_datasets import router as golden_datasets_router
from app.api.governance import router as governance_router
from app.api.grants import router as grants_router
from app.api.guardrails import router as guardrails_router
from app.api.ingestion import documents_router as ingestion_documents_router
from app.api.ingestion import router as ingestion_sources_router  # Ingestion framework
from app.api.insights import router as insights_router
from app.api.integrations import router as integrations_router
from app.api.knowledge import router as knowledge_router
from app.api.lab import router as lab_router
from app.api.memory import router as memory_router
from app.api.observability import router as observability_router
from app.api.perception import router as perception_router
from app.api.replay import router as replay_router
from app.api.rpa import router as rpa_router
from app.api.schedules import (
    events_router,
    nl_router,
    webhooks_router,
)
from app.api.schedules import (
    router as schedules_router,
)
from app.api.skills import router as skills_router
from app.api.solutions import router as solutions_router
from app.api.state_machines import router as state_machines_router  # Phase 3
from app.api.strategies import router as strategies_router
from app.api.system import router as system_router
from app.api.templates import router as templates_router
from app.api.tenants import router as tenants_router
from app.api.tools import router as tools_router
from app.api.training_export import router as training_export_router
from app.api.triggers import router as triggers_router  # Phase 4: full trigger CRUD
from app.api.workflows import router as workflows_router
from app.auth.google_oauth import router as google_oauth_router
from app.chat.router import router as chat_router
from app.chat.service import ChatService as _ChatService
from app.observability.cost_breakdown_api import router as cost_breakdown_api_router
from app.org.router import router as org_router  # AI Organization OS
from app.proactive.router import router as _proactive_router

# (log name, module, attribute, include prefix) for routers registered through
# _include_guarded — see register_routers for the failure contract.
_GUARDED_ROUTERS: tuple[tuple[str, str, str, str | None], ...] = (
    ("marketplace_monetization_router", "app.api.marketplace_monetization", "router", None),
    ("dpdp_router", "app.api.dpdp", "router", None),
    ("gst_billing_router", "app.api.gst_billing", "router", None),
    ("sla_router", "app.api.sla", "router", None),
    ("sessions_router", "app.api.sessions", "router", None),
    ("sandbox_router", "app.api.sandbox", "router", None),
    ("policy_rules_router", "app.api.policy_rules", "router", None),
    ("v1_router", "app.api.v1.router", "v1_router", None),
    ("model_registry_router", "app.api.model_registry", "router", None),
    ("embeddings_router", "app.api.embeddings", "router", None),
    ("multimodal_router", "app.api.multimodal", "router", None),
    ("knowledge_graph_router", "app.api.knowledge_graph", "router", None),
    ("rag_platform_router", "app.api.rag_platform", "router", None),
    ("agent_runtime_router", "app.api.agent_runtime", "router", None),
    ("guardrails_v2_router", "app.api.guardrails_v2", "router", None),
    ("trust_governance_router", "app.api.trust_governance", "router", None),
    ("skills_runtime_router", "app.api.skills_runtime", "router", None),
    ("ai_ops_router", "app.api.ai_ops", "router", None),
    ("memory_v2_router", "app.api.memory_v2", "router", None),
    ("ocr_router", "app.api.ocr", "router", None),
)


def _guard(app: FastAPI, settings: Any, logger: Any, name: str, include: Any) -> None:
    """Run ``include()``; on failure log at ERROR, record it, and in production
    abort startup rather than serve without an advertised API."""
    try:
        include()
        logger.info(f"{name}_registered")
    except Exception as exc:
        failed = getattr(app.state, "failed_routers", None)
        if failed is None:
            failed = []
            app.state.failed_routers = failed
        failed.append(name)
        logger.error("router_registration_failed", router=name, error=str(exc))
        if bool(getattr(settings, "is_production", False)):
            raise RuntimeError(f"router {name!r} failed to register: {exc}") from exc


def _include_guarded(
    app: FastAPI,
    settings: Any,
    logger: Any,
    name: str,
    module: str,
    attr: str,
    prefix: str | None,
) -> None:
    def _include() -> None:
        import importlib

        router = getattr(importlib.import_module(module), attr)
        if prefix:
            app.include_router(router, prefix=prefix)
        else:
            app.include_router(router)

    _guard(app, settings, logger, name, _include)


_proactive_log = structlog.get_logger("app.proactive.audit")


def _wire_proactive_engine(app: FastAPI) -> None:
    """Construct the ProactiveEngine on app.state with chat-backed delivery + audit."""
    from app.proactive.engine import ProactiveEngine

    chat_service = app.state.chat_service

    async def _deliver(signal: Any, proposal: Any) -> None:
        await chat_service.deliver_proactive(
            principal_id=signal.principal_id,
            tenant_id=signal.tenant_id,
            message=proposal.message,
            channel=signal.channel,
            channel_user_id=signal.payload.get("_channel_user_id"),
        )

    def _audit(event: dict[str, Any]) -> None:
        """Write a proactive delivery to the audit trail.

        This used to capture ``app.state.audit_log`` at wiring time (missing the
        lifespan's DB-backed swap) and call ``record(event_dict)`` — without the
        required ``tenant_ctx`` and with a dict instead of an AuditEvent — inside
        ``suppress(Exception)``, so every proactive audit was silently dropped.
        """
        import json as _json

        from app.governance.audit import AuditEvent
        from app.governance.permissions import ActionLevel
        from app.tenancy.context import PlanTier, TenantContext

        audit_log = getattr(app.state, "audit_log", None)  # resolved per call
        if audit_log is None:
            _proactive_log.warning("proactive_audit_unavailable")
            return
        tenant_id = str(event.get("tenant_id") or "")
        details = {k: v for k, v in event.items() if k != "tenant_id"}
        try:
            audit_log.record(
                AuditEvent(
                    goal_id="",
                    tool_name=f"proactive.{event.get('action') or 'message'}",
                    action_level=ActionLevel.ALLOW_LOG,
                    outcome="delivered",
                    note=_json.dumps(details, default=str)[:2000],
                    api_key_id="proactive-engine",
                ),
                tenant_ctx=TenantContext(
                    tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="proactive-engine"
                ),
            )
        except Exception as exc:
            _proactive_log.error(
                "proactive_audit_write_failed", tenant_id=tenant_id, error=str(exc)
            )

    app.state.proactive_engine = ProactiveEngine(deliver=_deliver, audit=_audit)


def register_routers(app: FastAPI, settings: Any, logger: Any) -> None:
    """Register all API routers onto *app*."""
    # ── Routers ───────────────────────────────────────────────────────────────
    # Chat (conversational agent interface)
    app.state.chat_service = _ChatService()
    # If the goal engine is already on app.state (constructed before routers in
    # some paths), wire chat GOAL turns to it now; the lifespan re-attaches the
    # DB-backed goal_service when it swaps services in (Phase 0.3c).
    from app.org.service import resolve_llm_provider

    _existing_goal_svc = getattr(app.state, "goal_service", None)
    _ltm = getattr(app.state, "long_term_memory", None)
    _memory_recall = None
    _memory_writer = None
    if _ltm is not None:
        from app.chat.memory_adapter import build_memory_recall, build_memory_writer

        _memory_recall = build_memory_recall(_ltm)
        _memory_writer = build_memory_writer(_ltm)
    from app.chat.artifact_store import ChatArtifactStore
    from app.chat.skills.builtin import build_registry_from_app_state
    from app.identity import IdentityService

    # Binary store for chat-generated documents (Phase 4); registered before the
    # registry so the generate_document skill is wired.
    if getattr(app.state, "chat_artifact_store", None) is None:
        app.state.chat_artifact_store = ChatArtifactStore()

    # Dual-mode identity (Phase 3): unifies channel identities to principals so a
    # conversation continues across channels. In-memory now; a Postgres-backed
    # store swaps in with the identity_links migration.
    if getattr(app.state, "identity_service", None) is None:
        app.state.identity_service = IdentityService()

    # Voice/phone (Phase 8): registry maps a provisioned number → tenant so an
    # inbound Twilio call resolves to the right tenant before any tenant work.
    if getattr(app.state, "voice_phone_registry", None) is None:
        from app.gateway.channels.voice_phone import VoicePhoneChannelAdapter
        from app.gateway.voice_registry import VoicePhoneRegistry

        app.state.voice_phone_registry = VoicePhoneRegistry.from_env()
        app.state.voice_phone_adapter = VoicePhoneChannelAdapter()

    # Messaging channels (Telegram/WhatsApp) → unified ChatService: addressee→tenant.
    if getattr(app.state, "channel_registry", None) is None:
        from app.gateway.channel_registry import ChannelRegistry

        app.state.channel_registry = ChannelRegistry.from_env()

    app.state.chat_service.attach_engine(
        goal_service=_existing_goal_svc,
        answer_generator=resolve_llm_provider(app.state),
        memory_recall=_memory_recall,
        memory_writer=_memory_writer,
        nl_scheduler=getattr(app.state, "nl_scheduler", None),
        schedule_store=getattr(app.state, "schedule_store", None),
        skill_registry=build_registry_from_app_state(app.state),
        identity_service=app.state.identity_service,
    )
    app.include_router(chat_router)

    # Proactive-outreach engine (Phase 9): deliver approved outreach into the
    # principal's chat thread (+ origin channel) and audit it as source=proactive.
    _wire_proactive_engine(app)
    app.include_router(_proactive_router)
    # Core
    app.include_router(system_router)
    app.include_router(tenants_router)
    app.include_router(goals_router)
    app.include_router(strategies_router)
    app.include_router(coordination_router)
    app.include_router(coordination_handoffs_router)
    app.include_router(coordination_transcript_router)
    app.include_router(coordination_group_chat_router)
    app.include_router(coordination_magentic_router)
    app.include_router(coordination_moa_router)
    app.include_router(coordination_camel_router)
    app.include_router(coordination_generative_router)
    app.include_router(coordination_swarm_router)
    app.include_router(coordination_auction_router)
    app.include_router(connectors_router)
    # Native tools (code execution, file ops, email)
    app.include_router(tools_router)
    # SSO authentication
    app.include_router(auth_router)
    # Google OIDC / OAuth2 login
    app.include_router(google_oauth_router)
    logger.info("google_oauth_router_registered")
    # Agents, governance, knowledge, scheduling
    app.include_router(agents_router)
    # Per-agent credential management (Security Center → Agent Identity panel).
    # This router was previously defined but never mounted, so
    # GET/POST /agents/{agent_id}/keys 404'd for all clients.
    app.include_router(agent_credentials_router)
    app.include_router(governance_router)
    app.include_router(grants_router)
    app.include_router(knowledge_router)
    app.include_router(rpa_router)
    app.include_router(schedules_router)
    app.include_router(triggers_router)  # Phase 4: full trigger CRUD + DLQ
    app.include_router(channels_router)  # Phase 2: channel ingestion
    app.include_router(state_machines_router)  # Phase 3: state machines
    app.include_router(ingestion_sources_router)  # Ingestion: source CRUD + sync
    app.include_router(ingestion_documents_router)  # Ingestion: documents + DLQ + quota
    app.include_router(nl_router)
    app.include_router(webhooks_router)
    app.include_router(events_router)
    # Multi-channel gateway (telegram/whatsapp/slack/teams webhooks → goals +
    # document ingestion). Router carries its own /v1/gateway prefix and uses
    # per-channel signature auth (bypassed from tenant API-key middleware).
    _include_guarded(app, settings, logger, "gateway_router", "app.gateway.router", "router", None)
    # Memory + Artifacts
    app.include_router(memory_router)
    app.include_router(artifacts_router)
    # A2A + collaboration
    app.include_router(a2a_router)
    app.include_router(collab_router)
    # A2A Agent Directory (.well-known/agents — Phase 8)
    app.include_router(agent_directory_router)
    logger.info("a2a_directory_router_registered")
    # Domain Solution Packages (Phase 7)
    app.include_router(solutions_router)
    logger.info("solutions_router_registered")
    # Enterprise + marketplace + intelligence
    app.include_router(enterprise_router)
    app.include_router(marketplace_router)
    app.include_router(intelligence_router)
    # P2.10: Async GDPR export + consent management
    app.include_router(compliance_router)
    # SCIM 2.0 provisioning (mounted at /scim/v2)
    app.include_router(scim_router)
    # Perception
    app.include_router(perception_router)
    # Integrations (Slack, Zapier, email triggers)
    app.include_router(integrations_router)
    # Analytics
    app.include_router(analytics_router)
    # Replay (goal execution timeline)
    app.include_router(replay_router)
    # Training data export (intelligence)
    app.include_router(training_export_router)
    # Civilization (Agent Civilization — feature-flagged at request time)
    app.include_router(civilization_router)
    logger.info("civilization_router_registered")
    # Visual Workflow Builder
    app.include_router(workflows_router)
    logger.info("workflows_router_registered")
    # Agent Lab (unified playground, simulation, model comparison)
    app.include_router(lab_router)
    logger.info("lab_router_registered")
    # Insights & Intelligence
    app.include_router(insights_router)
    logger.info("insights_router_registered")
    # Observability (real-time logs, SSE stream, structured metrics)
    app.include_router(observability_router)
    logger.info("observability_router_registered")
    # Goal Templates
    app.include_router(templates_router)
    logger.info("templates_router_registered")
    # Cost optimization & monitoring
    app.include_router(costs_router)
    logger.info("costs_router_registered")
    # Guardrails
    app.include_router(guardrails_router)
    logger.info("guardrails_router_registered")
    # Billing & usage metering (Phase 1e)
    app.include_router(billing_router)
    logger.info("billing_router_registered")
    # Cost metrics API (Phase 2 Group F)
    app.include_router(cost_breakdown_api_router)
    logger.info("cost_breakdown_api_router_registered")
    # Platform admin (cross-tenant, X-Admin-Key authenticated)
    app.include_router(admin_router)
    logger.info("admin_router_registered")
    # Skills (composable instruction packs)
    app.include_router(skills_router)
    logger.info("skills_router_registered")
    # Builder (site/app generation — Phase 9)
    app.include_router(builder_router)
    logger.info("builder_router_registered")
    # Golden Datasets (eval promotion — Phase M11)
    app.include_router(golden_datasets_router)
    logger.info("golden_datasets_router_registered")

    # MFA (TOTP-based 2FA)
    from app.api.mfa import router as mfa_router

    app.include_router(mfa_router)
    logger.info("mfa_router_registered")

    # Public status page (unauthenticated; /status is in _BYPASS_PREFIXES).
    # Its router existed but was never included, so /status was a 404.
    from app.api.public_status import router as public_status_router

    app.include_router(public_status_router)
    logger.info("public_status_router_registered")

    # Capability routers that used to be wrapped in ``try/except: warning`` — an
    # import error silently dropped the whole API while the app booted "fine".
    # Now a failure is logged at ERROR, recorded on app.state.failed_routers, and
    # fails startup in production (every one of these is an advertised API).
    for _name, _modpath, _attr, _prefix in _GUARDED_ROUTERS:
        _include_guarded(app, settings, logger, _name, _modpath, _attr, _prefix)

    def _include_workflow_engine() -> None:
        from app.workflow.router import router as workflow_engine_router
        from app.workflow.router_hitl import router as workflow_hitl_router
        from app.workflow.router_runs import router as workflow_runs_router
        from app.workflow.router_templates import router as workflow_templates_router
        from app.workflow.router_versions import router as workflow_versions_router
        from app.workflow.webhook_router import router as workflow_webhook_router

        app.include_router(workflow_engine_router, prefix="/api/v1")
        app.include_router(workflow_runs_router, prefix="/api/v1")
        app.include_router(workflow_hitl_router, prefix="/api/v1")
        app.include_router(workflow_templates_router, prefix="/api/v1")
        app.include_router(workflow_versions_router, prefix="/api/v1")
        # Public webhook trigger — mounted at ROOT (no /api/v1) so its path is
        # /wf-hooks/{token}, matching the _BYPASS_PREFIXES entry. Auth is the
        # signed token in the path, not a tenant API key.
        app.include_router(workflow_webhook_router)

    # ── Workflow Automation Engine (Phase WE) ─────────────────────────────────
    _guard(app, settings, logger, "workflow_engine_routers", _include_workflow_engine)
    # ── AI Organization OS ─────────────────────────────────────────────────
    _guard(app, settings, logger, "org_os_router", lambda: app.include_router(org_router))
    # ── Voice (STT + goal refinement) ─────────────────────────────────────────
    _include_guarded(app, settings, logger, "voice_router", "app.voice.router", "router", None)
