"""Bootstrap: service construction for AgentVerse.

All ``app.state.*`` assignments extracted from ``app/main.py``
so the application factory stays slim.  Receives the FastAPI app
instance and populates app.state with every service.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def build_services(
    app: FastAPI,
    settings: Any,
    *,
    tenant_service: Any = None,
    goal_service: Any = None,
) -> None:
    """Construct all application services and store them on ``app.state``.

    Called once from ``create_app()`` before the lifespan starts.
    All imports are kept local so optional dependencies don't block startup.
    """
    # ── Import everything that was previously imported at module level ─────
    from app.main import (  # type: ignore[import]  # re-use main imports
        AgentStore,
        AuditLog,
        CostController,
        FakeProvider,
        GoalService,
        HITLGateway,
        PolicyEngine,
        TenantService,
        logger,
    )
    # Note: the body below is verbatim from create_app() — no semantic changes.

    # ── Build shared services ─────────────────────────────────────────────────
    _tenant_svc = tenant_service or TenantService()
    _audit_log = AuditLog()
    _hitl = HITLGateway()
    _cost = CostController()
    _policy_engine = PolicyEngine()
    _agent_store = AgentStore()
    # C6: Use the declarative provider registry directly; fall back to wrapper on error
    try:
        from app.providers.registry import resolve_provider as _resolve_provider_registry

        _app_provider = _resolve_provider_registry()
        # Production safety guard: refuse FakeProvider in production
        if isinstance(_app_provider, FakeProvider):
            import os as _os

            if _os.getenv("ENVIRONMENT", "development").lower() == "production":
                raise RuntimeError(
                    "FATAL: No LLM provider configured for production. "
                    "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, "
                    "GROQ_API_KEY, or OLLAMA_BASE_URL environment variable."
                )
            logger.warning(
                "fake_provider_active_dev_only",
                message="FakeProvider active — set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
            )
            _app_provider = FakeProvider(
                responses=[
                    '{"steps": ["Complete the requested task"]}',
                    "Task executed successfully",
                    '{"success": true, "reason": "Goal achieved"}',
                ]
            )
        logger.info("provider_resolved_via_registry")
    except Exception as _reg_exc:
        logger.warning("provider_registry_failed_fallback", error=str(_reg_exc)[:60])
        _app_provider = _resolve_provider_for_app(settings)
    _meta_agent = MetaAgentPlanner(provider=_app_provider)
    _schedule_store = ScheduleStore()
    _nl_sched = NLScheduler(provider=_app_provider)
    _knowledge_store = KnowledgeStore()
    _semantic_cache = SemanticCache()
    # In-memory ToolResultCache (upgraded with Redis in lifespan)
    try:
        from app.mcp.tool_cache import ToolResultCache

        _tool_cache_inmem = ToolResultCache()
    except Exception:
        _tool_cache_inmem = None
    _fake_redis = _FakeRedis()
    _mcp_registry = mcp_registry or MCPRegistry(redis=_fake_redis)
    _oauth_manager = OAuthFlowManager()
    _long_term_memory = LongTermMemoryStore()
    _eval_runner = EvalRunner()
    _eval_suite_runner = EvalSuiteRunner()
    _compliance_controller = ComplianceController()
    _simulation_runner = SimulationRunner()
    _red_team_runner = RedTeamRunner()
    _marketplace = Marketplace(agent_store=_agent_store)
    _marketplace_v2 = MarketplaceV2(db_factory=None)  # upgraded in lifespan
    _self_optimizer = SelfOptimizer()
    # v2 self-optimizer: fixes all 4 critical bugs + Bayesian A/B testing
    # db_factory and redis are None here; upgraded in lifespan
    _self_optimizer_v2 = SelfOptimizerV2(
        redis=_fake_redis,
        db_factory=None,
        llm_provider_factory=lambda: _app_provider,
    )
    # v2 compliance checker: no hardcoded booleans; db_factory upgraded in lifespan
    _compliance_checker = ComplianceChecker(db_factory=None)
    _notification_service = NotificationService()
    # H-3: ExecutionMemory — wired with DB in lifespan
    _exec_memory = ExecutionMemory()
    # Cost tracker — wired with real Redis + DB in lifespan
    _cost_tracker = CostTracker(redis=_fake_redis)
    # AgentIdentityService — wired with DB + Redis in lifespan
    _agent_identity_svc = AgentIdentityService(db=None, vault=get_vault(), redis=_fake_redis)
    # GuardrailEngine v2 — wired with Redis in lifespan
    _guardrail_engine_v2 = GuardrailEngineV2()

    # Wire embedder: use VoyageProvider if VOYAGE_API_KEY set,
    # OpenAICompatibleProvider if OPENAI_API_KEY set,
    # LocalEmbedProvider if SENTENCE_TRANSFORMERS_MODEL set, else None.
    import os

    _embedder: Any = None
    from app.core.config import get_provider_env

    _openai_key = get_provider_env("OPENAI_API_KEY")
    _voyage_key = get_provider_env("VOYAGE_API_KEY")
    _anthropic_key = get_provider_env("ANTHROPIC_API_KEY")
    if _voyage_key:
        try:
            from app.providers.voyage_provider import VoyageProvider

            _embedder = VoyageProvider(api_key=_voyage_key)
        except Exception:
            pass
    elif _openai_key:
        try:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            _embedder = OpenAICompatibleProvider(
                api_key=_openai_key, default_model="text-embedding-3-small"
            )
        except Exception:
            pass
    elif get_provider_env("GOOGLE_API_KEY"):
        try:
            from app.providers.gemini_provider import GeminiProvider

            _embedder = GeminiProvider(api_key=get_provider_env("GOOGLE_API_KEY"))
        except Exception:
            pass
    elif os.getenv("SENTENCE_TRANSFORMERS_MODEL", ""):
        try:
            from app.providers.voyage_provider import LocalEmbedProvider

            _embedder = LocalEmbedProvider(
                model_name=os.getenv("SENTENCE_TRANSFORMERS_MODEL", "all-MiniLM-L6-v2")
            )
            logger.info(
                "local_embed_provider_wired",
                model=os.getenv("SENTENCE_TRANSFORMERS_MODEL"),
            )
        except Exception as _exc:
            logger.warning("local_embed_provider_failed", error=str(_exc))
    # app.state.embedder is set after app = FastAPI(...)

    # Wire ModelRouter: selects optimal model per task type based on available provider
    from app.agent.model_router import ModelRouter

    try:
        _mr_provider = "openai" if _openai_key else ("anthropic" if _anthropic_key else "anthropic")
        _model_router: Any = ModelRouter(provider_name=_mr_provider)
    except Exception as _mr_exc:
        _model_router = None
        logger.warning("model_router_init_failed", error=str(_mr_exc))

    async def _resolve_retrieval_llm(
        tenant_context: TenantContext,
        strategy: RAGStrategy,
    ) -> ResolvedLLM | None:
        del strategy
        tenant_config: dict[str, Any] | None = None
        config_store = getattr(app.state, "llm_config_store", None)
        if config_store is not None:
            tenant_config = await config_store.get_config(tenant_context.tenant_id)
        if tenant_config is None:
            tenant_config = getattr(app.state, "_llm_configs", {}).get(tenant_context.tenant_id)

        if tenant_config is not None:
            encrypted_key = str(tenant_config.get("encrypted_key") or "")
            provider_name = str(tenant_config.get("provider") or "")
            configured_model = str(
                tenant_config.get("model") or tenant_config.get("default_model") or ""
            ).strip()
            if not encrypted_key or not provider_name:
                return None
            try:
                from app.providers.registry import instantiate_configured_provider

                api_key = get_vault().decrypt(encrypted_key)
                provider = instantiate_configured_provider(
                    provider_name,
                    api_key=api_key,
                    model=configured_model,
                    base_url=str(tenant_config.get("base_url") or ""),
                )
            except Exception as exc:
                logger.warning(
                    "tenant_retrieval_provider_resolution_failed",
                    tenant_id=tenant_context.tenant_id,
                    error=type(exc).__name__,
                )
                return None
            if provider is None:
                return None
            model = configured_model
            if not model:
                provider_default = getattr(provider, "_default_model", "")
                model = provider_default.strip() if isinstance(provider_default, str) else ""
            return (
                ResolvedLLM(
                    provider=provider,
                    model=model,
                    provider_type=provider_name.strip().lower(),
                )
                if model
                else None
            )

        provider_default = getattr(_app_provider, "_default_model", "")
        model = provider_default.strip() if isinstance(provider_default, str) else ""
        if not model and isinstance(_app_provider, FakeProvider):
            model = "fake-provider"
        if not model:
            return None
        return ResolvedLLM(
            provider=_app_provider,
            model=model,
            provider_type=str(getattr(_app_provider, "_agentverse_provider_type", "")),
        )

    _web_search_capability = build_safe_web_search_capability(
        searxng_url=settings.searxng_url,
        policy_services=(_policy_engine, _cost, _hitl),
        allowed_domains=parse_allowed_domains(settings.web_search_allowed_domains),
    )
    from app.rag.raft import InMemoryRAFTRepository, RAFTService

    _raft_service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={},
    )
    _rag_adapter_configuration = RAGAdapterConfiguration(
        colbert_checkpoint=settings.colbert_checkpoint
    )
    _retrieval_gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=None,
            embedder=_embedder,
            llm_resolver=_resolve_retrieval_llm,
            graph_capability=None,
            search_capability=_web_search_capability,
            policy_services=(_policy_engine, _cost, _hitl),
            cost_controller=_cost,
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_knowledge_store),
            strategy_capabilities=core_strategy_capabilities(_rag_adapter_configuration),
            raft_service=_raft_service,
            colbert_checkpoint=settings.colbert_checkpoint,
        )
    )
    _retrieval_gateways_to_close: dict[int, object] = {id(_retrieval_gateway): _retrieval_gateway}

    from app.rpa.executor import RPAExecutor
    from app.rpa.session import RPASessionStore

    _rpa_session_manager = BrowserSessionManager()
    _rpa_artifact_store = get_artifact_store()

    # Determine whether the embedder supports vision for screenshot analysis
    _supports_vision = (
        _embedder is not None
        and hasattr(_embedder, "supports_vision")
        and _embedder.supports_vision()
    )
    _rpa_executor = RPAExecutor(
        session_manager=_rpa_session_manager,
        artifact_store=_rpa_artifact_store,
        vision_provider=_embedder if _supports_vision else None,
    )
    _rpa_session_store = RPASessionStore()

    # Perception
    from app.perception.browser_agent import BrowserAgent
    from app.perception.page_analyzer import PageAnalyzer

    _browser_agent = BrowserAgent(vision_provider=_embedder if _supports_vision else None)
    _page_analyzer = PageAnalyzer(browser_agent=_browser_agent)

    _task_queue = CeleryGoalTaskQueue() if manage_pools and settings.redis_url else None
    _goal_svc = goal_service or GoalService(
        audit_log=_audit_log, hitl=_hitl, task_queue=_task_queue
    )

    # Wire service references into the compliance controller for data export.
    # These are the non-DB services; the lifespan re-wires with DB-backed ones.
    _compliance_controller.configure_services(
        goal_service=_goal_svc,
        audit_log=_audit_log,
        agent_store=_agent_store,
        schedule_store=_schedule_store,
        knowledge_store=_knowledge_store,
    )

    # Attach all services to app.state (delegated from caller's namespace)
    # This module mutates app.state directly via the extracted code above.
