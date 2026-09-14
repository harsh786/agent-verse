"""Bootstrap: router registration for the AgentVerse FastAPI application.

All ``app.include_router()`` calls extracted from ``app/main.py`` so the
factory function stays slim.  Import paths mirror the originals exactly.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.api.a2a import router as a2a_router
from app.api.admin import router as admin_router
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
    app.state.chat_service.attach_engine(
        goal_service=_existing_goal_svc,
        answer_generator=resolve_llm_provider(app.state),
        memory_recall=_memory_recall,
        memory_writer=_memory_writer,
        nl_scheduler=getattr(app.state, "nl_scheduler", None),
        schedule_store=getattr(app.state, "schedule_store", None),
    )
    app.include_router(chat_router)
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
    app.include_router(governance_router)
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
    try:
        from app.gateway.router import router as gateway_router

        app.include_router(gateway_router)
        logger.info("gateway_router_registered")
    except Exception as _gw_exc:  # pragma: no cover - defensive
        logger.warning("gateway_router_failed", error=str(_gw_exc))
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

    # Phase 13/14 — new capability routers
    try:
        from app.api.marketplace_monetization import router as _mktplace_mon

        app.include_router(_mktplace_mon)
    except Exception as _e:
        logger.warning("marketplace_monetization_router_failed", error=str(_e))
    try:
        from app.api.dpdp import router as _dpdp_router

        app.include_router(_dpdp_router)
    except Exception as _e:
        logger.warning("dpdp_router_failed", error=str(_e))
    try:
        from app.api.gst_billing import router as _gst_router

        app.include_router(_gst_router)
    except Exception as _e:
        logger.warning("gst_billing_router_failed", error=str(_e))
    try:
        from app.api.sla import router as _sla_router

        app.include_router(_sla_router)
    except Exception as _e:
        logger.warning("sla_router_failed", error=str(_e))
    try:
        from app.api.sessions import router as _sessions_router

        app.include_router(_sessions_router)
    except Exception as _e:
        logger.warning("sessions_router_failed", error=str(_e))
    try:
        from app.api.sandbox import router as _sandbox_router

        app.include_router(_sandbox_router)
    except Exception as _e:
        logger.warning("sandbox_router_failed", error=str(_e))
    try:
        from app.api.policy_rules import router as _policy_rules_router

        app.include_router(_policy_rules_router)
    except Exception as _e:
        logger.warning("policy_rules_router_failed", error=str(_e))
    try:
        from app.api.v1.router import v1_router

        app.include_router(v1_router)
    except Exception as _e:
        logger.warning("v1_router_failed", error=str(_e))
    try:
        from app.api.model_registry import router as model_registry_router

        app.include_router(model_registry_router)
    except Exception as _e:
        logger.warning("model_registry_router_failed", error=str(_e))

    # Phase 3: Embedding Platform
    try:
        from app.api.embeddings import router as embeddings_router

        app.include_router(embeddings_router)
        logger.info("embeddings_router_registered")
    except Exception as _e:
        logger.warning("embeddings_router_failed", error=str(_e))

    # Phase 4: Multimodal Intelligence
    try:
        from app.api.multimodal import router as multimodal_router

        app.include_router(multimodal_router)
        logger.info("multimodal_router_registered")
    except Exception as _e:
        logger.warning("multimodal_router_failed", error=str(_e))

    # Phase 5: Tenant Knowledge Graph
    try:
        from app.api.knowledge_graph import router as knowledge_graph_router

        app.include_router(knowledge_graph_router)
        logger.info("knowledge_graph_router_registered")
    except Exception as _e:
        logger.warning("knowledge_graph_router_failed", error=str(_e))

    # Phase 6: GraphRAG / RAG Platform
    try:
        from app.api.rag_platform import router as rag_platform_router

        app.include_router(rag_platform_router)
        logger.info("rag_platform_router_registered")
    except Exception as _e:
        logger.warning("rag_platform_router_failed", error=str(_e))

    # Phase 7: Agent Runtime 2.0
    try:
        from app.api.agent_runtime import router as agent_runtime_router

        app.include_router(agent_runtime_router)
        logger.info("agent_runtime_router_registered")
    except Exception as _e:
        logger.warning("agent_runtime_router_failed", error=str(_e))

    # Phase 8: Guardrails 2.0
    try:
        from app.api.guardrails_v2 import router as guardrails_v2_router

        app.include_router(guardrails_v2_router)
        logger.info("guardrails_v2_router_registered")
    except Exception as _e:
        logger.warning("guardrails_v2_router_failed", error=str(_e))

    # Phase 9: Trust and Governance 2.0
    try:
        from app.api.trust_governance import router as trust_governance_router

        app.include_router(trust_governance_router)
        logger.info("trust_governance_router_registered")
    except Exception as _e:
        logger.warning("trust_governance_router_failed", error=str(_e))

    # Phase 12: Skills Runtime (composable skill execution engine)
    try:
        from app.api.skills_runtime import router as skills_runtime_router

        app.include_router(skills_runtime_router)
        logger.info("skills_runtime_router_registered")
    except Exception as _e:
        logger.warning("skills_runtime_router_failed", error=str(_e))

    # Phase 10: AI Ops (Evals, Drift, Regression)
    try:
        from app.api.ai_ops import router as ai_ops_router

        app.include_router(ai_ops_router)
        logger.info("ai_ops_router_registered")
    except Exception as _e:
        logger.warning("ai_ops_router_failed", error=str(_e))

    # Phase 11: Agent Memory 2.0
    try:
        from app.api.memory_v2 import router as memory_v2_router

        app.include_router(memory_v2_router)
        logger.info("memory_v2_router_registered")
    except Exception as _e:
        logger.warning("memory_v2_router_failed", error=str(_e))

    # Phase 6 OCR: document text extraction
    try:
        from app.api.ocr import router as ocr_router

        app.include_router(ocr_router)
        logger.info("ocr_router_registered")
    except Exception as _e:
        logger.warning("ocr_router_failed", error=str(_e))

    # ── Workflow Automation Engine (Phase WE) ─────────────────────────────────
    try:
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
        logger.info("workflow_engine_routers_registered")
    except Exception as _we:
        logger.warning("workflow_engine_router_failed", error=str(_we))

    # ── AI Organization OS ─────────────────────────────────────────────────
    try:
        app.include_router(org_router)
        logger.info("org_os_router_registered")
    except Exception as _org_err:
        logger.warning("org_os_router_failed", error=str(_org_err))

    # ── Voice (STT + goal refinement) ─────────────────────────────────────────
    try:
        from app.voice.router import router as voice_router

        app.include_router(voice_router)
        logger.info("voice_router_registered")
    except Exception as _voice_err:
        logger.warning("voice_router_failed", error=str(_voice_err))
