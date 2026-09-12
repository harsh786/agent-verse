# Graph Report - agent-verse-backend  (2026-09-12)

## Corpus Check
- 2966 files · ~2,382,234 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 48732 nodes · 116415 edges · 1641 communities (1243 shown, 121 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 6967 edges (avg confidence: 0.94)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `2b33ed52`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- RollbackEngine
- EmbeddingOrchestrator
- rag/gateway.py
- GoalService
- EvalRunner
- test_graph_comprehensive_coverage.py
- AgentStore
- test_main_create_app.py
- RAGStrategy
- RedTeamRunner
- test_all_memory_types.py
- HITLGateway
- ToolContext
- test_agent_graph_build_gaps.py
- test_scheduler.py
- KnowledgeCollection
- GoalRuntimeProfile
- PolicyEngine
- tenants.py
- CrossEncoderReranker
- build_default_registry
- test_sandbox_runtime.py
- RuntimeFlags
- TenantContext
- CredentialVault
- RiskLevel
- api/knowledge.py
- RetrievalResult
- test_dispatcher.py
- test_colbert_runtime.py
- test_helpers.py
- org/router.py
- test_truly_live_everything.py
- strategy_contracts.py
- test_batch3_servers.py
- _post
- CostController
- test_extra_coverage_servers.py
- observability/metrics.py
- TeamFormationEngine
- test_remaining_servers_dispatch.py
- test_executor_playwright_mock.py
- test_evals_comprehensive.py
- TriggerConsumerSupervisor
- airtable_server.py
- test_agents_extra4.py
- test_governance_extra3.py
- registry_wiring.py
- test_batch7_servers.py
- ContentDeduplicator
- test_remaining_connectors.py
- ModelOrchestrator
- Complexity
- test_batch5_servers.py
- test_chunkers.py
- MCPServerConfig
- test_all_agent_patterns_comprehensive.py
- test_multimodal_e2e.py
- test_extra_coverage_servers2.py
- test_all_connectors_e2e.py
- get_connector
- test_phases_4_5.py
- PatternState
- SelfOptimizer
- AnthropicProvider
- StepTypeRegistry
- test_retrieval_gateway.py
- test_google_storage_payment_connectors.py
- test_database_analytics_connectors.py
- test_batch4_servers.py
- asyncio
- test_batch1_servers.py
- ContentType
- parser_registry.py
- ColBERTPattern
- _TemplateStore
- chunk_by_tokens
- test_knowledge_extra4.py
- test_batch6_servers.py
- LongTermMemoryStore
- IngestionOrchestrator
- test_phase5_api.py
- GuardrailChecker
- MockMCPClient
- StructuredPlan
- test_rbac_comprehensive2.py
- QueryPlanner
- test_tenant_service_extra2.py
- SIEMType
- client
- StepDefinition
- SystemTemplateStore
- reasoning_contracts.py
- test_multimodal_router.py
- ExecutionTier
- test_gateway_entrypoints.py
- test_tenants_extra4.py
- test_extra_coverage_servers5.py
- test_comms_servers_dispatch.py
- test_governor.py
- _record_goal_duration_metric
- test_catalog_comprehensive.py
- role_taxonomy.py
- test_agent_identity_comprehensive.py
- test_goal_tree_comprehensive.py
- ToolReliabilityStore
- test_crm_servers_dispatch.py
- api/governance.py
- test_extra_coverage_servers3.py
- Constitution
- _CollabPubSub
- test_modular_rag.py
- create_app
- test_rag_patterns_real_openai.py
- PeerReviewPattern
- FakeProvider
- WorkflowExecutor
- test_routers.py
- test_program09_coordination_api.py
- api/civilization.py
- HandoffRecord
- rpa/test_artifacts_comprehensive.py
- FakeRedis
- test_devtools_servers_dispatch.py
- test_productivity_servers_dispatch.py
- KnowledgeStore
- StrategyRunner
- OrgService
- civilization/test_metrics.py
- test_batch2_servers.py
- CodeInterpreter
- triggers/test_security.py
- coordination/store.py
- test_dynamic_orchestration_e2e.py
- test_celery_maintenance_real.py
- CollaborationStore
- _ingest_repo_background
- test_runtime_scorecard.py
- test_semantic_cache_world_class.py
- RPAArtifactStore
- BrowserSessionManager
- ReflexionStore
- connectors.py
- goals.py
- test_policies_extra.py
- WorkflowTestRunner
- Message
- TestWorkflowExecutorDispatch
- api/ingestion.py
- test_enterprise.py
- test_insights_extra.py
- WorkflowCompiler
- AgentState
- test_security_runtime.py
- test_connectors_extra2.py
- RuntimeSSEEmitter
- ModelRouter
- SIEMConfig
- Classification
- QualityGateSystem
- cli/main.py
- chat/router.py
- test_bus.py
- api/test_governance_comprehensive.py
- test_enterprise_intelligence_gaps.py
- CapabilitySearch
- test_condition.py
- test_session_store_comprehensive.py
- test_tasks_helpers_extra.py
- test_org_advanced_endpoints.py
- test_training_export_comprehensive2.py
- test_all_models.py
- EmailTool
- test_collab_extra3.py
- test_cloud_servers_dispatch.py
- test_finance_servers_dispatch.py
- test_executor_all_tools.py
- check_grounding
- civilization/test_events.py
- CostTracker
- test_extra_coverage_servers4.py
- test_executor_standalone_paths.py
- PostgresWorkflowApprovalStore
- test_learning.py
- ModelGateway
- SupervisorAgent
- agents.py
- CostOptimizer
- OrgCommand
- test_middleware_comprehensive.py
- chat/models.py
- ScheduleStore
- _deps.py
- build_envelope
- ComplianceController
- test_identity_action_safety.py
- PromptVariant
- test_security_audit_findings.py
- WorkflowState
- test_raft_lifecycle.py
- gateway/router.py
- test_group_chat.py
- test_knowledge_store_comprehensive.py
- analytics/aggregator.py
- test_rls_isolation.py
- Any
- _redact_pii
- WorkflowDefinition
- api/a2a.py
- NotificationService
- AuditEvent
- _scheduled_goal_id
- WebhookDeliverySystem
- .register_builtin_handler
- SemanticCache
- test_a2a_comprehensive.py
- DebateOrchestrator
- test_consent_retention.py
- test_main_comprehensive.py
- triggers.py
- TemplateSecurityReviewer
- MetaAgentPlanner
- InMemoryPatternCheckpointStore
- FileOps
- test_reasoning_retrieval_strategies.py
- extract_tool_call
- test_state_machine.py
- SLOTracker
- _FakeRedis
- test_extra_coverage_servers6.py
- test_data_servers_dispatch.py
- routers.py
- Any
- test_scope_enforcement_comprehensive.py
- test_blackboard.py
- AgentCollabSession
- test_schedules_extra.py
- voice/router.py
- NLTriggerResolver
- workflow/router.py
- test_keycloak_comprehensive.py
- InMemoryCancellationRepository
- PolicyResult
- test_tool_cache.py
- test_tracing_comprehensive.py
- secrets.py
- test_new_tools.py
- MultimodalPipeline
- test_org_router.py
- IntentRouter
- _make_app
- org/events.py
- ImageAttachment
- assert_public_url
- test_goals_comprehensive.py
- test_main_extra2.py
- test_perception_gaps.py
- a2a_security.py
- test_society.py
- GoalCostBreakdown
- asyncio
- TestSemanticCacheHashAndKey
- test_sanitization_comprehensive.py
- test_auction_swarm_runtime.py
- coordination/contracts.py
- test_hallucination_fixes.py
- WorkflowService
- AuctionAnnouncement
- MetaOrchestrator
- _WorkflowStore
- test_openapi_importer_comprehensive.py
- BrowserAgent
- FasterWhisperSTT
- RuntimeConstraints
- test_saml_provider.py
- factory
- ExperimentRegistry
- Governor
- test_cost_tracker.py
- _make_breaker
- test_skills_executor.py
- resolve_expires_at
- test_servers_comprehensive.py
- get_inverse_fn
- test_auth_api_coverage.py
- WorkItem
- _make_service
- channels/ingestion.py
- Event
- skills_runtime.py
- PageAnalyzer
- test_security_org.py
- RoutingDecision
- StallDetector
- test_marketplace_endpoint_gaps.py
- RuntimeProfileBuilder
- OutboundWebhookService
- test_enterprise_comprehensive2.py
- LedgerRevision
- SimulationRunner
- test_generative_agent.py
- MCPClient
- DeletionOrchestrator
- ComplianceBundleManager
- MQTTTriggerConsumer
- test_explainability.py
- Tokenizer
- test_workflow_planner_comprehensive.py
- ComplianceChecker
- WorkflowHITLRequest
- DepartmentMemory
- ChannelRateLimiter
- test_redis_factory.py
- test_enterprise_api.py
- test_program09_core.py
- classify_tool_risk
- memory_v2.py
- asyncio
- KnowledgeGraphStore
- test_oauth_flow.py
- ExecutionMemory
- ABTestingEngine
- test_quantization.py
- _make_agents_app
- ._make
- test_gateway_gap_closure.py
- ToolSelector
- outbox.py
- RAFTJobRecord
- test_validators.py
- org/connectors/__init__.py
- test_consumers.py
- PromptOptimizer
- asyncio
- test_tenants.py
- test_router_runs.py
- check_tool_args_for_exfil
- test_goals_final.py
- LateChunker
- test_governance_integration.py
- AgentVersePlugin
- InMemoryCacheBackend
- test_insights_comprehensive.py
- test_integrations_api_comprehensive.py
- test_phase14_deep_coverage.py
- OrgMCPServer
- schedules.py
- auth/mfa.py
- test_scim_handler.py
- LLMResponseCache
- test_knowledge_base_pipeline.py
- test_compliance_endpoint_gaps.py
- test_collab_api_comprehensive.py
- api/memory.py
- test_context_pipeline.py
- CodeExecutionWorkload
- test_knowledge_comprehensive.py
- ChatService
- test_a2a_dispatch.py
- rpa.py
- test_fakeredis_gaps.py
- test_agents_api.py
- test_communication_connectors.py
- test_mfa.py
- test_celery_tasks_coverage.py
- test_expression_engine.py
- _make_app
- test_connectors_comprehensive2.py
- _vec
- test_full_stack_e2e.py
- GoalAnalyticsAggregator
- test_stream.py
- MarketplaceV2
- test_agents_extra.py
- ContextEngine
- test_condition_consumer.py
- test_chat_api.py
- _headers
- AuditV3
- test_orchestrator.py
- api/billing.py
- TestSolutionsCatalog
- test_scopes_rbac.py
- asyncio
- TenantOptimizationState
- _make_app
- test_plan_runtime.py
- ProvenanceRecord
- api/test_connectors.py
- workflows.py
- test_document_parser_comprehensive2.py
- AgentRouter
- costs.py
- PermissionCache
- test_hosted_reranker.py
- test_costs_comprehensive.py
- test_voice_router.py
- test_ip_allowlist_comprehensive.py
- rag/engine.py
- VoyageProvider
- test_executor_comprehensive.py
- get_extractor
- FallbackChain
- test_guardrails_comprehensive2.py
- JiraIngestor
- test_agent_patterns_real_openai.py
- test_all_providers.py
- test_tasks_coverage_gaps.py
- KnowledgeRuntimeProfile
- capabilities/test_capability_registry.py
- Goal
- a2a/__init__.py
- api/auth.py
- HealthCheck
- test_prompt_optimizer_persistence.py
- SIEMAdapter
- test_priority_queue.py
- test_domain_role_templates.py
- monitoring/test_parsers.py
- test_enterprise_extra3.py
- test_memory_comprehensive.py
- test_infrastructure_e2e.py
- test_schedules_api.py
- api/mfa.py
- test_marketplace_v2.py
- test_phase4_connectors.py
- test_agent_builder.py
- RAFTDatasetRecord
- test_scope_seeder.py
- MarketplaceAgentContent
- test_tenants_comprehensive.py
- _svc
- org/test_security.py
- test_retrieval_engine.py
- _sign
- test_governance_comprehensive2.py
- OcrEngine
- test_civilization_api.py
- get_logger
- LargePayloadStore
- test_connectors_comprehensive.py
- guardrails_v2/engine.py
- MoAProposal
- api/model_registry.py
- test_docker_compose.py
- test_self_optimizer_v2_comprehensive2.py
- collab.py
- FastAPI
- test_phase3_4_multimodal.py
- ContentLoader
- execution_environment/models.py
- test_execution_strategy.py
- _make_app
- test_phase8_9_guardrails_trust.py
- WorkingMemory
- test_otel.py
- SemanticChunker
- test_raft_repository_integration.py
- router_runs.py
- api/knowledge_graph.py
- test_knowledge_comprehensive2.py
- HttpApiConnector
- test_enterprise_v2.py
- asyncio
- test_audit_scopes_limits.py
- ConsensusVerifier
- test_guardrail_block_e2e.py
- test_templates_comprehensive2.py
- BoundedAsyncExecutor
- ProviderCircuitBreaker
- test_nl_scheduler_comprehensive.py
- OrgMCPResources
- test_goal_classifier_doc4.py
- InjectionGuard
- audit_v3.py
- ExtractedField
- intent_router.py
- test_telegram_server.py
- test_schedules_comprehensive.py
- test_store_comprehensive3.py
- IdempotencyStore
- parse_verifier_verdict
- _make_connectors_app
- AuditFlusher
- AgentManifest
- test_rls_behavioral_isolation.py
- test_conversational_consumer.py
- models/coordination.py
- OrgLoopDetector
- test_self_optimization_v2_comprehensive.py
- GoalExecutionLock
- _make_app
- MockMCPServer
- UsageService
- tenancy/billing.py
- ArtifactTool
- router_hitl.py
- test_perception_comprehensive.py
- ReflexionPattern
- test_suggestions_shape.py
- ai_ops.py
- _make_app
- dpdp.py
- Any
- SelfConsistencyPattern
- TreeOfThoughtsPattern
- estimate_cost
- SelfOptimizerV2
- test_devops_connectors.py
- OpenAIFineTuneProvider
- test_webhook_trigger_activation.py
- test_tools_comprehensive.py
- test_agents_comprehensive2.py
- ProspectiveMemoryService
- test_mcp_circuit_breaker.py
- asyncio
- program_of_thought.py
- TestAgentRuntimeModels
- MemoryConsolidator
- PatternConfig
- _safe_eval_condition
- guardrails_v2.py
- test_code_rag.py
- OAuthFlowManager
- test_workflows.py
- CapabilityRegistry
- OrgDigitalTwin
- org/rbac.py
- fire_due_schedules
- is_valid_transition
- TenantUserService
- router_versions.py
- _synthesize_goal_tree_results
- test_analytics_db.py
- test_civilization_extra4.py
- SCIMHandler
- test_hitl_new_endpoints.py
- test_store_final_coverage.py
- test_voice_e2e.py
- TriggerType
- test_agent_identity_layer.py
- StreamingGuard
- start_policy_subscriber
- ClaimRepository
- execution_environment/test_artifacts.py
- GitHubIngestor
- test_workflows_comprehensive2.py
- SalienceScorer
- .generate
- test_greeting.py
- WorkflowVariableStore
- test_mission_flow.py
- RPAExecutor
- NLIChecker
- test_knowledge_api.py
- workflow/test_router.py
- Phased Roadmap (9 layers → 47 components, TDD each)
- SearchDirectiveParser
- test_notification_service.py
- emarsys_server.py
- generate_gst_invoice
- test_workflows_comprehensive.py
- test_knowledge_rpa.py
- perception.py
- TenantService
- MemoryAPI
- test_p3_phases.py
- CoordinationStreams
- test_configured_model_registry_api.py
- test_a2a_extra2.py
- RerankPolicy
- OAuthState
- test_default_path_rerank.py
- ContextPipeline
- workflow_executor.py
- _resolve_checkpointer
- test_phase2_model_registry.py
- tasks.py
- ._evaluate_rule
- PIIDetector
- TestUniversalArgumentResolver
- CodeWorkloadValidator
- test_api.py
- EvalSuiteRunner
- test_context_manager.py
- GuardrailViolation
- logging.py
- DocumentType
- EntityVersionManager
- RedisCircuitBreaker
- LimitsV2Checker
- _strip_secret_redis_schedule_fields
- test_rag_migration_roundtrip.py
- test_perception_api.py
- workflow/test_context.py
- test_eval_suite_offline_discrimination.py
- test_batch8_servers.py
- rag/raft.py
- voyager.py
- ChatSearchEngine
- test_collaboration_runtime.py
- api/test_collab.py
- _make_db_mock
- test_celery_agentgraph.py
- check_and_process_emails
- test_project_management_connectors.py
- Any
- get_builtin_server_configs
- RetrievalEvaluator
- MarkdownParser
- SelfRefinePattern
- PostgresWorkflowRunStore
- _make_app
- test_golden_datasets.py
- test_rpa_comprehensive.py
- test_self_optimizer_v2_comprehensive.py
- MCPWebSocketClient
- test_audit_v2.py
- TestAnswerSynthesizer
- _cron_missed_runs_utc
- api/guardrails.py
- marketplace_monetization.py
- ConversationContext
- ServicesAPI
- test_optimizer_wired.py
- CommandScheduler
- CivilizationOrchestrator
- GoalRefinementPipeline
- models/knowledge.py
- calibrate_scores
- LlmStructuredExtractor
- build_result_artifact
- SubTenantService
- test_backend_fixes.py
- test_civilization_api_comprehensive2.py
- test_connectors_catalog.py
- ._node_plan
- test_phase10_11_ai_ops_memory.py
- HITLWorkflowGateway
- BenchmarkStore
- test_untested_modules.py
- few_shot_cot.py
- api/tools.py
- trust_governance.py
- RuntimeProfilesRegistry
- test_spawn_tool.py
- SharePointConnector
- CRDTRoomManager
- test_ingestion_pipeline.py
- PlanMode
- guardrail_engine.py
- KnowledgeAccessPolicy
- test_wait_event_gate.py
- ModelRouter
- test_tools_api_comprehensive.py
- api/test_artifacts_comprehensive.py
- test_middleware_full.py
- ChatCodeExecutor
- GoldenTask
- chat/service.py
- LATSRuntime
- test_goals.py
- embeddings.py
- run_mocked_certification
- CredentialInjector
- pools.py
- agent/test_router.py
- test_layer4_complete.py
- VoiceAlertManager
- ToxicityClassifier
- test_ghost_run.py
- ReflexionService
- InClusterKubernetesClient
- test_tool_risk.py
- _make_app
- test_api_extended.py
- test_safe_web_capability.py
- insights.py
- AgentCredentialStore
- DelegationChain
- test_tenants_comprehensive2.py
- .run
- EmbeddingRouter
- NotionConnector
- scan_for_encoding_attacks
- test_router_hitl.py
- test_tenant_service_db.py
- test_multi_agent_auto_selection.py
- OcrDocumentTool
- test_phase6_7_rag_runtime.py
- test_local_runner.py
- test_rss.py
- tool_allowed_for_autonomy
- PolicyEvidenceEngine
- test_replay_comprehensive.py
- ConfluenceIngestor
- RedisBulkhead
- test_gap_fill_phase2.py
- coordination_handoffs.py
- warm_permission_cache
- test_worker_entrypoint.py
- observability.py
- _FakeSession
- magentic/adapter.py
- test_entitlements.py
- VoiceStreamingSession
- skills.py
- call_external_a2a_agent
- test_analytics_comprehensive.py
- test_workflows_extra2.py
- agent/test_persistence.py
- test_retrieval_strategies_comprehensive.py
- InMemoryMemoryRepository
- test_cost_dashboard_api.py
- agent/test_errors.py
- AgentTestHarness
- orchestration.py
- admin.py
- test_main_lifespan.py
- test_artifact_tool.py
- test_real_simulation.py
- test_polling.py
- CeleryGoalTaskQueue
- test_agent_advanced.py
- _svc
- _update_task_status
- _fixture
- exceptions.py
- wait_for_status
- test_memory_learning_services.py
- GDriveConnector
- LLMJudge
- api/analytics.py
- Any
- _ctx
- upsert_google_user
- GraphFactory
- decide_rollout
- _make_guardrails_app
- test_guardrail_rules_persistence.py
- test_production_safety.py
- AutonomyEnforcer
- test_untested_modules2.py
- get_org_event_publisher
- test_supervisor_debate_nodes.py
- test_composer_llm_wiring.py
- test_live_platform.py
- test_vault.py
- test_oauth.py
- test_step_enforcement.py
- api/coordination.py
- EvalSuite
- SubAgentTask
- TestDomainPolicies
- GraphAccessControl
- SourceConfig
- test_ingestors_coverage.py
- MFAStore
- org/metrics.py
- CorpusSample
- test_phase12_13_skills_frontend.py
- test_migrations.py
- TestTenantServiceCachedLookup
- SourceConfigStore
- BulkheadRegistry
- test_gap_integrations.py
- OrgHealthScore
- OrgEventPublisher
- _make_db_mock
- api/artifacts.py
- ocr.py
- .probe_rag_strategy_contract
- GuardrailEngine
- verify_goal_token
- models/auth.py
- ChannelAuthGuard
- test_rollback_experiment.py
- test_token_metrics.py
- StructuredLogStore
- api/policy_rules.py
- test_workflow_builder.py
- test_durable_execution.py
- RedisBulkheadRegistry
- test_dr_drill.py
- Any
- test_a2a.py
- test_agent_patterns.py
- GoalPersistenceEngine
- LLMQueryTransformer
- test_cli.py
- test_state_runtime.py
- test_goal_hitl_lifecycle_e2e.py
- grant_elevation
- _get_client_ip
- google_oauth.py
- TestToolReliabilityStoreWithDBError
- decision_store.py
- test_system_comprehensive.py
- PromptCompressor
- Any
- CSVParser
- test_critical_fixes.py
- _make_integrations_app
- test_rate_limiter.py
- _MockSession
- GoalDeduplicator
- test_phase5_knowledge_graph.py
- extract_roles_from_jwt
- TestAdminRouter
- test_marketplace_v2_extra.py
- test_user_models.py
- TestRunGoalPaths
- TestAuth
- ReActPattern
- coordination_group_chat.py
- system.py
- TestCostOptimizerSuggestions
- DecisionTrace
- multi_turn_eval.py
- coordination_magentic.py
- coordination_moa.py
- test_guardrails_v3.py
- scan_output_for_anomalies
- verify_stream_token
- test_magentic_api.py
- GuardrailsEngine
- test_graph_persistence_wiring.py
- _StatefulMockSession
- _make_eval_runner
- test_workflow_definition_bridge.py
- agent_runtime.py
- test_workflow_trigger_e2e.py
- summarize_pattern_selection
- has_permission
- test_prompt_variants_api.py
- .acquire
- SlackIngestor
- test_family_a_time.py
- list_active_sessions
- test_oauth_security.py
- test_supervisor_debate_wiring.py
- TestFireDueSchedules
- test_routing.py
- test_goals_debate.py
- scan_tool_output
- notebook_parser.py
- TestMultimodalPipeline
- test_comprehensive_coverage.py
- MemoryRecord
- RoutingOptimizer
- .create_workflow_approval
- test_agent_teams_e2e.py
- TestCheckAuthRateLimit
- test_builder_preview.py
- agent_credentials_api.py
- TestToolReliabilityDBPaths
- evaluate_rule
- _inject_goal
- requires_consensus
- test_rbac.py
- firebase_server.py
- EmailChannelAdapter
- integrations.py
- TelegramChannelAdapter
- test_keycloak.py
- ConversationManager
- record_desired_workers
- TenantScopedStore
- make_resp
- TenantMiddleware
- Agent pattern canary rollback
- Agent pattern runaway limits
- Auction anomalies
- Coordination delivery lag
- Coordination lease reclaims
- Magentic stalls and fallbacks
- Reflexion quality poisoning
- Sandbox denial outage
- Tenant policy anomalies
- generate_api_key
- PluginRegistry
- test_insights.py
- ._delivery
- test_ocr_persist_kb.py
- .exchange_code
- _default_marketplace
- FeatureFlagService
- test_org_mission_hitl_e2e.py
- TestPdfIngestor
- get_my_sla
- test_phase_metrics.py
- chat/templates.py
- TestSelectVariant
- evaluate_progress
- resize_image_b64
- test_benchmarking.py
- BenchmarkRun
- test_gemini_provider_comprehensive.py
- TestMaybePromote
- certify_sandbox
- _mock_boto3_ses
- KokoroTTS
- OmniVoiceTTS
- test_tool_aware_planning.py
- test_kubernetes_runner.py
- test_trigger_fire_e2e.py
- RedisDeduplicationCache
- load/goal_submission.js
- test_celery_critical.py
- test_hitl_db_persistence.py
- test_sse_bridge_sentinel.py
- get_sandbox_config
- has_feature
- meta_agent.py
- agent_credentials.py
- AgentBenchmark
- Reranker
- test_prompted_tool_fallback.py
- test_cost_atomic.py
- TestMakeAgentGraphForTenant
- TestRedisCache
- TestIsSignificant
- test_http_fallback.py
- ToolTrace
- MacOSSayTTS
- AuditLog
- LLMConfigStore
- todoist_server.py
- api/test_memory_api.py
- test_mfa_module.py
- advanced_services.py
- Bulkhead
- api/test_replay.py
- StoreGateway
- test_helm_chart.py
- test_k8s_manifests.py
- test_message_editing.py
- TestIsIPAllowed
- ._bayesian_prob_better
- VoiceWebhookAdapter
- test_self_optimizer_auto_apply_flag.py
- SupervisorPattern
- _decode_header_value
- roles.py
- ModelStats
- codeact.py
- .list_async
- TestInputClamping
- OAuthToken
- WebCrawlConnector
- mailchimp_server.py
- test_billing_real.py
- test_eval_judge_output.py
- test_openapi_schema.py
- ._percentile
- GoalTokenStore
- test_tts_engine.py
- RoleResolver
- asyncio
- test_hitl_gate.py
- TestSubmitGoal
- _build_goal_kwargs_for_alert
- TestDocxIngestor
- agent/consensus.py
- get_public_status
- test_knowledge_graph_e2e.py
- mattermost_server.py
- 0097_coordination_runtime.py
- Any
- _name_tokens
- .score
- _setup_sigterm
- MemoryWriteRequest
- test_guardrail_kwarg.py
- freshsales_server.py
- test_supervisor_goal_id_threading.py
- looker_server.py
- _FakeDb
- models/__init__.py
- yotpo_server.py
- Any
- _cleanup_expired_crdt_tokens
- .refresh_token
- parametrize
- main_services.py
- query_reformulator.py
- _Noop
- .check
- test_folders.py
- AgentVerse Load Tests
- qa-agent.md
- wave_server.py
- SlackChannelAdapter
- smoke.js
- test_mission_execute_wired_services.py
- WebhookChannelAdapter
- activecampaign_server.py
- test_append_operation_in_memory_version_conflict
- customerio_server.py
- facebook_conversions_server.py
- error_response
- generate_api_key
- 0101_magentic_moa.py
- 0102_camel_generative_swarm_auction.py
- 0104_memory_learning.py
- _Message
- AzureTTS
- test_ingestion_pipeline_e2e.py
- .publish_target
- pandadoc_server.py
- ElevenLabsTTS
- OpenAITTS
- TestPdfIngestor
- TestDocxIngestor
- test_mission_decomposition.py
- test_capabilities.py
- TestEvalSuiteWave4
- docusign_server.py
- TestPersistOutcome
- test_tool_reliability.py
- TestLockReleaseFix
- WhatsAppChannelAdapter
- clickup_server.py
- agent/supervisor.py
- linear_server.py
- _FakeLuaScript
- load_roles_from_db
- .maybe_promote
- ._assign
- _FakeOrgService
- smartsuite_server.py
- ApprovalChainEngine
- monday_server.py
- notion_server.py
- teamwork_server.py
- NotionSourceConnector
- expensify_server.py
- smoke_e2e.sh
- grafana_server.py
- test_platform_wiring_e2e.py
- tool_policy.py
- pytest_collection_modifyitems
- compliance_endpoints.js
- Runbook: Backend Instance Down
- Runbook: High Goal Failure Rate
- security-reviewer.md
- TestLabAPI
- .find_completed_model
- evernote_server.py
- test_org_db_e2e.py
- gmail_server.py
- test_internal_url_is_blocked
- convertkit_server.py
- wrike_server.py
- test_redbeat_config.py
- TestRateLimiterTenantIsolation
- mandrill_server.py
- cloudinary_server.py
- TestSubscribeEvents
- v1/router.py
- stripe_server.py
- 0091_rag_ingestion_structures.py
- 0100_handoffs_group_chat.py
- slack_server.py
- mailerlite_server.py
- test_aggregator.py
- woocommerce_server.py
- youtube_server.py
- TestGuardrailInternalHelpers
- zendesk_server.py
- TestPhase3Wiring
- zoom_server.py
- RedisCapabilityTracker
- get_goal_cost_metrics
- IngestionPipeline
- discord_server.py
- TestScopeEnforcementBypass
- acoustic_server.py
- amadeus_server.py
- apache_kafka_server.py
- microsoft_teams_server.py
- long_term_extractor.py
- CompletionRequest
- dr-drill.sh
- asana_server.py
- TestImapListenerIsEnabled
- test_goal_service_integration.py
- TestInflationTest
- test_bg_switch_script.py
- doordash_server.py
- AgentVerse Load Tests
- soak.js
- ecwid_server.py
- TestSubscribeHITLRejections
- 0045_civilization.py
- 0092_repository_ingestion_leases.py
- 0103_routing_safety_optimization.py
- test_keycloak_sso.py
- TestPauseGoal
- env.py
- 0120_knowledge_chunks_binary_index.py
- PromptVariantSelector
- pipedrive_server.py
- snovio_server.py
- toast_pos_server.py
- appsheet_server.py
- zoho_crm_server.py
- Plan: Concise-planner nudge for verbose reasoning models
- capsule_crm_server.py
- clearbit_server.py
- dynamics365_server.py
- TestEngineLeakFix
- TestPhase2Wiring
- affinity_server.py
- amazon_ses_server.py
- netsuite_server.py
- amazon_sqs_server.py
- apollo_server.py
- emma_server.py
- recruitee_server.py
- zuora_server.py
- loadtest/goal_submission.js
- AgentVerse — Backend
- _digest
- attio_server.py
- aweber_server.py
- auth_throughput.js
- test_structlog_migration.py
- bigquery_server.py
- bitly_server.py
- test_worker_embedder.py
- braintree_server.py
- test_spec_module_importable
- encharge_server.py
- clockify_server.py
- coordination_sessions.js
- group_chat_ws.js
- thresholds.js
- basecamp_server.py
- analyze_gaps.py
- app/agent/nodes/__init__.py
- app/agent/tools/__init__.py
- freshbooks_server.py
- app/analytics/__init__.py
- bootstrap/__init__.py
- app/civilization/__init__.py
- app/cli/__init__.py
- auction/__init__.py
- camel/__init__.py
- generative/__init__.py
- magentic/__init__.py
- moa/__init__.py
- swarm/__init__.py
- greenhouse_server.py
- gusto_server.py
- campaign_monitor_server.py
- harvest_server.py
- gateway/channels/__init__.py
- app/gateway/__init__.py
- app/routing_runtime/__init__.py
- app/scaling/__init__.py
- app/state_runtime/__init__.py
- hive_server.py
- switch-traffic.sh
- coordination_streams.js
- sse_stream.js
- tests/agent/nodes/__init__.py
- microsoft_outlook_server.py
- api/conftest.py
- invoice_ninja_server.py
- knack_server.py
- miro_server.py
- ninox_server.py
- fullcontact_server.py
- _stub_ocr_engine
- test_configure_saml_no_db
- test_configure_saml_with_db
- test_saml_acs_missing_saml_response
- test_provision_scim_token_no_db
- test_provision_scim_token_with_db
- test_list_templates_v2
- test_compliance_status_hipaa_with_checker
- pivotal_tracker_server.py
- close_crm_server.py
- test_saml_metadata_with_row
- procore_server.py
- test_deploy_template_v2_install_failed
- test_deploy_template_v2_success
- test_add_review_v2_failure
- test_add_review_v2_success
- test_list_reviews_v2
- test_search_templates_v2
- test_get_simulation_available_tools_without_mcp_client
- test_get_simulation_available_tools_mcp_exception
- test_run_red_team
- cloudflare_server.py
- _mint_viewer_client
- profitwell_server.py
- test_apply_suggestion_not_found
- redmine_server.py
- test_list_eval_suites_no_runner
- TestCostOptimizerSummaryReport
- samcart_server.py
- smartsheets_server.py
- test_compliance_status_no_checker
- toggl_server.py
- test_compliance_status_unsupported_framework
- test_rerun_compliance_check_no_checker
- test_list_contracts_no_db
- test_sign_contract_invalid_type
- tests/multimodal/__init__.py
- constant_contact_server.py
- copper_server.py
- ._categorise
- tests/routing_runtime/__init__.py
- tests/state_runtime/__init__.py
- core/errors.py
- zoho_books_server.py
- agent-verse-backend
- zoho_invoice_server.py
- trello_server.py
- drip_server.py
- gainsight_server.py
- figma_server.py
- formstack_server.py
- getresponse_server.py
- gong_server.py
- google_forms_server.py
- google_slides_server.py
- .load_tokens_from_db
- google_tasks_server.py
- gravity_forms_server.py
- hubspot_server.py
- jotform_server.py
- linkedin_server.py
- loom_server.py
- loops_server.py
- mailgun_server.py
- manychat_server.py
- test_rpa_pdf_e2e.py
- _create_cron_trigger
- maropost_server.py
- microsoft_excel_server.py
- microsoft_onenote_server.py
- microsoft_todo_server.py
- highlevel_server.py
- moosend_server.py
- omnisend_server.py
- onesignal_server.py
- order_desk_server.py
- planhat_server.py
- plivo_server.py
- postmark_server.py
- pushbullet_server.py
- ringcentral_server.py
- salesforce_server.py
- shipstation_server.py
- TestAuditChainSeeding
- signnow_server.py
- sugarcrm_server.py
- surveymonkey_server.py
- twitch_server.py
- typeform_server.py
- wufoo_server.py
- hubspot_marketing_server.py
- TestSSEEndpointLastEventId
- TestWorkerWiring
- insightly_server.py
- buffer_server.py
- instagram_server.py
- ebay_server.py
- etsy_server.py
- facebook_lead_ads_server.py
- facebook_pages_server.py
- filestack_server.py
- gumroad_server.py
- whatsapp_server.py
- hootsuite_server.py
- kajabi_server.py
- lightspeed_server.py
- magento_server.py
- pinterest_server.py
- pushover_server.py
- sonarqube_server.py
- spotify_server.py
- sprout_social_server.py
- squarespace_server.py
- storyblok_server.py
- substack_server.py
- teachable_server.py
- .get_suggestions
- thinkific_server.py
- vimeo_server.py
- vonage_server.py
- wistia_server.py
- klaviyo_server.py
- klenty_server.py
- _extract_text
- konnektive_server.py
- leadpages_server.py
- test_tool_reliability_store_get_unreliable
- lemlist_server.py
- orbit_server.py
- outreach_server.py
- .summary_report
- overloop_server.py
- podio_server.py
- postgres_server.py
- reply_io_server.py
- salesloft_server.py
- segment_server.py
- sendgrid_server.py
- twilio_server.py
- test_goals_batch_submit_route_exists
- asyncio
- _MockGoalService
- .test_db_exception_falls_back_to_memory
- aws_server.py
- .purge_expired
- .__init__
- .purge_expired
- DeptAnalytics
- .list_api_keys
- test_toolcall_import_exists
- test_tenancy_integration.py
- .test_with_scorecard_returns_scores
- .select_tier
- .run
- .release
- test_persistence_attempt_written_to_db
- .eval
- .available_slots_sync
- .available_slots
- ._db_create_tenant
- ._db_revoke_api_key
- .get_tenant_by_sso_sub
- selfhosted-providers.md
- .get_key_by_sso_sub
- .sync_from_db
- tenancy/store.py
- test_delete_role_with_db_not_found
- test_list_ip_allowlist_with_db_rows
- test_delete_ip_allowlist_with_db_success
- test_saml_login_not_configured
- test_list_suggestions_applied_filter
- test_apply_experiment_no_self_opt_v2
- test_apply_experiment_applies_pending_winner
- test_get_template_v2_found
- test_deploy_template_v2_missing_connectors
- test_record_consent_no_db
- test_revoke_consent_no_db
- test_sign_contract_no_db
- test_saml_metadata_not_configured
- test_tenant_service_key_expiry
- test_tenant_service_list_api_keys
- test_tenant_service_revoke_wrong_tenant_raises
- test_agentgraph_accepts_bulkhead_registry
- .test_require_role_sync_dependency_raises_403_directly
- test_expired_key_not_resolved
- test_create_tenant_returns_complete_data
- test_get_tenant_not_found_raises
- test_list_api_keys_excludes_hash
- test_create_api_key_raw_key_returned_once
- test_resolve_invalid_key_returns_none

## God Nodes (most connected - your core abstractions)
1. `TenantContext` - 1452 edges
2. `FakeProvider` - 755 edges
3. `get_logger()` - 596 edges
4. `AgentGraph` - 462 edges
5. `PlanTier` - 462 edges
6. `create_app()` - 400 edges
7. `GoalService` - 359 edges
8. `_post()` - 353 edges
9. `GoalStatus` - 329 edges
10. `CompletionRequest` - 316 edges

## Surprising Connections (you probably didn't know these)
- `test_singleton_exists()` --uses--> `GoalClassifier`  [INFERRED]
  tests/agent/test_goal_classifier_doc4.py → app/agent/goal_classifier.py
- `test_agent_graph_has_node_rag_prime()` --uses--> `AgentGraph`  [INFERRED]
  tests/agent/test_rag_nodes.py → app/agent/graph.py
- `test_agent_graph_has_node_rag_remediate()` --uses--> `AgentGraph`  [INFERRED]
  tests/agent/test_rag_nodes.py → app/agent/graph.py
- `test_agent_graph_has_node_refine()` --uses--> `AgentGraph`  [INFERRED]
  tests/agent/test_rag_nodes.py → app/agent/graph.py
- `test_legacy_model_router_delegates_complexity_to_canonical_classifier()` --uses--> `ModelRouter`  [INFERRED]
  tests/orchestration/test_ownership_reconciliation.py → app/agent/model_router.py

## Import Cycles
- None detected.

## Communities (1641 total, 121 thin omitted)

### Community 0 - "RollbackEngine"
Cohesion: 0.02
Nodes (170): AuditLog, ExecutionMemory, ResultProcessor, Consume the ONE selector's multi-agent decision for this goal. Returns an empty…, Extract tool name — prefers structured tool_calls, then registry, then…, CircuitBreaker, Per-tool circuit breaker. Args: failure_threshold: Number of consecutive…, Return True if a call is allowed now (handles HALF_OPEN probe window). (+162 more)

### Community 1 - "EmbeddingOrchestrator"
Cohesion: 0.03
Nodes (57): DimensionPolicy, DimensionPolicy — maps model IDs to standard vector dimensions., EmbeddingModelRegistry, EmbeddingModelSpec, EmbeddingModelRegistry — catalogue of available embedding models., BatchEmbeddingResult, EmbeddingOrchestrator, EmbeddingSelectionResult (+49 more)

### Community 2 - "rag/gateway.py"
Cohesion: 0.02
Nodes (144): get_settings(), Return the cached process-wide settings singleton., AgenticDecision, Agentic Chunking pattern — LLM-driven proposition extraction as atomic chunks.…, _claims_support_answer(), _format_decision_evidence(), Any, Bounded agentic RAG decisions over canonical retrieval primitives. (+136 more)

### Community 3 - "GoalService"
Cohesion: 0.01
Nodes (302): __getattr__(), Any, Agent package - canonical LangGraph-based autonomous execution kernel., Load the graph lazily so state-only imports cannot form a cycle., AgentExecutionPlan, A typed execution plan for an agent run., GoalStatus, Agent execution state — the typed graph state for LangGraph. All fields are… (+294 more)

### Community 4 - "EvalRunner"
Cohesion: 0.04
Nodes (119): Result of executing a single planned step., StepResult, EvalRunner, Scores a completed AgentState on the 7 evaluation dimensions. Every weight,…, Return all 7 dimension names scored by this runner., score_async must overwrite the heuristic coherence with the LLM score., test_score_async_replaces_heuristic_coherence(), _tenant_ctx() (+111 more)

### Community 5 - "test_graph_comprehensive_coverage.py"
Cohesion: 0.03
Nodes (134): StepStatus, _make_agent_state(), _make_graph(), asyncio, FakeProvider, Comprehensive coverage tests for app/agent/graph.py. Targets the uncovered…, _node_think discards private reasoning and returns aggregate evidence., With enable_cot=True, chain-of-thought node runs before planning. (+126 more)

### Community 6 - "AgentStore"
Cohesion: 0.04
Nodes (88): AgentStore, Synchronous in-memory delete (used by tests / no-DB mode)., Soft-delete from PostgreSQL (is_active=FALSE) and remove from memory cache., Per-tenant in-memory agent registry. Key: (tenant_id, agent_id) → agent record…, _make_app(), Any, FastAPI, Comprehensive tests for /agents API endpoints — targets 19% → 55%+ coverage. (+80 more)

### Community 7 - "test_main_create_app.py"
Cohesion: 0.03
Nodes (44): asyncio, Coverage tests for app/main.py — create_app() factory, _FakeRedis,…, CORS and security middleware are registered., At least 3 middleware layers registered., App has routes registered (health route is deeply nested)., Expired sorted set returns 0., Expired sorted set for zremrangebyscore returns 0., Expired string key returns None. (+36 more)

### Community 8 - "RAGStrategy"
Cohesion: 0.03
Nodes (91): _LazyRuntimeCapabilities, StrEnum, RAGCapabilityCatalogueEntry, RAGRuntimeDependency, Authoritative runtime catalogue for the 20 canonical RAG strategies., One strategy's adapter, dependencies, readiness rule, and probe owner., Named runtime capabilities that can make a strategy unavailable., One sanitized dependency fact shared across catalogue predicates. (+83 more)

### Community 9 - "RedTeamRunner"
Cohesion: 0.03
Nodes (66): BehavioralRedTeamRunner, Any, Red-team runner — adversarial testing for agent safety and robustness., Submits adversarial payloads to the live agent and checks behavioral response.…, Returns True if agent correctly rejected the adversarial input., Runs adversarial test cases against the agent using real guardrail detection., Run red-team cases through real guardrail detection. Returns a report., RedTeamReport (+58 more)

### Community 10 - "test_all_memory_types.py"
Cohesion: 0.03
Nodes (72): Episode, EpisodicMemoryStore, Any, EpisodicMemoryStore — DB-backed storage for past goal experiences. Episodic…, Recall similar past episodes for a given goal., Format episodes as a context block for planner prompt., A single past experience., Format for injection into planner context. (+64 more)

### Community 11 - "HITLGateway"
Cohesion: 0.01
Nodes (215): ApprovalRequest, ApprovalStatus, _AwaitableBool, HITLGateway, Any, Human-In-The-Loop gateway. Dual-mode implementation: * asyncio.Event (in-…, Allow comparison with plain request_id strings for backward compat., Hash equals hash(request_id) so ApprovalRequest works as a dict key. (+207 more)

### Community 12 - "ToolContext"
Cohesion: 0.03
Nodes (102): Any, Planner-facing tool context built from an agent's connectors., One-line ``name(param1, param2) — description[:80]`` for a tool., Render a ToolSelection into a tiered prompt string. Three sections: -…, Return a readable tool list suitable for planner prompt context. When…, Find a tool by unqualified name or by Server.tool_name., _render_signature(), to_tiered_prompt() (+94 more)

### Community 13 - "test_agent_graph_build_gaps.py"
Cohesion: 0.04
Nodes (78): _fake_provider(), asyncio, FakeProvider, Tests for app/agent/graph.py AgentGraph._build branches and helper methods that…, _build adds the refine branch edges when self_refine enabled., _build adds reflect node when reflection enabled., _build adds peer_review node when enable_peer_review enabled., _build adds supervisor node when _enable_supervisor set via kwargs (H8). (+70 more)

### Community 14 - "test_scheduler.py"
Cohesion: 0.04
Nodes (69): FakeRunner, Fake in-process runner — used for unit tests and when no real runner is…, In-process fake runner. Runs :class:`~app.agent.graph.AgentGraph` or any pre-…, AlwaysHealthyCheck, AlwaysUnhealthyCheck, HealthStatus, ABC, Health-check interface for execution-environment runners. Each concrete runner… (+61 more)

### Community 15 - "KnowledgeCollection"
Cohesion: 0.02
Nodes (156): Chunk, Document, KnowledgeCollection, RAG data models — Knowledge collections, documents, and chunks., A named container for a set of related documents., A single ingested source document (before chunking)., A sub-document fragment with its embedding vector., Tenant-scoped knowledge store with persisted PostgreSQL retrieval. In… (+148 more)

### Community 16 - "GoalRuntimeProfile"
Cohesion: 0.05
Nodes (140): Any, DynamicGraphAssembler — builds a per-goal LangGraph from PatternConfig., CompatibilityDecision, Deterministic composition of one primary strategy and auxiliary capabilities., AgentPatternConfig, EvalConfig, GoalProperties, GoalRuntimeProfile (+132 more)

### Community 17 - "PolicyEngine"
Cohesion: 0.05
Nodes (49): Policy, PolicyEngine, Returns True if current time (in policy.timezone) is within policy's allowed…, Evaluate tool access. parent_policy_ids allows sub-agents to inherit parent…, Reload policies from DB. If tenant_id given, reload only that tenant's…, Evaluates tool calls against a set of policies. Policies are intentionally…, _load_worker_policy_engine(), PolicyEngine.evaluate must skip policies from other tenants. (+41 more)

### Community 18 - "tenants.py"
Cohesion: 0.03
Nodes (119): create_ip_allowlist_entry(), create_key(), create_role(), CreateIPAllowlistRequest, CreateKeyRequest, CreateRoleRequest, delete_ip_allowlist_entry(), delete_role() (+111 more)

### Community 19 - "CrossEncoderReranker"
Cohesion: 0.06
Nodes (40): cross_encode(), CrossEncoderBackend, CrossEncoderReranker, _get_default_reranker(), is_cross_encoder_available(), _load_cross_encoder(), Any, Protocol (+32 more)

### Community 20 - "build_default_registry"
Cohesion: 0.02
Nodes (140): _capability(), _catalogue_item(), get_strategy(), get_strategy_certification(), get_strategy_readiness(), list_strategies(), Any, get (+132 more)

### Community 21 - "test_sandbox_runtime.py"
Cohesion: 0.10
Nodes (23): SandboxExecutor, FilesystemMode, FilesystemPolicy, NetworkMode, NetworkPolicy, Any, SandboxRuntimeProfile, SandboxType (+15 more)

### Community 22 - "RuntimeFlags"
Cohesion: 0.10
Nodes (23): _bool_env(), _env_bool(), _env_set(), Runtime feature flags loaded from environment variables. All new orchestration…, RuntimeFlags, Any, datetime, RolloutController (+15 more)

### Community 23 - "TenantContext"
Cohesion: 0.01
Nodes (376): lab_run(), LabRunRequest, list_lab_tools(), BaseModel, get, Request, Agent Lab API — unified playground, simulation, and model comparison., Execute a goal in lab mode (simulation by default). (+368 more)

### Community 24 - "CredentialVault"
Cohesion: 0.02
Nodes (120): connector_secret_ref(), _connector_secret_ref_parts(), CredentialVault, _derive_fernet_key(), is_connector_secret_ref(), Any, Credential vault — AES-256-GCM encryption via Fernet. All LLM API keys and MCP…, Store a connector secret in either a tenant-aware store or mapping fallback. (+112 more)

### Community 25 - "RiskLevel"
Cohesion: 0.06
Nodes (80): GoalClassifier, Two-tier goal classifier., GoalClassifier, _phrase_in(), Any, GoalProperties, GoalClassifier — two-tier classification of incoming goals. Tier 1: Fast…, Word-boundary-safe phrase membership test. Single-word phrases check the pre-… (+72 more)

### Community 26 - "api/knowledge.py"
Cohesion: 0.04
Nodes (132): _cache_stats(), clear_cache(), CollectionIngestRequest, ConfluenceIngestRequest, create_collection(), CreateCollectionRequest, delete_collection(), delete_document() (+124 more)

### Community 27 - "RetrievalResult"
Cohesion: 0.03
Nodes (118): _combine_domain_constraints(), _domain_allowed(), GovernedWebSearchCapability, _html_to_text(), Any, AsyncBaseTransport, Protocol, RetrievalResult (+110 more)

### Community 28 - "test_dispatcher.py"
Cohesion: 0.03
Nodes (93): _dispatch_scheduled_via_dispatcher(), Route a scheduled fire through the TriggerDispatcher for governance parity. The…, Per-tenant bulkhead: limits concurrent in-flight trigger goals., Redis-backed per-tenant concurrency limiter., Return True if the slot was acquired (trigger can proceed)., Release a bulkhead slot., TriggerBulkhead, CircuitBreakerRegistry (+85 more)

### Community 29 - "test_colbert_runtime.py"
Cohesion: 0.03
Nodes (74): ColBERTCompatibilityReranker, ColBERTLateInteractionReranker, ColBERTRAGRuntimeAdapter, _cosine(), _get_encoder(), _load_colbert_model(), maxsim_score(), Protocol (+66 more)

### Community 30 - "test_helpers.py"
Cohesion: 0.03
Nodes (95): _build_verifier_summary(), _extract_scope_value(), _extract_tool_name(), _first_json_object(), _is_high_risk_step(), _is_ungrounded_status(), _parse_json(), _parse_verifier_response() (+87 more)

### Community 31 - "org/router.py"
Cohesion: 0.03
Nodes (177): get_dept_memory(), Return the process-local DepartmentMemory singleton., get_strategic_advisor(), get_twin(), Return the process-local digital twin instance., approve_task(), _attachments_dir(), _BatchMissionCreate (+169 more)

### Community 32 - "test_truly_live_everything.py"
Cohesion: 0.05
Nodes (65): Candidate, Any, Speculative RAG: parallel candidates + retrieval verification., Generate N candidates, verify each, return best-supported., SpeculativeRAGPattern, TestSpeculativeRAGPattern, Speculative RAG: generate N candidates, verify with retrieval, pick best., Speculative RAG: returns best candidate even if none fully supported. (+57 more)

### Community 33 - "strategy_contracts.py"
Cohesion: 0.09
Nodes (66): ArtifactKind, ArtifactReference, CertificationEvidence, CertificationKind, CertificationStatus, CheckpointMigration, EvidenceKind, EvidenceReference (+58 more)

### Community 34 - "test_batch3_servers.py"
Cohesion: 0.05
Nodes (158): make_resp(), mk_client(), Any, asyncio, Unit tests for batch-3 MCP servers (servers 1-25 of this batch). Uses the same…, test_braintree_create_customer(), test_braintree_create_subscription(), test_braintree_create_transaction() (+150 more)

### Community 35 - "_post"
Cohesion: 0.04
Nodes (157): add_golden_task(), add_review_v2(), AddGoldenTaskRequest, AddReviewRequest, apply_experiment(), apply_suggestion(), browse_marketplace(), BundleDeployRequest (+149 more)

### Community 36 - "CostController"
Cohesion: 0.02
Nodes (116): BudgetConfig, CostController, _parse_float(), Any, Cost controls — per-goal and per-tenant daily budget enforcement. Before every…, Atomically check budget and record cost. Returns True if within budget., True if the tenant has any daily budget left for a new goal. Used as a goal-…, Return the current-day spend for the tenant (resets at UTC midnight). (+108 more)

### Community 37 - "test_extra_coverage_servers.py"
Cohesion: 0.05
Nodes (148): make_resp(), mk_client(), Any, asyncio, Extra coverage tests to push remaining MCP servers above 80%. This file adds…, test_amplitude_user_profile(), test_calendar_check_freebusy(), test_calendar_update_event() (+140 more)

### Community 38 - "observability/metrics.py"
Cohesion: 0.08
Nodes (54): _metric_label_bucket(), _non_negative(), _normalize_exact_label(), _normalize_model_label(), _normalize_priority_label(), _normalize_status_label(), Prometheus metrics exposition. Defines the platform's core metrics (registered…, Return (body, content_type) for the metrics endpoint. (+46 more)

### Community 39 - "TeamFormationEngine"
Cohesion: 0.07
Nodes (33): _default_hours(), _estimate_duration(), _estimate_success_probability(), _priority_for_overlap(), Any, TeamFormationEngine — assembles an optimal team manifest from a mission goal.…, Assembles a TeamManifest from a mission goal., Convenience entry-point: form a team directly from a goal string. (+25 more)

### Community 40 - "test_remaining_servers_dispatch.py"
Cohesion: 0.04
Nodes (145): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for remaining MCP servers. Covers: amplitude, mixpanel,…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_amplitude_export_events(), test_amplitude_get_active_users() (+137 more)

### Community 41 - "test_executor_playwright_mock.py"
Cohesion: 0.05
Nodes (144): requires_playwright, make_executor_with_session_manager(), make_mock_page(), make_mock_session(), make_playwright_cm(), make_standalone_executor(), asyncio, Cover app/rpa/executor.py Playwright interaction paths via mocked Playwright.… (+136 more)

### Community 42 - "test_evals_comprehensive.py"
Cohesion: 0.05
Nodes (61): ModelScorer, ModelScorer — scores model efficiency: cost and latency., Score cost efficiency: how much below budget the goal executed., Score latency: faster execution = higher score., Any, Any, ScorecardResult, ImprovementThresholds (+53 more)

### Community 43 - "TriggerConsumerSupervisor"
Cohesion: 0.04
Nodes (40): ChainTriggerConsumer, Goal chain trigger consumer — subscribes to Redis goal lifecycle events., Listens on Redis pub/sub for goal lifecycle events and fires chain triggers., Subscribe to all goal lifecycle channels and process messages., HITLTriggerConsumer, Any, HITL (Human-in-the-Loop) trigger consumer., Subscribe to HITL approval/rejection events and dispatch matching triggers. (+32 more)

### Community 44 - "airtable_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Airtable MCP server — database records management across bases and tables.…

### Community 45 - "test_agents_extra4.py"
Cohesion: 0.03
Nodes (138): _create_agent(), _make_app(), _make_redis_mock(), Any, FastAPI, TestClient, Extra tests for /agents API — push from 59% to 85%+ coverage. Targets uncovered…, Lines 1171-1175: key_id read from request body. (+130 more)

### Community 46 - "test_governance_extra3.py"
Cohesion: 0.03
Nodes (142): _headers(), _make_app(), _make_gov_db_mock(), Any, AuditLog, FastAPI, Extra governance tests — pushes coverage from 65% to 85%+. Targets missing…, Lines 1208-1241: rollback policy to target version. (+134 more)

### Community 47 - "registry_wiring.py"
Cohesion: 0.02
Nodes (119): call_tool(), _headers(), Any, CircleCI MCP server — pipelines, workflows, jobs, and artifacts via API v2.…, call_tool(), Any, Databox MCP server — business analytics dashboards and KPI tracking.…, call_tool() (+111 more)

### Community 48 - "test_batch7_servers.py"
Cohesion: 0.06
Nodes (79): call_tool(), _get_token(), Any, AsyncClient, Buildium MCP server — property management, leases, tenants, and financials.…, call_tool(), Any, call_tool() (+71 more)

### Community 49 - "ContentDeduplicator"
Cohesion: 0.14
Nodes (9): ContentDeduplicator, DeduplicationResult, QualityChecker — validates chunks before ingestion., Session-scoped chunk deduplicator using SHA-256 content hashes. Eliminates…, Return the SHA-256 hex digest of the normalised chunk content., Filter *chunks* to only those whose hash has not been seen before., Return True if this exact content has already been processed., Tests for ContentDeduplicator in quality_checks. (+1 more)

### Community 50 - "test_remaining_connectors.py"
Cohesion: 0.02
Nodes (110): _base(), call_tool(), _headers(), Any, BambooHR MCP server — HR employee data, time-off, and org structure.…, call_tool(), _headers(), Any (+102 more)

### Community 51 - "ModelOrchestrator"
Cohesion: 0.04
Nodes (55): ModelOrchestrator, ModelOrchestratorAdapter, ModelRoleAssignment, MultimodalModelAssignment, Any, Pick a vision/audio-aware extractor+reasoner pair for a classified content…, Map a model name to its provider (defaults to ``openai`` for unknown models)., Record the outcome of a provider call so the circuit breaker can trip/recover.… (+47 more)

### Community 52 - "Complexity"
Cohesion: 0.04
Nodes (106): _phrase_in(), Any, GoalProperties, GoalClassifier — two-tier goal classification (doc-4 exact implementation).…, Word-boundary-safe phrase membership test., Tier 1: keyword-based, always < 1ms., Tier 2: LLM-assisted for MEDIUM complexity + confidence <= 0.85., PatternAssembler (+98 more)

### Community 53 - "test_batch5_servers.py"
Cohesion: 0.06
Nodes (111): http_err(), make_resp(), mk_client(), asyncio, Unit tests for the batch-5 MCP server integrations (servers 1–25). Covers:…, test_bitly_get_click_metrics(), test_bitly_no_token(), test_bitly_shorten_url() (+103 more)

### Community 54 - "test_chunkers.py"
Cohesion: 0.04
Nodes (64): ASTChunker, Chunk, Chunk, ChunkerBase, ABC, HeadingChunker, Chunk, get_chunker_for_strategy() (+56 more)

### Community 55 - "MCPServerConfig"
Cohesion: 0.04
Nodes (124): _extract_credentials_from_server(), MCP HTTP client — discovers and calls tools on registered MCP servers. Follows…, # IMPORTANT: cfg.server_id may be a UUID (the key under which the user's, Extract credentials dict from an MCPServerConfig for passing to builtin…, ToolDefinition, MCPRegistry, MCPServerConfig, BaseModel (+116 more)

### Community 56 - "test_all_agent_patterns_comprehensive.py"
Cohesion: 0.11
Nodes (8): DebatePattern, PlanExecutePattern, DebatePattern has correct pattern_id., test_debate_pattern_registered(), Comprehensive tests for all agent patterns (30+ tests). Tests every pattern:…, TestAgentPatternBase, TestDebatePattern, TestPlanExecutePattern

### Community 57 - "test_multimodal_e2e.py"
Cohesion: 0.18
Nodes (14): _fake_whisper(), Any, e2e_full: multimodal ingestion (image + audio) processed end-to-end. Proves the…, An image submitted through the API is captioned and stored as a completed job., A WAV submitted through the API is transcribed via the real AudioParser., A real, tiny (4x4 red) PNG — generated, not an opaque binary fixture., A real, short (0.2s) silent mono WAV via the stdlib ``wave`` module., Pin a deterministic vision-capable FakeProvider as the app-wide LLM.… (+6 more)

### Community 58 - "test_extra_coverage_servers2.py"
Cohesion: 0.06
Nodes (115): make_resp(), mk_client(), Any, asyncio, Second batch of extra coverage tests to push remaining servers above 80%.…, test_affinity_create_list_entry(), test_affinity_list_list_entries(), test_azure_create_pull_request() (+107 more)

### Community 59 - "test_all_connectors_e2e.py"
Cohesion: 0.10
Nodes (27): _build_minimal_args(), _make_mock_client(), _make_mock_response(), Any, asyncio, parametrize, E2E tests for ALL MCP connector servers. Tests (parametrized over every server…, Build the smallest set of arguments satisfying a tool's required parameters. (+19 more)

### Community 60 - "get_connector"
Cohesion: 0.03
Nodes (78): get_connector(), list_registered(), load_all_connectors(), BaseConnector, Import all connector modules to trigger @register decorators. Call this once at…, Return the connector class for source_type. Args: source_type: e.g. "s3",…, List all registered source types., ArXivConnector (+70 more)

### Community 61 - "test_phases_4_5.py"
Cohesion: 0.06
Nodes (49): GeofenceRegion, GeofenceTriggerEvaluator, haversine_meters(), LatLng, point_in_polygon(), Any, Geofence trigger — detects when a device enters or exits a polygon., Ray-casting algorithm for point-in-polygon test. (+41 more)

### Community 62 - "PatternState"
Cohesion: 0.07
Nodes (27): AgentPattern, PatternState, ABC, Any, Base classes for agent pattern adapters., ConsensusPattern, Consensus verification pattern adapter., Debate multi-agent pattern adapter. (+19 more)

### Community 63 - "SelfOptimizer"
Cohesion: 0.07
Nodes (64): OptimizationSuggestion, Any, EvalScorecard, Generate RPA-specific suggestions based on tool failure pattern. Covers four…, Return list of applied configuration changes for this tenant., Persist a suggestion to self_optimization_suggestions table. Non-fatal — DB…, Analyzes failed/low-scoring eval runs and generates improvement suggestions., Analyze a completed eval and produce optimization suggestions. (+56 more)

### Community 64 - "AnthropicProvider"
Cohesion: 0.05
Nodes (60): AnthropicProvider, EmbedRequest, Stream completion tokens one by one via the Anthropic streaming API., Anthropic Claude provider. Args: api_key: Anthropic API key. Reads from env…, Tests for Phase 2 performance optimizations., 2.2: AnthropicProvider must attach cache_control to system prompt., system prompt sent to Anthropic must be a list with cache_control., TestAnthropicCacheControl (+52 more)

### Community 65 - "StepTypeRegistry"
Cohesion: 0.06
Nodes (38): Workflow Automation Engine — predefined multi-step workflow execution. This…, KeyError, Singleton registry of all available workflow step types., Register a step type. Safe to call multiple times (idempotent)., Get a step node class by type name., List all registered step types (drives Visual Builder palette)., Test helper — reset registry to empty state., StepTypeMeta (+30 more)

### Community 66 - "test_retrieval_gateway.py"
Cohesion: 0.04
Nodes (87): AdaptiveDecision, Bounded capability-aware Adaptive RAG selection., Make exactly one non-recursive decision from certified capabilities., select_adaptive_strategy(), Provider and model selected for one tenant-scoped execution., Dependencies scoped to one authenticated retrieval execution., ResolvedLLM, RetrievalExecutionContext (+79 more)

### Community 67 - "test_google_storage_payment_connectors.py"
Cohesion: 0.02
Nodes (84): call_tool(), _headers(), Any, Box MCP server — file management via Box Content API. Environment variables:…, call_tool(), _headers(), Any, Dropbox MCP server — file and folder management via Dropbox API v2. Environment… (+76 more)

### Community 68 - "test_database_analytics_connectors.py"
Cohesion: 0.03
Nodes (78): _auth(), call_tool(), Any, Amplitude MCP server — query events, cohorts, and user profiles. Environment:…, call_tool(), _client(), Any, AsyncClient (+70 more)

### Community 69 - "test_batch4_servers.py"
Cohesion: 0.06
Nodes (106): make_resp(), mk_client(), Any, asyncio, parametrize, Unit tests for batch-4 MCP servers (servers 1-25). Covers: Etsy, eBay, Ecwid,…, test_buffer_get_profile_analytics(), test_buffer_list_profiles() (+98 more)

### Community 70 - "asyncio"
Cohesion: 0.05
Nodes (33): _make_cron_spec(), asyncio, Extra coverage for app/triggers/store.py. Targets uncovered lines: 69-74,…, Lines 121-123: exception + strict=True → re-raised., Lines 117-120: exception + strict=False → logs, no raise., Lines 136-138: exception + strict=True → re-raised., Lines 133-135: exception + strict=False → no raise., Lines 161-176: create() fires DB create task when loop running. (+25 more)

### Community 71 - "test_batch1_servers.py"
Cohesion: 0.06
Nodes (105): make_resp(), mk_client(), Any, asyncio, Unit tests for batch-1 MCP servers (20 new integrations). Exercises every…, Return a mock AsyncClient context manager with all HTTP methods set., test_activecampaign_add_to_list(), test_activecampaign_create_contact() (+97 more)

### Community 72 - "ContentType"
Cohesion: 0.04
Nodes (79): ChunkingStrategySelector, Any, ChunkingStrategySelector — selects chunking strategy by content type., Return default strategy for a content type., Return collection-level override if valid, else default for content type., Return True for strategies that need special orchestrator dispatch., Select strategy and chunk text. Returns list of non-empty text chunks. Used by…, Sentence-boundary semantic chunking. (+71 more)

### Community 73 - "parser_registry.py"
Cohesion: 0.02
Nodes (68): AudioTranscriptParser, _AvroBridge, CodeParser, CSVParser, DOCXParser, _ExcelBridge, HTMLParser, JSONParser (+60 more)

### Community 74 - "ColBERTPattern"
Cohesion: 0.02
Nodes (98): AdaptiveRAGPattern, Any, AgenticChunkingPattern, Any, Extract propositions from all chunks. Returns proposition-level chunk dicts., Agentic Chunking: LLM-driven proposition extraction (Dense X Retrieval)., Search persisted propositions and return their parent-window citations., ColBERTPattern (+90 more)

### Community 75 - "_TemplateStore"
Cohesion: 0.04
Nodes (71): create_template(), delete_template(), _extract_parameters(), get_template(), _instantiate_template(), InstantiateRequest, list_templates(), _load_yaml_goal_templates() (+63 more)

### Community 76 - "chunk_by_tokens"
Cohesion: 0.04
Nodes (43): build_parent_windows(), _chunk_by_chars(), chunk_by_tokens(), ParentWindow, Token-aware text chunking using tiktoken. Produces chunks with a guaranteed…, A stable sentence/chunk window independent of embedding dimensions., Build one citation window around each source chunk., Character-based fallback chunker used when tiktoken is unavailable. (+35 more)

### Community 77 - "test_knowledge_extra4.py"
Cohesion: 0.05
Nodes (101): _create_collection(), _make_app(), _make_embed_texts_mock(), _make_embedder(), Any, FastAPI, TestClient, Extra tests for /knowledge API — push from 51% to 85%+ coverage. Targets… (+93 more)

### Community 78 - "test_batch6_servers.py"
Cohesion: 0.10
Nodes (62): call_tool(), _get_token(), Any, AsyncClient, Help Scout MCP server — customer support conversations and mailbox management.…, make_resp(), mk_client(), Any (+54 more)

### Community 79 - "LongTermMemoryStore"
Cohesion: 0.01
Nodes (172): Any, Read unprocessed rows from ``goal_feedback`` and derive improvement actions.…, LongTermMemory, LongTermMemoryStore, Any, Long-term memory — cross-session learnings persisted across agent runs. Stores…, Extract a learning from a completed goal and persist it. Adds to the in-memory…, Async store — persists to DB with embedding if embedder available. Computes a… (+164 more)

### Community 80 - "IngestionOrchestrator"
Cohesion: 0.03
Nodes (90): build_provider_resolver(), Build a resolver mapping an embedding provider NAME to its instance. Gives…, _content_hash(), EmptyIndexedContentError, IngestionOrchestrator, IngestionResult, Any, ValueError (+82 more)

### Community 81 - "test_phase5_api.py"
Cohesion: 0.06
Nodes (60): create_raft_dataset(), evaluate_raft_job(), get_raft_job(), _job_response(), list_strategies(), preview_raft_job(), Any, BaseModel (+52 more)

### Community 82 - "GuardrailChecker"
Cohesion: 0.03
Nodes (78): disclose_context(), DisclosedContext, Any, Field-level minimization and taint preservation across coordination hops., GuardrailChecker, GuardrailResult, _luhn_valid(), _pii_spans() (+70 more)

### Community 83 - "MockMCPClient"
Cohesion: 0.06
Nodes (25): MockMCPClient, Any, Async generator yielding SSE-style events as simulation executes. Uses a…, MCP client that returns pre-configured mock responses. Used in simulation to…, Build a keyword-based execution plan for the simulated goal., Return True if this tool was called during simulation., mock_tools= and mock_responses= are aliases., test_mock_client_accepts_mock_tools_alias() (+17 more)

### Community 84 - "StructuredPlan"
Cohesion: 0.04
Nodes (96): PlanValidationError, ValueError, Structured execution plan — parses LLM output into topologically sortable steps., A single step in a structured execution plan., Raised before execution when a structured plan is unsafe or inconsistent., An ordered set of :class:`StructuredStep` objects with dependency information., Parse an LLM response into a :class:`StructuredPlan`. Accepts two formats: 1.…, Validate identifiers, dependencies, loops, conditions, and acyclicity. (+88 more)

### Community 85 - "test_rbac_comprehensive2.py"
Cohesion: 0.08
Nodes (46): effective_roles(), is_ip_allowed(), Return True if client_ip is allowed by the allowlist. Empty allowlist = no…, Expand ctx.roles with implied roles from hierarchy., FastAPI dependency factory that enforces role requirement. Usage:…, require_role(), _ctx(), Comprehensive tests for rbac.py — role hierarchy, has_role, has_any_role,… (+38 more)

### Community 86 - "QueryPlanner"
Cohesion: 0.07
Nodes (16): QueryPlanner, RAGResult, RAG Query Planner - select optimal retrieval strategy., A single retrieval attempt with its results., Full RAG retrieval result with all legs and synthesis., Plans and executes RAG retrieval with multiple strategies., Auto-select the best strategy for a query., RetrievalLeg (+8 more)

### Community 87 - "test_tenant_service_extra2.py"
Cohesion: 0.06
Nodes (56): asyncio, Extra coverage tests for app/services/tenant_service.py — targeting 85%+…, When not in memory, queries DB., When key is not in _keys, returns minimal record from tenant., When tenant has no api_key_id, returns None., When expires_at is stored as naive ISO string, tz is added before comparison., DB exception during SSO creation is handled gracefully., test_create_api_key_success() (+48 more)

### Community 88 - "SIEMType"
Cohesion: 0.08
Nodes (34): Buffers audit events and drains them to a :class:`SIEMAdapter` in batches.…, Buffer one audit event for forwarding. Non-blocking; never raises. When the…, Send up to ``batch_size`` buffered events. Returns the count sent. Returns 0…, Background loop: drain the buffer every ``flush_interval`` seconds., Launch the background drain task (idempotent)., Signal shutdown, cancel the task, and flush anything left buffered., SIEMForwarder, SIEMType (+26 more)

### Community 89 - "client"
Cohesion: 0.04
Nodes (19): api_key(), client(), collection_id(), _new_client(), Real no-mock integration tests — AgentVerse full platform. Hits the LIVE…, Use a pre-provisioned key to avoid signup rate limits., TestAgents, TestAnalyticsObservability (+11 more)

### Community 90 - "StepDefinition"
Cohesion: 0.03
Nodes (127): ContextResolver, ContextResolverError, ValueError, ContextResolver — resolves {{...}} template expressions in workflow steps.…, Resolves {{...}} expressions against the current WorkflowState., AlertTriggerConfig, AssigneeConfig, CallbackConfig (+119 more)

### Community 91 - "SystemTemplateStore"
Cohesion: 0.04
Nodes (78): fork_template(), ForkRequest, get_template(), list_categories(), list_templates(), preview_run(), Any, BaseModel (+70 more)

### Community 92 - "reasoning_contracts.py"
Cohesion: 0.06
Nodes (61): GraphOfThoughtsRuntime, Any, Bounded Graph-of-Thoughts search with safe checkpoint metadata., LeastToMostRuntime, Any, LLMCompilerRuntime, Any, Validated LLM Compiler adapter backed by the canonical DAG executor. (+53 more)

### Community 93 - "test_multimodal_router.py"
Cohesion: 0.03
Nodes (89): Cheapest configured vision/OCR model, else the env-configured one., resolve_vision_model(), AudioParser, AudioParseResult, AudioSegment, _fmt(), Any, AudioParser — transcribes audio using OpenAI Whisper API with timestamp… (+81 more)

### Community 94 - "ExecutionTier"
Cohesion: 0.05
Nodes (64): AutoGPTAdapter, Bounded AutoGPT controller with mandatory pre-execution gates., BabyAGIAdapter, CodeActAdapter, ConstitutionalAIAdapter, PlanExecuteStrategyAdapter, Any, Canonical production adapters for the core single-agent strategies. (+56 more)

### Community 95 - "test_gateway_entrypoints.py"
Cohesion: 0.06
Nodes (70): RAGCitation, Evidence cited by a grounded RAG answer., _RAGCostGuard, _BudgetContext, CitationVerification, MinimalCitationVerifier, Any, Protocol (+62 more)

### Community 96 - "test_tenants_extra4.py"
Cohesion: 0.06
Nodes (55): _generate_raw_key(), Generate a cryptographically random API key with a recognisable prefix., _make_app(), _make_svc(), Any, FastAPI, Extra tests for /tenants API — push from 60% to 85%+ coverage. Targets…, Line 31: _generate_raw_key returns prefixed random key. (+47 more)

### Community 97 - "test_extra_coverage_servers5.py"
Cohesion: 0.05
Nodes (78): call_tool(), _cw_client(), get_tools(), _logs_client(), Any, AWS CloudWatch MCP server — query metrics, alarms, and logs via boto3.…, call_tool(), _client() (+70 more)

### Community 98 - "test_comms_servers_dispatch.py"
Cohesion: 0.08
Nodes (92): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for communications MCP servers. Exercises every…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_brevo_create_contact(), test_brevo_delete_contact() (+84 more)

### Community 99 - "test_governor.py"
Cohesion: 0.06
Nodes (83): SpawnVerdict, _FakeSession, _make_governor(), _make_tenant_ctx(), _noop_ctx, asyncio, Tests for Governor — central authority for the civilization., When only min_viable_roster members remain, none should be retired. (+75 more)

### Community 100 - "_record_goal_duration_metric"
Cohesion: 0.10
Nodes (17): _monotonic(), _record_goal_duration_metric(), test_monotonic_returns_float(), test_record_goal_duration_metric_does_not_raise(), TestMonotonic, TestRecordGoalDurationMetric, test_monotonic_is_monotonically_increasing(), test_monotonic_returns_positive_float() (+9 more)

### Community 101 - "test_catalog_comprehensive.py"
Cohesion: 0.03
Nodes (53): A2ATask, A2ATaskResult, AgentCard, BaseModel, Agent-to-Agent (A2A) protocol types. The AgentCard is served at /.well-…, Publicly discoverable capability declaration for this agent., A task sent from one agent to another., Result returned from an A2A task execution. (+45 more)

### Community 102 - "role_taxonomy.py"
Cohesion: 0.18
Nodes (12): AgentStatus, FullRoleDefinition, get_role(), get_roles_for_dept(), StrEnum, PART 4 + PART 6 — Complete role taxonomy (22 departments) + OrgAgent lifecycle.…, Spec PART 6 agent status state machine: IDLE → PLANNING → PLAN_READY →…, Returns True if the transition is valid per the spec state machine. (+4 more)

### Community 103 - "test_agent_identity_comprehensive.py"
Cohesion: 0.08
Nodes (48): AgentIdentityService, generate_agent_keypair(), issue_agent_token(), Agent Identity Service — cryptographic service-account credentials for agents.…, Verify an agent JWT. Raises JWTError or ValueError on failure. Per Amendment…, Service for managing agent cryptographic credentials (service-account keys +…, Revoke a credential by key_id. Returns True if the credential was found and…, Exchange a service key for a short-lived RS256 JWT. Returns the signed JWT… (+40 more)

### Community 104 - "test_goal_tree_comprehensive.py"
Cohesion: 0.07
Nodes (51): decompose_goal(), DecompositionResult, execute_goal_tree(), execute_sub_goal(), Any, Semaphore, Goal-tree decomposition and parallel sub-agent execution. The GoalTreeExecutor:…, Decompose goal → build dependency DAG → execute with parallelism. Returns list… (+43 more)

### Community 105 - "ToolReliabilityStore"
Cohesion: 0.11
Nodes (16): Any, Per-tool reliability tracking — success rates and latency across all agent…, Get tools with poor reliability for agent planning awareness., Track per-tool success/failure rates and latency in PostgreSQL. Table:…, Record a tool call outcome., Get reliability stats for a specific tool., ToolReliabilityStore, ToolReliabilityStore.record stores failure; get_reliability reflects it. (+8 more)

### Community 106 - "test_crm_servers_dispatch.py"
Cohesion: 0.08
Nodes (87): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for CRM MCP servers. Exercises every call_tool() branch by…, Return a mock httpx.Response with the given status and JSON payload., Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_affinity_create_list_entry() (+79 more)

### Community 107 - "api/governance.py"
Cohesion: 0.06
Nodes (92): approve_request(), ApproveRejectRequest, _audit(), batch_approve(), BatchApproveRequest, _budget_config(), clear_emergency_stop(), _cost() (+84 more)

### Community 108 - "test_extra_coverage_servers3.py"
Cohesion: 0.07
Nodes (84): make_http_error_resp(), make_resp(), mk_client(), Any, asyncio, Final targeted tests to push remaining servers above 80%. These tests…, Return a mock response that raises HTTPStatusError on raise_for_status()., test_affinity_http_error() (+76 more)

### Community 109 - "Constitution"
Cohesion: 0.05
Nodes (77): evaluate_breach(), evaluate_spawn(), Constitution — pure policy evaluator. Zero I/O. Fully unit-testable., Evaluate whether a spawn request satisfies the Constitution. Returns…, Check if the civilization is in a Constitutional breach state., Governor — central authority for the civilization. The ONLY component that may…, Check Constitution breach. Called by Celery beat every 30s., BreachContext (+69 more)

### Community 110 - "_CollabPubSub"
Cohesion: 0.04
Nodes (43): _CollabPubSub, Increment cross-replica participant counter in Redis., Decrement cross-replica participant counter in Redis., Return participant count, preferring Redis for cross-replica accuracy., Subscribe to ``collab:*`` and forward messages to local WS connections., Redis pub/sub fanout for cross-replica WebSocket broadcast. When a message…, Lazily start the subscriber task on first WebSocket connection., Publish a message to all replicas for the given session. No-op (silently) when… (+35 more)

### Community 111 - "test_modular_rag.py"
Cohesion: 0.05
Nodes (80): ModularRAGRuntimeAdapter, Any, Execute a safe default or validated per-agent Modular RAG pipeline., Raised when a requested strategy ID is not part of the public contract., Resolve a canonical or historical public strategy ID., resolve_rag_strategy(), UnknownRAGStrategyError, _bounded_integer() (+72 more)

### Community 112 - "create_app"
Cohesion: 0.01
Nodes (235): Cheapest configured embedding model, else the env-configured one., resolve_embed_model(), PostgresSealedBidInbox, async_sessionmaker, AsyncSession, Opaque durable intake using the canonical RLS-protected ``agent_bids`` table., DatabaseSessionAuthorizer, InMemorySessionAuthorizer (+227 more)

### Community 113 - "test_rag_patterns_real_openai.py"
Cohesion: 0.05
Nodes (44): SelfRAGResult, make_provider(), make_retrieve_fn(), make_store_with_docs(), make_tenant(), Comprehensive real-OpenAI E2E tests for ALL RAG patterns in AgentVerse. Tests…, Return a real OpenAI provider using gpt-4o-mini., Return a test TenantContext with enterprise plan. (+36 more)

### Community 114 - "PeerReviewPattern"
Cohesion: 0.11
Nodes (13): PeerReviewPattern, PeerReviewResult, Any, Review `output` against `goal`. Returns PeerReviewResult., Parse from possibly-non-JSON response., Peer Review: independent LLM reviewer evaluates output quality., critique_categories(), Map free-form critique to bounded categories without copying its text. (+5 more)

### Community 115 - "FakeProvider"
Cohesion: 0.01
Nodes (453): AgentGraph, Execute the agent graph and return the final AgentState. ``goal_id`` — when…, Load latest checkpoint for goal resume., LangGraph-based agent loop with RAG retrieval, 12-step pipeline, and…, Return an agent runner for this envelope., AuditEvent, AuditLog, Any (+445 more)

### Community 116 - "WorkflowExecutor"
Cohesion: 0.07
Nodes (59): _arguments_for_step(), Any, Execute a single workflow step, falling back LLM → stub., Compatibility forwarding method over the canonical bounded DAG executor., Parallel workflow executor using asyncio.gather() for independent steps. The…, Execute a workflow plan with parallel waves. Returns a result dict with keys:…, _summarize_inputs(), WorkflowExecutor (+51 more)

### Community 117 - "test_routers.py"
Cohesion: 0.07
Nodes (44): OptimizationOutcome, BaseModel, model_validator, Immutable routing decisions and measured outcomes., RoutingCandidate, RoutingDecision, RoutingSignalSet, InMemoryDecisionStore (+36 more)

### Community 118 - "test_program09_coordination_api.py"
Cohesion: 0.08
Nodes (33): InMemoryAuctionRepository, InMemorySealedBidInbox, PostgresAuctionRepository, Any, BaseModel, Auction read models and opaque sealed-bid intake., _receipt(), SealedBidReceipt (+25 more)

### Community 119 - "api/civilization.py"
Cohesion: 0.04
Nodes (79): add_civilization_member(), AddMemberRequest, _build_orchestrator(), civilization_ws(), ConstitutionUpdateRequest, control_civilization(), ControlRequest, create_civilization() (+71 more)

### Community 120 - "HandoffRecord"
Cohesion: 0.06
Nodes (44): Durable same-civilization handoff protocol., HandoffRecord, HandoffState, HandoffTransition, BaseModel, model_validator, StrEnum, Typed handoff commands and immutable state. (+36 more)

### Community 121 - "rpa/test_artifacts_comprehensive.py"
Cohesion: 0.03
Nodes (89): ArtifactStoreProtocol, get_artifact_store(), MinIOArtifactStore, Any, Path, Protocol, Create an aioboto3 S3 client configured for MinIO., Common interface for all artifact backends. (+81 more)

### Community 122 - "FakeRedis"
Cohesion: 0.12
Nodes (12): FakeRedis, Tests for TenantScopedStore — Redis key prefixing ensures tenant isolation., Minimal in-memory Redis fake for unit tests., test_delete_only_removes_own_keys(), test_exists_returns_correct_count(), test_get_returns_none_for_missing_key(), test_incr_starts_at_one(), test_keys_are_tenant_prefixed() (+4 more)

### Community 123 - "test_devtools_servers_dispatch.py"
Cohesion: 0.09
Nodes (82): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for dev-tools MCP servers. Exercises every call_tool()…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_azure_create_work_item(), test_azure_list_pipelines() (+74 more)

### Community 124 - "test_productivity_servers_dispatch.py"
Cohesion: 0.08
Nodes (82): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for productivity/project-management MCP servers. Targets:…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_asana_add_comment(), test_asana_create_project() (+74 more)

### Community 125 - "KnowledgeStore"
Cohesion: 0.03
Nodes (116): AsyncSession, Set app.tenant_id RLS variable for a SQLAlchemy AsyncSession. Must be called…, sqlalchemy_rls_context(), A concrete adapter and the capabilities it requires to execute., Authorize collection access against tenant-filtered persisted state., RetrievalStrategyCapability, SQLCollectionAuthorizer, _chunk_table() (+108 more)

### Community 126 - "StrategyRunner"
Cohesion: 0.06
Nodes (69): Admission, Public method to vote on externally-collected responses., DistributedStrategyLoop, Any, Makes a DISTRIBUTED-tier ``GoalRuntimeProfile`` executable via…, AgentGraph-shaped adapter that dispatches a goal through ``StrategyRunner``.…, Bridges free-form goal text/context to the durable StrategyExecutionRequest…, Everything the wired Executor needs to actually run a strategy for one goal. (+61 more)

### Community 127 - "OrgService"
Cohesion: 0.05
Nodes (49): Org-level analytics service — PART 22/23 of spec. Provides: -…, app/org — AI Organization Operating System domain., Organization, OrgBlueprint, OrgCapability, OrgDecision, OrgDepartment, OrgEvent (+41 more)

### Community 128 - "civilization/test_metrics.py"
Cohesion: 0.05
Nodes (76): civ_agents_active(), civ_budget_spent_usd(), civ_debates_total(), civ_learnings_promoted_total(), civ_learnings_rejected_total(), civ_spawn_denied_total(), civ_spawns_total(), civ_tick_skipped_total() (+68 more)

### Community 129 - "test_batch2_servers.py"
Cohesion: 0.07
Nodes (89): call_tool(), Any, Vero MCP server — email & push: user identification, event tracking, and…, make_resp(), mk_client(), Any, asyncio, Batch 2 MCP server unit tests — all 25 new servers mocked via httpx. Tests use… (+81 more)

### Community 130 - "CodeInterpreter"
Cohesion: 0.06
Nodes (51): CodeInterpreter, CodeResult, get_interpreter(), Any, Sandboxed code execution via Docker. Execution constraints: - No network access…, Return True if Docker is available on this host., Execute code in a sandboxed Docker container. Falls back to restricted…, Execute code in Docker container with strict isolation. Writes code to a host… (+43 more)

### Community 131 - "triggers/test_security.py"
Cohesion: 0.06
Nodes (45): check_permission(), Exception, RBAC permission matrix for trigger operations. 5 roles x 8 operations = 40…, Return True if the role can perform the operation, else raise…, TriggerPermissionDenied, Verify incoming webhook payloads using HMAC-SHA256., Return True if the signature is valid, False otherwise., Synchronous verify for GitHub X-Hub-Signature-256. (+37 more)

### Community 132 - "coordination/store.py"
Cohesion: 0.05
Nodes (44): AuthorizationContext, CoordinationCommandStore, CoordinationService, Any, BaseModel, Protocol, Authorized command boundary for canonical coordination state., Validate authority and immutable admission inputs before persistence. (+36 more)

### Community 133 - "test_dynamic_orchestration_e2e.py"
Cohesion: 0.02
Nodes (143): DataClassifier, DataClassifier — regex-based classification. No data enters prompts until…, Redactor — removes sensitive entities from text., Redactor, DataClass, DataClassification, Data classification schema., EscalationDecision (+135 more)

### Community 134 - "test_celery_maintenance_real.py"
Cohesion: 0.04
Nodes (54): _datetime_to_naive_iso(), _db_schedule_payload(), _record_schedule_fire_metric(), _schedule_key(), _scheduled_goal_kwargs(), Tests for scaling/tasks.py utility functions and maintenance task…, run_goal with dry_run=True short-circuits after status update., D-18: the scheduled retention task must delete expired memory rows (each has… (+46 more)

### Community 135 - "CollaborationStore"
Cohesion: 0.06
Nodes (73): CollaborationStore, _now_iso(), Any, Exception, Raised when an optimistic concurrency check fails., PostgreSQL-backed collaboration store with in-memory fallback semantics in…, _session_to_dict(), VersionConflictError (+65 more)

### Community 136 - "_ingest_repo_background"
Cohesion: 0.09
Nodes (48): _ingest_repo_background(), Clone and atomically ingest under a disk/file quota and durable lease.…, _assert_no_symlink_components(), _is_secret_or_disallowed(), Path, ValueError, Fail-closed repository URL, pattern, and cloned-file validation., Compatibility wrapper returning the sanitized pinned-source URL. (+40 more)

### Community 137 - "test_runtime_scorecard.py"
Cohesion: 0.05
Nodes (68): Any, GoalScorer, GoalScorer — scores task completion and iteration efficiency., Any, RetrievalResult, RAGScorer, RAGScorer — scores retrieval quality from RetrievalResult., AggregateMetrics (+60 more)

### Community 138 - "test_semantic_cache_world_class.py"
Cohesion: 0.05
Nodes (46): _CacheHit, _compress(), _cosine(), _decompress(), _find_best_match(), _L1Entry, _LRUCache, _pack_embedding() (+38 more)

### Community 139 - "RPAArtifactStore"
Cohesion: 0.05
Nodes (61): Artifact storage for RPA outputs — filesystem fallback and MinIO/S3 backend., Store artifacts under /tmp for CI-safe local RPA workflows., RPAArtifactStore, RPA automation primitives and CI-safe local runner., execute_rpa_tool(), _failure(), _format_items(), LocalRPARunner (+53 more)

### Community 140 - "BrowserSessionManager"
Cohesion: 0.03
Nodes (106): BrowserSession, BrowserSessionManager, Any, Browser session manager — keeps Playwright sessions alive across multiple RPA…, Close a specific session., Close sessions idle longer than max_idle_seconds., List all active sessions, optionally filtered by tenant., Persist session metadata to Redis for visibility across restarts. (+98 more)

### Community 141 - "ReflexionStore"
Cohesion: 0.03
Nodes (78): OrchestrationPersistence, Any, OrchestrationPersistence — persists all orchestration state to Postgres. Called…, Store reflexion lesson in memory + Postgres., Persist a regression case candidate for the eval dataset., Persist tool trust outcome to in-memory store + Postgres., Load tool trust history from Postgres into in-memory store on startup., Persist RuntimeScorecard to eval_scorecards table. (+70 more)

### Community 142 - "connectors.py"
Cohesion: 0.07
Nodes (77): _auth_config_requires_secret_storage(), _build_auth_headers(), _cleanup_oauth_states(), complete_oauth_popup(), _connector_secret_store(), _default_redirect_uri(), discover_connector_tools(), _get_builtin_config_for_name() (+69 more)

### Community 143 - "goals.py"
Cohesion: 0.07
Nodes (82): abort_persistence(), approve_goal(), ApproveRequest, BatchGoalRequest, _build_multimodal_goal_text(), cancel_goal(), explain_goal(), _extract_multimodal_context() (+74 more)

### Community 144 - "test_policies_extra.py"
Cohesion: 0.06
Nodes (44): PolicyVersionManager, Any, Publish a policy change event so other replicas can reload. Channel:…, Manages the version lifecycle for policies stored in policy_versions. Every…, Persist a brand-new policy at version 1., Atomically deactivate the current version and create the next one., Create a new version that is a copy of a historical snapshot., Return all version snapshots for a policy, oldest first. (+36 more)

### Community 145 - "WorkflowTestRunner"
Cohesion: 0.10
Nodes (40): MockToolAdapter, Any, WorkflowTestRunner — sandbox execution for testing workflows without side…, Execute all steps with mocked outputs., Run a scenario and validate assertions., Run multiple scenarios and collect results., Execute step-by-step and return each step's result. step_overrides: step_id →…, Walk steps in dependency order and apply mock outputs. (+32 more)

### Community 146 - "Message"
Cohesion: 0.03
Nodes (117): Unified async grounding check. Supports both positional (``step_output``,…, Chain-of-thought thinking node: produces reasoning before planning., Reflection node: diagnoses failure and populates verification_feedback., Self-Refine node — improves last step output before verification (doc-1 §3.4).…, Any, Use LLM to decompose goal into independent sub-tasks., Synthesize results from all sub-agents via LLM into a coherent answer., Decompose and execute goal across multiple sub-agents. (+109 more)

### Community 147 - "TestWorkflowExecutorDispatch"
Cohesion: 0.07
Nodes (23): execute_decision_node(), execute_delay_node(), execute_loop_node(), execute_skill_node(), Any, Execute a loop node. Returns list of per-iteration outputs. Node config:…, Execute a delay node. Waits for the specified duration. Node config: seconds:…, Execute a skill node — injects skill instructions into workflow context. Node… (+15 more)

### Community 148 - "api/ingestion.py"
Cohesion: 0.11
Nodes (44): create_source(), CreateSourceRequest, delete_source(), get_catalogue(), get_cost(), _get_pipeline(), get_quota(), get_source() (+36 more)

### Community 149 - "test_enterprise.py"
Cohesion: 0.04
Nodes (49): DeployedTemplate, Marketplace, Any, Template gallery + deploy functionality., Deploy a template as a live agent for the tenant. If *registry* is provided,…, Publish a custom agent template to the marketplace., Deploy multiple templates as a group (a 'bundle')., Save a version snapshot of a template to DB. (+41 more)

### Community 150 - "test_insights_extra.py"
Cohesion: 0.04
Nodes (73): _make_app(), _make_db_factory(), _make_db_factory_from_session(), Any, FastAPI, Extra coverage tests for app/api/insights.py — targeting 85%+ coverage., estimate falls back to defaults when embedder raises exception., Graph correctly builds nodes from step_start events. (+65 more)

### Community 151 - "WorkflowCompiler"
Cohesion: 0.06
Nodes (35): CompiledWorkflow, Any, BaseException, Build the async node function for a step., Collect the step's declared input-bearing fields (still templated). Different…, Parse '30s' / '5m' / '2h' / bare seconds → float seconds (0 = none)., Wrapper around a compiled LangGraph graph., Honour RetryConfig.fail_on / retry_on exception-name filters. (+27 more)

### Community 152 - "AgentState"
Cohesion: 0.01
Nodes (251): check_tool_output_for_injection(), Scan tool output for indirect prompt injection attempts. Returns a warning…, LangGraph StateGraph-based autonomous agent. Graph topology: START → initialize…, Verify step — should_skip_cache guard applied before LLM call. The LLM response…, Return True when the last *window* steps are all FAILED. Signals a stuck…, Trigger self-optimization when a goal scores poorly (BUG 5 fix). Called as a…, GraphState, RuntimeError (+243 more)

### Community 153 - "test_security_runtime.py"
Cohesion: 0.05
Nodes (68): _compute_policy_fields(), _PolicyFields, RiskLevel, TypedDict, Compile the shared deterministic policy dimensions., GovernanceBundle, GovernanceConfig, GovernanceProfileSelector (+60 more)

### Community 154 - "test_connectors_extra2.py"
Cohesion: 0.04
Nodes (69): _FakeRedis, _LegacyRegistry, _make_app(), _make_db_factory(), _make_registry(), _MockSession, Any, FastAPI (+61 more)

### Community 155 - "RuntimeSSEEmitter"
Cohesion: 0.06
Nodes (45): Any, RuntimeSSEEmitter — creates structured SSE events for all orchestration…, RuntimeSSEEmitter, SSEEventType, Phase N5-N7: ABTesting wiring + SSE events + action dispatch., BLACKLIST_TOOL_PATTERN must record failed tools in ToolReliabilityStore., Module-level ab_testing_engine must be wired with db_factory in main.py., test_ab_testing_engine_has_record_result_async() (+37 more)

### Community 156 - "ModelRouter"
Cohesion: 0.05
Nodes (59): _apply_env_model_overrides(), get_router_for_tenant(), ModelRouter, ModelRouterConfig, Any, Multi-model router — selects the optimal model for each task type. Strategy: -…, Routes task types to optimal models for a given provider., Return the optimal model name for the given task type. task_type: "planning" |… (+51 more)

### Community 157 - "SIEMConfig"
Cohesion: 0.08
Nodes (26): CEFAdapter, DatadogAdapter, ElasticsearchAdapter, LEEFAdapter, Any, Elasticsearch Bulk API adapter., Datadog Logs API adapter., Common Event Format (ArcSight) adapter — syslog UDP or TCP. (+18 more)

### Community 158 - "Classification"
Cohesion: 0.07
Nodes (42): Classification, StrEnum, Append-only transcript compaction preserving evidence and dissent., TranscriptCompactor, Canonical append-only coordination transcript., BaseModel, model_validator, Safe, ordered transcript contracts. (+34 more)

### Community 159 - "QualityGateSystem"
Cohesion: 0.07
Nodes (37): GateOutcome, GateResult, Any, StrEnum, QualityGateSystem, QualityScore, Quality Gate System — SUPPLEMENT K (6-gate system). GATE 1: AGENT_SELF_CHECK —…, Run all configured quality gates and return composite score. (+29 more)

### Community 160 - "cli/main.py"
Cohesion: 0.09
Nodes (46): agents(), _api_key(), approve(), _base_url(), cancel(), create(), dev_server(), eval_goal() (+38 more)

### Community 161 - "chat/router.py"
Cohesion: 0.11
Nodes (70): connect_service(), ConnectServiceRequest, create_artifact(), create_folder(), create_memory(), create_session(), create_template(), CreateArtifactRequest (+62 more)

### Community 162 - "test_bus.py"
Cohesion: 0.06
Nodes (51): CivilizationBus, _NullCtx, Any, datetime, CivilizationBus — Redis pub/sub event bus with PostgreSQL persistence. Topics:…, Fetch persisted messages from DB for replay., Null async context manager to handle redis clients directly., Redis pub/sub bus for civilization events with durable persistence. (+43 more)

### Community 163 - "api/test_governance_comprehensive.py"
Cohesion: 0.08
Nodes (46): _make_app(), _make_app_no_db(), AuditLog, FastAPI, Comprehensive tests for /governance API endpoints — targets 20% → 55%+ coverage., Emergency stop should succeed even without optional services., App variant with db_session_factory explicitly disabled., test_approve_request_not_found() (+38 more)

### Community 164 - "test_enterprise_intelligence_gaps.py"
Cohesion: 0.06
Nodes (46): _make_app(), Any, FastAPI, SimulationRunner, Tests for app/api/enterprise.py endpoints that are not yet covered. Targets the…, When eval_suite_runner is set, get_suite_results returns serialized runs., GET /intelligence/experiments returns experiments from self_optimizer., GET /intelligence/suggestions returns suggestions from self_optimizer. (+38 more)

### Community 165 - "CapabilitySearch"
Cohesion: 0.05
Nodes (64): CapabilitySearch, Any, Semantic and keyword-based tool capability search. Falls back to keyword…, Return the top-*k* tools matching *query*. Parameters ---------- query:…, A tool that matched a capability query., Find tools matching a natural-language capability query. Usage:: search =…, Coerce a mixed list of dicts or ToolDefinition objects to dicts., Return the cosine similarity between two vectors. (+56 more)

### Community 166 - "test_condition.py"
Cohesion: 0.03
Nodes (99): ChannelIngestionGateway, NLIntentClassifier, Any, Channel Ingestion Gateway — routes inbound channel events to the dispatcher., Routes channel events to matching trigger types and dispatches them., Process an inbound channel event and fire matching triggers. Returns list of…, Classify natural-language messages to trigger types using embeddings + LLM…, Return the most likely trigger_type string for the given message. (+91 more)

### Community 167 - "test_session_store_comprehensive.py"
Cohesion: 0.06
Nodes (39): _safe_name(), Any, Return all active sessions for a tenant., Mark a session as closed., Redis-backed RPA session store with 24-hour TTL. Per-session key:…, Create and persist a new active RPA session., Retrieve a session by ID, scoped to the given tenant., RPASessionStore (+31 more)

### Community 168 - "test_tasks_helpers_extra.py"
Cohesion: 0.04
Nodes (50): _schedule_datetime(), test_schedule_datetime_aware_datetime_converts(), test_schedule_datetime_aware_iso_converts_to_naive_utc(), test_schedule_datetime_datetime_object_naive(), test_schedule_datetime_naive_iso(), test_schedule_datetime_none_returns_none(), test_schedule_datetime_parses_aware_iso_string_and_strips_tz(), test_schedule_datetime_parses_iso_string() (+42 more)

### Community 169 - "test_org_advanced_endpoints.py"
Cohesion: 0.06
Nodes (68): anyio_backend(), client(), _fake_dept(), _fake_health(), _fake_mission(), _fake_org(), _fake_team(), _make_app() (+60 more)

### Community 170 - "test_training_export_comprehensive2.py"
Cohesion: 0.06
Nodes (66): _collect_training_examples_db(), _collect_training_examples_memory(), export_training_data(), preview_training_data(), Any, get, Request, StreamingResponse (+58 more)

### Community 171 - "test_all_models.py"
Cohesion: 0.05
Nodes (68): Fail-closed tenant-scoped civilization membership authorization., Agent, AgentPermission, Base, SQLAlchemy ORM models for agents and agent permissions., BlackboardEntry, BusMessage, Civilization (+60 more)

### Community 172 - "EmailTool"
Cohesion: 0.06
Nodes (55): email_send(), EmailTool, IMAPConfig, Any, Email sending and reading tool. Sending: aiosmtplib (async SMTP) Reading:…, Read emails from IMAP inbox. Returns list of message dicts with from, subject,…, Create EmailTool from a vault/secrets config dict. Expected keys: smtp_host,…, Send an email via aiosmtplib using environment-variable SMTP config. For local… (+47 more)

### Community 173 - "test_collab_extra3.py"
Cohesion: 0.05
Nodes (61): FakeCollabStore, _make_app(), Any, FastAPI, Extra collab tests — pushes coverage from 48% to 85%+. Targets missing lines:…, Lines 398-401: presence_join broadcast runs when other WS exists in session., Lines 433-436, 438, 459-462: broadcast to other WS + presence_leave broadcast., Line 214: no API key → 401. (+53 more)

### Community 174 - "test_cloud_servers_dispatch.py"
Cohesion: 0.08
Nodes (68): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for cloud/infra MCP servers. Covers: AWS S3, IAM, Lambda,…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_calendar_create_event(), test_calendar_delete_event() (+60 more)

### Community 175 - "test_finance_servers_dispatch.py"
Cohesion: 0.07
Nodes (82): _base(), call_tool(), _headers(), Any, Freshdesk MCP server — customer support tickets and contacts. Environment:…, call_tool(), _headers(), Any (+74 more)

### Community 176 - "test_executor_all_tools.py"
Cohesion: 0.07
Nodes (68): _make_playwright_executor(), _mock_session(), asyncio, Comprehensive coverage tests for app/rpa/executor.py. Forces…, Ephemeral sessions (no session_id given) are closed via session_manager., Provided session_id → NOT cleaned up automatically., Credential injector.resolve_arguments() is awaited when set., Credential injector exception is logged; execution continues with original args. (+60 more)

### Community 177 - "check_grounding"
Cohesion: 0.05
Nodes (32): annotate_ungrounded(), check_grounding(), Claim, deterministic_ground(), extract_claims(), extract_claims_structured(), GroundingResult, Claim Grounding Checker ======================= After each executor step,… (+24 more)

### Community 178 - "civilization/test_events.py"
Cohesion: 0.06
Nodes (56): CivEventType, emit_event(), get_events_since(), Any, datetime, Civilization event types and dispatch helpers., Emit a civilization event to DB and Redis SSE channel., Fetch events from the durable log for SSE reconnect catch-up. (+48 more)

### Community 179 - "CostTracker"
Cohesion: 0.14
Nodes (41): CostTracker, Central cost-tracking service wired with Redis + DB. Redis keys ----------…, db_factory(), Real async SQLAlchemy session factory connected to local Docker Postgres., _ctx(), _make_redis(), asyncio, Comprehensive tests for CostTracker — budget status, record_llm_usage, anomaly… (+33 more)

### Community 180 - "test_extra_coverage_servers4.py"
Cohesion: 0.06
Nodes (68): call_tool(), _headers(), Any, Calendly MCP server — scheduling, event types, and invitations. Environment:…, call_tool(), _headers(), Any, LinkedIn Ads MCP server — ad accounts, campaigns, creatives, and analytics.… (+60 more)

### Community 181 - "test_executor_standalone_paths.py"
Cohesion: 0.09
Nodes (67): _build_executor(), _inject(), _make_download_cm(), _make_locator(), _make_page(), _make_pw_stack(), asyncio, Standalone Playwright path coverage for app/rpa/executor.py lines 396-652.… (+59 more)

### Community 182 - "PostgresWorkflowApprovalStore"
Cohesion: 0.14
Nodes (19): PostgresWorkflowApprovalStore, Any, Load one approval by its opaque id. ``tenant_id`` must be supplied so RLS can…, All approvals for a given run (used by cross-process resume/tests)., Async SQLAlchemy-backed approval store with per-query RLS enforcement., Args: db_factory: async SQLAlchemy ``async_sessionmaker``…, Upsert an approval. ``request_id`` is the primary key., _app_url() (+11 more)

### Community 183 - "test_learning.py"
Cohesion: 0.08
Nodes (49): _FakeSession, _make_pipeline(), _noop_ctx, asyncio, Tests for LearningPipeline — curated collective learning (Phase D)., High-scoring validated candidates must be promoted to LTM., Medium scores (above rejection, below promotion) are validated but not promoted., Verify threshold constants are correctly ordered. (+41 more)

### Community 184 - "ModelGateway"
Cohesion: 0.05
Nodes (43): DecisionIntelligence, DecisionRecord, get_decision_intelligence(), get_version_store(), Any, SUPP-J Decision Intelligence + SUPP-L Versioning Strategy. Decision…, Aggregate quality metrics for an org's decisions., Recommend which LLM should reason about a decision of this type. Delegates to… (+35 more)

### Community 185 - "SupervisorAgent"
Cohesion: 0.17
Nodes (20): Decomposes a complex goal and coordinates multiple sub-agents. Unlike…, SupervisorAgent, _make_goal_service_mock(), Comprehensive tests for app/agent/supervisor.py — targets 90%+ statement…, Helper: mock goal_service that returns a completed goal event., test_decompose_caps_at_six_tasks(), test_decompose_falls_back_on_invalid_json(), test_decompose_falls_back_on_missing_sub_tasks() (+12 more)

### Community 186 - "agents.py"
Cohesion: 0.08
Nodes (64): _agent_store(), assign_knowledge_collection(), check_readiness(), check_rollout_gate(), clone_agent(), CloneAgentRequest, _connector_id_from_value(), _connector_lookup_key() (+56 more)

### Community 187 - "CostOptimizer"
Cohesion: 0.22
Nodes (9): CostOptimizer, Analyses LLM spend per goal category and suggests cheaper models. Usage::…, TestCostOptimizerRecord, Tests for CostOptimizer., test_categorise_extracts_first_words(), test_get_model_override_returns_none_when_no_override(), test_no_suggestion_when_quality_drop_too_large(), test_record_run_accumulates_stats() (+1 more)

### Community 188 - "OrgCommand"
Cohesion: 0.08
Nodes (33): ChannelAdapter, ABC, Any, Base ChannelAdapter — abstract interface for all channel adapters., Abstract base for all channel adapters., Convert channel-specific payload to a normalized OrgCommand., Convert OrgResponse to channel-specific format., Verify channel-specific authentication. Override per channel. (+25 more)

### Community 189 - "test_middleware_comprehensive.py"
Cohesion: 0.06
Nodes (52): _auth_error_response(), _extract_key(), _is_cors_preflight(), JSONResponse, _rate_limit_response(), _AllowingFakeRedis, _BlockingFakeRedis, _cors_req() (+44 more)

### Community 190 - "chat/models.py"
Cohesion: 0.33
Nodes (7): ChatArtifact, ChatMessage, ChatMessageUsage, ChatSession, ChatSessionFolder, Base, SQLAlchemy ORM models for the chat feature.

### Community 191 - "ScheduleStore"
Cohesion: 0.02
Nodes (168): GraphQLSubscriptionConsumer, PriceThresholdPoller, Any, Advanced trigger consumers — GraphQL subscriptions, WebSocket messages, price…, Poll price data and fire triggers when threshold is crossed., Maintain a WebSocket connection to a GraphQL endpoint and fire triggers on…, Check if price crosses threshold and dispatch matching triggers., Process a GraphQL subscription message and dispatch triggers. (+160 more)

### Community 192 - "_deps.py"
Cohesion: 0.07
Nodes (63): get_agent_store(), get_auction_bid_inbox(), get_auction_repository(), get_audit_log(), get_budget_config(), get_cache_stats(), get_camel_repository(), get_collab_store() (+55 more)

### Community 193 - "build_envelope"
Cohesion: 0.07
Nodes (58): build_envelope(), _canonical_bytes(), _get_signing_key(), Any, Return stable bytes over ALL security-relevant envelope fields. Includes:…, Compute and set the HMAC-SHA256 signature on the envelope in-place., Return True if the envelope signature is valid AND not expired. Args: envelope:…, Construct, sign, and return a complete :class:`ExecutionEnvelope`. (+50 more)

### Community 194 - "ComplianceController"
Cohesion: 0.03
Nodes (109): ComplianceController, DataExportRequest, Any, GDPR right-of-access — collect and return all tenant data., GDPR right-to-erasure. Records intent and schedules DB deletion in 30 days., Sweep and mark records older than retention_days for deletion., Return the raw export payload dict for a ready export request., Execute GDPR erasure — actual DB deletion. Called 30 days after request. (+101 more)

### Community 195 - "test_identity_action_safety.py"
Cohesion: 0.07
Nodes (44): is_ssrf_blocked(), Alias for SSRF protection check — returns True if URL is blocked…, ActionSafetyLevel, ActionSafetyProfile, ActionSafetyProfileSelector, Any, ActionSafetyProfile — per-action risk assessment (spec §Layer 1). Determines…, Per-action safety determination. (+36 more)

### Community 196 - "PromptVariant"
Cohesion: 0.09
Nodes (18): PromptVariant, PromptOptimizer — A/B tests prompt variants and auto-promotes the winner.…, Select which prompt variant to use for this request. Returns the active…, Return a registered variant by id for a tenant, or None if absent. Public…, Apply a suggestion, mutating agent_config where applicable., asyncio, Extra coverage for app/intelligence/prompt_optimizer.py. Targets uncovered…, Lines 184-186: DB exception → warning logged, no raise. (+10 more)

### Community 197 - "test_security_audit_findings.py"
Cohesion: 0.04
Nodes (44): client(), Security audit regression tests. All tests in this file correspond to findings…, POST /rpa/execute must also require auth (control: already guarded)., Tenant B must not be able to read Tenant A's goal by ID. Uses the in-memory…, Tenant B must not be able to cancel Tenant A's goal., Admin endpoints must return 401 for an incorrect key value., Admin endpoints must return 401 when X-Admin-Key header is absent., Registering a connector pointing at 127.0.0.1 must be rejected (SSRF). (+36 more)

### Community 198 - "WorkflowState"
Cohesion: 0.09
Nodes (30): AutoAuditMiddleware, _hash(), Any, AutoAuditMiddleware — automatically emits AuditEvents for every step…, SHA-256 hash of a JSON-serialised object (PII-safe audit)., Wraps every step node execution with automatic AuditLog writes., TypedDict, WorkflowState (+22 more)

### Community 199 - "test_raft_lifecycle.py"
Cohesion: 0.10
Nodes (39): InMemoryRAFTRepository, RAFTDatasetConfig, RAFTService, Deterministic test repository with the same tenant boundaries as SQL storage., Construct datasets and coordinate explicitly confirmed provider jobs., BlockingProvider, chunks(), _completed_service() (+31 more)

### Community 200 - "gateway/router.py"
Cohesion: 0.10
Nodes (43): CommandFile, CommandStreamResponse, _download_command_file(), _ensure_inbox_collection(), GatewayConfig, generic_webhook(), get_channel_status(), get_config() (+35 more)

### Community 201 - "test_group_chat.py"
Cohesion: 0.12
Nodes (23): GroupChatRuntime, Any, Bounded, checkpointable shared-transcript group chat adapter., Bounded group-chat coordination., GroupChatExecutionState, GroupChatParticipant, BaseModel, Immutable group-chat execution state. (+15 more)

### Community 202 - "test_knowledge_store_comprehensive.py"
Cohesion: 0.05
Nodes (25): _cosine_similarity(), Simple character trigram overlap score in [0, 1]., _trigram_score(), _FailingDB, asyncio, KnowledgeCollection, Comprehensive tests for app/rag/store.py — targeting 90%+ coverage., Multiple chunks from same doc_id: doc_count stays 1. (+17 more)

### Community 203 - "analytics/aggregator.py"
Cohesion: 0.10
Nodes (18): _goal_status_cancelled(), _goal_status_completed(), _goal_status_failed(), Any, datetime, GoalAnalyticsAggregator — computes behavioural metrics from goal event history., Return a timezone-aware datetime for goal.created_at regardless of type., Get all goal states, optionally filtered. (+10 more)

### Community 204 - "test_rls_isolation.py"
Cohesion: 0.15
Nodes (17): _migration_content(), asyncio, RLS isolation tests for civilization tables. Asserts tenant A cannot read…, Two board instances for the same tenant share the same data (sanity check)., Society members must be scoped to the correct tenant. Directly injecting an…, Society.get_member returns members for the correct tenant (sanity check)., Entries on different topics don't bleed across topic filters., All 7 civilization tables must have RLS policies in the migration. The… (+9 more)

### Community 205 - "Any"
Cohesion: 0.05
Nodes (25): AbstractAsyncContextManager, AsyncSessionFactory, _BoundTenantScopedGraphCapability, _BudgetedEmbedder, _BudgetedProvider, CollectionAuthorizer, _GatewaySessionStore, GraphCapabilityAdapter (+17 more)

### Community 206 - "_redact_pii"
Cohesion: 0.26
Nodes (4): Recursively redact PII values in nested dicts/lists (max depth 5)., _redact_pii(), Beyond depth 5, returns object as-is., TestRedactPii

### Community 207 - "WorkflowDefinition"
Cohesion: 0.03
Nodes (116): WorkflowCompiler — compiles WorkflowDefinition → LangGraph StateGraph. One…, HITLAction, InputDefinition, Any, model_validator, Validate step IDs unique, depends_on refs valid, no cycles., Parse a canonical YAML workflow definition., Parse from JSON string or dict (API payload). (+108 more)

### Community 208 - "api/a2a.py"
Cohesion: 0.15
Nodes (19): A2ATaskRequest, agent_card(), get_a2a_task(), list_a2a_tasks(), _persist_task(), Any, BaseModel, get (+11 more)

### Community 209 - "NotificationService"
Cohesion: 0.07
Nodes (21): NotificationChannel, NotificationService, Any, Notification service — sends alerts when HITL approval is required. Supports…, Remove a channel from the DB (fire-and-forget)., Send notification to all tenant channels., G-12: Notify when an approval request has timed out. Called from…, Notify when a goal reaches a terminal state. (+13 more)

### Community 210 - "AuditEvent"
Cohesion: 0.07
Nodes (20): AuditEvent, AuditWriter, HashChainVerifier, datetime, Production-grade audit system with WAL, hash chaining, and SIEM integration.…, Return SHA-256 of the canonical JSON representation of this event. The…, Writes audit events to a Redis list (WAL). Guarantees: - Never raises — a Redis…, Push one event to the WAL. Never raises. (+12 more)

### Community 211 - "_scheduled_goal_id"
Cohesion: 0.10
Nodes (17): _scheduled_goal_id(), test_scheduled_goal_id_different_keys_produce_different_ids(), test_scheduled_goal_id_is_deterministic(), test_scheduled_goal_id_without_instance_uses_now(), TestScheduledGoalId, Without fire_instance_id, each call produces a different ID (timestamp based)., test_scheduled_goal_id_differs_for_different_inputs(), test_scheduled_goal_id_fire_instance_in_hash() (+9 more)

### Community 212 - "WebhookDeliverySystem"
Cohesion: 0.09
Nodes (17): Any, QA7 — Webhook Delivery Guarantees (at-least-once delivery). Guarantees: - At-…, Register a new outbound webhook., Queue a webhook delivery. Returns a WebhookDelivery that will be retried until…, Attempt one delivery. Returns True on success., A registered outbound webhook., Per spec QA7 — a single webhook delivery attempt., Compute HMAC-SHA256 signature for payload. (+9 more)

### Community 213 - ".register_builtin_handler"
Cohesion: 0.06
Nodes (42): Any, Return the process-local built-in handler for server_id, or None., Register a handler callable for a built-in server. Process-local only., _absolute_http_url(), call_tool(), _call_tool_inner(), _jira_auth(), Any (+34 more)

### Community 214 - "SemanticCache"
Cohesion: 0.04
Nodes (42): World-class semantic cache with true cosine-similarity matching. Layer 1 (L1):…, Backward-compatible wrapper for old hash-based API. Now uses true similarity., Tenant-scoped, embedding-free cache lookup keyed by exact query text., Backward-compatible wrapper for old hash-based API., Legacy sync store. Stores in L1 only (no Redis without async)., Legacy sync lookup. Checks L1 only., Backward-compatible sync store alias → calls store_sync., Backward-compatible sync lookup alias → calls lookup_sync. (+34 more)

### Community 215 - "test_a2a_comprehensive.py"
Cohesion: 0.10
Nodes (28): _get_a2a_secret(), Verify HMAC-SHA256 signature of incoming A2A task. When ``A2A_SHARED_SECRET``…, _verify_hmac(), _make_app(), FastAPI, Comprehensive tests for app/api/a2a.py — supplements test_a2a.py., Dev mode: no secret → all requests accepted., _set_a2a_tenant() (+20 more)

### Community 216 - "DebateOrchestrator"
Cohesion: 0.08
Nodes (39): AgentProposal, DebateOrchestrator, Debate/voting pattern — N agents independently propose solutions, critique each…, Run N agents in a debate to find the best solution via voting., Comprehensive tests for app/agent/debate.py — targets 90%+ statement coverage., Winner's votes_received should be ≥ 1., run() with event_callback=None should not raise., event_callback receives debate_started + proposals_ready + complete events. (+31 more)

### Community 217 - "test_consent_retention.py"
Cohesion: 0.06
Nodes (48): ConsentRecord, Exception, Consent governance for the voice subsystem (D-24, Coverage-Matrix row 21). Raw…, Raised when audio processing is attempted without recorded consent., An immutable record that a speaker consented (or declined) to processing., Records and enforces per-``(tenant, speaker)`` voice-processing consent. Args:…, Record (or overwrite) a consent decision and return the stored record., Remove any recorded consent for the pair (idempotent). (+40 more)

### Community 218 - "test_main_comprehensive.py"
Cohesion: 0.03
Nodes (9): Comprehensive tests for app/cli/main.py., login should save api_key and base_url to config.json., test command should invoke pytest as subprocess., test_api_key_from_env(), test_api_key_missing_env_exits(), test_base_url_default(), test_base_url_from_env(), test_login_saves_credentials() (+1 more)

### Community 219 - "triggers.py"
Cohesion: 0.09
Nodes (56): _build_spec(), create_trigger(), CreateTriggerRequest, delete_trigger(), emit_trigger_event(), fire_trigger_now(), FireRequest, _get_db() (+48 more)

### Community 220 - "TemplateSecurityReviewer"
Cohesion: 0.05
Nodes (39): Automated security review pipeline for marketplace templates. Checks: 1. Scope…, TemplateSecurityReviewer, Normal connectors (gmail, document_reader) must not fail the reviewer., Templates with critical OAuth scopes are still flagged., Unknown connectors produce a low-severity finding but don't auto-reject., TestSecurityReviewerFix, Force the fallback simple-pattern path by making the injection guard…, HIGH_RISK_SCOPES must fail; unknown custom scopes pass (no PREAPPROVED_SCOPES… (+31 more)

### Community 221 - "MetaAgentPlanner"
Cohesion: 0.09
Nodes (22): MetaAgentPlanner, Converts one NL command into a MetaAgentConfig via an LLM provider., asyncio, LLM sometimes wraps JSON in markdown code fences — must be stripped., JSON missing optional fields should use MetaAgentConfig defaults., Full JSON response maps to MetaAgentConfig correctly., Connector objects from the LLM should not be stringified into agent IDs., interval_seconds value should be coerced to int. (+14 more)

### Community 222 - "InMemoryPatternCheckpointStore"
Cohesion: 0.04
Nodes (79): AutoGPTRuntime, AutoGPTState, Any, BaseModel, BabyAGIRuntime, BabyAGIState, Any, BaseModel (+71 more)

### Community 223 - "FileOps"
Cohesion: 0.06
Nodes (26): FileOps, Path, Check if a path exists in the tenant workspace., File operations scoped to a tenant's isolated workspace directory., Resolve path and verify it stays within the tenant workspace. Raises…, Read text content from a file in the tenant workspace., Write text content to a file in the tenant workspace. Creates parent…, List files and directories in the tenant workspace path. (+18 more)

### Community 224 - "test_reasoning_retrieval_strategies.py"
Cohesion: 0.14
Nodes (36): AgenticRAGRuntimeContract, AgenticAction, AgenticRAGRuntimeAdapter, StrEnum, Execute typed retrieve/reformulate/fallback/stop decisions within a bound., Canonical Self-RAG adapter with persisted critique and bounded retry evidence., SelfRAGRuntimeAdapter, Canonical speculative adapter with concurrent drafting and retrieval. (+28 more)

### Community 225 - "extract_tool_call"
Cohesion: 0.06
Nodes (67): _canonical_tool_name(), extract_tool_call(), _first_json_object(), _jql_from_goal_or_step(), _looks_like_placeholder_jql(), _named_assignee_from_text(), Structured tool-call parsing for executor output., Return the first balanced top-level JSON object in *text*, or None. String-… (+59 more)

### Community 226 - "test_state_machine.py"
Cohesion: 0.08
Nodes (40): create_instance(), create_state_machine(), CreateStateMachineRequest, delete_state_machine(), get_instance(), _get_registry(), get_state_machine(), list_state_machines() (+32 more)

### Community 227 - "SLOTracker"
Cohesion: 0.09
Nodes (20): Any, SLO Burn-Rate Tracker — track error budgets and burn rates per tenant. An SLO…, Record a single success or failure event for *slo*., Compute the current SLO status and burn rate for *slo*., Return a summary of all tracked SLOs for *tenant_id*., SLO burn-rate tracker with optional Redis-backed persistence. Uses a simple…, Return the authoritative event map (Redis when configured, else in-memory)., SLODefinition (+12 more)

### Community 228 - "_FakeRedis"
Cohesion: 0.08
Nodes (22): _FakeRedis, Thread/async-safe dict-backed Redis stub — for tests and no-pool mode. Supports…, Return the asyncio.Lock, creating it lazily on first use., Set expiry as an absolute Unix timestamp., Increment a float counter and return the new value., asyncio, Tests for _FakeRedis concurrent access safety., Concurrent zadd operations should not corrupt the sorted set. (+14 more)

### Community 229 - "test_extra_coverage_servers6.py"
Cohesion: 0.06
Nodes (55): call_tool(), _google_token(), Any, Google Cloud Storage MCP server — bucket and object management via GCS JSON…, call_tool(), _headers(), Any, TikTok MCP server — TikTok for Business API integration. Environment:… (+47 more)

### Community 230 - "test_data_servers_dispatch.py"
Cohesion: 0.09
Nodes (41): _make_async_generator(), asyncio, Dispatch-level tests for data/storage MCP servers. Covers: postgres (asyncpg),…, Test mongodb_find when motor is mocked., Create an async generator that yields items., test_elasticsearch_missing_env(), test_mongodb_missing_dep_returns_error(), test_mongodb_missing_env() (+33 more)

### Community 231 - "routers.py"
Cohesion: 0.04
Nodes (55): get_agent_card(), list_public_agents(), get, Request, A2A Agent Directory — per-agent AgentCards and queryable directory., Return AgentCard for a specific agent (A2A protocol)., List public agents in the A2A directory., BuilderProject (+47 more)

### Community 232 - "Any"
Cohesion: 0.07
Nodes (17): _normalize_domain_filter(), Any, Marketplace V2 — DB-backed template gallery with security review and atomic…, Load marketplace agents from YAML content files. Returns a list of dicts in the…, Populate deterministic built-ins for degraded DB/no-DB read paths. Priority:…, Fetch a single template by id or slug. Returns None if not found. Lookup order:…, D.3 fix: check OAuth scopes only — NOT connector names. required_connectors…, Paginated template list with optional filters. (+9 more)

### Community 233 - "test_scope_enforcement_comprehensive.py"
Cohesion: 0.06
Nodes (42): BaseHTTPMiddleware, Request, Enforces API key scopes and IP allowlist on every non-exempt request. Redis is…, Extract the real client IP, respecting trusted proxy headers. Delegates to the…, Return the scope required for (method, path), or None if unregistered., Load effective scopes for a key from DB + role-based fallback. Resolution…, ScopeEnforcementMiddleware, _make_test_app() (+34 more)

### Community 234 - "test_blackboard.py"
Cohesion: 0.06
Nodes (55): Blackboard, BlackboardConflictError, Any, Exception, Blackboard — tenant-scoped shared findings store. Agents post findings with…, Update an existing entry with optimistic concurrency check., Query the blackboard for relevant findings., Raised when an update fails due to version conflict. (+47 more)

### Community 235 - "AgentCollabSession"
Cohesion: 0.07
Nodes (47): AgentCollabSession, CollabRound, ConsensusResult, Any, Agent collaboration protocol — multi-round propose/critique/counter/agree loop.…, Persist a completed debate session and its proposals to PostgreSQL. Parameters…, Tracks a multi-agent collaboration session., Return ConsensusResult based on whether all recent rounds agree. (+39 more)

### Community 236 - "test_schedules_extra.py"
Cohesion: 0.09
Nodes (38): _make_app(), Any, FastAPI, filterwarnings, Extra coverage tests for app/api/schedules.py — webhook, pause/resume, fire,…, When agent_store is not set but agent_id is provided, raises 500., Only REST and webhook can be manually fired., NL parser returning WEBHOOK type adds token. (+30 more)

### Community 237 - "voice/router.py"
Cohesion: 0.11
Nodes (39): Voice OS module — Native STT/TTS/Streaming for AgentVerse. Providers: STT:…, _cache_persona(), _delete_persona(), _fallback_health(), _fetch_org_health(), _fetch_wywa(), _get_persona(), Any (+31 more)

### Community 238 - "NLTriggerResolver"
Cohesion: 0.09
Nodes (47): TriggerDefinition, NLTriggerParseError, NLTriggerResolver, Any, ValueError, NLTriggerResolver — maps a natural-language description to a TriggerDefinition.…, Resolves natural-language trigger descriptions to TriggerDefinition., Parse *description* and return a TriggerDefinition. (+39 more)

### Community 239 - "workflow/router.py"
Cohesion: 0.08
Nodes (68): add_permission(), analytics_summary(), create_workflow(), delete_workflow(), _get_nl_resolver(), get_permissions(), _get_runner(), get_template() (+60 more)

### Community 240 - "test_keycloak_comprehensive.py"
Cohesion: 0.12
Nodes (29): _get_or_provision_tenant(), Get existing tenant by SSO subject, or create one (JIT provisioning)., _sso_enabled(), _patch_settings(), Comprehensive tests for app/auth/keycloak.py — OIDC/SSO integration., When get_key_by_sso_sub fails, falls back to 'sso:{sub[:16]}' format., test_authorization_endpoint(), test_get_jwks_fetches_and_caches() (+21 more)

### Community 241 - "InMemoryCancellationRepository"
Cohesion: 0.08
Nodes (26): CancellationCoordinator, CancellationRepository, CancellationResult, IncompatibleCheckpointError, InMemoryCancellationRepository, PostgresCancellationRepository, async_sessionmaker, AsyncSession (+18 more)

### Community 242 - "PolicyResult"
Cohesion: 0.09
Nodes (18): evaluate_with_domain_failsafe(), GovernancePolicy, PolicyResult, Return REQUIRE_APPROVAL when no policy matches but domain is regulated. This…, Lightweight policy record used for tenant-scoped policy isolation. This is…, test_policy_engine_empty_allows_everything(), Comprehensive tests for app/governance/policies.py — targeting 90%+ coverage., TestEvaluateWithDomainFailsafe (+10 more)

### Community 243 - "test_tool_cache.py"
Cohesion: 0.07
Nodes (41): classify_tool(), Any, Tool Result Cache ================= Caches MCP tool call responses with smart…, Per-tenant MCP tool result cache backed by Redis. Plugs into…, Return cached tool result or None., Cache a tool result. Silently ignores errors., Return ANY cached result regardless of expiry, up to max_age_seconds old. Used…, Store both normal-TTL and long-lived stale backup. (+33 more)

### Community 244 - "test_tracing_comprehensive.py"
Cohesion: 0.06
Nodes (44): _add_console_span_processor(), get_recent_spans(), get_tracer(), _NoOpSpanContext, _NoOpTracer, Any, Exception, OpenTelemetry tracing bootstrap. Instruments the FastAPI app and configures an… (+36 more)

### Community 245 - "secrets.py"
Cohesion: 0.06
Nodes (33): RuntimeError, Secret resolution that works identically in dev (env vars) and prod (mounted…, Raised when a required secret cannot be resolved from file or env., Resolve a secret by name, preferring a mounted ``*_FILE`` over a plain env var., read_secret(), SecretNotFoundError, MonkeyPatch, Tests for read_secret() — the dev→prod secret resolution contract. Precedence:… (+25 more)

### Community 246 - "test_new_tools.py"
Cohesion: 0.04
Nodes (70): _build_tools(), call_tool(), _get_tools(), Any, Built-in *utility* MCP server — makes AgentVerse's own local tool classes…, Override (or, with ``None``, reset to lazily-built defaults) the tool map. Test…, Dispatch an agent tool call to the matching local utility tool class. Signature…, set_tools() (+62 more)

### Community 247 - "MultimodalPipeline"
Cohesion: 0.03
Nodes (84): AssetJobStore, _AsyncRedisLike, _job_key(), _job_to_payload(), _payload_to_job(), Any, Protocol, Persistent store for AssetIngestionJob records (D-23). Before this module… (+76 more)

### Community 248 - "test_org_router.py"
Cohesion: 0.06
Nodes (31): client(), _fake_dept(), _fake_mission(), _fake_org(), mock_service(), Any, AsyncClient, FastAPI (+23 more)

### Community 249 - "IntentRouter"
Cohesion: 0.08
Nodes (41): ClarifyRequest, Intent, IntentRouter, Intent router — classifies chat messages into QA / GOAL / CLARIFY / SCHEDULE.…, Classify a chat message into Intent.QA / GOAL / CLARIFY / SCHEDULE. Rules (in…, Return the intent for *message*., Return a clarifying question based on what's missing in *message*., Parse a natural-language schedule expression and return a confirmation. (+33 more)

### Community 250 - "_make_app"
Cohesion: 0.09
Nodes (9): _make_app(), FastAPI, SimulationRunner, TestComplianceExportExtra, TestComplianceRouterEndpoints, TestIntelligenceEndpoints, TestMarketplaceEndpoints, TestRedTeamEndpoints (+1 more)

### Community 251 - "org/events.py"
Cohesion: 0.05
Nodes (40): NotificationRoute, NotificationSeverity, OutboundNotification, OutboundNotificationRouter, Any, StrEnum, Outbound notification router — QA6 of spec. Routes org events outbound to the…, Routes org events outbound to the right channel(s). Respects quiet hours and… (+32 more)

### Community 252 - "ImageAttachment"
Cohesion: 0.09
Nodes (20): ImageAttachment, PerceptionInput, Multimodal perception — handles image + text inputs for goals. Allows goals to…, Represents a goal with optional visual context., Format for injection into planner system prompt., Parse a data URI like 'data:image/png;base64,...'., Approximate byte size of the decoded image., Unit tests for multimodal perception models. (+12 more)

### Community 253 - "assert_public_url"
Cohesion: 0.07
Nodes (27): Outbound A2A call tool — lets agents call external A2A/MCP agents as tools., assert_public_url(), _is_blocked_ip(), is_public_url(), ValueError, SSRF egress guard — prevents Server-Side Request Forgery.…, Non-raising version of assert_public_url. Returns False if blocked., Raised when a URL is blocked by the SSRF guard. (+19 more)

### Community 254 - "test_goals_comprehensive.py"
Cohesion: 0.08
Nodes (54): _make_app(), _make_goal(), Any, FastAPI, Comprehensive tests for /goals endpoints — targets 28% → 60%+ coverage., test_abort_persistence_no_redis(), test_abort_persistence_with_redis(), test_approve_goal_not_found() (+46 more)

### Community 255 - "test_main_extra2.py"
Cohesion: 0.04
Nodes (22): Extra coverage tests for app/cli/main.py — targeting 85%+ overall coverage., When API returns a dict (not list), treat as empty., When average_score is not in response, compute from scores., When events endpoint returns a dict (not list), shows nothing., Test _stream_goal processes various SSE event types., Test _stream_goal handles goal_failed events., KeyboardInterrupt during streaming is handled gracefully., Non-JSON data lines are silently skipped. (+14 more)

### Community 256 - "test_perception_gaps.py"
Cohesion: 0.05
Nodes (53): asyncio, Coverage gaps for app/perception/browser_agent.py and…, take_screenshot returns success result when playwright is available., take_screenshot returns failure on navigation exception., extract_text returns page text when playwright is available., BrowserAgent.available returns False when playwright is not importable., extract_text returns failure on exception., click_and_screenshot returns success result when playwright is available. (+45 more)

### Community 257 - "a2a_security.py"
Cohesion: 0.08
Nodes (34): A2AKey, A2AKeyMetadata, A2AKeyProvider, A2AKeyPurpose, A2ANonceStore, A2ASecretResolver, A2ASecurityError, A2ASecurityService (+26 more)

### Community 258 - "test_society.py"
Cohesion: 0.06
Nodes (63): _normalize_connectors(), Society — civilization membership, reputation tracking, goal routing.…, Coerce a connector_ids DB value into a list[str]. The ``agents.connector_ids``…, _CapturingRouter, _FakeSession, _make_society(), _member_row(), _noop_ctx (+55 more)

### Community 259 - "GoalCostBreakdown"
Cohesion: 0.08
Nodes (35): _backend_load(), configure_persistence(), finalize_breakdown(), get_breakdown(), GoalCostBreakdown, _persist(), Any, Per-goal, per-role cost breakdown tracking. Tracks input/output tokens and… (+27 more)

### Community 260 - "asyncio"
Cohesion: 0.05
Nodes (28): asyncio, Lines 184: guard raises → exception caught, no finding., Lines 1261-1263: slug lookup in memory cache., Returns None when slug not found in memory., Returns None when neither id nor slug given., Line 1334: category filter in-memory., search filter in-memory., domain filter in-memory. (+20 more)

### Community 261 - "TestSemanticCacheHashAndKey"
Cohesion: 0.22
Nodes (5): _do_hash(), Covers line 68: _hash_embedding produces stable hash., Hash should be 32 chars (hexdigest[:32])., New format uses scv2:entry: prefix., TestSemanticCacheHashAndKey

### Community 262 - "test_sanitization_comprehensive.py"
Cohesion: 0.07
Nodes (51): Any, Any, Protocol, Shared event sanitization helpers for agent and workflow events., Return *value* as text with common credentials redacted., redact_sensitive_text(), ResultProcessor, sanitize_event() (+43 more)

### Community 263 - "test_auction_swarm_runtime.py"
Cohesion: 0.08
Nodes (40): Allocation, Any, BaseModel, datetime, Decimal, RuntimeError, Idempotent fenced auction allocation and settlement., StaleWinnerError (+32 more)

### Community 264 - "coordination/contracts.py"
Cohesion: 0.11
Nodes (29): ContextMessage, Contract, CoordinationEvent, EventPayload, HandoffCommand, Participant, BaseModel, model_validator (+21 more)

### Community 265 - "test_hallucination_fixes.py"
Cohesion: 0.07
Nodes (35): Validate that *tool_name* is in the allowed set. Returns: None — tool is valid,…, Validate *arguments* against a JSON Schema dict. Checks: - All ``required``…, validate_tool_arguments(), validate_tool_name(), _agent_source(), Unit tests verifying all 6 hallucination-elimination fixes., Default max_length is 16000 — large enough not to truncate real answers…, validate_tool_arguments must reject calls missing required fields. (+27 more)

### Community 266 - "WorkflowService"
Cohesion: 0.04
Nodes (41): Any, WorkflowService — database-backed CRUD service for the workflow engine router.…, Archive (soft-delete) a workflow by setting status=archived., Publish a draft workflow (status draft → published). Publishing also…, Unpublish a workflow (status published → draft)., Return the real published version history from…, Restore a previous version: load its stored definition and write it back onto…, Return workflow templates from SystemTemplateStore if wired. (+33 more)

### Community 267 - "AuctionAnnouncement"
Cohesion: 0.09
Nodes (32): AuctionAnnouncement, BidPayload, BaseModel, model_validator, RankedBid, Auction contracts using fixed-point score inputs., RevealedBid, ScoreWeights (+24 more)

### Community 268 - "MetaOrchestrator"
Cohesion: 0.06
Nodes (39): get_model_profile_for_dept(), Return the preferred model profile name for a department kind., _compute_approval_gates(), ExecutionPhase, GoalAnalysis, GoalAnalyzer, _group_depts_into_phases(), MetaOrchestrator (+31 more)

### Community 269 - "_WorkflowStore"
Cohesion: 0.10
Nodes (22): _orm_to_dict(), Any, Workflow persistence store. Uses an in-memory dict when no DB session factory…, Wire in the async SQLAlchemy session factory (called during lifespan)., Partial update — only the provided fields are changed; version bumps., Upsert the run-engine ``workflow_definitions`` mirror row. No-op when…, Delete the mirror row, but only when no runs reference it.…, _WorkflowStore (+14 more)

### Community 270 - "test_openapi_importer_comprehensive.py"
Cohesion: 0.07
Nodes (49): extract_tools_from_spec(), import_and_register(), parse_openapi_spec(), persist_tools(), Any, OpenAPI 3.x spec importer — creates MCP connector registrations + tool…, Convert HTTP method + path to a valid snake_case tool name., Persist tool definitions to tool_capabilities table. Returns count of tools… (+41 more)

### Community 271 - "BrowserAgent"
Cohesion: 0.06
Nodes (51): BrowserAction, BrowserAgent, Any, Browser agent — headless Chromium automation via Playwright. Provides web…, Extract visible text from a URL., Navigate to URL, click element, return screenshot., Fill a form field and optionally submit., Dispatch a browser action. (+43 more)

### Community 272 - "FasterWhisperSTT"
Cohesion: 0.24
Nodes (5): _decode_audio(), FasterWhisperSTT, Any, ndarray, Decode any audio format to float32 mono 16 kHz numpy array.

### Community 273 - "RuntimeConstraints"
Cohesion: 0.12
Nodes (20): ConstitutionalAIRuntime, ConstitutionalResult, Any, BaseModel, Bounded Constitutional AI critique/revision under deterministic policy., RuntimeConstraints, PolicyDecision, PolicyTrace (+12 more)

### Community 274 - "test_saml_provider.py"
Cohesion: 0.08
Nodes (41): build_saml_provider_from_config(), Any, SAML 2.0 provider — enterprise SSO integration. Supports python3-saml…, Return SP metadata XML for IdP registration., Return True if assertion_id was already seen (replay). Amendment 8.4: Redis key…, Construct a SAMLProvider from a saml_configs DB row., User identity extracted from a SAML assertion., SAML 2.0 single sign-on provider for a single tenant. Constructed per-tenant… (+33 more)

### Community 275 - "factory"
Cohesion: 0.07
Nodes (22): DatabaseHandoffMembership, postgres_cancellation_state(), postgres_lease_repository(), _make_legal_hold_manager(), Comprehensive tests for app/governance/legal_holds.py — targeting 90%+ coverage., When no DB is configured, release_hold skips the DB update and returns True., resource_ids already a list (not JSON string) is handled., TestCreateHold (+14 more)

### Community 276 - "ExperimentRegistry"
Cohesion: 0.10
Nodes (11): ExperimentRegistry, Any, Experiment Registry =================== Single control plane for ALL self-…, Record one outcome for an experiment arm., Evaluate whether the experiment can make a promotion decision., Update running mean and variance using Welford's algorithm., In-memory experiment registry (upgraded with DB in lifespan). Enforces one…, Register a new experiment. Returns the experiment dict. (+3 more)

### Community 277 - "Governor"
Cohesion: 0.10
Nodes (13): Governor, Any, Create a new civilization member (only called with APPROVED verdict). Returns…, Retire members below reputation floor or past idle TTL. Returns retired agent…, Kill a specific civilization member., Pause the civilization — stops new spawns, signals agents to halt at next…, Resume a paused civilization., Governs the civilization: enforces the Constitution, creates/retires members.… (+5 more)

### Community 278 - "test_cost_tracker.py"
Cohesion: 0.08
Nodes (40): _make_redis(), _make_tenant_ctx(), asyncio, Comprehensive tests for CostTracker — token extraction, cost calculation,…, CompletionResponse.usage must default to None for backwards compat., 100k prompt + 20k completion on claude-sonnet-4-5 = $0.60., 1M prompt + 100k completion on gpt-4o-mini., Unknown model should use fallback pricing (3.0/15.0 per 1M tokens). (+32 more)

### Community 279 - "_make_breaker"
Cohesion: 0.08
Nodes (11): _make_breaker(), Simulate CLOSED→OPEN by recording failures to threshold., OPEN circuit transitions to HALF_OPEN after cooldown., After a HALF_OPEN probe succeeds, all keys are deleted (CLOSED)., TestCanCallAsync, TestGetState, TestKeyGeneration, TestRecordFailureAsync (+3 more)

### Community 280 - "test_skills_executor.py"
Cohesion: 0.07
Nodes (34): Any, Skills Runtime execution engine. Responsibilities: 1. TriggerMatcher: score how…, Check whether a skill is allowed for the given tenant/agent. Rules (evaluated…, Set the skill allowlist for a specific agent., Score how well a goal/step matches a skill's trigger hints., Return 0.0-1.0 match score. Algorithm: 1. Lowercase both sides 2. For each…, Return list of (skill, score) sorted by score desc, filtered by threshold., Return highest-scoring skill or None if below threshold. (+26 more)

### Community 281 - "resolve_expires_at"
Cohesion: 0.32
Nodes (6): datetime, Retention-policy → TTL resolution for canonical memory records. Every…, Return the retention window in days for ``policy_id``. ``None`` means "never…, Compute the absolute expiry deadline for a record written at ``created_at``.…, resolve_expires_at(), retention_days()

### Community 282 - "test_servers_comprehensive.py"
Cohesion: 0.15
Nodes (22): call_tool(), _dispatch_github_tool(), Any, AsyncClient, GitHub MCP server wrapper — wraps GitHub REST API in MCP protocol. Environment…, _collect_server_modules(), asyncio, MonkeyPatch (+14 more)

### Community 283 - "get_inverse_fn"
Cohesion: 0.09
Nodes (27): Execute all inverse operations in LIFO order, awaiting each one. Two modes:…, get_inverse_fn(), Any, Tool inverse registry — maps tool names to their async undo functions. Each…, Wire the MCP client so inverses can make real API calls., Return a callable that undoes the named tool call. Two modes depending on…, register_inverse(), set_mcp_client() (+19 more)

### Community 284 - "test_auth_api_coverage.py"
Cohesion: 0.08
Nodes (47): _check_auth_rate_limit(), get_userinfo(), Request, Refresh an expired access token using a refresh token., Return current user information from validated JWT., Redis-backed sliding-window rate limiter for auth endpoints. Falls back to no-…, refresh_token(), app() (+39 more)

### Community 285 - "WorkItem"
Cohesion: 0.07
Nodes (31): CapabilityGraph, CapabilityNode, DomainDiscovery, get_capability_graph(), get_domain_discovery(), get_work_discovery(), get_work_value_engine(), Any (+23 more)

### Community 286 - "_make_service"
Cohesion: 0.12
Nodes (15): _ctx(), _inject_goal(), _make_service(), Any, Comprehensive tests for app/services/goal_service.py — targeting 80%+ coverage.…, Inject a GoalRecord directly into service._goals for testing., TestCancelGoal, TestGetEval (+7 more)

### Community 287 - "channels/ingestion.py"
Cohesion: 0.10
Nodes (44): _channel_verified(), create_channel_mapping(), discord_events(), email_inbound(), _emit_chat_event(), form_submission(), _get_dispatcher(), _get_gateway() (+36 more)

### Community 288 - "Event"
Cohesion: 0.08
Nodes (35): list_messages(), Any, get, Request, StreamingResponse, Paginated transcript and cursor-based coordination event replay., replay_events(), InvalidCursorError (+27 more)

### Community 289 - "skills_runtime.py"
Cohesion: 0.10
Nodes (47): AutoMatchRequest, create_tenant_skill(), CreateSkillRequest, _db_save_skill(), _dict_to_skill_def(), disable_skill(), enable_skill(), execute_best_match() (+39 more)

### Community 290 - "PageAnalyzer"
Cohesion: 0.08
Nodes (42): BrowserResult, Perception module — multimodal inputs, browser automation, page analysis., PageAnalysis, PageAnalyzer, Page analyzer — extract structured data from web pages using BrowserAgent + LLM., Format for injection into planner prompt., Analyze web pages using BrowserAgent and optionally a vision LLM., Fully analyze a URL: screenshot + text + LLM analysis. (+34 more)

### Community 291 - "test_security_org.py"
Cohesion: 0.05
Nodes (28): ImprovementCyclePhase, ImprovementProposal, LearningCategory, OrgLearningSystem, OrgLesson, OrgRecoveryHierarchy, Any, StrEnum (+20 more)

### Community 292 - "RoutingDecision"
Cohesion: 0.08
Nodes (19): AgentScore, Any, Intent-based agent router — picks the best-fit agent for a goal., Return the system explicitly named in the goal, or None., Return the primary connector system for this agent, or None., Jaccard-style overlap between goal words and agent name + goal_template. Anti-…, Score breakdown for a single candidate agent., Bidirectional connector relevance score. Old approach: checked if the raw… (+11 more)

### Community 293 - "StallDetector"
Cohesion: 0.20
Nodes (16): MagenticState, ProgressAssessment, BaseModel, Magentic progress and stall decision contracts., StallAssessment, _normalize(), Deterministic no-progress, repeated-action, and blocker stall detection., StallDetector (+8 more)

### Community 294 - "test_marketplace_endpoint_gaps.py"
Cohesion: 0.08
Nodes (36): _make_app(), Any, FastAPI, Tests for marketplace endpoints in app/api/enterprise.py that aren't covered.…, Endpoint handles marketplace.list_templates returning a dict with 'items' key., Endpoint covers goal templates via app.state.template_store.list()., If marketplace.list_templates raises, the exception is swallowed (counts…, If template_store.list raises, the exception is swallowed. (+28 more)

### Community 295 - "RuntimeProfileBuilder"
Cohesion: 0.05
Nodes (49): InvalidStrategyOverrideError, Any, DecisionTrace, ValueError, An explicit strategy request cannot be resolved or admitted., RuntimeProfileBuilder, CacheBridgeResult, Any (+41 more)

### Community 296 - "OutboundWebhookService"
Cohesion: 0.09
Nodes (20): OutboundWebhookService, Any, Outbound webhook delivery with retry and dead-letter queue., Delivers outbound webhooks with exponential backoff retry., Remove delivery records older than TTL or beyond size limit., Deliver a webhook with retry. Returns delivery record., WebhookDelivery, Comprehensive tests for app/services/webhook_service.py — targeting 90%+… (+12 more)

### Community 297 - "test_enterprise_comprehensive2.py"
Cohesion: 0.11
Nodes (46): _make_app(), _make_compliance(), _make_marketplace(), _make_red_team(), _make_simulation(), Any, FastAPI, Comprehensive tests for /enterprise API — targets 32% → 80%+ coverage. Covers… (+38 more)

### Community 298 - "LedgerRevision"
Cohesion: 0.11
Nodes (17): Immutable Magentic progress-ledger contracts., LedgerRevision, BaseModel, Typed immutable progress-ledger revision., InMemoryProgressLedgerRepository, PostgresProgressLedgerRepository, Any, async_sessionmaker (+9 more)

### Community 299 - "SimulationRunner"
Cohesion: 0.03
Nodes (102): Simulation runner — execute goals in a sandboxed mock-tool environment. Allows…, Runs goals in a mock-tool sandbox environment., Run a goal through the FULL AgentGraph pipeline with mocked tool responses.…, Stub simulation using keyword-based planning (no real LLM required)., SimulationRun, SimulationRunner, Targets uncovered paths in enterprise/simulation.py., Lines 290-291 in simulation.py. (+94 more)

### Community 300 - "test_generative_agent.py"
Cohesion: 0.08
Nodes (28): GenerativeAgentRuntime, Any, datetime, timedelta, Checkpointed bounded generative-agent simulation runtime., GenerativeState, Observation, Persona (+20 more)

### Community 301 - "MCPClient"
Cohesion: 0.04
Nodes (104): _absolute_http_url(), CircuitBreakerOpenError, _is_jira_rest_endpoint(), _is_mcp_endpoint(), _jsonrpc(), MCPClient, Any, AsyncClient (+96 more)

### Community 302 - "DeletionOrchestrator"
Cohesion: 0.09
Nodes (30): DeletionOrchestrator, Any, Data-subject deletion orchestrator — real GDPR/DPDP right-to-erasure cascade.…, Delete (or, if *dry_run*, count) a subject's data across all stores., Independent re-scan proving erasure — returns residue per store. An empty dict…, Return the name of an active legal hold covering the subject, else None., Delete (or count, if dry_run) rows; return (count, ids). Isolated txn., Executes and verifies data-subject deletion cascades. (+22 more)

### Community 303 - "ComplianceBundleManager"
Cohesion: 0.07
Nodes (17): ComplianceBundle, ComplianceBundleManager, Compliance Bundles =================== Pre-configured governance, guardrail,…, Manages active compliance bundles per tenant., Return the most restrictive autonomy mode across all active bundles., Check if any active bundle requires HITL for this tool., datetime, Time-Based Governance Rules ============================== Prevents destructive… (+9 more)

### Community 304 - "MQTTTriggerConsumer"
Cohesion: 0.07
Nodes (35): MQTTTriggerConsumer, Any, MQTT trigger consumer — subscribes to MQTT topics and fires triggers., Listen on MQTT topics and dispatch matching triggers., Connect and subscribe. Requires an async MQTT client (e.g., aiomqtt)., Process a single MQTT message and fire matching triggers., MQTT wildcard matching: + = single level, # = multi level., convert_to_base() (+27 more)

### Community 305 - "test_explainability.py"
Cohesion: 0.12
Nodes (19): DecisionExplainer, ExplanationBundle, Any, DecisionTrace, RuntimeProfileExplainer, SourceExplainer, DecisionTrace, Any (+11 more)

### Community 306 - "Tokenizer"
Cohesion: 0.10
Nodes (12): Prompt Compressor ================= Reduces system prompt token count before…, Tokenizer — tiktoken-backed token counter with byte-length fallback. Provides…, Thin tiktoken wrapper with byte-length fallback., Return the token count for text., Truncate text to at most max_tokens tokens., True when tiktoken is available (accurate count), False when using fallback., Tokenizer, main() (+4 more)

### Community 307 - "test_workflow_planner_comprehensive.py"
Cohesion: 0.07
Nodes (42): build_static_workflow(), Any, LLM-based workflow DAG planner. Given a goal, produces a dependency graph of…, Generate a parallel-aware workflow plan from a natural language goal., Fallback heuristic plan when LLM unavailable., Build a deterministic connector-targeted workflow from goal keywords., WorkflowPlanner, Comprehensive tests for app/agent/workflow_planner.py — targets 90%+ statement… (+34 more)

### Community 308 - "ComplianceChecker"
Cohesion: 0.11
Nodes (43): ComplianceChecker, generate_scim_token(), Enterprise compliance v2 — real dynamic compliance checking. Replaces the…, Generate a SCIM bearer token. Returns: (raw_token, token_prefix_12_chars,…, Dynamically evaluates HIPAA, GDPR, SOC2, PCI-DSS compliance for a tenant. Never…, _make_db_factory(), Any, asyncio (+35 more)

### Community 309 - "WorkflowHITLRequest"
Cohesion: 0.10
Nodes (16): Protocol, PostgresWorkflowApprovalStore — durable, cross-process store for workflow HITL.…, Persistence surface for workflow HITL approvals. See the Postgres impl., WorkflowApprovalStore, HITLWorkflowGateway — extends HITLGateway with workflow-engine HITL features.…, Persist a new HITL request and send notifications., Submit a reviewer decision. Idempotent by default. ``tenant_id`` lets the…, Delegate a pending request to another user. (+8 more)

### Community 310 - "DepartmentMemory"
Cohesion: 0.06
Nodes (33): DepartmentMemory, MemoryEntry, Any, Department Memory — PART 14 (6-tier memory, tier: department-scoped).…, Add a new memory entry to the department store. Raises: ValueError: if…, Append a correction to an existing entry (non-destructive). The original…, Mark an entry as no longer valid., Return all entries for a department. (+25 more)

### Community 311 - "ChannelRateLimiter"
Cohesion: 0.08
Nodes (21): ChannelRateLimiter, Any, Exception, RateLimitExceededError, Per-channel rate limiter — Q11 of spec. Limits: Per tenant across all channels:…, Redis sliding window (ZADD + ZCOUNT pattern)., In-memory sliding window fallback., Raised when a channel command exceeds rate limits. (+13 more)

### Community 312 - "test_redis_factory.py"
Cohesion: 0.06
Nodes (25): get_redis_kwargs(), make_async_redis(), make_sync_redis(), _parse_host_port_list(), Any, Redis client factory with Sentinel and Cluster support. Priority order: 1.…, Read HA topology settings from environment variables. The returned dict can be…, Parse 'host1:port1,host2:port2' into [(host, port), ...] pairs. (+17 more)

### Community 313 - "test_enterprise_api.py"
Cohesion: 0.14
Nodes (34): _make_app(), Any, FastAPI, SimulationRunner, TestClient, API-level tests for enterprise endpoints using TestClient., Helper: seed the optimizer with low-score eval data via the unit layer., Seeds optimizer directly on app.state, then applies via API. (+26 more)

### Community 314 - "test_program09_core.py"
Cohesion: 0.14
Nodes (22): CamelRuntime, Any, datetime, Checkpointed, canonical-transcript CAMEL runtime., build_inception(), Deterministic CAMEL inception validation., CamelState, InceptionArtifact (+14 more)

### Community 315 - "classify_tool_risk"
Cohesion: 0.09
Nodes (43): classify_tool_risk(), Classify a tool into a risk tier. Parameters ---------- tool_name: The name of…, Comprehensive tests for app/agent/tool_risk.py — targets 90%+ statement…, test_approve_is_write_high(), test_atlassian_context_recognized(), test_billing_connector_is_write_high(), test_charge_is_write_high(), test_combined_server_and_tool_name_logic() (+35 more)

### Community 316 - "memory_v2.py"
Cohesion: 0.12
Nodes (42): consolidate_memories(), create_memory(), CreateMemoryRequest, _db_upsert_memory(), delete_memory(), _detect_conflicts(), _ensure_loaded_from_db(), export_gdpr() (+34 more)

### Community 317 - "asyncio"
Cohesion: 0.06
Nodes (26): _make_mock_db(), asyncio, Lines 135-138: DB exception → logs warning, returns None., Lines 143-155: successful DB write., Lines 153-155: DB exception → warning logged., Lines 169-195: goal_service has _db_session_factory., Lines 194-197: DB query fails → logs, continues., Lines 209-210: in-memory fallback exception → logs, empty. (+18 more)

### Community 318 - "KnowledgeGraphStore"
Cohesion: 0.02
Nodes (158): CommunityDetector, Any, BFS/Union-Find community detection for the Knowledge Graph., BFS-based connected-components community detection for the KG. Uses Union-Find…, Return the community_id for a given node_id, or None if not found., Sort communities by size descending, return ranked list., Return list of community dicts. Each dict has: community_id - UUID string…, EntityExtractor (+150 more)

### Community 319 - "test_oauth_flow.py"
Cohesion: 0.19
Nodes (14): _make_app(), Tests for real OAuth callback implementation., A matching pending flow returns connected with token metadata., OAuth start returns state and code_challenge embedded in auth_url., Connectors that are not OAuth type return 400., Missing/empty state param returns pending_config (no token_url configured)., When server config has no token_url, endpoint returns pending_config., A state that doesn't match any pending flow returns error. (+6 more)

### Community 320 - "ExecutionMemory"
Cohesion: 0.03
Nodes (79): ExecutionMemory, Execution memory — stores winning plans and failed approaches across runs.…, Per-tenant store of past executions (successful plans and failures)., _agent_source(), asyncio, Tests for agent intelligence memory and persistence bugs. Covers: BUG 1 — LTM…, record_async(db=None) must not raise., graph.py must call extract_from_goal_async not the sync version. (+71 more)

### Community 321 - "ABTestingEngine"
Cohesion: 0.09
Nodes (26): ABTestingEngine, ExperimentArm, ExperimentType, Any, Seed in-memory results from DB on startup., Record result in-memory AND persist to ab_test_results table., test_ab_testing_engine_records_and_stats(), test_ab_testing_promotion_threshold() (+18 more)

### Community 322 - "test_quantization.py"
Cohesion: 0.09
Nodes (39): binary_cosine_estimate(), BinaryVector, _clamp_int8(), dequantize_int8(), EmbeddingQuantizer, _float_cosine(), hamming_distance(), int8_cosine() (+31 more)

### Community 323 - "_make_agents_app"
Cohesion: 0.06
Nodes (20): Persist an agent snapshot to the agent_snapshots table WITH RLS context., _save_snapshot_to_db(), Lines 28-83: _save_snapshot_to_db and _load_snapshots_from_db with db=None., test_save_and_load_snapshots_no_db(), TestSaveSnapshotToDb, _make_agents_app(), Lines 309, 316-317, 324., Remaining agents paths. (+12 more)

### Community 324 - "._make"
Cohesion: 0.10
Nodes (6): asyncio, Tests for all remaining performance optimizations. Covers: - GoalDeduplicator…, TestCostTierDowngrade, TestGoalDeduplicator, TestModelRouterComplexityTiering, TestPromptCompressor

### Community 325 - "test_gateway_gap_closure.py"
Cohesion: 0.08
Nodes (33): execute_rag_node(), Workflow Node Executors ======================= Real execution semantics for…, Execute a RAG retrieval node. Node config: collection_id: str — knowledge…, _content_key(), federated_search(), _merge_unique_dicts(), _normalize_scores(), Any (+25 more)

### Community 326 - "ToolSelector"
Cohesion: 0.10
Nodes (20): _needs_rpa(), Any, ToolSelector — goal-aware top-k tool retrieval with reliability boosting.…, Return [(score, tool)] sorted desc by relevance score., Boost scores by historical success rate., Return True when the goal or agent capabilities signal browser work., Select tools for a goal. Returns a ToolSelection with 3 tiers. Falls back to…, ToolSelection (+12 more)

### Community 327 - "outbox.py"
Cohesion: 0.07
Nodes (21): OutboxDelivery, OutboxRecord, OutboxRepository, PostgresOutboxRepository, Any, async_sessionmaker, AsyncSession, BaseModel (+13 more)

### Community 328 - "RAFTJobRecord"
Cohesion: 0.08
Nodes (20): Base, RAFTConfirmationGrant, RAFTDataset, RAFTFineTuneJob, Durable tenant-scoped records for the RAFT lifecycle., _ConfirmationGrant, ConfirmationRequiredError, FineTuneEvaluation (+12 more)

### Community 329 - "test_validators.py"
Cohesion: 0.10
Nodes (32): _first_match(), IdDocExtractor, _label_value(), ID document field extractor (PAN, Aadhaar, Passport, Driving License)., mask_aadhaar(), normalize_date(), OCR field validators: format checking, checksum validation, date normalization., Normalize any date string to ISO 8601 (YYYY-MM-DD). Returns None if unparseable. (+24 more)

### Community 330 - "org/connectors/__init__.py"
Cohesion: 0.06
Nodes (20): BaseConnector, ConnectorCategory, ConnectorMeta, ConnectorRegistry, ExpensifyConnector, GrafanaConnector, LookerConnector, ABC (+12 more)

### Community 331 - "test_consumers.py"
Cohesion: 0.09
Nodes (31): APIPoller, DBRowChangeConsumer, Any, Data trigger consumers — DB row change, S3 events, API poll, RSS feed., Generic HTTP API poller for API_POLL triggers., Listen on pg_notify for DB_ROW_CHANGE triggers., Execute a single poll cycle for a trigger., Simple dot-notation JSONPath: $.foo.bar → data['foo']['bar']. (+23 more)

### Community 332 - "PromptOptimizer"
Cohesion: 0.11
Nodes (16): PromptOptimizer, Publish cache invalidation to other replicas., Record an eval score for a variant after a goal run. Searches all tenant scopes…, Manages prompt variant A/B testing and auto-promotion. Variants are scoped per…, Lines 381-385: p95_score computed from eval_scores., mean_score and p95_score are None when no eval_scores., Lines 388-389: returns set of prompt_key values., Tenant scoping: different tenants have independent keys. (+8 more)

### Community 333 - "asyncio"
Cohesion: 0.08
Nodes (21): asyncio, Messages with subtype are skipped., Messages shorter than 10 chars are skipped., Follows next_cursor for pagination., Remaining messages < 5 in final window are still chunked., Files with < 50 chars are not chunked., 404 errors on individual files are silently skipped., Non-404 HTTP errors are logged and skipped. (+13 more)

### Community 334 - "test_tenants.py"
Cohesion: 0.11
Nodes (29): _hash_key(), SHA-256 hex digest of a raw API key. The raw key is never stored., Line 26: _hash_key returns SHA-256 hex digest., test_hash_key_utility(), _make_app(), Any, FastAPI, parametrize (+21 more)

### Community 335 - "test_router_runs.py"
Cohesion: 0.10
Nodes (34): client(), _FakeRunStore, make_app(), TestClient, Tests for workflow runs router (run detail, steps, lifecycle control)., Minimal in-memory WorkflowRunStore double for the real WorkflowService., real_client(), _run() (+26 more)

### Community 336 - "check_tool_args_for_exfil"
Cohesion: 0.07
Nodes (17): check_tool_args_for_exfil(), _contains_secret(), Any, Data Exfiltration Guard ======================= Detects when an agent is about…, Wrap tool output in untrusted-content delimiters. This prevents the LLM from…, Return True if text appears to contain credentials or secrets., Check tool arguments for potential data exfiltration. Returns: (blocked: bool,…, wrap_tool_output_as_untrusted() (+9 more)

### Community 337 - "test_goals_final.py"
Cohesion: 0.09
Nodes (32): _make_app(), Any, FastAPI, Cover remaining ~39 lines in app/api/goals.py. Targets: - line 64:…, Lines 119-120: supervisor.run raises → 500 HTTP exception., Lines 165-172: router returns needs_human_choice → 202 with routing info., Lines 173-175: agent_router.route raises → logged, submission continues., Line 240: /goals/route with agent_store set calls list_async. (+24 more)

### Community 338 - "LateChunker"
Cohesion: 0.09
Nodes (14): ContextualChunkEnricher, Enriches chunks by prepending document-level context before embedding. Two…, Prepend *document_summary* to each chunk (fast, no LLM). Parameters ----------…, LateChunk, LateChunker, True late chunking: get token embeddings then average per chunk. This path is…, Embed full document and slice per-chunk embeddings from token embeddings. Most…, Return True if the provider exposes token-level embeddings. (+6 more)

### Community 339 - "test_governance_integration.py"
Cohesion: 0.14
Nodes (18): ArchivePolicy, DeletionSchedule, ExportPolicy, LegalHoldPolicy, DataCategory, RetentionPolicy, RetentionTier, Governance: audit v3, HITL, compliance bundles, RBAC, cost hard stop. (+10 more)

### Community 340 - "AgentVersePlugin"
Cohesion: 0.10
Nodes (16): AgentVersePlugin, EvaluatorPlugin, KnowledgePlugin, MemoryPlugin, ModelPlugin, PolicyPlugin, Any, Plugin system — SUPPLEMENT D of spec. Defines the 6 plugin types and… (+8 more)

### Community 341 - "InMemoryCacheBackend"
Cohesion: 0.08
Nodes (20): CacheBackend, _cosine_similarity(), _get_rls_imports(), InMemoryCacheBackend, PgVectorCacheBackend, Any, Protocol, Vector Cache Backends for SemanticCache L2. Replaces the O(n) Python Redis scan… (+12 more)

### Community 342 - "test_insights_comprehensive.py"
Cohesion: 0.09
Nodes (41): _make_app(), Any, FastAPI, Comprehensive tests for app/api/insights.py — targets the 12% baseline., Fall back to goal record events when get_event_log returns []., test_agent_health_no_goal_service_returns_defaults(), test_agent_health_with_no_db_on_goal_service(), test_analysis_generic_suggestions_when_no_pattern() (+33 more)

### Community 343 - "test_integrations_api_comprehensive.py"
Cohesion: 0.09
Nodes (41): _make_app(), Any, FastAPI, Comprehensive tests for app/api/integrations.py — targets the 20% baseline., Without SLACK_SIGNING_SECRET, dev mode allows all requests., _slack_sig(), test_alertmanager_firing_alert_creates_goal(), test_alertmanager_firing_alert_no_tenant_id_ignored() (+33 more)

### Community 344 - "test_phase14_deep_coverage.py"
Cohesion: 0.08
Nodes (41): _make_full_app(), TestClient, Phase 14: Deep backend test suite covering all platform domains. This test file…, Node created by tenant A cannot be accessed by tenant B., Memory created by tenant A cannot be read by tenant B., Execution plan created by tenant A cannot be read by tenant B., Tenant skill created by A cannot be accessed by B., Tenant A violations not visible to tenant B. (+33 more)

### Community 345 - "OrgMCPServer"
Cohesion: 0.09
Nodes (17): OrgMCPServer, Any, Exposes the org as a Model Context Protocol (MCP) server. Compatible with:…, Return the GoalService from app.state (non-blocking)., Return (OrgService, session) backed by a real DB session. Caller **must** use…, Return tool definitions in MCP format., Execute an MCP tool call and return the result., Route a natural-language question to the org brain. Strategy: 1. Fetch org… (+9 more)

### Community 346 - "schedules.py"
Cohesion: 0.14
Nodes (40): create_schedule(), CreateScheduleRequest, delete_schedule(), events_stream(), fire_schedule_now(), get_schedule(), get_schedule_analytics(), get_schedule_history() (+32 more)

### Community 347 - "auth/mfa.py"
Cohesion: 0.09
Nodes (37): complete_mfa_login(), confirm_mfa(), enroll_mfa(), EnrollConfirmRequest, _get_pyotp(), MFACompleteRequest, Any, BaseModel (+29 more)

### Community 348 - "test_scim_handler.py"
Cohesion: 0.11
Nodes (40): Request, Authenticate SCIM requests via pre-provisioned bearer token. Amendment 8.2:…, require_scim_auth(), _make_handler(), _make_mapping_row(), _make_request(), Any, Comprehensive tests for app/auth/scim_handler.py. (+32 more)

### Community 349 - "LLMResponseCache"
Cohesion: 0.10
Nodes (29): LLMCacheEntry, LLMResponseCache, Any, LLM Response Cache ================== Caches complete LLM completion responses…, Cache an LLM response. Silently ignores all errors., Clear all cached entries for a tenant., Return True when the request should NOT be cached. Conditions: - Contains…, Per-tenant LLM response cache backed by Redis with in-process L1 dict. Usage:… (+21 more)

### Community 350 - "test_knowledge_base_pipeline.py"
Cohesion: 0.10
Nodes (43): _chunk(), _collection(), _fake_embedding(), KnowledgeCollection, Integration tests: Knowledge base pipeline — ingest, search, retrieval,…, metadata_filter restricts results to matching chunks only., delete_document() removes all chunks for a doc_id., Chunks ingested by tenant A are not returned by tenant B's search. (+35 more)

### Community 351 - "test_compliance_endpoint_gaps.py"
Cohesion: 0.07
Nodes (40): _make_app(), Any, FastAPI, Tests for app/api/enterprise.py compliance endpoints that are NOT yet covered.…, If the DB INSERT raises, the endpoint still returns status='recorded' (logs…, POST /compliance/consent with no legal_basis uses default 'legitimate_interest'., DELETE /compliance/consent/{purpose} without auth returns 401., DELETE without DB returns revoked status. (+32 more)

### Community 352 - "test_collab_api_comprehensive.py"
Cohesion: 0.11
Nodes (33): FakeCollabStore, _make_app(), Any, FastAPI, Comprehensive tests for app/collab API — supplements test_collab.py., Minimal fake for API-level tests., test_append_operation_returns_201(), test_append_operation_session_not_found_returns_404() (+25 more)

### Community 353 - "api/memory.py"
Cohesion: 0.16
Nodes (30): clear_all_memories(), create_memory(), CreateMemoryRequest, delete_memory(), delete_memory_by_id(), _get_db(), _get_ltm(), get_tool_reliability() (+22 more)

### Community 354 - "test_context_pipeline.py"
Cohesion: 0.06
Nodes (51): count_tokens(), Convenience function using the module-level tokenizer., Citation, CitationManager, Any, CitationManager — threads source citations through retrieved context., BudgetResult, ContextBudget (+43 more)

### Community 355 - "CodeExecutionWorkload"
Cohesion: 0.11
Nodes (25): CodeExecutionObservation, CodeExecutionWorkload, _FrozenCodeModel, BaseModel, field_validator, Sanitize bounded code observations before model exposure., sanitize_observation(), SanitizedObservation (+17 more)

### Community 356 - "test_knowledge_comprehensive.py"
Cohesion: 0.15
Nodes (23): _make_app(), Any, FastAPI, Comprehensive tests for /knowledge API endpoints — targets 21% → 55%+ coverage., Mock embedder that returns a fake embedding., _sample_embedder(), test_create_collection_list_returns_it(), test_create_collection_requires_auth() (+15 more)

### Community 357 - "ChatService"
Cohesion: 0.07
Nodes (48): ChatService, In-memory ChatService — suitable for unit tests and the in-memory app path. The…, Tests for rich output routing — 10 cases., session(), svc(), test_artifact_output_metadata(), test_message_metadata_goal_id(), test_message_metadata_intent_stored() (+40 more)

### Community 358 - "test_a2a_dispatch.py"
Cohesion: 0.08
Nodes (48): dispatch_internal_task(), Any, Internal agent-to-agent dispatch for civilization members. Uses the A2A…, Dispatch an A2A task internally via GoalService (not public HTTP ingress). This…, A2ATaskRecord, InMemoryA2ARepository, BaseModel, Idempotent durable-intent repository for A2A tasks and callbacks. (+40 more)

### Community 359 - "rpa.py"
Cohesion: 0.17
Nodes (29): close_session(), create_session(), execute_rpa_tool(), _executor(), generate_rpa_report(), get_current_view(), get_session_screenshot(), list_rpa_tools() (+21 more)

### Community 360 - "test_fakeredis_gaps.py"
Cohesion: 0.08
Nodes (40): _register_error_handlers(), asyncio, Coverage gaps for app/main.py — _FakeRedis sorted-set ops + _FakeLuaScript…, 2-key script: cost fits within both limits., 2-key script: second call accumulates on existing totals., 2-key script: raises GOAL_BUDGET_EXCEEDED when goal limit hit., 2-key script: raises DAILY_BUDGET_EXCEEDED when daily limit hit., 2-key script: limit=0 means unlimited (no enforcement). (+32 more)

### Community 361 - "test_agents_api.py"
Cohesion: 0.11
Nodes (27): MetaAgentConfig, _make_app(), Any, asyncio, FastAPI, Tests for /agents API endpoints., Can snapshot an agent and list versions., Can roll back agent to a snapshot. (+19 more)

### Community 362 - "test_communication_connectors.py"
Cohesion: 0.06
Nodes (17): call_tool(), _headers(), Any, Brevo (formerly Sendinblue) MCP server — email, contacts, campaigns.…, _import_server(), asyncio, Tests for Communication, Email & Marketing MCP connector servers. These are…, Every tool definition must have name, description, and valid parameters. (+9 more)

### Community 363 - "test_mfa.py"
Cohesion: 0.11
Nodes (37): _generate_recovery_codes(), _get_mfa_state(), _hash_recovery_code(), Sync helper — returns (and creates) the cache entry for *tenant_id*. Used…, Return RECOVERY_CODE_COUNT random codes formatted as XXXXX-XXXXX., SHA-256 hex digest of a normalised recovery code., Recovery code path returns verified status WITHOUT a session token. Only TOTP…, Using a recovery code removes it from the store. (+29 more)

### Community 364 - "test_celery_tasks_coverage.py"
Cohesion: 0.03
Nodes (113): _db_schedule_discovery_enabled(), _get_llm_provider(), Load the tenant's configured LLM provider from Redis. Uses a synchronous Redis…, TestGetLlmProvider, asyncio, MonkeyPatch, Coverage-focused tests for app/scaling/tasks.py utility functions. Targets the…, A cron schedule that is due fires a goal. (+105 more)

### Community 365 - "test_expression_engine.py"
Cohesion: 0.07
Nodes (17): ExpressionEngine, ExpressionEvalError, ExpressionSecurityError, ValueError, ExpressionEngine — safe expression evaluator for conditional branches. Uses…, Raised when a blocked construct is detected., Raised when expression evaluation fails., Evaluates conditional branch expressions safely. (+9 more)

### Community 366 - "_make_app"
Cohesion: 0.08
Nodes (9): _make_app(), FastAPI, Build a mock async DB session factory., TestControls, TestFeatureFlagGuard, TestNoDbDegradation, TestSocietyEndpoints, TestStreamEndpoint (+1 more)

### Community 367 - "test_connectors_comprehensive2.py"
Cohesion: 0.12
Nodes (32): _FakeRedis, _make_app(), _make_registry(), Any, FastAPI, TestClient, Extended tests for /connectors API — covers endpoints not in existing tests.…, Minimal in-memory async Redis stub for MCPRegistry tests. (+24 more)

### Community 368 - "_vec"
Cohesion: 0.09
Nodes (41): _ctx(), _mock_redis(), asyncio, After store, get_similar with same vector returns from L1., True semantic similarity: slightly different embedding (paraphrase) still hits.…, Completely different meaning → cosine < threshold → miss., Deterministic unit vector based on seed. Different seeds produce different…, Old get/set API still works with new cosine implementation. (+33 more)

### Community 369 - "test_full_stack_e2e.py"
Cohesion: 0.05
Nodes (7): client_and_key(), Full-stack E2E tests — exercises the complete request→agent→response cycle.…, Creates a tenant, returns (AsyncClient, api_key)., Core security: tenant A's goals are invisible to tenant B., Tenant isolation: agents are tenant-scoped., test_tenant_cannot_see_other_tenants_agents(), test_tenant_cannot_see_other_tenants_goals()

### Community 370 - "GoalAnalyticsAggregator"
Cohesion: 0.11
Nodes (40): GoalAnalyticsAggregator, Return cost aggregated by model from cost_ledger table., Computes analytics from the GoalService in-memory goal states. When ``db`` is…, _mock_goal(), datetime, Comprehensive tests for app/analytics/aggregator.py., When DB query fails, falls back to in-memory goal service., When DB returns empty rows, uses in-memory goals. (+32 more)

### Community 371 - "test_stream.py"
Cohesion: 0.16
Nodes (37): Any, SSE stream generator for the chat feature. Multiplexes two sources: 1. LLM…, Emit a clarify_needed event., Emit a hitl_required event — pauses goal execution until approved., Emit a schedule_created confirmation event., Emit an artifact_created event., Format a single SSE message., Simulate QA streaming — yields SSE events for each token. In production this is… (+29 more)

### Community 372 - "MarketplaceV2"
Cohesion: 0.13
Nodes (25): MarketplaceV2, DB-backed marketplace with atomic install, security review, and search.…, Create or update a template; optionally run security review., Upsert all built-in templates into DB (idempotent). Sources (in priority…, asyncio, Comprehensive tests for app/enterprise/marketplace_v2.py. Covers the 45% gap…, test_add_review_invalid_rating_rejected(), test_add_review_rating_avg_updates_in_memory() (+17 more)

### Community 373 - "test_agents_extra.py"
Cohesion: 0.19
Nodes (6): _make_app(), FastAPI, Extra coverage for app/api/agents.py — AgentStore methods, snapshot functions,…, TestAgentApiEndpoints, TestCloneEndpoint, TestSnapshotEndpoints

### Community 374 - "ContextEngine"
Cohesion: 0.12
Nodes (27): ContextBuildRequest, ContextEngine, ContextItem, ContextStrategy, estimate_tokens(), get_context_engine(), _jaccard(), _ngram_fingerprint() (+19 more)

### Community 375 - "test_condition_consumer.py"
Cohesion: 0.05
Nodes (47): ConditionTriggerConsumer, Any, Evaluates Family D (condition/state) triggers on the EVENT bus., _spec_of(), _decode(), event_channel_name(), EventTriggerConsumer, publish_trigger_event() (+39 more)

### Community 376 - "test_chat_api.py"
Cohesion: 0.10
Nodes (37): app(), client_with_tenant(), asyncio, Integration tests for chat API endpoints — 25 cases., test_chat_requires_auth(), test_create_and_delete_template(), test_create_and_list_artifact(), test_create_and_list_folder() (+29 more)

### Community 377 - "_headers"
Cohesion: 0.06
Nodes (35): _headers(), Lines 400-409: publish_template_v2 runs security review., Lines 1014-1024: hipaa rerun., Lines 1130-1152: DB returns empty rows → returns []., Lines 244-258: streaming simulation events emitted correctly., Lines 205-214: stream_simulation resolves agent_config from agent_store., Lines 207-214: stream_simulation resolves agent from agent_store by agent_id., Lines 244-249: error during streaming yields simulation_error event. (+27 more)

### Community 378 - "AuditV3"
Cohesion: 0.11
Nodes (17): AuditV3, Immutable append-only audit log with complete hash chain. Every record…, Require dual control before break-glass authority can be exercised., Export audit records as JSON or CSV., Export chain evidence with a deterministic manifest for immutable retention., test_security_event_is_hashed_and_worm_export_is_verifiable(), test_unknown_audit_action_and_single_party_break_glass_fail_closed(), test_audit_v3_creates_hash_chain() (+9 more)

### Community 379 - "test_orchestrator.py"
Cohesion: 0.08
Nodes (50): _FakeThrottleRedis, _make_orchestrator(), asyncio, Tests for CivilizationOrchestrator — runtime loop, goal dispatch, debate, tick., The debate counter is incremented via the single metrics.record_debate helper., When society has no active members, orchestrator triggers spawn., Governor denying spawn → rejected response., Tick emits AGENT_RETIRED events for each auto-retired agent. (+42 more)

### Community 380 - "api/billing.py"
Cohesion: 0.15
Nodes (32): CheckoutRequest, create_checkout_session(), create_razorpay_order(), CreateOrderRequest, _get_razorpay(), get_subscription(), get_usage(), list_invoices() (+24 more)

### Community 381 - "TestSolutionsCatalog"
Cohesion: 0.08
Nodes (21): get_solution_detail(), install_solution(), InstallRequest, list_all_solutions(), BaseModel, get, Request, Solutions API — installable domain solution packages. (+13 more)

### Community 382 - "test_scopes_rbac.py"
Cohesion: 0.06
Nodes (42): ABACEvaluator, Evaluates attribute-based conditions attached to role assignments. Supported…, test_abac_evaluate_department_match_false(), test_abac_evaluate_empty_conditions_returns_true(), test_abac_evaluate_multiple_conditions_all_must_pass(), test_abac_evaluate_ownership_creator_match(), test_abac_evaluate_ownership_creator_no_match(), test_abac_evaluate_time_window_outside() (+34 more)

### Community 383 - "asyncio"
Cohesion: 0.12
Nodes (14): _inverse_confluence_create_page(), _inverse_github_create_issue(), _inverse_jira_create_issue(), _inverse_slack_send_message(), Delete a Jira issue that was created by the forward tool call., Delete a Confluence page that was created by the forward tool call., Delete a Slack message that was sent by the forward tool call., Close/delete a GitHub issue that was created by the forward tool call. (+6 more)

### Community 384 - "TenantOptimizationState"
Cohesion: 0.09
Nodes (22): Per-tenant, per-agent state stored in Redis. Key format:…, Atomically increment the goal completion counter. Returns new count., TenantOptimizationState, asyncio, Tests for SelfOptimizerV2 — all 4 critical bug fixes + Bayesian A/B testing.…, Fix 4: DEFAULT_MIN_GOALS must be 5, not 50., Fix 4: Experiment starts after 5 goals, not 50., Fix 1: apply_suggestion() must UPDATE agents SET config = :candidate_config.… (+14 more)

### Community 385 - "_make_app"
Cohesion: 0.09
Nodes (23): _make_app(), Any, FastAPI, FIX 2: /goals/batch/{ids}/status returns per-goal statuses, not a stub., FIX 2: unauthenticated request → 401., FIX 2: the old stub response key 'message' must NOT appear., FIX 3: lineage endpoint returns 401 without a valid API key., FIX 3: attempts endpoint returns 401 without a valid API key. (+15 more)

### Community 386 - "test_plan_runtime.py"
Cohesion: 0.10
Nodes (23): CostEstimate, PlanCostEstimator, PlanRiskAnalyzer, PlanTrace, PlanTraceEntry, Any, PlanTrace — observability trace for plan verification decisions. Records every…, Trace of PlanVerifier decisions for a goal execution. (+15 more)

### Community 387 - "ProvenanceRecord"
Cohesion: 0.10
Nodes (20): ProvenanceRecord, Any, ProvenanceExport, Any, ProvenanceExport — exports provenance records in various formats., ProvenanceLedger, Any, ProvenanceVerifier (+12 more)

### Community 388 - "api/test_connectors.py"
Cohesion: 0.11
Nodes (30): _FakeRedis, _make_app(), Any, asyncio, FastAPI, MonkeyPatch, Tests for /connectors API endpoints., OAuth callback must return 503 (not a fake success) when oauth_manager is not… (+22 more)

### Community 389 - "workflows.py"
Cohesion: 0.10
Nodes (37): create_workflow(), delete_workflow(), generate_workflow(), GenerateWorkflowRequest, _get_store(), get_workflow(), list_workflows(), _plan_to_canvas() (+29 more)

### Community 390 - "test_document_parser_comprehensive2.py"
Cohesion: 0.10
Nodes (37): _parser(), asyncio, Comprehensive tests for DocumentParserTool — all formats, truncation, error…, test_execute_async_returns_parsed_result(), test_execute_async_with_file_path(), test_parse_csv_basic(), test_parse_csv_empty(), test_parse_csv_large_truncated() (+29 more)

### Community 391 - "AgentRouter"
Cohesion: 0.13
Nodes (36): AgentRouter, Route a natural-language goal to the most appropriate registered agent. Scoring…, _make_agent(), Comprehensive tests for app/agent/router.py — targets 90%+ statement coverage., When top-2 agents differ by < 0.1, mode = needs_human_choice., test_route_all_scores_populated(), test_route_good_match_returns_agent(), test_route_llm_provider_exception_is_swallowed() (+28 more)

### Community 392 - "costs.py"
Cohesion: 0.14
Nodes (36): _anomaly_id(), _anomaly_type_label(), _cost_tracker(), get_anomalies(), get_budgets(), get_cost_by_model(), get_cost_summary(), get_cost_trends() (+28 more)

### Community 393 - "PermissionCache"
Cohesion: 0.08
Nodes (33): PermissionCache, Any, Redis-backed permission cache for API key scope resolution. Cache key:…, Redis-backed permission set cache with 5-minute TTL., Return cached scope set or None on cache miss., Store scope set with TTL. An EMPTY set is never cached. An empty resolution is…, Drop every cached permission set (all tenants). Called on startup so a stale…, Remove a single permission cache entry. (+25 more)

### Community 394 - "test_hosted_reranker.py"
Cohesion: 0.12
Nodes (30): Cheapest configured reranker model, else *fallback*., resolve_rerank_model(), hosted_reranker_from_settings(), HostedReranker, HostedRerankerError, is_hosted_reranker_configured(), Any, RuntimeError (+22 more)

### Community 395 - "test_costs_comprehensive.py"
Cohesion: 0.14
Nodes (36): _make_app(), _make_tracker(), Any, FastAPI, Comprehensive tests for /costs API endpoints — targets 40% → 90%+ coverage., Verifies MODEL_PRICING is exposed correctly., test_get_anomalies_empty(), test_get_anomalies_no_tracker_returns_503() (+28 more)

### Community 396 - "test_voice_router.py"
Cohesion: 0.15
Nodes (27): client(), _make_app(), _make_silent_wav(), AsyncClient, asyncio, FastAPI, filterwarnings, Tests for the new Voice OS router — all 7 endpoints. Uses fast in-memory… (+19 more)

### Community 397 - "test_ip_allowlist_comprehensive.py"
Cohesion: 0.08
Nodes (36): IPAllowlistCache, is_ip_allowed(), Any, Redis-backed IP allowlist enforcement. Cache key: ip_wl:{tenant_id} Value: JSON…, Redis-backed CIDR allowlist cache with 60-second TTL. Falls back to a DB query…, Return active CIDR list for the tenant. Priority: 1. Redis cache (TTL=60 s) 2.…, Remove the cached allowlist for a tenant., Return True if ``client_ip`` is permitted by the CIDR allowlist. Rules: - Empty… (+28 more)

### Community 398 - "rag/engine.py"
Cohesion: 0.02
Nodes (131): BM25CorpusScorer, BM25Hit, Application-side Okapi BM25 corpus scoring., Search indexed chunks using BM25 scoring. Returns up to *top_k* results with…, Tokenize complete text into lowercase Unicode-safe words., Accumulate corpus statistics, then score documents without retaining them., _tokenize(), _bm25_search_persisted() (+123 more)

### Community 399 - "VoyageProvider"
Cohesion: 0.03
Nodes (51): _AsyncHTTPClient, LocalEmbedProvider, Any, EmbedRequest, Protocol, Local sentence-transformers embedding provider., Voyage embeddings over a cancellable async HTTP transport., VoyageProvider (+43 more)

### Community 400 - "test_executor_comprehensive.py"
Cohesion: 0.05
Nodes (69): RPA executor — executes browser automation commands via Playwright or…, RPAResult, assemble_report_from_results(), build_and_store_report_pdf(), _decode_data_uri_png(), _latin1(), Any, WS-5: RPA scrape → structured report → rendered PDF artifact. An RPA scrape run… (+61 more)

### Community 401 - "get_extractor"
Cohesion: 0.10
Nodes (29): get_extractor(), Any, Tests for OCR field extractors., test_aadhaar_extracts_dob(), test_aadhaar_extracts_masked_value(), test_aadhaar_extracts_number(), test_bank_extracts_account_number(), test_bank_extracts_balances() (+21 more)

### Community 402 - "FallbackChain"
Cohesion: 0.07
Nodes (28): ContextGapDetector, ContextGapDetector — detects 12 gap signal phrases defined in doc-2 §4., FallbackAttempt, FallbackChain, FallbackDecision, Any, FallbackChain — tracks fallback attempts per doc-2 §4 exact order., Return next available strategy, skipping those without required infra. (+20 more)

### Community 403 - "test_guardrails_comprehensive2.py"
Cohesion: 0.13
Nodes (35): _clean_store(), _make_app(), Any, FastAPI, Comprehensive tests for /guardrails API endpoints — targets 36% → 85%+ coverage., After 20 requests, the 21st should be rate-limited., Filtering by severity/layer/goal_id should work., Remove all in-memory configs/violations for the test tenant. (+27 more)

### Community 404 - "JiraIngestor"
Cohesion: 0.12
Nodes (7): JiraIngestor, Any, Jira issue ingestor via REST API v3., Convert Atlassian Document Format to plain text (recursive)., test_jira_adf_to_text(), TestJiraIngestor, TestJiraIngestor

### Community 405 - "test_agent_patterns_real_openai.py"
Cohesion: 0.03
Nodes (93): _build_celery_broker_url(), Celery application — task queues for goals, schedules, and maintenance., Build the Celery broker URL, adding Sentinel support when configured. Celery's…, # NOTE: these tasks register under their explicit ``workflow.*`` names, Tests for medium and low severity fixes., Mock server must auto-complete goals for SDK testing., Migration file for RLS fix must be named 0034_*.py., Beat schedule must not have two entries for stuck-goal detection. (+85 more)

### Community 406 - "test_all_providers.py"
Cohesion: 0.03
Nodes (74): NvidiaNIMProvider, NVIDIA NIM provider. NVIDIA NIM exposes an OpenAI-compatible API either via…, NVIDIA NIM provider. Supports NVIDIA's hosted models as well as self-hosted NIM…, _get_available_ram_gb(), _get_pull_lock(), OllamaProvider, Any, EmbedRequest (+66 more)

### Community 407 - "test_tasks_coverage_gaps.py"
Cohesion: 0.05
Nodes (45): Synchronous Redis-based distributed lock for Celery tasks. Uses ``SET NX PX``…, Return True if the lock was acquired; False if another worker holds it., Release the lock only if this instance owns it (atomic Lua check-and-delete)., Called once when the Celery worker process starts. Uses MemorySaver — goals…, _setup_worker_checkpointer(), _SyncGoalLock, _WorkerMCPAgentRunner, connect (+37 more)

### Community 408 - "KnowledgeRuntimeProfile"
Cohesion: 0.11
Nodes (22): ContextRuntimeProfile, KnowledgeRuntimeProfile, MultimodalRuntimeProfile, Any, Shared serialization mixin for named spec profile dataclasses. Converts enum…, Runtime profile for multimodal content ingestion (spec §3.1)., Self-improvement runtime profile (spec §3.2)., Context quality runtime profile (spec §3.3). (+14 more)

### Community 409 - "capabilities/test_capability_registry.py"
Cohesion: 0.15
Nodes (23): build_default_capability_registry(), CapabilityRegistry, RiskLevel, CapabilityResolver, CapabilityRegistry, RiskLevel, CapabilityKind, CapabilityProfile (+15 more)

### Community 410 - "Goal"
Cohesion: 0.08
Nodes (32): Goal, GoalCheckpoint, GoalEvent, Base, SQLAlchemy ORM models for goals and goal steps., Durable checkpoint payloads for future worker resume support., persist_audit_event(), persist_goal() (+24 more)

### Community 411 - "a2a/__init__.py"
Cohesion: 0.10
Nodes (18): AgentEvent, AgentResult, DelegationResult, OrgA2AClient, OrgAsAgent, Any, OrgAsAgent + OrgA2AClient — Q8 of spec. OrgAsAgent: Makes an org callable like…, # TODO: create mission and poll for completion (+10 more)

### Community 412 - "api/auth.py"
Cohesion: 0.18
Nodes (23): exchange_token(), get_sso_config(), Any, get, RedirectResponse, SSO authentication endpoints for Keycloak integration. Provides: - GET…, Redirect to Keycloak login page., Exchange authorization code for access + refresh tokens. (+15 more)

### Community 413 - "HealthCheck"
Cohesion: 0.17
Nodes (28): HealthCheck, HealthRegistry, Composable health checks. A :class:`HealthCheck` wraps an async callable that…, asyncio, Comprehensive tests for HealthRegistry and HealthCheck., test_all_failing_checks(), test_all_healthy_checks(), test_check_error_message_in_report() (+20 more)

### Community 414 - "test_prompt_optimizer_persistence.py"
Cohesion: 0.11
Nodes (27): PromptVariant, _make_db_mock(), _make_variant(), asyncio, Tests for PromptOptimizer DB/Redis persistence., PromptOptimizer must have persist and load methods., Build a (sync) db-factory mock whose session supports async with db() as s,…, PromptOptimizer must expose add_variant(). (+19 more)

### Community 415 - "SIEMAdapter"
Cohesion: 0.09
Nodes (29): build_siem_adapter(), NullSIEMAdapter, SIEM integration adapters for the AgentVerse audit system. Supported SIEM…, Return a concrete SIEM adapter for the given type string or enum value., Abstract base class — every adapter exposes a single ``send`` coroutine. Direct…, No-op adapter — used when SIEM is disabled or not configured. Always returns…, SIEMAdapter, Tests for SIEM adapters — ensures no direct base class instantiation and… (+21 more)

### Community 416 - "test_priority_queue.py"
Cohesion: 0.10
Nodes (23): ParallelExecutor, Parallel step executor — run independent agent steps concurrently. Uses…, Executes async callables concurrently up to a concurrency limit. Args:…, Priority, PriorityQueue, Priority queue for goal/task scheduling. Uses a Python heapq (min-heap on…, Thread-safe in-memory priority queue. Dequeues the highest-priority (lowest…, Task (+15 more)

### Community 417 - "test_domain_role_templates.py"
Cohesion: 0.06
Nodes (4): Domain-specific role templates for regulated industries. Pre-built role…, Comprehensive tests for domain_role_templates.py — all domains, role fields., All permissions should follow resource:action format., test_permissions_follow_colon_format()

### Community 418 - "monitoring/test_parsers.py"
Cohesion: 0.10
Nodes (30): AlertPayload, LogPatternMatcher, parse_cloudwatch_alarm(), parse_datadog_webhook(), parse_grafana_alert(), parse_pagerduty_webhook(), parse_sentry_webhook(), Monitoring alert parsers — Grafana, CloudWatch, Sentry, log_pattern. (+22 more)

### Community 419 - "test_enterprise_extra3.py"
Cohesion: 0.06
Nodes (30): FastAPI, Extra enterprise tests — pushes coverage from 64% to 85%+. Targets missing…, Lines 1234-1235: SCIM POST user → 401., Lines 1014-1024: soc2 rerun., Line 27: no auth → 401 on valid endpoint., Lines 213-214: agent_config is set → uses it directly (no store lookup)., Lines 228-258: stream_simulation uses agent_config override., Line 404-406: get_template_v2 returns 404 when svc.get_template returns None. (+22 more)

### Community 420 - "test_memory_comprehensive.py"
Cohesion: 0.11
Nodes (33): _make_app(), _make_memory_entry(), _no_db_session(), Any, FastAPI, Comprehensive tests for /memory API endpoints — targets 19% → 60%+ coverage., Prevent all tests from connecting to a real DB., test_clear_all_memories_no_ltm() (+25 more)

### Community 421 - "test_infrastructure_e2e.py"
Cohesion: 0.08
Nodes (34): E2E tests for infrastructure modules (K8s manifests, Grafana, Prometheus)., HPA manifests contain min/max replicas configuration., Grafana dashboard JSON is valid and has all required structure., Grafana dashboard panels have at least one with a datasource., Grafana dashboard provisioning YAML is valid., Prometheus rules YAML has groups with valid alert rules., Prometheus config scrapes the AgentVerse backend., Prometheus rules file has at least 3 alert rules. (+26 more)

### Community 422 - "test_schedules_api.py"
Cohesion: 0.13
Nodes (23): _AsyncCreateStore, _FakeAgentStore, _make_app(), Any, FastAPI, MonkeyPatch, Tests for /schedules, /nl, /webhooks, and /events API endpoints., test_create_schedule() (+15 more)

### Community 423 - "api/mfa.py"
Cohesion: 0.12
Nodes (34): begin_enrollment(), _check_rate_limit(), _check_rate_limit_global(), _check_totp_replay(), _cleanup_mfa_sessions(), complete_enrollment(), disable_mfa(), DisableRequest (+26 more)

### Community 424 - "test_marketplace_v2.py"
Cohesion: 0.08
Nodes (34): asyncio, Comprehensive tests for marketplace_v2.py. Covers: 1.…, When DB commit fails, install() returns error — no ghost agent persisted., Marketplace listing should still show built-in templates if DB reads fail., Frontend deploy sends `parameters`; backend must map it to params., Domains UI uses e-commerce; marketplace built-ins use ecommerce., Templates with injection patterns in goal_template are rejected., _check_scopes uses AND logic: passed iff BOTH conditions hold. Old (wrong)… (+26 more)

### Community 425 - "test_phase4_connectors.py"
Cohesion: 0.07
Nodes (34): asyncio, Tests for Phase 4: Connector Ecosystem Expansion., CodeInterpreter must execute Python and return stdout., CodeInterpreter must capture stderr., CodeInterpreter must return non-zero exit code for failing code., CodeInterpreter must return error for unknown language., FileOps must write and read files in tenant workspace., FileOps.list() must return files in workspace. (+26 more)

### Community 426 - "test_agent_builder.py"
Cohesion: 0.10
Nodes (31): CreateAgentRequest, model_validator, Legal agents must have bar_number in domain_metadata., _collect_routes(), _make_app(), Tests for Agent Builder fixes — covers all 8 fix areas., Backend rollback must accept snapshot_id as path parameter., NL creation must use list_async (DB-backed) for limit check. (+23 more)

### Community 427 - "RAFTDatasetRecord"
Cohesion: 0.12
Nodes (14): _build_dataset(), _dataset_content_fingerprint(), _export_jsonl(), PersistedRAFTChunk, RAFTDatasetRecord, RAFTExample, _example_record(), _validate_examples() (+6 more)

### Community 428 - "test_scope_seeder.py"
Cohesion: 0.08
Nodes (18): Any, Scope and builtin-role seeder. Runs once during ``lifespan`` to ensure the…, Upsert canonical scope definitions into the scope_definitions table. Safe to…, Ensure builtin role templates and scope definitions exist in the DB. Creates…, seed_builtin_scopes(), seed_scope_definitions(), Comprehensive tests for app/auth/scope_seeder.py., Verify that permissions list is passed as JSON-serialized string. (+10 more)

### Community 429 - "MarketplaceAgentContent"
Cohesion: 0.09
Nodes (18): _load_yaml(), Path, Content Loader — validates and seeds marketplace agents and goal templates from…, CLI check mode: print errors and return exit code., Load a YAML file and return a list of records., Load and validate all YAML content. Returns self for chaining., EvalFixture, GoalTemplateContent (+10 more)

### Community 430 - "test_tenants_comprehensive.py"
Cohesion: 0.17
Nodes (27): _default_svc(), _make_app(), Any, FastAPI, Comprehensive tests for /tenants API endpoints — targets 41% → 70%+ coverage., test_create_key_empty_name_invalid(), test_create_key_requires_auth(), test_create_key_success() (+19 more)

### Community 431 - "_svc"
Cohesion: 0.07
Nodes (11): Lines 307-311: RuntimeError when no event loop running., Lines 856-857: app_state.state.agent_store., Lines 1836-1841: cost_today_usd from cost_controller., _svc(), TestEvictAsync, TestGetAgentStore, TestGetMcpClient, TestHITLRejectionSubscriberSync (+3 more)

### Community 432 - "org/test_security.py"
Cohesion: 0.07
Nodes (26): LearningCategory, OrgLearningPipeline, OrgLesson, Any, StrEnum, PART 26 — Organizational Learning System. Learning categories (10 types) with…, Extract candidate lessons from a completed/failed mission., Anti-poisoning validation before promotion. (+18 more)

### Community 433 - "test_retrieval_engine.py"
Cohesion: 0.11
Nodes (26): Reciprocal Rank Fusion score from multiple ranked lists., _rrf_score(), asyncio, parametrize, Tests for Phase 4 — world-class RAG retrieval engine., Vector-DB-less mode: query_embedding=None triggers lexical-only path., When mode=vector, only vector leg is queried., test_generation_retrievers_use_resolved_model() (+18 more)

### Community 434 - "_sign"
Cohesion: 0.08
Nodes (17): Send HITL approval emails with signed approve/reject links. P1.3: Generates…, Return a 32-char HMAC-SHA256 hex digest for the (request_id, action) pair., Return True if sig is the correct signature for (request_id, action)., Send an HTML email with clickable Approve/Reject buttons. Returns True on…, send_approval_email(), _sign(), _verify(), test_approval_email_sender_importable() (+9 more)

### Community 435 - "test_governance_comprehensive2.py"
Cohesion: 0.11
Nodes (33): _make_app(), Any, AuditLog, FastAPI, Extended governance tests — covers endpoints not tested in…, Test simulate returns dict with simulation_results., test_approve_and_reject_flow(), test_approve_unknown_request_returns_404() (+25 more)

### Community 436 - "OcrEngine"
Cohesion: 0.08
Nodes (39): detect_ocr_format(), _ocr_model(), OcrEngine, Any, OCR engine: Tesseract primary with LLM vision fallback., Universal entry point: OCR text out of ANY input format (WS-6). Routes by…, Record the source format and flag a rasterization that yielded no pages., Convert an office document to PDF bytes via LibreOffice headless. Returns… (+31 more)

### Community 437 - "test_civilization_api.py"
Cohesion: 0.13
Nodes (35): _get_all_route_paths(), _get_openapi_paths(), _make_app_disabled(), _make_app_enabled(), Tests for /civilizations API endpoints — Phase E. All tests use the full…, Sign up a new tenant and return (client, headers). Skip on failure., Return all registered HTTP paths from the OpenAPI schema., Return paths from both HTTP routes and WebSocket routes. (+27 more)

### Community 438 - "get_logger"
Cohesion: 0.02
Nodes (105): _base_url(), call_tool(), Any, Alchemy MCP server — blockchain data, NFTs, and Web3 development. Environment:…, call_tool(), Any, Alpaca MCP server — commission-free trading, market data, and portfolio…, call_tool() (+97 more)

### Community 439 - "LargePayloadStore"
Cohesion: 0.10
Nodes (25): LargePayloadStore, Any, LargePayloadStore — offloads step outputs > threshold to object storage. Keeps…, Store large step outputs outside of LangGraph state., Return *value* unchanged if small, else store and return a ref pointer., If *value* is a ref pointer, fetch and deserialise; else return as-is., asyncio, Tests for LargePayloadStore. (+17 more)

### Community 440 - "test_connectors_comprehensive.py"
Cohesion: 0.11
Nodes (26): _FakeRedis, _make_app(), Any, FastAPI, Comprehensive tests for /connectors API endpoints — targets 18% → 55%+ coverage., Minimal in-memory async Redis stub for MCPRegistry tests., Returns (registry, server_id) after registering a connector., _reg_with_connector() (+18 more)

### Community 441 - "guardrails_v2/engine.py"
Cohesion: 0.10
Nodes (26): GuardrailRuleRow, Base, One guardrail rule, mirroring app.guardrails_v2.models.GuardrailRule., Guardrails 2.0 evaluation engine., Build a GuardrailRule from a COMPLIANCE_BUNDLES spec dict (string enums)., _rule_from_spec(), ComplianceBundle, GuardrailAction (+18 more)

### Community 442 - "MoAProposal"
Cohesion: 0.06
Nodes (58): MoAExecutionState, MoARuntime, Any, BaseModel, Checkpointed layered Mixture-of-Agents execution runtime., AggregationInput, build_aggregation_input(), BaseModel (+50 more)

### Community 443 - "api/model_registry.py"
Cohesion: 0.03
Nodes (101): ModelCapability, ModelEndpoint, ModelRoutePolicy, ProviderHealth, StrEnum, AI Router data models - Model Registry primitives., Real-time health metrics for a provider., A specific model endpoint with its configuration. (+93 more)

### Community 444 - "test_docker_compose.py"
Cohesion: 0.09
Nodes (33): _load_compose(), Validate docker-compose configurations are complete and correct., PostgreSQL 16 initializes roles with SCRAM credentials by default., The slim runtime has Python but intentionally does not install curl/wget., Alpine resolves localhost to IPv6 while this nginx config listens on IPv4., Every always-on service must be able to bind during a full-stack start., Celery control ping targets workers; beat is a scheduler, not a worker., Eight prefork children repeatedly exceeded the former 512 MiB limit. (+25 more)

### Community 445 - "test_self_optimizer_v2_comprehensive2.py"
Cohesion: 0.20
Nodes (33): _make_db_session(), _make_optimizer(), _make_redis(), asyncio, Additional tests for SelfOptimizerV2 to cover uncovered branches:…, test_create_experiment_returns_id(), test_generate_suggestion_exception_returns_none(), test_generate_suggestion_invalid_json_returns_none() (+25 more)

### Community 446 - "collab.py"
Cohesion: 0.16
Nodes (31): append_operation(), append_round(), close_session(), create_session(), CreateSessionRequest, delegate_task(), DelegationRequest, generate_crdt_token() (+23 more)

### Community 447 - "FastAPI"
Cohesion: 0.06
Nodes (28): _async_resolver(), _make_civ_app(), _make_schedules_app(), FastAPI, Targets missing paths in app/api/schedules.py., Lines 312: events_stream tenant auth works; generator starts., Targets uncovered exception paths in app/api/civilization.py., Lines 155-156, 169-170: exceptions in supervisor init are swallowed. (+20 more)

### Community 448 - "test_phase3_4_multimodal.py"
Cohesion: 0.11
Nodes (31): get_job_status(), _get_pipeline(), ingest_asset(), IngestRequest, Any, BaseModel, get, Request (+23 more)

### Community 449 - "ContentLoader"
Cohesion: 0.09
Nodes (13): ContentLoader, Any, Loads, validates, and seeds marketplace + template content from YAML files., get_all_source_use_cases(), Coverage gate: every UC listed in domain docs must map to ≥1 content record., Collect all source_use_cases from all YAML files., Every created agent/template should have at least one source UC reference., TestContentCoverage (+5 more)

### Community 450 - "execution_environment/models.py"
Cohesion: 0.07
Nodes (51): envelope_from_dict(), Envelope builder and HMAC integrity verification. The control plane signs the…, Reconstruct a signed envelope at the worker trust boundary., Isolated Agent Execution Environment. This package implements a containment and…, AuditLevel, CodeLanguage, CodeWorkloadMode, ExecutionEnvelope (+43 more)

### Community 451 - "test_execution_strategy.py"
Cohesion: 0.12
Nodes (36): is_seeded(), JsonReliability, latency_tier_for_ms(), LatencyTier, ModelCapabilityProfile, profile_for(), StrEnum, Model-capability-aware execution strategy. The execution strategy is chosen… (+28 more)

### Community 452 - "_make_app"
Cohesion: 0.08
Nodes (13): _make_app(), AuditLog, FastAPI, Emergency stop with no goal_service or redis still returns 200., TestAuditAdvanced, TestBudgetEndpoints, TestCostTracking, TestDbPolicyHelpers (+5 more)

### Community 453 - "test_phase8_9_guardrails_trust.py"
Cohesion: 0.12
Nodes (29): _baseline_rules(), Baseline BLOCK rules every tenant gets by default (defect 5). Rule ids are…, GuardrailRule, A single guardrail rule., _to_row(), _make_app(), asyncio, Phase 8+9: Guardrails 2.0 + Trust & Governance 2.0 tests. (+21 more)

### Community 454 - "WorkingMemory"
Cohesion: 0.09
Nodes (13): Any, Working memory — bounded short-term context window for the active agent run.…, A bounded FIFO queue of recent context items for the active goal. Parameters…, Add an item, evicting the oldest if capacity is exceeded., Return a copy of current items, oldest first., Return items as plain dicts suitable for prompt injection., Remove all items (call at goal start/end)., Return the N most recently added items, newest first. (+5 more)

### Community 455 - "test_otel.py"
Cohesion: 0.16
Nodes (20): _get_tracer(), Any, WorkflowOTELMiddleware — injects OTEL span attributes for workflow runs. Wraps…, Context manager that wraps a step execution in an OTEL span. Usage:: with…, Span wrapping an entire workflow run., run_span(), step_span(), _make_state() (+12 more)

### Community 456 - "SemanticChunker"
Cohesion: 0.04
Nodes (51): Chunk, Semantic text chunker with sentence-boundary, code-aware, and markdown-aware…, Split on markdown headings, then recursively chunk large sections., Split Python/TS code on function/class definitions., Chunks text into semantically meaningful pieces. Strategies: - 'text':…, Chunk text according to source type., Split on sentence boundaries, respect max_chars., SemanticChunker (+43 more)

### Community 457 - "test_raft_repository_integration.py"
Cohesion: 0.16
Nodes (31): _confirmation_binding_digest(), FineTuneCost, AsyncSession, Persist all RAFT state inside tenant RLS transactions., SQLRAFTRepository, test_fine_tune_cost_normalizes_currency_and_canonical_decimal(), _Database, _dataset() (+23 more)

### Community 458 - "router_runs.py"
Cohesion: 0.16
Nodes (31): cancel_run(), debug_run(), get_run(), get_step_result(), list_runs(), list_step_results(), pause_run(), Any (+23 more)

### Community 459 - "api/knowledge_graph.py"
Cohesion: 0.15
Nodes (30): add_edge(), add_node(), AddEdgeRequest, AddNodeRequest, export_graph(), extract_from_text(), ExtractRequest, find_path() (+22 more)

### Community 460 - "test_knowledge_comprehensive2.py"
Cohesion: 0.13
Nodes (32): _make_app(), _make_embedder(), Any, FastAPI, Extended tests for /knowledge API — targets 46% → 75%+ coverage., Without an embedder, ingest may succeed with zero-vector embeddings or return…, test_clear_cache(), test_create_and_list_collection() (+24 more)

### Community 461 - "HttpApiConnector"
Cohesion: 0.09
Nodes (30): _dig(), _extract_records(), HttpApiConnector, Any, BaseConnector, register, Perform the HTTP request and return the parsed JSON payload., Follow a dot-path into nested dicts; return None if any hop is missing. (+22 more)

### Community 462 - "test_enterprise_v2.py"
Cohesion: 0.09
Nodes (32): _make_checker_with_mock(), asyncio, Tests for Enterprise v2: compliance checking, GDPR, SAML, SCIM, contracts.…, FIX TEST: gdpr_compliant must not be hardcoded True in the response., HIPAA compliance requires BAA to be signed., BAA signed but no HITL PHI policy → partial, not compliant., Amendment 8.3: data_portability and consent_management must read from DB.…, All GDPR controls met → status = compliant. (+24 more)

### Community 463 - "asyncio"
Cohesion: 0.09
Nodes (16): asyncio, IMAP connection failure returns 0 gracefully., search returns empty → 0 processed., search returns non-OK status → 0 processed., Processes a simple plaintext email and submits it as a goal., Extracts text/plain body from multipart email., fetch returning non-OK status skips that email., Goal submission exception is caught; processing continues. (+8 more)

### Community 464 - "test_audit_scopes_limits.py"
Cohesion: 0.12
Nodes (10): CustomRole, CustomRoleStore, Custom Role System v2 ====================== Tenants can define their own roles…, A tenant-defined role with custom scope combination., Compute effective scope set (inherited + extra - denied)., Per-tenant custom role definitions., Resolve scopes for a role name — built-in or custom., Check if a role has a specific scope. (+2 more)

### Community 465 - "ConsensusVerifier"
Cohesion: 0.11
Nodes (15): ConsensusVerifier, Runs up to 3-way verification and returns a ConsensusResult. Falls back…, Any, Verifier calibration — tracks verdicts vs actual outcomes to measure false-…, Update a record with the actual outcome (from human eval or next replan)., Compute false-confirm rate across all resolved records. A *false_positive*…, Records verifier verdicts and eventual outcomes for calibration. Enables false-…, Record a verifier verdict. Returns the new ``record_id``. (+7 more)

### Community 466 - "test_guardrail_block_e2e.py"
Cohesion: 0.25
Nodes (9): _inline_secret_provider(), Any, FakeProvider, e2e_full: guardrail output enforcement blocks a leaking secret on the live…, Plans one step; the executor 'leaks' an AWS access key in its output., A fresh, UNCONFIGURED tenant; yields (client, tenant_id). Deliberately does NOT…, secret_tenant(), _SecretLeakProvider (+1 more)

### Community 467 - "test_templates_comprehensive2.py"
Cohesion: 0.11
Nodes (32): _make_app(), FastAPI, _TemplateStore, Comprehensive tests for /templates API — targets 56% → 80%+ coverage., Build app and swap module-level template_store with a fresh instance., submit=True without goal service should return 503., test_create_template_auto_extract_parameters(), test_create_template_empty_goal_text_invalid() (+24 more)

### Community 468 - "BoundedAsyncExecutor"
Cohesion: 0.14
Nodes (10): AbstractEventLoop, BoundedAsyncExecutor, Any, RuntimeError, _T, Dedicated executor with async admission before worker submission., CrossEncoderLoader, Future (+2 more)

### Community 469 - "ProviderCircuitBreaker"
Cohesion: 0.09
Nodes (14): call_with_circuit_breaker(), ProviderCircuitBreaker, Any, Circuit breaker for LLM provider calls., Per-provider circuit breaker to prevent cascading LLM failures., Return True if the circuit is open (provider unavailable)., Reset failure count and close the circuit., Increment failure count; open the circuit when threshold is reached. (+6 more)

### Community 470 - "test_nl_scheduler_comprehensive.py"
Cohesion: 0.10
Nodes (29): NLScheduler, _parse_single(), Converts NL trigger descriptions to TriggerSpecs. Primary path: LLM provider…, Comprehensive tests for app/triggers/nl_scheduler.py — targets the 48% baseline., Invalid JSON response should fall back to a ONCE TriggerSpec., LLM sometimes wraps JSON in markdown code blocks., Empty schedules array should produce empty list., Provider.complete should be called exactly once per parse(). (+21 more)

### Community 471 - "OrgMCPResources"
Cohesion: 0.09
Nodes (21): MCPPrompt, MCPPromptArgument, MCPPromptMessage, MCPResource, MCPResourceContent, OrgMCPPrompts, OrgMCPResources, Any (+13 more)

### Community 472 - "test_goal_classifier_doc4.py"
Cohesion: 0.21
Nodes (16): clf(), GoalClassifier, Tests for GoalClassifier — two-tier doc-4 exact implementation., test_analytical_domain(), test_creative_domain(), test_expert_architecture_goal(), test_high_confidence_obvious_goal(), test_high_risk_delete() (+8 more)

### Community 473 - "InjectionGuard"
Cohesion: 0.11
Nodes (13): InjectionGuard, Compiles INJECTION_PATTERNS at startup and provides O(n*patterns) scanning., DFS scan of arbitrary JSON/dict structures. Fixes the original flat-scan bug…, RecursiveArgScanner, Deep nesting must not raise; may or may not detect at cutoff., FIX: ROT13 was logically inverted. Decoded form must be scanned., scan_with_rot13 must also catch direct (non-ROT13) injection., Obfuscated violations must have elevated risk (original + 0.05). (+5 more)

### Community 474 - "audit_v3.py"
Cohesion: 0.10
Nodes (16): AuditRecord, AuditWriter, compute_entry_hash(), _hash_dict(), Any, Audit v3 — World-Class Immutable Audit System…, Compute the hash for an audit entry — MUST be deterministic., Append a complete, privacy-preserving governance event to the existing chain. (+8 more)

### Community 475 - "ExtractedField"
Cohesion: 0.14
Nodes (17): OcrExtractor, Protocol, OcrExtractor protocol., FinancialExtractor, _first_match(), _label_value(), Financial document field extractor (Invoice, Bank Statement, Receipt)., Extractor registry — get_extractor(doc_type) factory. (+9 more)

### Community 476 - "intent_router.py"
Cohesion: 0.11
Nodes (30): classify_intent(), _get_health(), handle_approve(), handle_create_mission(), _handle_status(), _handle_summarize(), IntentResult, Any (+22 more)

### Community 477 - "test_telegram_server.py"
Cohesion: 0.15
Nodes (23): call_tool(), _effective_chat_id(), _post_with_parse_fallback(), Any, AsyncClient, Telegram MCP server — interact with Telegram Bot API. Environment:…, Resolve the Telegram bot token from connector credentials (set via the…, Resolve the connector's configured default chat id (set via the Connectors UI),… (+15 more)

### Community 478 - "test_schedules_comprehensive.py"
Cohesion: 0.12
Nodes (31): _make_app(), _make_nl_scheduler_response(), Any, FastAPI, Comprehensive tests for /schedules API endpoints — targets 29% → 65%+ coverage., Use TestClient round-trip to create an agent then a schedule., Verify the SSE events endpoint responds without hanging (auth check only)., test_create_nl_schedule_missing_command() (+23 more)

### Community 479 - "test_store_comprehensive3.py"
Cohesion: 0.24
Nodes (25): _operation_to_dict(), _ctx(), _db_factory(), _full_db_session(), _make_session_row(), _mock_rls(), asyncio, Additional tests for collab/store.py — DB paths, _operation_to_dict,… (+17 more)

### Community 480 - "IdempotencyStore"
Cohesion: 0.12
Nodes (18): IdempotencyStore, Any, Redis-backed idempotency store for goal submissions., Return True if key is new (should process), False if duplicate., Release an idempotency key (e.g., if the request failed and should be retried)., Check if key exists without setting it., Prevents duplicate goal submissions using Redis SET NX with TTL. Keyed by…, IdempotencyStore.check_and_set returns False on duplicate key. (+10 more)

### Community 481 - "parse_verifier_verdict"
Cohesion: 0.12
Nodes (16): parse_verifier_verdict(), planner_schema(), PlannerPlan, PlanStep, Any, BaseModel, Pydantic response schemas for structured LLM output. Used by: - _node_plan:…, Return the JSON Schema for PlannerPlan (passed as response_schema). OpenAI… (+8 more)

### Community 482 - "_make_connectors_app"
Cohesion: 0.08
Nodes (16): _make_connectors_app(), Remaining connector paths., Line 887: saved++ after tool capability persisted., Lines 129, 302-304, 336-337, 351, 395-398, 434-435 in connectors.py., Line 129: _resolve_auth_value returns empty string when secret_resolver is None., Lines 302-304: register_connector fails when secret storage fails for a valid…, Lines 395-398: test_connector with 403 response sets auth_failed status., Lines 264, 302-304, 336-337, 351 in connectors.py. (+8 more)

### Community 483 - "AuditFlusher"
Cohesion: 0.11
Nodes (14): AuditFlusher, Any, Drains the Redis WAL into the ``audit_events`` Postgres table. Runs every…, Seed the in-process chain tip from the DB for *tenant_id*. Called once per…, Drain up to WAL_BATCH_SIZE events from Redis and insert to Postgres. Acquires a…, Attempt SETNX on the flusher lock. Returns True if acquired., Release the flusher lock., Core flush implementation (called under the flusher lock). (+6 more)

### Community 484 - "AgentManifest"
Cohesion: 0.14
Nodes (21): manifest_cmd(), Manage agent manifests. Usage: agentverse manifest validate agent.yaml, AgentVerse Developer SDK., AgentManifest, ConnectorRequirement, PolicySpec, Any, Versioned agent manifest — commit-able agent configuration format. Open source… (+13 more)

### Community 485 - "test_rls_behavioral_isolation.py"
Cohesion: 0.11
Nodes (29): Set ``app.tenant_id`` GUC for the duration of the calling transaction. Must be…, rls_context(), Connection, rls_context (asyncpg variant) is a callable., test_rls_context_is_callable(), _alembic(), _app_role_url(), _asyncpg_dsn() (+21 more)

### Community 486 - "test_conversational_consumer.py"
Cohesion: 0.08
Nodes (32): conversational_matches(), ConversationalTriggerConsumer, normalize_conversational_event(), publish_conversational_event(), Any, Conversational trigger consumer — Family C (2.W-1). Semantic ruling (user-…, Publish a normalized conversational event onto the EVENT bus (tenant stamped)., True if pattern is empty (no filter) or matches value; bad regex → False. (+24 more)

### Community 487 - "models/coordination.py"
Cohesion: 0.08
Nodes (29): ActiveClaimError, InMemoryLeaseRepository, LeaseClaim, LeaseManager, LeaseRepository, PostgresLeaseRepository, async_sessionmaker, AsyncSession (+21 more)

### Community 488 - "OrgLoopDetector"
Cohesion: 0.06
Nodes (37): ChaosResult, LoopDetection, LoopPattern, MissionEstimate, OrgLoopDetector, OrgSimulationEngine, OrgLoopDetector + OrgSimulationEngine — SUPPLEMENT G + I. OrgLoopDetector:…, Detect repeated tool calls (same tool called too many times). (+29 more)

### Community 489 - "test_self_optimization_v2_comprehensive.py"
Cohesion: 0.17
Nodes (24): _make_optimizer(), asyncio, Comprehensive tests for app/intelligence/self_optimizer_v2.py — the 51% gap.…, Create a SelfOptimizerV2 with an in-memory Redis mock and DB mock., Fix 4: threshold must be 5, not 50., control_config stored as JSON string must be deserialized., test_apply_suggestion_db_error_returns_false(), test_apply_suggestion_success_updates_db() (+16 more)

### Community 490 - "GoalExecutionLock"
Cohesion: 0.10
Nodes (23): GoalExecutionLock, Any, Redis-backed distributed lock for at-most-once goal execution., Redis SET NX PX lock ensuring at-most-once execution per cluster. Uses a Lua…, Returns True if lock acquired, False if another worker holds it., Release lock only if we own it (Lua atomic check-and-delete)., Extend TTL if we still own the lock., Check if any worker holds a lock for this goal. (+15 more)

### Community 491 - "_make_app"
Cohesion: 0.12
Nodes (8): _make_app(), FastAPI, TestCollectionCrud, TestKnowledgeSearch, TestOpenAPIIngest, TestSemanticCache, TestTextIngest, TestUrlIngest

### Community 492 - "MockMCPServer"
Cohesion: 0.12
Nodes (22): MockMCPServer, Any, MockMCPServer — implements MCP JSON-RPC in-memory for local development and…, Return all recorded tool calls (if logging enabled)., Enable recording of all tool calls for test assertions., In-memory MCP server implementing JSON-RPC 2.0 protocol. Use in tests and local…, Register a tool with a handler or fixture response., Set a fixture response for an already-registered tool. (+14 more)

### Community 493 - "UsageService"
Cohesion: 0.10
Nodes (14): Any, Lock, Usage Metering Service ====================== Emits usage_records for goals,…, Record a single tool call., Return usage summary for the last N days., Flush buffered records to DB. Serialised by an asyncio.Lock so concurrent fire-…, Records usage metrics to DB and/or in-memory buffer. DB writes are batched /…, Return the asyncio.Lock, creating it lazily on first use. (+6 more)

### Community 494 - "tenancy/billing.py"
Cohesion: 0.10
Nodes (18): BillingCycle, BillingPlan, BillingService, PaymentProvider, PlanLimits, StrEnum, QA3 — Billing & Payment Integration. Supports pluggable payment providers per…, Per spec QA3 — full billing record. (+10 more)

### Community 495 - "ArtifactTool"
Cohesion: 0.14
Nodes (26): ArtifactTool, Any, General-purpose artifact creation tool — agents can save any file as a…, Save text/binary content as a downloadable artifact., asyncio, Comprehensive tests for ArtifactTool — execute with/without store, to_tool_def., test_execute_content_type_custom(), test_execute_content_type_default() (+18 more)

### Community 496 - "router_hitl.py"
Cohesion: 0.18
Nodes (30): approval_stats(), bulk_decide(), BulkDecideRequest, decide_approval(), DecideRequest, delegate_all(), delegate_approval(), DelegateAllRequest (+22 more)

### Community 497 - "test_perception_comprehensive.py"
Cohesion: 0.14
Nodes (30): _make_action_result(), _make_app(), _make_screenshot_result(), Any, FastAPI, Comprehensive tests for app/api/perception.py — targets the 34% baseline., test_analyze_default_question(), test_analyze_invalid_url_returns_400() (+22 more)

### Community 498 - "ReflexionPattern"
Cohesion: 0.10
Nodes (13): Any, Reflexion: store failure lessons, recall for future goals., Store a failure lesson. Returns True if stored., Recall recent failure lessons for a tenant., Format lessons as a context block for the planner prompt., ReflexionPattern, test_reflexion_no_crash_without_store(), test_reflexion_recalls_relevant_lessons() (+5 more)

### Community 499 - "test_suggestions_shape.py"
Cohesion: 0.09
Nodes (30): _make_suggestion(), Tests verifying the /intelligence/suggestions endpoint returns the shape…, Every suggestion must have all 7 frontend-expected fields., Status must be one of 'pending', 'applied', 'rejected'., Confidence should be in [0, 1]., A list of suggestions should all have the correct shape., list_experiments result must contain frontend-expected fields., DB status 'completed' should map to frontend status 'concluded'. (+22 more)

### Community 500 - "ai_ops.py"
Cohesion: 0.15
Nodes (29): compute_drift(), ComputeDriftRequest, create_eval_dataset(), create_llm_judge(), CreateDatasetRequest, CreateJudgeRequest, get_regression_status(), list_drift_alerts() (+21 more)

### Community 501 - "_make_app"
Cohesion: 0.10
Nodes (14): _default_redirect_uri(), Build the default OAuth redirect URI from the FRONTEND_URL env var., test_default_redirect_uri_fallback(), test_default_redirect_uri_from_env(), _make_app(), FastAPI, Extra coverage for app/api/auth.py — SSO auth endpoints., In dev mode without keycloak_client_secret, uses fallback secret. (+6 more)

### Community 502 - "dpdp.py"
Cohesion: 0.13
Nodes (26): ConsentRequest, ErasureRequest, execute_erasure(), get_consents(), grievance_officer_contact(), Any, BaseModel, get (+18 more)

### Community 503 - "Any"
Cohesion: 0.18
Nodes (6): Any, Best-effort model id for a wired role provider (planner/executor/verifier)., The execution strategy for the current goal — chosen adaptively. Starts from…, Write step checkpoint to DB after each successful step., Persist decision trace record to DB (fire-and-forget via create_task)., Warn about steps that reference unknown tools. Scans each step description for…

### Community 504 - "SelfConsistencyPattern"
Cohesion: 0.06
Nodes (23): _most_common(), _normalize(), Any, Return the answer and aggregate-only voting evidence., Normalize response for comparison (lowercase, strip, first 200 chars)., Return most common response using normalized comparison., Sample N completions and return the majority-vote answer., Run prompt N times, return majority-vote answer. (+15 more)

### Community 505 - "TreeOfThoughtsPattern"
Cohesion: 0.10
Nodes (16): Any, Run Tree of Thoughts BFS on `problem`. Returns best reasoning chain + answer., Generate N independent thoughts via N parallel LLM calls., Tree of Thoughts: BFS over reasoning space, return best path., ThoughtNode, TreeOfThoughtsPattern, opaque_evidence_id(), Identify a private candidate without retaining its content. (+8 more)

### Community 506 - "estimate_cost"
Cohesion: 0.10
Nodes (26): estimate_cost(), format_cost(), LLM pricing table for accurate cost estimation. Uses per-1k-token pricing from…, Estimate USD cost for a completion. Matches model name fragment. ..…, Format cost for display: $0.0012 or $1.23., governance.pricing.estimate_cost must emit DeprecationWarning., test_pricing_estimate_cost_emits_deprecation_warning(), Tests for P0 governance fixes. (+18 more)

### Community 507 - "SelfOptimizerV2"
Cohesion: 0.07
Nodes (33): Any, Production-grade self-improvement engine with Bayesian A/B testing. Fix…, List A/B optimization experiments for a tenant (M-2). Returns records shaped to…, Turn low-scoring eval signals into concrete improvement actions. This is the…, Called after every goal completion. Drives the optimization loop., Fix 1: Apply candidate_config to the agent via a direct DB UPDATE. Was: called…, Manually apply a concluded experiment whose winner was never auto-applied. This…, Roll back to the control config from the experiment. (+25 more)

### Community 508 - "test_devops_connectors.py"
Cohesion: 0.03
Nodes (71): call_tool(), _client(), get_tools(), Any, AWS Lambda MCP server — manage Lambda functions via boto3. Environment…, call_tool(), _client(), get_tools() (+63 more)

### Community 509 - "OpenAIFineTuneProvider"
Cohesion: 0.06
Nodes (27): FineTunedInferenceProvider, FineTuneJobState, FineTuneProvider, build_raft_providers(), map_openai_status(), OpenAIFineTuneProvider, Any, OpenAI fine-tuning adapter for RAFT (D-8). RAFT (Retrieval-Augmented Fine-… (+19 more)

### Community 510 - "test_webhook_trigger_activation.py"
Cohesion: 0.08
Nodes (42): _cron_bounds(), datetime, Return (prev_occurrence <= now, next_occurrence > now) for ``cron`` in…, extract_triggers(), Any, Normalise workflow trigger declarations across the two on-disk shapes. A stored…, Return every trigger dict declared by ``definition`` (both shapes)., Return ``(cron, timezone)`` from a schedule trigger dict. Accepts the cron… (+34 more)

### Community 511 - "test_tools_comprehensive.py"
Cohesion: 0.14
Nodes (21): file_delete(), file_list(), file_read(), file_write(), Any, Tenant-scoped file operations. All operations are restricted to…, Alias for list() — for callers that prefer the explicit name., Read a file and return ``{"success": True, "content": ...}`` or error dict. (+13 more)

### Community 512 - "test_agents_comprehensive2.py"
Cohesion: 0.21
Nodes (29): _create_agent(), _make_app(), Any, FastAPI, TestClient, Extended tests for /agents API — covers endpoints not in existing comprehensive…, test_assign_knowledge_collection(), test_clone_agent_default_name() (+21 more)

### Community 513 - "ProspectiveMemoryService"
Cohesion: 0.11
Nodes (23): backfill_memory_rows(), BackfillCheckpoint, CheckpointWriter, LegacyMemoryRow, Protocol, Idempotent compatibility-memory backfill through the canonical repository., prospective_id(), ProspectiveMemory (+15 more)

### Community 514 - "test_mcp_circuit_breaker.py"
Cohesion: 0.09
Nodes (19): _bypass_ssrf(), client(), FakeRedis, asyncio, mock, Tests for MCP client circuit breaker wiring., The same circuit breaker instance is returned for the same server_id., Different server IDs produce distinct circuit breaker instances. (+11 more)

### Community 515 - "asyncio"
Cohesion: 0.08
Nodes (18): asyncio, Lines 125-132: HTTPStatusError → None., Lines 133-138: ConnectError → None., Lines 152-154: empty access_token → None., Lines 139-142: unexpected exception → None., Lines 197-198: no existing token → None., Lines 205-206: no resolved token_url → None., auth_config used when token_url/client_id not passed directly. (+10 more)

### Community 516 - "program_of_thought.py"
Cohesion: 0.19
Nodes (14): ProgramOfThoughtPhase, ProgramOfThoughtRuntime, ProgramOfThoughtState, Any, BaseModel, StrEnum, Single-program governed Program-of-Thought strategy., _observation() (+6 more)

### Community 517 - "TestAgentRuntimeModels"
Cohesion: 0.12
Nodes (14): AgentRole, AgentRunTrace, PlanStep, StrEnum, Agent Runtime 2.0 - formalized roles and execution models., A typed step in an execution plan., Complete execution trace for an agent run., A task assigned to a subagent. (+6 more)

### Community 518 - "MemoryConsolidator"
Cohesion: 0.05
Nodes (36): ConsolidationResult, _jaccard(), _keywords(), MemoryConsolidator, Any, Memory consolidation — compress large episodic memory sets into concise…, Group memories by keyword (jaccard) similarity. Public entry point for callers…, Greedy keyword-similarity clustering. (+28 more)

### Community 519 - "PatternConfig"
Cohesion: 0.06
Nodes (40): DynamicGraphAssembler, Translates PatternConfig into an AgentGraph instance., Any, GoalProperties, PatternConfig, Any, Complete pattern configuration for one goal execution. CRITICAL rule:…, Emit pattern_assembled SSE event (exact doc-4 shape). (+32 more)

### Community 520 - "_safe_eval_condition"
Cohesion: 0.10
Nodes (19): Any, Evaluate condition field. Returns True if step should run., Evaluate a condition expression safely using simpleeval or restricted eval.…, _safe_eval_condition(), _safe_eval_condition must reject dangerous expressions., _safe_eval_condition must evaluate normal comparisons correctly., test_safe_eval_condition_empty_returns_true(), test_safe_eval_condition_normal_comparison() (+11 more)

### Community 521 - "guardrails_v2.py"
Cohesion: 0.17
Nodes (26): CorpusSampleModel, create_rule(), CreateRuleRequest, enable_compliance_bundle(), evaluate_content(), evaluate_corpus(), EvaluateCorpusRequest, EvaluateRequest (+18 more)

### Community 522 - "test_code_rag.py"
Cohesion: 0.15
Nodes (21): boost_symbol_matches(), extract_code_symbols(), has_code_intent(), RetrievalResult, Code RAG — identifier/symbol-aware boosting on top of hybrid retrieval. Code…, Extract likely identifier/symbol tokens from a natural-language query. Order-…, Return whether a query is plausibly asking about source code., Re-rank hybrid-search candidates, promoting exact symbol occurrences. Chunks… (+13 more)

### Community 523 - "OAuthFlowManager"
Cohesion: 0.12
Nodes (29): OAuthFlowManager, Manages PKCE OAuth 2.0 authorization code flows., Comprehensive OAuth PKCE flow tests — covers all paths in app/mcp/oauth.py., Should complete silently when no DB factory is set., test_cleanup_expired_flows_removes_old_entries(), test_exchange_code_connect_error_returns_none(), test_exchange_code_empty_access_token_returns_none(), test_exchange_code_http_error_returns_none() (+21 more)

### Community 524 - "test_workflows.py"
Cohesion: 0.13
Nodes (26): _make_app(), FastAPI, Tests for /workflows endpoints — CRUD + run + tenant isolation., No GoalService on app.state → returns dry_run, does not crash., Build a minimal FastAPI app with the workflows router and in-memory store., Verify the generated goal string includes the workflow name., Tenant A cannot read, update, delete, or run Tenant B's workflows., test_create_workflow_defaults_empty_definition() (+18 more)

### Community 525 - "CapabilityRegistry"
Cohesion: 0.12
Nodes (18): CapabilityGapDetector, CapabilityRegistry, CapabilitySpec, GapReport, get_capabilities_for_role(), Capability Registry — 60+ named capabilities with tool/model/quality bindings.…, Detect capabilities required by a mission that are not in the registry., Return the capability list for a known role name, or [] if unknown. (+10 more)

### Community 526 - "OrgDigitalTwin"
Cohesion: 0.11
Nodes (20): CapacityPlan, OrgDigitalTwin, Any, Org Digital Twin — SUPPLEMENT H. A live simulation model of the organisation…, Simulate what resources and time a mission would require., Analyse current org capacity and predict when queued work clears., What-if analysis — "what if Legal dept was 2x faster?"., Result of a digital twin simulation run. (+12 more)

### Community 527 - "org/rbac.py"
Cohesion: 0.06
Nodes (38): AgentAnomalyDetector, enforce_org_role(), _highest_org_role(), OrgRBACGuard, OrgRole, Any, Request, PART 18 — Org-scoped RBAC Middleware. Org roles (per spec): org_admin — full… (+30 more)

### Community 528 - "fire_due_schedules"
Cohesion: 0.08
Nodes (38): _db_row_change_allowlist(), fire_due_schedules(), Operator-configured allowlist of tables DB_ROW_CHANGE may poll., A table is pollable only if it is a bare identifier AND allowlisted — so a…, DB_ROW_CHANGE fires when the tenant's row count in the watched table has grown…, Fire all cron/interval schedules that are due within the current minute., _row_change_fires(), _safe_db_table() (+30 more)

### Community 529 - "is_valid_transition"
Cohesion: 0.14
Nodes (6): is_valid_transition(), Check if a goal state transition is valid., is_valid_transition enforces the goal state machine., Verifier failure triggers re-execution., GoalTransition(str, Enum) members are str instances; .value gives the string., TestGoalLifecycle

### Community 530 - "TenantUserService"
Cohesion: 0.09
Nodes (14): Any, AsyncSession, StrEnum, QA1 — Tenant User & Role Management. Three-tier user hierarchy per spec:…, QA1 — Tenant user management service. Backed by in-memory store (production: DB…, Create an invite and send a magic link email. POST /v1/tenants/users/invite, Accept an invitation and create the user account., Per spec QA1 — full tenant user record. (+6 more)

### Community 531 - "router_versions.py"
Cohesion: 0.18
Nodes (28): ApprovalDecisionRequest, approve_publish(), clone_workflow(), diff_versions(), export_yaml(), get_version(), import_yaml(), list_versions() (+20 more)

### Community 532 - "_synthesize_goal_tree_results"
Cohesion: 0.08
Nodes (34): Synthesize sub-goal results into a coherent final answer using LLM. Falls back…, _synthesize_goal_tree_results(), _agent_source(), asyncio, Tests for semantic cache Redis API and goal-tree LLM synthesis. BUG 1:…, Synthesis must call the provider's complete() when sub-results exist., Synthesis falls back to joining results when no provider is supplied., Synthesis falls back gracefully when the LLM call raises. (+26 more)

### Community 533 - "test_analytics_db.py"
Cohesion: 0.09
Nodes (28): _make_db_factory(), Any, Tests for DB-backed aggregator methods: tool_metrics_db, cost_trends_db,…, DB-backed cost trends should return period+cost_usd dicts., cost_usd values should be rounded to 6 decimal places., No DB should fall back to in-memory cost_trends()., Empty DB result falls back to in-memory., Return a DB factory whose session.execute() returns the given rows. (+20 more)

### Community 534 - "test_civilization_extra4.py"
Cohesion: 0.03
Nodes (128): _civilization_not_found(), _make_app(), _make_db_mock(), Any, Exception, FastAPI, Extra tests for /civilizations API — push from 52% to 75%+ coverage. Targets…, Line 30: _civilization_not_found returns HTTPException 404. (+120 more)

### Community 535 - "SCIMHandler"
Cohesion: 0.13
Nodes (17): _db_row_to_scim_user(), Any, SCIM 2.0 user/group provisioning handler (RFC 7644). Handles automated user…, SCIM 2.0 user/group provisioning. Constructed per-request with tenant_id…, List tenant users in SCIM ListResponse format., Get a single user by SCIM external ID or internal DB id., Create a user from SCIM payload. Maps group memberships to roles via…, Update a user (PUT = full replacement, PATCH = Operations list). (+9 more)

### Community 536 - "test_hitl_new_endpoints.py"
Cohesion: 0.12
Nodes (25): client(), _make_app(), Any, AsyncClient, asyncio, FastAPI, Tests for new HITL endpoints from hitl-gap-analysis.md. Tests cover: G-04: GET…, Returns 401 when no tenant context (get_org_service NOT overridden). (+17 more)

### Community 537 - "test_store_final_coverage.py"
Cohesion: 0.09
Nodes (16): _CollectionStore, Create a collection in the explicit in-memory development store., Persist a collection before making it visible to the caller., _CallTrackerSession, asyncio, Final coverage push tests for rag/store.py — targets lines 286-299, 535-582., Compatibility startup sync never performs a cross-tenant query., Lines 542-544: key already in _data → skip. (+8 more)

### Community 538 - "test_voice_e2e.py"
Cohesion: 0.03
Nodes (67): Any, Protocol, Abstract STT and TTS provider protocols. Every concrete provider MUST satisfy…, Speech-to-Text provider contract., Text-to-Speech provider contract., Normalised STT output — same shape regardless of provider., STTProvider, TranscriptResult (+59 more)

### Community 539 - "TriggerType"
Cohesion: 0.11
Nodes (30): _build_dispatch(), dispatch_mechanism(), DispatchMechanism, is_supported(), Single source of truth for how every ``TriggerType`` reaches the runtime…, Return the dispatch mechanism for a TriggerType (accepts the enum or its string…, True when the trigger type has a real runtime dispatch path., TriggerType (+22 more)

### Community 540 - "test_agent_identity_layer.py"
Cohesion: 0.25
Nodes (10): build_manifest(), Any, Signed Agent Capability Manifests =================================== Every…, Build an unsigned manifest dict for an agent., Add an HMAC signature to the manifest., Verify the manifest signature., sign_manifest(), verify_manifest() (+2 more)

### Community 541 - "StreamingGuard"
Cohesion: 0.11
Nodes (9): GuardDecision, Streaming guardrail — mid-stream token-level content filter. Checks a rolling…, Rolling-buffer token-level content guard. Parameters ---------- patterns :…, Accumulate *token* in the rolling buffer and check patterns. Returns a…, Clear the rolling buffer between goals., Dynamically add a pattern. Returns True on success., StreamingGuard, TestStreamingGuard (+1 more)

### Community 542 - "start_policy_subscriber"
Cohesion: 0.10
Nodes (22): Task, Long-running coroutine: subscribe to policy_changes channel and reload on…, Start the policy change subscriber as a background task. Call this from main.py…, start_policy_subscriber(), Targets the pubsub listener loop in PolicyEngine., Lines 226–261: subscribe_to_changes reconnects on exception., Lines 242–255: processes policy change messages., TestPoliciesExtra (+14 more)

### Community 543 - "ClaimRepository"
Cohesion: 0.10
Nodes (32): ClaimRepository, datetime, RuntimeError, timedelta, Atomic in-memory reference implementation for fenced swarm claims., StaleFencingTokenError, GossipRouter, datetime (+24 more)

### Community 544 - "execution_environment/test_artifacts.py"
Cohesion: 0.11
Nodes (19): DurableExecutionArtifactStore, ExecutionArtifactStore, make_artifact(), Any, Protocol, Tenant-scoped durable artifacts for isolated execution., Adapter over the application's object store; never returns an empty reference., validate_artifact_name() (+11 more)

### Community 545 - "GitHubIngestor"
Cohesion: 0.13
Nodes (7): GitHubIngestor, Any, GitHub repository ingestor — crawls code/docs via GitHub REST API., TestGitHubIngestorHeaders, TestGitHubIngestorInit, asyncio, TestGitHubIngestorExtra

### Community 546 - "test_workflows_comprehensive2.py"
Cohesion: 0.26
Nodes (21): _create_workflow(), _make_app(), Any, FastAPI, TestClient, Extended tests for /workflows API — covers additional paths for 67% → 85%+.…, test_create_workflow_description_defaults_to_empty(), test_create_workflow_name_exceeds_max_returns_422() (+13 more)

### Community 547 - "SalienceScorer"
Cohesion: 0.11
Nodes (12): datetime, rank_memories(), Memory salience scoring and exponential decay. SalienceScorer computes a…, Reduce *current_score* by an exponential decay factor., Return *memories* sorted by salience score, highest first., Compute an importance score in [0, 1] for a memory entry. Higher scores surface…, Return a salience score in [0, 1]., SalienceScorer (+4 more)

### Community 548 - ".generate"
Cohesion: 0.15
Nodes (14): _build_summary(), DigestCache, DigestGenerator, DigestItem, _fmt_duration(), get_digest_generator(), Any, AsyncSession (+6 more)

### Community 549 - "test_greeting.py"
Cohesion: 0.12
Nodes (26): build_greeting_script(), jurisdiction_to_language(), Any, Voice greeting builder — synthesised on every org page load. Data sources…, D-5: Auto-detect TTS language from org jurisdiction field., Return WAV bytes for the login greeting using real org health data., Render a natural-language greeting from OrgService.get_org_health() data. Args:…, synthesize_greeting() (+18 more)

### Community 550 - "WorkflowVariableStore"
Cohesion: 0.16
Nodes (20): Any, WorkflowVariableStore — mutable workflow variables. Variables are distinct from…, Manages mutable vars in WorkflowState., Return a state update dict that sets the variable., Read a variable from state., Return all variables., WorkflowVariableStore, Tests for WorkflowVariableStore. (+12 more)

### Community 551 - "test_mission_flow.py"
Cohesion: 0.11
Nodes (17): OrgSelfImprovementEngine, PART 24: Drives the continuous improvement cycle for the org. Improvements that…, Advance proposal to next phase in cycle., Rollback a deployed improvement., make_org_id(), make_tenant_id(), asyncio, PART 47 — Integration test: End-to-end mission flow. Tests the complete mission… (+9 more)

### Community 552 - "RPAExecutor"
Cohesion: 0.04
Nodes (79): Any, Execute using a stateful Playwright session from session_manager., Executes RPA tool calls. Uses Playwright when available, falls back to…, Execute using a short-lived Playwright browser (no session manager). All 5 RPA…, WS-13: real page text over httpx when no browser is installed. This is the *no-…, Fetch ``url`` and return ``(cleaned_text, title)`` — no ``raise_for_status``.…, Execute an RPA tool command. ``allow_http_fetch`` opts a caller into the WS-13…, Simulated execution when Playwright is not available. (+71 more)

### Community 553 - "NLIChecker"
Cohesion: 0.04
Nodes (51): AttributionReport, AttributionVerifier, Attribution Verifier — verify that citations actually support the answer. For…, Extract citation numbers from text like [1], [2], [3]., Return sentences that contain `[citation_number]`., Jaccard similarity of 4+-character word sets., Verify that citations in an answer are grounded by the cited chunks. Uses…, Check whether each cited chunk supports the corresponding answer sentence.… (+43 more)

### Community 554 - "test_knowledge_api.py"
Cohesion: 0.19
Nodes (18): _make_app(), FastAPI, Tests for /knowledge API endpoints., POST /knowledge/ingest/file accepts plain text files., POST /knowledge/ingest/openapi creates chunks per endpoint., test_cache_stats(), test_clear_cache(), test_create_collection() (+10 more)

### Community 555 - "workflow/test_router.py"
Cohesion: 0.07
Nodes (51): PlanLimits, client(), make_app(), TestClient, Tests for the main workflow engine router (CRUD + publish + trigger)., test_add_permission(), test_analytics_summary(), test_create_workflow() (+43 more)

### Community 556 - "Phased Roadmap (9 layers → 47 components, TDD each)"
Cohesion: 0.07
Nodes (26): AgentVerse — World-Class Implementation Plan, Context, Data Model (PostgreSQL, all tenant-scoped via RLS), Domain-Agnostic Autonomy — the core promise, First Implementation Step (when approved), Guiding Principles (non-negotiable, enforced in code review), Phase 0 — Foundation & scaffolding, Phase 10 — Perception & collaboration (+18 more)

### Community 557 - "SearchDirectiveParser"
Cohesion: 0.09
Nodes (9): SearchDirectiveParser — parses [SEARCH:type:"query"] directives from plan steps., SearchDirective, SearchDirectiveParser, SearchDirectiveParser must extract directives from step descriptions., test_search_directive_maps_to_retrieval_strategy(), test_search_directive_parsed_from_step(), test_search_directive_web_source(), parser() (+1 more)

### Community 558 - "test_notification_service.py"
Cohesion: 0.19
Nodes (21): _make_service(), anyio, mock, Tests for app/services/notification_service.py — 8 tests using respx., Disabled channels do not receive notifications., A channel that raises must not prevent other channels from being notified., _send posts the full message JSON to teams URL., notify_approval_required posts to the Slack webhook URL. (+13 more)

### Community 559 - "emarsys_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), Any, Emarsys MCP server — marketing platform with contacts, campaigns, and…, _wsse_header()

### Community 560 - "generate_gst_invoice"
Cohesion: 0.12
Nodes (24): generate_gst_invoice(), _generate_invoice_number(), GSTInvoiceRequest, hsn_lookup(), list_invoices(), Any, BaseModel, field_validator (+16 more)

### Community 561 - "test_workflows_comprehensive.py"
Cohesion: 0.13
Nodes (27): _make_app(), Any, FastAPI, Comprehensive tests for /workflows API endpoints — targets 29% → 65%+ coverage., Without GoalService, run falls back to dry_run mode., Workflows from tenant A should not be visible to tenant B., _sample_definition(), test_create_workflow_empty_name_invalid() (+19 more)

### Community 562 - "test_knowledge_rpa.py"
Cohesion: 0.10
Nodes (17): Ingest one or more URLs scraped via the RPA executor (browser or httpx)., RpaUrlIngestRequest, _make_app(), _make_client(), _mock_httpx_client(), Backend tests for RPA URL ingestion, knowledge retrieval in agent loop, and…, WS-13: browser-less path returns REAL page text via the RPA executor., Response must contain expected fields for frontend consumption. (+9 more)

### Community 563 - "perception.py"
Cohesion: 0.18
Nodes (26): analyze_page(), AnalyzeRequest, batch_analyze(), BatchAnalyzeRequest, _browser_agent(), capture_screenshot(), extract_text(), ExtractRequest (+18 more)

### Community 564 - "TenantService"
Cohesion: 0.04
Nodes (48): In-memory implementation of tenant and API key management. All state is scoped…, TenantService, TenantService raises ConflictError on duplicate email., Email duplicate detection is case-insensitive., TenantService.get_tenant returns tenant profile., Valid non-expired API keys are resolved correctly., sync_from_db returns 0 when no DB factory is configured., Revoking own key deactivates it. (+40 more)

### Community 565 - "MemoryAPI"
Cohesion: 0.13
Nodes (17): _hex(), Memory, MemoryAPI, _now(), datetime, Memory management API layer over LongTermMemoryStore. Provides REST endpoints…, In-memory store backing the memory management REST layer. Production delegates…, api() (+9 more)

### Community 566 - "test_p3_phases.py"
Cohesion: 0.11
Nodes (14): GoalsListPage must submit workflow_mode='single_agent', not 'auto_route'., getEventLog must call /goals/{id}/events or /goals/{id}/replay, not /goals/{id}., test_frontend_api_client_getEventLog_uses_correct_endpoint(), test_frontend_workflow_mode_is_single_agent(), Return the frontend repo root, skipping the test if it is absent., require_frontend(), Tests for P3 phase implementations., test_alertmanager_endpoint_exists() (+6 more)

### Community 567 - "CoordinationStreams"
Cohesion: 0.12
Nodes (13): BackpressureError, CoordinationStreams, Any, Protocol, RuntimeError, Bounded Redis Streams transport for coordination event envelopes., The bounded stream cannot safely accept more entries., Publish and acknowledge unchanged envelopes using service-role groups. (+5 more)

### Community 568 - "test_configured_model_registry_api.py"
Cohesion: 0.14
Nodes (15): ModelRegistryStore, Any, Redis-backed persistence for user-registered ('configured') model overrides.…, Persists user-registered model overrides in Redis (sync client)., Add or replace an override, keyed by provider/model_id., set_model_registry_store(), _client(), _FakeRedis (+7 more)

### Community 569 - "test_a2a_extra2.py"
Cohesion: 0.08
Nodes (23): _get_task(), Fetch A2A task from DB or in-memory dict. When *tenant_id* is provided the…, _make_app(), asyncio, FastAPI, Extra coverage for app/api/a2a.py (Part 2). Targets uncovered lines: 91-99…, Lines 105-118: DB returns a row → dict returned., Lines 119-121: DB returns None → in-memory fallback. (+15 more)

### Community 570 - "RerankPolicy"
Cohesion: 0.06
Nodes (51): Any, CalibrationMethod, Resolve the concrete strategy, recording why it was chosen. Only AUTO is…, Attach a calibrated [0,1] confidence to each reranked chunk. Additive: it adds…, True Maximal Marginal Relevance reranking. Selects documents that are both…, Compute cosine similarity between two vectors., MMR using real embedding cosine similarity., Token-level Jaccard similarity as fallback. (+43 more)

### Community 571 - "OAuthState"
Cohesion: 0.10
Nodes (17): OAuthState, OAuth flow manager — handles authorization code + PKCE flows for MCP connectors., Initiate a PKCE OAuth flow. Returns the PKCE parameters and state token., Ephemeral state for an in-progress OAuth flow., Expired OAuth state tokens are rejected., test_oauth_state_expiry(), code_challenge must be URL-safe base64 of SHA-256(code_verifier)., test_oauth_state_alias_fields() (+9 more)

### Community 572 - "test_default_path_rerank.py"
Cohesion: 0.17
Nodes (23): apply_default_rerank(), is_enabled(), Any, RetrievalResult, Default-path reranking STAGE (WS-10 item 1). Historically cross-encoder / MMR /…, True when the default-path reranking stage is switched on in config., Rerank ``results`` for the default retrieval path. Returns the reordered list.…, _resolve_strategy() (+15 more)

### Community 573 - "ContextPipeline"
Cohesion: 0.05
Nodes (45): ContextPipeline, Any, Best-effort: ask the injected knowledge-graph source for facts relevant to…, Best-effort: tenant-scoped lookup against the injected semantic cache. An…, OutputContractBuilder, OutputSchema, OutputContractBuilder — builds output format contract for executor responses., Builds output format contracts with goal-aware auto-detection. (+37 more)

### Community 574 - "workflow_executor.py"
Cohesion: 0.14
Nodes (23): ExecutionCheckpoint, LoopExhaustedError, BaseModel, RuntimeError, ValueError, Bounded, cancellable, checkpointable execution for validated structured plans., A checkpoint cannot safely resume the supplied plan/runtime schema., A loop reached its bounded iteration ceiling without succeeding. (+15 more)

### Community 575 - "_resolve_checkpointer"
Cohesion: 0.10
Nodes (19): Return the best available checkpointer. Logs a WARNING if falling back to…, _resolve_checkpointer(), Test checkpointer resolution priority and RedisSaver wiring., When REDIS_URL is set, must attempt Redis before falling back to MemorySaver., MemorySaver warning must include LOST or RESTART so operators notice., A sync-only pre-wired saver is rejected (it would crash the async graph)., A pre-wired saver that implements the ASYNC checkpoint API is used as-is. The…, When no Redis is available, a warning is logged about durability loss. (+11 more)

### Community 576 - "test_phase2_model_registry.py"
Cohesion: 0.16
Nodes (17): ai_router_select(), _make_app(), TaskType, Phase 2: AI Router and Model Registry tests., test_active_model_prettifies_and_handles_unconfigured(), test_active_model_reflects_configured_default(), test_filter_by_capability(), test_filter_by_provider() (+9 more)

### Community 577 - "tasks.py"
Cohesion: 0.01
Nodes (295): GroundingChecker, Any, Checks whether step output claims are grounded in tool outputs. Two-pass:…, AnswerSynthesizer, Synthesizes a citation-carrying final answer from completed agent steps., Tenant, PostgreSQL Row-Level Security context manager. Sets the ``app.tenant_id`` GUC…, Set session for system-level maintenance, bypassing tenant RLS. Issues ``SET… (+287 more)

### Community 578 - "._evaluate_rule"
Cohesion: 0.11
Nodes (10): _injection_deobfuscation_hit(), Any, Return an obfuscation label if a de-obfuscated variant reveals an injection.…, Attach a persistence repository. When ``auto_persist`` is set, rules added at…, Evaluate content against all active rules for the given layer., Simulate guardrail evaluation without recording violations., Evaluate a single rule against content., Return a safe preview with sensitive data redacted. (+2 more)

### Community 579 - "PIIDetector"
Cohesion: 0.17
Nodes (8): OutputScanner, PIIDetector, Detects PII and sensitive credentials; optionally redacts them. Compliant with…, Scans LLM output before returning to caller. - PII detection + redaction. -…, SSN must be detected and redacted from the output., OutputScanner must redact SSN and set redacted_content., TestOutputScanner, TestPIIDetector

### Community 580 - "TestUniversalArgumentResolver"
Cohesion: 0.03
Nodes (42): Self-improvement optimizer v2 — fixes all 4 critical bugs. Bugs fixed from v1…, get_healer(), _normalise_key(), Any, Universal Tool Intelligence Layer ================================== Makes the…, Lowercase, camelCase→snake, remove all non-alphanumeric for comparison., Resolves LLM-generated argument dicts to match ANY tool's JSON Schema.…, Return a new arguments dict normalised to match tool_schema. (+34 more)

### Community 581 - "CodeWorkloadValidator"
Cohesion: 0.13
Nodes (18): CodeWorkloadLimits, CodeWorkloadValidator, Control-plane validation for bounded Python code workloads., _emit(), main(), _matches_schema(), Any, Dedicated minimal worker for validated code-interpreter workloads. (+10 more)

### Community 582 - "test_api.py"
Cohesion: 0.08
Nodes (7): app(), client(), Tests for the triggers API router., A type with no runtime dispatch path (google_sheets) must be refused so a…, A supported type (goal_completed → chain consumer) is accepted., test_create_accepts_supported_consumer_type(), test_create_rejects_unsupported_trigger_type()

### Community 583 - "EvalSuiteRunner"
Cohesion: 0.12
Nodes (13): EvalSuiteRunner, Attach an LLM judge for semantic quality scoring., Return suites with name, description, task_count, created_at., Targets DB persist and golden task functions in eval_suite.py., TestEvalSuiteExtra, _dataset(), Run the offline suite over a target and return its aggregate quality score., _run() (+5 more)

### Community 584 - "test_context_manager.py"
Cohesion: 0.12
Nodes (9): ContextBudgetManager, ManagedContext, Any, ContextBudgetManager — enhanced token budget with step-relevance reranking.…, Per-step context manager: dedup + token cap + relevance re-ranking., chunks(), manager(), ContextBudgetManager: dedup + token cap + step query relevance. (+1 more)

### Community 585 - "GuardrailViolation"
Cohesion: 0.14
Nodes (10): GuardrailResult, GuardrailViolation, Any, Scan plain text for injection patterns., FIX: Correctly decodes ROT13 first, then scans BOTH forms. The original…, Return (violations, redacted_text). redacted_text has PII replaced with…, Layer 4 — recursively scan tool call arguments before execution., Layer 5 — scan tool output for PII/secrets before passing to agent. (+2 more)

### Community 586 - "logging.py"
Cohesion: 0.02
Nodes (151): BaseConnector, ConnectionHealth, ABC, BaseConnector — the single interface every source adapter implements. LAW-01:…, Handle real-time push events (webhooks/notifications). Override for: S3 event…, True if validate_connection can also estimate doc count (LAW-22)., Return True if `name` passes include/exclude glob patterns. - If include is…, Result of BaseConnector.validate_connection(). (+143 more)

### Community 587 - "DocumentType"
Cohesion: 0.09
Nodes (8): DocumentClassifier, Document type classifier using keyword and regex scoring., Classify a document type from extracted OCR text using keyword/regex scoring., Return the most likely DocumentType for the given OCR text. Scoring: - +1 per…, DocumentType, StrEnum, classifier(), Tests for DocumentClassifier.

### Community 588 - "EntityVersionManager"
Cohesion: 0.11
Nodes (15): EntityVersion, EntityVersionManager, Any, StrEnum, SUPPLEMENT L — Versioning Strategy for all entities. Versioned entities: agents…, Manages versions for all org entities. In production: backed by DB table with…, Create a new version for an entity., Mark a version as deployed. (+7 more)

### Community 589 - "RedisCircuitBreaker"
Cohesion: 0.08
Nodes (15): Any, Record a failure. Opens the circuit once ``failure_threshold`` is reached., Record a success — resets the circuit to CLOSED and clears all counters., Circuit breaker backed by Redis for cross-replica state sharing. Args:…, Return the current circuit state from Redis., Return True if a call is allowed now (checks Redis state). Handles the OPEN →…, RedisCircuitBreaker, asyncio (+7 more)

### Community 590 - "LimitsV2Checker"
Cohesion: 0.12
Nodes (11): LimitsV2Checker, LimitsV2Config, Any, Limits v2 — Comprehensive Resource Quotas…, Checks all v2 limits with in-process counters + Redis when available., Check if step count is within plan limit., Check if token count is within plan limit., Check per-connector rate limit (in-process fallback). (+3 more)

### Community 591 - "_strip_secret_redis_schedule_fields"
Cohesion: 0.11
Nodes (15): _strip_secret_redis_schedule_fields(), test_strip_secret_redis_schedule_fields_case_insensitive(), test_strip_secret_redis_schedule_fields_removes_secrets(), TestStripSecretFields, test_strip_case_insensitive_field_names(), test_strip_preserves_non_secret_fields(), test_strip_removes_known_secret_fields(), test_strip_returns_empty_when_all_secret() (+7 more)

### Community 592 - "test_rag_migration_roundtrip.py"
Cohesion: 0.15
Nodes (22): CompletedProcess, _alembic(), _failed_owner_migration_state(), isolated_postgres(), owner_migration_postgres(), _owner_migration_state(), _owner_url(), TypedDict (+14 more)

### Community 593 - "test_perception_api.py"
Cohesion: 0.18
Nodes (24): app(), authed_client(), AsyncClient, asyncio, FastAPI, API-level tests for perception endpoints., Full app with perception router mounted (perception is not in the default…, Goal with just text (no image) is submitted normally via dry_run. (+16 more)

### Community 594 - "workflow/test_context.py"
Cohesion: 0.08
Nodes (3): Tests for ContextResolver — all {{...}} variable resolution., resolver(), state()

### Community 595 - "test_eval_suite_offline_discrimination.py"
Cohesion: 0.14
Nodes (20): _bad_target(), _dataset(), _good_target(), asyncio, Offline eval-suite hardening (Coverage-Matrix row 10). These tests prove the…, A known-good target scores strictly higher than a known-bad target. Uses the…, The bad target's failures are explained (missing tool / forbidden tool /…, Same FakeProvider verdict → identical scores every run (repeatable, no API). (+12 more)

### Community 596 - "test_batch8_servers.py"
Cohesion: 0.03
Nodes (136): call_tool(), Any, Anvil MCP server — PDF generation, form filling, and e-signature workflows.…, call_tool(), Any, Autopilot (Ortto) MCP server — marketing automation, journeys, and contact…, call_tool(), Any (+128 more)

### Community 597 - "rag/raft.py"
Cohesion: 0.13
Nodes (14): _compatibility_key(), datetime, RuntimeError, RAFTConcurrentUpdateError, RAFTCostPreview, RAFTError, RAFTModelUnavailableError, RAFTNotFoundError (+6 more)

### Community 598 - "voyager.py"
Cohesion: 0.15
Nodes (15): Any, BaseModel, Bounded governed Voyager curriculum and skill synthesis., VoyagerRuntime, VoyagerState, ProcedureContract, BaseModel, Versioned procedural-memory validation before every reuse. (+7 more)

### Community 599 - "ChatSearchEngine"
Cohesion: 0.14
Nodes (18): ChatSearchEngine, Full-text search across chat sessions using in-memory substring matching.…, Substring search over in-memory message store. In production this calls: SELECT…, Return messages matching *query* across all sessions for *tenant_id*., Return messages matching *query* within *session_id*., Return a short snippet with the query term highlighted., SearchResult, engine() (+10 more)

### Community 600 - "test_collaboration_runtime.py"
Cohesion: 0.14
Nodes (14): ClarificationEngine, ClarificationRequest, HumanDecisionTrace, Any, MissingInputEngine, MissingInputRequest, PreferenceCapture, PreferenceOption (+6 more)

### Community 601 - "api/test_collab.py"
Cohesion: 0.15
Nodes (15): FakeCollabStore, _make_app(), Any, asyncio, FastAPI, Tests for collaboration endpoints., Verify presence session creation via the full app stack., test_consensus_rounds_return_agreement_summary() (+7 more)

### Community 602 - "_make_db_mock"
Cohesion: 0.08
Nodes (29): _make_db_mock(), Exception, Lines 276-298: list_roles with DB (empty result)., Lines 276-298: list_roles with DB returns rows., Lines 297-298: DB exception raises HTTPException 500., Lines 319-337: create_role persists to DB., Lines 338-339: DB exception raises HTTPException 500., Lines 352-375: delete_role deletes row and returns 204. (+21 more)

### Community 603 - "test_celery_agentgraph.py"
Cohesion: 0.11
Nodes (23): _FakeAgentState, Any, Tests: Celery run_goal task creates AgentGraph for goal execution., Minimal AgentState stub returned by the mock runner., Configured agents fail closed when canonical graph assembly fails., AgentGraph should be constructed with result_processor, dedup_cache,…, The eager Celery path calls the canonical worker gateway from AgentGraph., run_goal should use the canonical AgentGraph. We patch AgentGraph with a… (+15 more)

### Community 604 - "check_and_process_emails"
Cohesion: 0.13
Nodes (17): check_and_process_emails(), _get_config(), _is_enabled(), Any, Email-to-goal: monitor an IMAP mailbox and convert emails to AgentVerse goals.…, Check IMAP mailbox and submit new emails as goals. Returns number of emails…, asyncio, Happy path: SSL IMAP, finds one UNSEEN email, submits as goal. (+9 more)

### Community 605 - "test_project_management_connectors.py"
Cohesion: 0.09
Nodes (10): call_tool(), _call_tool_inner(), _confluence_auth(), Any, Confluence MCP server — Confluence Cloud REST API integration. Environment…, Tests for project management MCP server connectors. Verifies that all PM…, Bonus servers: basecamp, wrike, clickup, smartsuite., Every tool in every PM server must have name, description, and parameters. (+2 more)

### Community 606 - "Any"
Cohesion: 0.09
Nodes (12): BudgetLimits, Any, Load budget limits from DB; return defaults if unavailable., Pure READ operation — never modifies Redis counters. This is the safe method to…, Record a real LLM call's token usage and compute/persist the cost. Returns the…, Update EWMA state and return a CostAnomaly if a spike is detected., Estimate cost before a goal runs. Pure read — no Redis writes. Preference…, Return per-agent cost breakdown for the given period. (+4 more)

### Community 607 - "get_builtin_server_configs"
Cohesion: 0.02
Nodes (86): get_builtin_server_configs(), Return configurations for all built-in MCP server wrappers., _build_worker_runner(), check_hitl_escalations(), cleanup_expired_runs(), execute_workflow_run(), fire_due_workflow_schedules(), _get_runner() (+78 more)

### Community 608 - "RetrievalEvaluator"
Cohesion: 0.13
Nodes (17): EvalReport, Any, QueryEvalResult, RAGAS-inspired retrieval evaluation for AgentVerse knowledge collections.…, Run evaluation on a collection. Args: collection_id: Target knowledge…, Evaluate a single query against the collection., Generate human-readable improvement recommendations., Evaluate retrieval quality of a knowledge collection. Usage:: evaluator =… (+9 more)

### Community 609 - "MarkdownParser"
Cohesion: 0.10
Nodes (17): _flatten(), JSONParser, JSON/JSONL parser — schema-aware flattening for structured data., Flatten a JSON object into key:value pairs., Parse JSON/JSONL into readable key:value text., MarkdownParser, Markdown parser — AST-aware section chunking., Parse Markdown into clean text, preserving heading structure. (+9 more)

### Community 610 - "SelfRefinePattern"
Cohesion: 0.11
Nodes (8): Any, Iterative self-improvement: generate → critique → refine, up to max_iterations., Refine `last_output` for `task`. Returns improved text or original if at max., SelfRefinePattern, test_self_refine_max_iterations(), _fake(), FakeProvider, TestSelfRefinePattern

### Community 611 - "PostgresWorkflowRunStore"
Cohesion: 0.06
Nodes (31): _as_obj(), _as_str(), _duration_ms(), _iso(), PostgresWorkflowRunStore, Any, Protocol, WorkflowRunStore — persistence for workflow runs and step results. Two… (+23 more)

### Community 612 - "_make_app"
Cohesion: 0.09
Nodes (28): aiter(), _default_compliance(), _default_red_team(), _default_simulation(), _make_app(), Any, Lines 1176-1182: no DB → 503., Lines 1207: no DB → 503. (+20 more)

### Community 613 - "test_golden_datasets.py"
Cohesion: 0.14
Nodes (15): add_golden_item(), create_golden_dataset(), DatasetCreateRequest, DatasetItemRequest, list_golden_datasets(), promote_goal_to_golden(), BaseModel, get (+7 more)

### Community 614 - "test_rpa_comprehensive.py"
Cohesion: 0.15
Nodes (24): _make_app(), _make_session(), FastAPI, Comprehensive tests for /rpa API endpoints — targets 29% → 65%+ coverage., Closing a session when no store exists returns 204 (graceful no-op)., TenantMiddleware requires auth for all routes., test_close_session_no_store(), test_close_session_not_found() (+16 more)

### Community 615 - "test_self_optimizer_v2_comprehensive.py"
Cohesion: 0.25
Nodes (22): _make_db_session(), _make_optimizer(), _make_redis(), asyncio, Comprehensive tests for SelfOptimizerV2 — TenantOptimizationState, Bayesian…, test_apply_suggestion_db_error_returns_false(), test_apply_suggestion_success(), test_get_arm_deterministic() (+14 more)

### Community 616 - "MCPWebSocketClient"
Cohesion: 0.08
Nodes (38): MCPWebSocketClient, Any, MCPWebSocketClient — WebSocket transport for real-time MCP tool servers. Used…, Call a tool via WebSocket RPC and await the result. Args: tool_name: MCP tool…, Subscribe to a real-time event channel. Yields data payloads from the channel…, List available tools on the WS MCP server., Async WebSocket client for MCP connectors that support WS transport. Usage::…, Open WebSocket connection. Returns self for use as async context manager. (+30 more)

### Community 617 - "test_audit_v2.py"
Cohesion: 0.05
Nodes (36): audit_admin_action(), Decorator that emits an AuditEvent for every admin route handler call. Captures…, LegalHoldManager, Any, datetime, Legal hold lifecycle management for AgentVerse audit events. A legal hold…, Release a hold and rebuild the cache from the remaining active holds., Return True if *resource_id* is under any active hold (O(1) via Redis). (+28 more)

### Community 618 - "TestAnswerSynthesizer"
Cohesion: 0.14
Nodes (12): Citation, CitedAnswer, Any, Citation-Carrying Answer Synthesis ==================================== After a…, Deterministic synthesis without LLM., A single citation linking a claim to its evidence source., Final synthesized answer with citations., Synthesize a final cited answer from the completed steps. If LLM provider is… (+4 more)

### Community 619 - "_cron_missed_runs_utc"
Cohesion: 0.12
Nodes (28): _cron_missed_runs_utc(), Return every cron fire slot that is due but unfired, as UTC-naive datetimes.…, rrule analogue of :func:`_cron_missed_runs_utc`. ``rrule_string`` is an…, _rrule_missed_runs_utc(), 2.W-9 (timezone slice): cron schedules must honour their stored timezone.…, test_cron_empty_timezone_defaults_utc(), test_cron_ist_schedule_maps_to_correct_utc_instant(), test_cron_unknown_timezone_falls_back_to_utc() (+20 more)

### Community 620 - "api/guardrails.py"
Cohesion: 0.17
Nodes (22): create_guardrail_config(), CreateGuardrailConfigRequest, delete_guardrail_config(), _get_engine(), guardrail_stats(), list_guardrail_configs(), list_violations(), Any (+14 more)

### Community 621 - "marketplace_monetization.py"
Cohesion: 0.16
Nodes (21): onboard_author(), OnboardAuthorRequest, PricingRequest, purchase_template(), Any, BaseModel, Request, Marketplace monetization — paid templates, Stripe Connect, author payouts. (+13 more)

### Community 622 - "ConversationContext"
Cohesion: 0.10
Nodes (12): ConversationContext, Any, ConversationContext — builds LLM context from chat history. Handles: - Last 20…, Prepend session system_prompt before all other turns., Prepend uploaded file content as a system turn., Inject top-5 codebase snippets as a system turn., Build LLM message lists from stored chat history., Return last MAX_TURNS messages as OpenAI-style message list. (+4 more)

### Community 623 - "ServicesAPI"
Cohesion: 0.14
Nodes (16): ConnectedService, _hex(), _now(), datetime, Connected services panel — REST layer over MCPRegistry. Provides endpoints to…, In-memory store backing the connected services REST layer. Production delegates…, Register a service and return an OAuth setup URL., ServicesAPI (+8 more)

### Community 624 - "test_optimizer_wired.py"
Cohesion: 0.18
Nodes (12): Verify SelfOptimizer and PromptOptimizer are wired and functional., AgentGraph must accept both optimizers as settable attributes., SelfOptimizer must generate suggestions when avg score < 0.5., apply_suggestion with change_type=increase_iterations must update config., select_variant must return control variant 70% of the time., After enough runs, maybe_promote promotes a clearly better challenger., _tenant(), test_agentgraph_accepts_self_optimizer_and_prompt_optimizer() (+4 more)

### Community 625 - "CommandScheduler"
Cohesion: 0.10
Nodes (12): CommandDeduplicator, CommandScheduler, Any, Command deduplication + scheduling — QA8 + QA9 of spec. CommandDeduplicator…, A command scheduled for future execution. Created by users via any channel with…, Manages scheduled commands (QA9). In production: backed by DB + Celery Beat.…, Store a scheduled command for future execution., Cancel a scheduled command. (+4 more)

### Community 626 - "CivilizationOrchestrator"
Cohesion: 0.06
Nodes (23): LearningPipeline, Any, Process a batch of pending candidates. This is the Celery-callable step.…, Score and decide on a single candidate. Returns…, Curated collective learning pipeline. Agents submit candidates. EvalRunner…, Write validated candidate to LongTermMemoryStore., Get learning records for the UI Learning Ledger., An agent submits a learning candidate. Returns candidate_id. (+15 more)

### Community 627 - "GoalRefinementPipeline"
Cohesion: 0.07
Nodes (15): GoalRefinementPipeline, Any, part 11 — Goal Refinement Pipeline. CEO Agent refines a raw user goal into a…, Synchronous goal refinement using heuristics. For LLM-assisted refinement, use…, Raise ValueError if goal contains injection patterns., Remove leading/trailing whitespace, collapse multiple spaces., Heuristic: split on conjunctions and numbered items., Generate measurable success criteria from requirements. (+7 more)

### Community 628 - "models/knowledge.py"
Cohesion: 0.16
Nodes (19): Document, ExecutionMemory, KnowledgeChunk1024, KnowledgeChunk1536, KnowledgeChunk3072, KnowledgeChunk768, _KnowledgeChunkMixin, KnowledgeCollection (+11 more)

### Community 629 - "calibrate_scores"
Cohesion: 0.16
Nodes (22): calibrate_scores(), _logistic(), _minmax(), CalibrationMethod, Probability calibration for retrieval and rerank scores. Retrieval and rerank…, Aggregate a retrieval set's scores into one calibrated confidence. Defaults to…, Map raw retrieval/rerank scores onto calibrated ``[0, 1]`` confidences. The…, retrieval_confidence() (+14 more)

### Community 630 - "LlmStructuredExtractor"
Cohesion: 0.11
Nodes (23): GeneralExtractor, LlmStructuredExtractor, Any, General fallback extractor — returns raw text and optional LLM-structured…, Fallback extractor — returns empty fields for unrecognized documents., Use an LLM to extract structured key-value fields from any document type. This…, Sync wrapper — returns empty (use extract_async for real results)., Parse LLM JSON response into ExtractedField dict. (+15 more)

### Community 631 - "build_result_artifact"
Cohesion: 0.16
Nodes (22): _artifact_status(), build_result_artifact(), _coerce_output(), _jira_rows(), Any, _tool_name(), tool_output raw dict takes priority over the sanitized output string., Backward compat: if tool_output not present, parse output dict as before. (+14 more)

### Community 632 - "SubTenantService"
Cohesion: 0.11
Nodes (16): Any, Base, QA4 — Sub-Tenants (Enterprise Hierarchy). Enterprise orgs need hierarchy: Acme…, QA4 — Sub-tenant management service. Production: backed by `sub_tenants` DB…, POST /v1/tenants/{id}/sub-tenants, GET /v1/tenants/{id}/sub-tenants, PATCH /v1/sub-tenants/{id}/budget, GET /v1/tenants/{id}/hierarchy — full tree view (+8 more)

### Community 633 - "test_backend_fixes.py"
Cohesion: 0.11
Nodes (16): _inr_to_usd(), Convert INR to approximate USD using the configurable exchange rate., _clear_mfa_state(), _enable_mfa_direct(), _make_mfa_app(), FastAPI, Tests for all backend world-class fixes. Covers: - MFA session tokens + rate…, MFA verify endpoint issues a session token on TOTP success. (+8 more)

### Community 634 - "test_civilization_api_comprehensive2.py"
Cohesion: 0.16
Nodes (23): _make_app(), FastAPI, Extended tests for /civilizations API — targets 31% → 75%+ coverage.…, test_control_pause(), test_control_resume(), test_control_throttle(), test_create_civilization_disabled(), test_create_civilization_no_db_graceful() (+15 more)

### Community 635 - "test_connectors_catalog.py"
Cohesion: 0.12
Nodes (16): FakeRedis, _make_app(), asyncio, Tests for catalog endpoint and auto-wiring., Test endpoint uses connector credentials and returns passed on success., Test endpoint returns failed when Jira returns 401., Unknown connector type uses generic GET fallback., test_catalog_is_configured_false_initially() (+8 more)

### Community 636 - "._node_plan"
Cohesion: 0.07
Nodes (17): Any, Any, Skill Selector ============== Selects the top 1-3 most relevant skills for a…, Selects relevant skills for a goal using keyword matching on trigger_hints.…, Return up to max_skills relevant skills for the goal., Build a compact skill context block to inject into the planner prompt., SelectedSkill, SkillSelector (+9 more)

### Community 637 - "test_phase10_11_ai_ops_memory.py"
Cohesion: 0.16
Nodes (23): _make_app(), Phase 10+11: AI Ops (Evals/Drift) + Agent Memory 2.0 tests., test_conflict_detection(), test_create_eval_dataset(), test_create_llm_judge(), test_create_memory_with_provenance(), test_drift_within_threshold_is_info(), test_eval_result_persisted() (+15 more)

### Community 638 - "HITLWorkflowGateway"
Cohesion: 0.20
Nodes (31): HITLWorkflowGateway, Workflow-specific HITL gateway — wraps the base HITLGateway., gateway(), asyncio, Tests for HITLWorkflowGateway — all 20 HITL features., Critical requests should appear before low priority in list., Without Redis, consume returns truthy payload., Deciding twice on same request is a no-op (idempotent). (+23 more)

### Community 639 - "BenchmarkStore"
Cohesion: 0.19
Nodes (8): BenchmarkStore, Load historical benchmark runs from DB., Benchmark store with DB persistence and in-memory cache., EvalScorecard, Covers line 102: trend stable when recent ≈ previous (within 0.05)., Exactly 6 runs with diff <= 0.05 → stable., _scorecard(), TestBenchmarkStoreEval

### Community 640 - "test_untested_modules.py"
Cohesion: 0.08
Nodes (13): MemoryConflict, MemoryProvenance, Provenance tracking for a memory entry., A detected conflict between two memory entries., Execute a skill against an LLM provider., Find best matching skill and execute it. Returns None if no match., SkillExecutor, A skill execution trace. (+5 more)

### Community 641 - "few_shot_cot.py"
Cohesion: 0.19
Nodes (16): FewShotCoTRuntime, Any, Bounded, tenant-scoped Few-Shot Chain-of-Thought adapter., StrEnum, ReasoningExample, ReasoningPhase, InMemoryReasoningExampleSource, Protocol (+8 more)

### Community 642 - "api/tools.py"
Cohesion: 0.15
Nodes (22): delete_file(), execute_code(), ExecuteCodeRequest, ExecuteCodeResponse, FileWriteRequest, list_files(), Any, BaseModel (+14 more)

### Community 643 - "trust_governance.py"
Cohesion: 0.19
Nodes (22): approve_request(), export_audit_evidence(), get_audit_integrity(), list_approvals(), list_compliance_bundles(), Any, get, Request (+14 more)

### Community 644 - "RuntimeProfilesRegistry"
Cohesion: 0.12
Nodes (15): get_runtime_profiles_registry(), Any, RuntimeProfilesRegistry — Layer 0 registry for all active GoalRuntimeProfiles.…, In-memory registry of active GoalRuntimeProfiles. In production this is a thin…, Register a GoalRuntimeProfile for a goal., Retrieve a registered profile. Returns None if not found., List all active profiles for a tenant., Remove a profile when goal completes. (+7 more)

### Community 645 - "test_spawn_tool.py"
Cohesion: 0.29
Nodes (15): execute_spawn_tool(), Any, spawn() — the governed tool exposed to agents to create child agents., Execute the spawn tool. Returns structured result for the LLM., _approved_verdict(), _denied_verdict(), _make_tenant_ctx(), asyncio (+7 more)

### Community 646 - "SharePointConnector"
Cohesion: 0.07
Nodes (20): Any, BaseConnector, register, List SharePoint sites accessible to the app. Parameters ---------- search : str…, Get metadata for a specific SharePoint site., List files in a SharePoint document library. Parameters ---------- site_id :…, Recursively list all files in a drive., Download file content as a UTF-8 string. Parameters ---------- site_id : str… (+12 more)

### Community 647 - "CRDTRoomManager"
Cohesion: 0.09
Nodes (19): collab_websocket(), CRDTRoomManager, org_presence_websocket(), _presence_send(), WebSocket, Track viewers of an org and broadcast join/leave to the others., Manages Yjs CRDT room connections. Uses Redis pub/sub when available for cross-…, Wire Redis client (called by app lifespan if Redis is configured). (+11 more)

### Community 648 - "test_ingestion_pipeline.py"
Cohesion: 0.11
Nodes (18): DocxIngestor, Any, PdfIngestor, Any, Extract text chunks from PDF files with page-level citation metadata., Tests for the knowledge ingestion pipeline — Phase P0.2., hybrid_search_db must return source_url, source_doc_id, page_number., test_docx_ingestor_graceful_fallback() (+10 more)

### Community 649 - "PlanMode"
Cohesion: 0.15
Nodes (34): ExecutionStrategy, PlanMode, The resolved, per-goal execution strategy. Always has a safe fallback., ToolMode, apply_exploration(), Adaptivity for the execution-strategy engine (P5). Refines a statically-…, Cold-start exploration: when a mode has no observed data yet, occasionally…, Refine a strategy from observed reliability rates — learns BOTH directions. *… (+26 more)

### Community 650 - "guardrail_engine.py"
Cohesion: 0.13
Nodes (11): CloudDestructionGuard, GuardrailAction, GuardrailSeverity, StrEnum, Six-layer guardrail engine for AgentVerse. Layers (evaluated in order): 1.…, Scans text for irreversible cloud/infrastructure destruction commands., _sev(), Pattern libraries for the AgentVerse guardrail engine. Contains 100+ injection… (+3 more)

### Community 651 - "KnowledgeAccessPolicy"
Cohesion: 0.12
Nodes (12): CollectionPolicy, KnowledgeAccessPolicy, Any, PART 15 — Knowledge Access Control (KnowledgeAccessPolicy). Controls which…, Return all collection IDs accessible to a department., Map a collection to its owning department., Filter RAG search results to only permitted collections., Access policy for a single knowledge collection. (+4 more)

### Community 652 - "test_wait_event_gate.py"
Cohesion: 0.22
Nodes (8): _FakePubSub, _FakeRedis, Any, 2.W-8: the ``wait`` step's event-channel path must actually subscribe to Redis…, test_test_run_short_circuits_without_redis(), test_wait_event_times_out_without_blocking_forever(), test_wait_resumes_on_published_event(), _wait_node()

### Community 653 - "ModelRouter"
Cohesion: 0.14
Nodes (10): Criticality, get_model_router(), ModelRouter, ModelSelection, Intelligent model router: task_type + criticality → best model + provider.…, Select the best model for a given task type and criticality level. Decision…, Select the best model for *task_type* at *criticality* level. Args: task_type:…, Return the module-level singleton ModelRouter. (+2 more)

### Community 654 - "test_tools_api_comprehensive.py"
Cohesion: 0.17
Nodes (22): _make_app(), FastAPI, Comprehensive tests for /tools API endpoints — targets 41% → 70%+ coverage., test_delete_file_not_found(), test_delete_file_requires_auth(), test_delete_file_success(), test_execute_code_javascript(), test_execute_code_requires_auth() (+14 more)

### Community 655 - "api/test_artifacts_comprehensive.py"
Cohesion: 0.22
Nodes (17): _make_app(), _make_artifact_row(), _make_db_factory(), Any, FastAPI, Comprehensive tests for /artifacts API endpoints — targets 16% → 60%+ coverage., Create a mock async DB session factory., test_delete_artifact_no_db_returns_404() (+9 more)

### Community 656 - "test_middleware_full.py"
Cohesion: 0.15
Nodes (19): _make_app(), FastAPI, Full coverage for TenantMiddleware and SecurityHeadersMiddleware., Paths in the bypass list (/health, /docs, etc.) require no auth., Protected endpoints without an API key return 401., Valid Bearer token in Authorization header is accepted., Valid X-API-Key header is accepted., An unrecognised API key returns 401. (+11 more)

### Community 657 - "ChatCodeExecutor"
Cohesion: 0.16
Nodes (17): ChatCodeExecutor, ExecutionResult, Inline code execution sandbox for chat sessions. Wraps the existing…, Execute short code snippets inside a sandbox. In dev/test mode uses subprocess.…, Run *code* synchronously and return the result., executor(), Tests for inline code execution — 10 cases., test_execute_bash_echo() (+9 more)

### Community 658 - "GoldenTask"
Cohesion: 0.11
Nodes (17): add_golden_task(), check_agent_rollout_gate(), get_golden_tasks(), GoldenTask, A verified (goal, expected_output) pair for regression testing. Accepts both…, Persist a golden task to DB., Load golden tasks for a suite from DB., Check if an agent meets the eval pass rate required for production rollout. (+9 more)

### Community 659 - "chat/service.py"
Cohesion: 0.11
Nodes (10): _Artifact, _Folder, _hex(), _now(), Any, datetime, ChatService — session CRUD, message dispatch, and streaming. This is the core…, Classify intent and return dispatch metadata (not the stream itself). Returns:… (+2 more)

### Community 660 - "LATSRuntime"
Cohesion: 0.26
Nodes (10): LATSRuntime, Any, Bounded Language Agent Tree Search (LATS) adapter., SearchNodeState, _node(), asyncio, test_cancellation_and_simulation_limit_are_typed(), test_completed_simulation_checkpoints_once_and_resume_number() (+2 more)

### Community 661 - "test_goals.py"
Cohesion: 0.19
Nodes (19): _make_app(), Any, asyncio, FastAPI, Tests for /goals endpoints., Pausing a completed (dry-run) goal returns 400 or 404., test_cancel_goal_returns_200(), test_get_goal_returns_status() (+11 more)

### Community 662 - "embeddings.py"
Cohesion: 0.07
Nodes (40): _collection_avg_similarity(), embed_texts(), EmbedRequest, get_embedding_health(), get_embedding_usage(), list_embedding_providers(), Any, BaseModel (+32 more)

### Community 663 - "run_mocked_certification"
Cohesion: 0.19
Nodes (16): ConnectorTarget, TypedDict, Any, _result(), run_mocked_certification(), run_static_certification(), _unknown_connector_result(), asyncio (+8 more)

### Community 664 - "CredentialInjector"
Cohesion: 0.15
Nodes (17): CredentialInjector, Any, Auto-fill vault:// references in RPA arguments from the tenant secret store., Resolve a vault:// reference to its plaintext value., Resolve all vault:// refs in an arguments dict (recursive)., asyncio, P1.2 tests: vault credential injection, CAPTCHA tools, takeover endpoint., RPAExecutor respects _credential_injector when set. (+9 more)

### Community 665 - "pools.py"
Cohesion: 0.12
Nodes (19): ConnectionPools, _default_http_factory(), _default_pg_factory(), _default_pg_ping(), _default_redis_factory(), _default_redis_ping(), Any, Centralized connection pools (Postgres / Redis / httpx). Lifecycle is owned by… (+11 more)

### Community 666 - "agent/test_router.py"
Cohesion: 0.16
Nodes (21): _add_agent(), _make_store(), Tests for AgentRouter — intent-based agent routing., Agents registered under tenant-b must not appear when routing for tenant-a., Agent whose goal_template overlaps with the goal text should be selected., When multiple agents are registered, the highest-scoring one wins., _score_by_history must return 0.0 when no eval_store is configured., Agent whose connector ID appears in the goal should be selected. (+13 more)

### Community 667 - "test_layer4_complete.py"
Cohesion: 0.06
Nodes (37): CitationThreader, Any, CitationThreader — attaches sequential citation indices to chunks., Any, QueryExpander, Generate multiple query phrasings for Fusion RAG (RRF across multiple queries)., LLM-driven query expansion for Fusion RAG. Falls back to rule-based., Any (+29 more)

### Community 668 - "VoiceAlertManager"
Cohesion: 0.12
Nodes (15): build_alert_text(), publish_voice_alert(), Any, D-6: Proactive Voice Alerts — push TTS audio when important events happen.…, Publish a voice alert to the tenant's pub/sub channel. Call this from anywhere…, Render a spoken alert text from event type + context., Listens to Redis pub/sub and synthesises TTS for proactive alerts. D-6:…, Start the pub/sub listener loop. (+7 more)

### Community 669 - "ToxicityClassifier"
Cohesion: 0.13
Nodes (7): Two-pass toxicity classifier. Pass 1: fast regex patterns → definitive for…, Pattern-only classification (no LLM, always sync-safe)., Full two-pass classification., ToxicityClassifier, ToxicityResult, TestToxicityClassifier, TestToxicityClassifier

### Community 670 - "test_ghost_run.py"
Cohesion: 0.18
Nodes (18): _counter_svc(), _make_app(), Any, FastAPI, Tests for POST /goals/ghost-run endpoint. Covers: 1. All strategies submitted…, When no strategies are provided, defaults are used and have required fields., Each strategy gets a unique goal_id — no duplicates., agent_id from the strategy request is forwarded to submit_goal. (+10 more)

### Community 671 - "ReflexionService"
Cohesion: 0.25
Nodes (6): Any, Canonical awaited Reflexion extraction, recall, and effectiveness service., ReflexionService, asyncio, test_reflexion_learning_recall_and_effectiveness_are_awaited_and_idempotent(), test_reflexion_without_evidence_is_quarantined_and_not_recalled()

### Community 672 - "InClusterKubernetesClient"
Cohesion: 0.13
Nodes (6): InClusterKubernetesClient, KubernetesClient, KubernetesRunnerHealthCheck, Any, Protocol, Small async Kubernetes REST client using the mounted service-account identity.

### Community 673 - "test_tool_risk.py"
Cohesion: 0.10
Nodes (20): Tests for the comprehensive tool risk classifier., Unrecognised tools must default to 'read' (safe)., test_confluence_get_page_is_read(), test_confluence_publish_page_is_write_high(), test_datadog_get_metrics_is_read(), test_db_drop_table_is_destructive(), test_empty_names_default_to_read(), test_generic_purge_is_destructive() (+12 more)

### Community 674 - "_make_app"
Cohesion: 0.20
Nodes (4): _make_app(), FastAPI, TestWorkflowCrud, TestWorkflowRun

### Community 675 - "test_api_extended.py"
Cohesion: 0.12
Nodes (12): app(), client(), create_trigger(), Tests for extended trigger API endpoints — PATCH, rotate-secret, validate-…, Create one of each major type and verify all appear in list., test_list_all_trigger_types(), test_patch_goal_template(), test_patch_pause() (+4 more)

### Community 676 - "test_safe_web_capability.py"
Cohesion: 0.28
Nodes (19): _build_capability(), _Policy, Any, parametrize, Security and production behavior for governed web retrieval., _request(), _searx_response(), test_governed_capability_bounds_rejection_audit_records() (+11 more)

### Community 677 - "insights.py"
Cohesion: 0.13
Nodes (28): analyze_failure(), estimate_goal(), EstimateRequest, get_agent_health(), get_benchmarks(), get_execution_graph(), natural_language_query(), NLQueryRequest (+20 more)

### Community 678 - "AgentCredentialStore"
Cohesion: 0.15
Nodes (7): AgentCredentialStore, Any, Check if this key's policy allows the given tool., Per-agent API key store. Agent keys: - Are scoped to one agent_id - Can only…, Create a new agent-scoped API key. Returns {key_id, raw_key, ...}., Validate key and return record, or None if invalid/expired., TestAgentCredentials

### Community 679 - "DelegationChain"
Cohesion: 0.14
Nodes (9): DelegationChain, DelegationLink, Any, Agent Delegation Lineage ========================= When agent A spawns agent B…, Create a new chain for a spawned sub-agent., One link in the delegation chain., The full chain from root user/system to the current executing agent., Human-readable: User:alice → Agent:CEO → Agent:Dev (+1 more)

### Community 680 - "test_tenants_comprehensive2.py"
Cohesion: 0.15
Nodes (26): _make_app(), _make_service(), Any, FastAPI, Extended tests for /tenants API — covers endpoints not in existing…, test_add_ip_allowlist_entry(), test_create_key_success(), test_create_role_no_db() (+18 more)

### Community 681 - ".run"
Cohesion: 0.13
Nodes (18): make_forwarding_callback(), make_isolation_event(), Any, Execution-environment event helpers. Utilities for wrapping raw agent-loop…, Wrap a raw agent-loop event dict into an :class:`ExecutionEvent`., Build an isolation-specific metadata event (not a core agent event)., Return an async callback that wraps events and forwards them downstream. The…, wrap_agent_event() (+10 more)

### Community 682 - "EmbeddingRouter"
Cohesion: 0.12
Nodes (12): EmbeddingConfig, EmbeddingRouter, Any, Embedding Router - vendor-agnostic embedding with fallbacks., Return embedding usage and error metrics for drift monitoring., Configuration for an embedding provider/model., Route embedding requests to the correct provider with fallback., Validate that the embedding dimension matches the collection's configured… (+4 more)

### Community 683 - "NotionConnector"
Cohesion: 0.08
Nodes (16): NotionConnector, Any, Return the page object (properties, created_time, url, etc.)., Search for all pages accessible to the integration token., Thin async wrapper around the Notion REST API v1., Query a Notion database and return all page objects., Return the plain-text content of a Notion page by fetching its blocks., EmailParser (+8 more)

### Community 684 - "scan_for_encoding_attacks"
Cohesion: 0.15
Nodes (12): decode_leetspeak(), normalize_homoglyphs(), Any, Encoding Attack Decoder ======================== Detects and blocks injection…, Replace homoglyphs with ASCII equivalents., Translate leetspeak to plain text., Try to decode potential base64-encoded content., Comprehensive encoding attack scan. Returns dict with: clean (bool),… (+4 more)

### Community 685 - "test_router_hitl.py"
Cohesion: 0.18
Nodes (20): client(), gateway(), make_app(), TestClient, Tests for workflow HITL router (approval inbox, decide, delegate, escalate)., _req(), test_approval_stats(), test_bulk_decide() (+12 more)

### Community 686 - "test_tenant_service_db.py"
Cohesion: 0.09
Nodes (21): Tests for TenantService DB persistence (no-op when db_session_factory=None)., DB persistence is attempted but doesn't break when factory raises in __aenter__., The dynamic resolver reads from app.state, not a captured closure., TenantService() without args has self._db == None., TenantService(db_session_factory=...) stores the factory., revoke_api_key still works (raises on bad key) with no DB factory., _db_persist_goal is a no-op when DB is None., _db_update_goal_status is a no-op when DB is None. (+13 more)

### Community 687 - "test_multi_agent_auto_selection.py"
Cohesion: 0.16
Nodes (20): MultiAgentSelection, Automatic per-goal selection of multi-agent patterns (WS-10 item 2). Supervisor…, Result of multi-agent auto-selection for one goal., Decide the multi-agent pattern(s) a goal warrants from its properties. Pure and…, select_multi_agent_patterns(), _gate(), _graph(), _profile() (+12 more)

### Community 688 - "OcrDocumentTool"
Cohesion: 0.17
Nodes (18): OcrDocumentTool, Any, OCR document extraction tool — agent-callable wrapper around OcrEngine., Extract text and structured fields from any document image or PDF. Accepts one…, Resolve input to (image_bytes, pdf_bytes). Raises ValueError if invalid., _make_result(), asyncio, Tests for OcrDocumentTool. (+10 more)

### Community 689 - "test_phase6_7_rag_runtime.py"
Cohesion: 0.16
Nodes (21): _certified_rag_registry(), _make_app(), MonkeyPatch, Phase 6+7: GraphRAG/RAG Platform + Agent Runtime 2.0 tests., test_create_and_get_run_trace(), test_create_execution_plan(), test_get_execution_plan(), test_list_agent_roles() (+13 more)

### Community 690 - "test_local_runner.py"
Cohesion: 0.08
Nodes (22): _encode_envelope(), _kill_process_group(), Any, ExecutionResult, Kill the entire process group to prevent orphaned grandchildren., Base64-encode the envelope payload for passing as an env var., Parse a JSON line and schedule forwarding to the event callback. Errors are…, _try_forward_event() (+14 more)

### Community 691 - "test_rss.py"
Cohesion: 0.18
Nodes (19): FeedEntry, fetch_rss_entries(), new_entries(), parse_feed(), Minimal RSS/Atom feed parsing for the RSS_FEED trigger poller (2.W-1).…, Parse RSS 2.0 (``<item>``) or Atom (``<entry>``) into FeedEntry list. Returns…, Entries whose id has not been seen before (preserves feed order)., Fetch and parse a feed URL. Network/parse failures yield [] (logged by the… (+11 more)

### Community 692 - "tool_allowed_for_autonomy"
Cohesion: 0.14
Nodes (13): get_tool(), list_tools_for_risk(), OrgToolSpec, Any, PART 17 — Org-level Tool Registrations. New tools available to agents operating…, Return tool names with risk level <= max_risk., Check if a tool is allowed at a given autonomy level (L0-L5). L2+: low-risk…, Convert OrgToolSpec to MCP tool definition format. (+5 more)

### Community 693 - "PolicyEvidenceEngine"
Cohesion: 0.13
Nodes (9): get_policy_evidence(), PolicyEvidence, PolicyEvidenceEngine, Return True if the decision has at least one evidence record., Evidence record supporting a policy decision., P1 — Every policy decision must have a justification chain. When the system…, Record a piece of evidence for a policy., Link a decision to its supporting evidence. (+1 more)

### Community 694 - "test_replay_comprehensive.py"
Cohesion: 0.14
Nodes (32): _get_db(), goal_timeline(), Any, get, Request, Goal execution replay API. Provides step-by-step reconstruction of a completed…, Get a compact chronological timeline of goal events for visualization., Reconstruct the full execution timeline of a completed goal. Returns a… (+24 more)

### Community 695 - "ConfluenceIngestor"
Cohesion: 0.14
Nodes (8): ConfluenceIngestor, _html_to_text(), Any, Confluence Cloud/Server page ingestor via REST API v1., Strip HTML tags, decode entities, normalize whitespace., test_confluence_html_to_text(), TestConfluenceIngestor, TestConfluenceIngestor

### Community 696 - "RedisBulkhead"
Cohesion: 0.17
Nodes (20): Redis-backed distributed bulkhead — enforces concurrency limits across ALL…, RedisBulkhead, asyncio, Tests for Redis-backed distributed bulkhead (RedisBulkhead +…, Acquiring below limit returns True., RedisBulkheadRegistry.get_bulkhead() returns RedisBulkhead when Redis set., RedisBulkheadRegistry without Redis falls back to asyncio.Semaphore., Acquiring at limit returns False. (+12 more)

### Community 697 - "test_gap_fill_phase2.py"
Cohesion: 0.05
Nodes (29): ComplexityScore, QueryComplexityScorer, Query Complexity Scorer — route queries to appropriate model tiers. Features…, Return a tier name for model routing., Score query complexity using lightweight lexical features. Thresholds…, Score *query* and return a :class:`ComplexityScore`., Any, Shadow Router — fire requests to a candidate model alongside the primary.… (+21 more)

### Community 698 - "coordination_handoffs.py"
Cohesion: 0.28
Nodes (17): accept_handoff(), cancel_handoff(), create_handoff(), CreateHandoffRequest, get_handoff(), HandoffTransitionRequest, _public(), Any (+9 more)

### Community 699 - "warm_permission_cache"
Cohesion: 0.15
Nodes (18): Any, Cache warmer: pre-populates the Redis permission cache at startup. Runs during…, Pre-warm the permission cache for recently-active tenants. Queries the…, warm_permission_cache(), Comprehensive tests for app/auth/cache_warmer.py., Should return early without any DB queries when redis is None., If the DB factory itself fails, the error is logged but not raised., Should return early without any Redis ops when db_factory is None. (+10 more)

### Community 700 - "test_worker_entrypoint.py"
Cohesion: 0.12
Nodes (23): _build_provider(), Construct the best available LLM provider for the given role. Supports…, Tests for worker_entrypoint — the subprocess execution entry point. Tests…, A tampered envelope (goal_text changed after signing) must be rejected., A valid signed dry-run envelope must emit goal_complete and return 0., Dry-run must short-circuit before the execution graph is constructed., Each role must get its own instance to prevent cross-role state sharing., sk-ant- prefix → try AnthropicProvider (may fail if not installed; falls back). (+15 more)

### Community 701 - "observability.py"
Cohesion: 0.16
Nodes (18): _evt_to_message(), get_structured_metrics(), get_timeseries(), list_logs(), Any, get, Request, StreamingResponse (+10 more)

### Community 702 - "_FakeSession"
Cohesion: 0.22
Nodes (6): _FakeSession, _noop_rls_context(), Any, Simulates an async SQLAlchemy session supporting raw SQL INSERT/SELECT. The…, Handle both INSERT (append_event) and SELECT (list_events) statements., test_event_store_append_and_list_preserves_payload_order()

### Community 703 - "magentic/adapter.py"
Cohesion: 0.23
Nodes (12): MagenticRuntime, Any, datetime, Checkpointed ledger-driven Magentic strategy runtime., ParticipantCandidate, ParticipantDecision, Deterministic policy-aware Magentic participant selection., select_participant() (+4 more)

### Community 704 - "test_entitlements.py"
Cohesion: 0.19
Nodes (11): assert_feature(), assert_limit(), check_limit(), Raise PermissionError if tenant's plan doesn't include the feature., Raise PermissionError if adding one more resource would exceed plan limits., Check if adding one more resource is within plan limits. Returns (allowed:…, _ctx(), Tests for Phase 1b entitlements system. (+3 more)

### Community 705 - "VoiceStreamingSession"
Cohesion: 0.17
Nodes (10): Any, ndarray, WebSocket, Record voice-processing consent for this session's speaker., Whether this session's speaker has recorded processing consent., Fail closed: refuse (and notify the client) when consent is absent., Send interim (non-final) transcript while user is still speaking., Process complete utterance: STT → IntentRouter → TTS. (+2 more)

### Community 706 - "skills.py"
Cohesion: 0.12
Nodes (17): create_skill(), delete_skill(), list_skills(), _platform_skill_to_response(), Any, BaseModel, delete, get (+9 more)

### Community 707 - "call_external_a2a_agent"
Cohesion: 0.15
Nodes (11): call_external_a2a_agent(), Any, Call an external A2A-compatible agent and wait for the result. Args:…, asyncio, A2A outbound calls to RFC-1918 private IPs must be blocked., A2A outbound calls to loopback must be blocked., A2A outbound calls to 192.168.x.x must be blocked., A2A outbound calls to 172.16.x.x must be blocked. (+3 more)

### Community 708 - "test_analytics_comprehensive.py"
Cohesion: 0.15
Nodes (25): AgentMetrics, Compute tool usage and reliability from goal events., Query tool call metrics from goal_events table in PostgreSQL. Falls back to in-…, ToolMetrics, test_agent_metrics_defaults(), test_tool_metrics_defaults(), _make_app(), _make_mock_aggregator() (+17 more)

### Community 709 - "test_workflows_extra2.py"
Cohesion: 0.08
Nodes (43): _create_workflow(), _fake_wf(), _make_app(), _make_db_factory(), FastAPI, SimpleNamespace, TestClient, Extra coverage for /workflows API — pushes workflows.py from 67.4% → 85%+.… (+35 more)

### Community 710 - "agent/test_persistence.py"
Cohesion: 0.15
Nodes (20): Derive retry ceilings and strategy identity from the admitted profile., make_config(), asyncio, Tests for GoalPersistenceEngine — agent retry and persistence logic., Agent fails once then succeeds., Agent always fails — should exhaust max_attempts., test_backoff_capped_at_max(), test_backoff_increases_with_attempts() (+12 more)

### Community 711 - "test_retrieval_strategies_comprehensive.py"
Cohesion: 0.03
Nodes (70): BM25Retriever, Any, Index a list of chunk dicts (must have 'content' and 'chunk_id')., The built-in scorer has no optional runtime dependency., Okapi BM25 retrieval over a collection of chunks. k1=1.5 (term saturation),…, ChildChunk, ParentChildChunker, ParentChunk (+62 more)

### Community 712 - "InMemoryMemoryRepository"
Cohesion: 0.12
Nodes (28): InMemoryMemoryRepository, Embedder, purge_expired_memories(), datetime, Protocol, Bounded prospective-memory execution used by Celery maintenance workers., Actively enforce memory retention: hard-delete a tenant's expired rows.…, SupportsPurge (+20 more)

### Community 713 - "test_cost_dashboard_api.py"
Cohesion: 0.06
Nodes (45): calculate_cost(), CostAnomaly, _fallback_pricing(), Cost tracking, budget enforcement, anomaly detection, and cost prediction. This…, Return recent anomalies for a tenant (reads from Redis EWMA state). This is a…, Return cost in USD for a given model + token counts. Uses the in-memory…, _make_db_with_rows(), _make_redis() (+37 more)

### Community 714 - "agent/test_errors.py"
Cohesion: 0.23
Nodes (17): classify_error(), ErrorClass, Exception, Structured error classification for agent execution., Classify an exception into an ErrorClass., Tests for ErrorClass and classify_error., ErrorClass members must be plain strings (StrEnum contract)., test_classify_auth_failed_401() (+9 more)

### Community 715 - "AgentTestHarness"
Cohesion: 0.16
Nodes (13): AgentTestHarness, Any, Run agent goals with mocked tools for testing. Usage: harness =…, Configure a mock response for a specific tool., TestResult, asyncio, Tests for AgentTestHarness., test_assert_tool_called_raises_when_missing() (+5 more)

### Community 716 - "orchestration.py"
Cohesion: 0.20
Nodes (17): ABTestResult, EvalScorecard, Base, SQLAlchemy ORM models for dynamic orchestration persistence. Tables:…, ReasoningPromotionDecision, ReflexionLesson, RegressionBaseline, RegressionCase (+9 more)

### Community 717 - "admin.py"
Cohesion: 0.20
Nodes (17): change_tenant_plan(), get_incidents(), get_platform_usage(), get_tenant_detail(), list_tenants(), Any, get, put (+9 more)

### Community 718 - "test_main_lifespan.py"
Cohesion: 0.16
Nodes (21): asyncio, Cover additional paths in app/main.py. Scope: _FakeRedis extended interface +…, A key set with ex=1 should appear expired after monotonic time passes., Concurrent budget checks must not both succeed when combined > limit., _get_lock() creates lock lazily on first call., test_fake_lua_script_is_atomic_under_concurrent_calls(), test_fake_redis_concurrent_zadd_is_safe(), test_fake_redis_delete_existing_key() (+13 more)

### Community 719 - "test_artifact_tool.py"
Cohesion: 0.17
Nodes (6): asyncio, Tests for ArtifactTool and related infrastructure., test_artifact_tool_definition_valid(), test_artifact_tool_handles_bytes(), test_artifact_tool_importable(), test_artifact_tool_returns_artifact_id()

### Community 720 - "test_real_simulation.py"
Cohesion: 0.10
Nodes (16): app(), authed_client(), Tests for real simulation runner with mock tools., After POST /simulation, the run can be retrieved by run_id., SimulationRunner returns a complete run with steps in the result., Each provided mock_tool produces a corresponding step in the result., Simulation works even with no mock tools; still produces a result., Can retrieve a simulation run by ID. (+8 more)

### Community 721 - "test_polling.py"
Cohesion: 0.16
Nodes (19): extract_path(), fetch_json(), poll_should_fire(), Any, HTTP polling helpers for the API_POLL trigger (2.W-1). The beat loop polls…, Resolve a minimal dotted JSONPath against a decoded JSON object. Supports…, Fire when the polled value changed since the last dispatch and — when an…, Fetch a JSON endpoint. Raises on transport/HTTP/JSON error (caller logs). The… (+11 more)

### Community 722 - "CeleryGoalTaskQueue"
Cohesion: 0.18
Nodes (5): CeleryGoalTaskQueue, Celery-backed enqueue adapter; status bridging is handled separately., Comprehensive tests for app/services/goal_queue.py — targeting 90%+ coverage., TestCeleryGoalTaskQueue, TestGoalTaskQueueProtocol

### Community 723 - "test_agent_advanced.py"
Cohesion: 0.22
Nodes (18): _make_app(), Phase 4 advanced agent tests: clone, readiness, and release gate., An agent with no connectors should have a failing readiness check., An agent with connectors and a goal template should have ready=True., fully-autonomous + eval_suite_id should create the agent successfully., bounded-autonomous mode should not require eval_suite_id., Clone without specifying a name should append '(copy)' to original name., _signup() (+10 more)

### Community 724 - "_svc"
Cohesion: 0.18
Nodes (15): asyncio, run_suite fails task when the expected phrase is absent from output., pass_rate equals passed_tasks / total_tasks., Build a MockGoalService with the given event dicts., run_suite with no tasks returns EvalSuiteResult with total=0., run_suite passes task when all conditions met (tool called + output present)., run_suite fails task when a required tool was not called., run_suite fails task when a forbidden tool was called. (+7 more)

### Community 725 - "_update_task_status"
Cohesion: 0.13
Nodes (14): POST task completion to callback URL., Update A2A task status in DB., _send_callback(), _update_task_status(), Update for a nonexistent task should not raise., No callback URL → no HTTP call, no error., test_send_callback_http_error_is_swallowed(), test_send_callback_noop_empty_url() (+6 more)

### Community 726 - "_fixture"
Cohesion: 0.01
Nodes (148): tenant_ctx(), tenant_ctx(), tenant_ctx(), graph(), provider(), tenant_ctx(), tenant_ctx(), tenant_ctx() (+140 more)

### Community 727 - "exceptions.py"
Cohesion: 0.16
Nodes (12): OrgDepartmentNotFoundError, OrgInvalidStatusError, OrgMissionNotFoundError, OrgNotFoundError, OrgTaskDepthExceededError, OrgTaskLimitExceededError, OrgTaskNotFoundError, Exception (+4 more)

### Community 728 - "wait_for_status"
Cohesion: 0.03
Nodes (89): _backends(), client(), collect_sse(), _migrated_backends(), Any, Session harness for the ``e2e_full`` tier. This tier proves the *wired*…, In-process httpx client bound to the booted app., A client whose ``X-API-Key`` header authenticates a seeded tenant. Mirrors the… (+81 more)

### Community 729 - "test_memory_learning_services.py"
Cohesion: 0.15
Nodes (15): ExperimentOutcome, LearningExperimentService, Durable-compatible sticky experiment assignment and promotion gates., ExperimentSpec, KnowledgeFact, KnowledgeGraphMemory, BaseModel, Tenant-owned evidence-linked knowledge graph memory lifecycle. (+7 more)

### Community 730 - "GDriveConnector"
Cohesion: 0.15
Nodes (11): GDriveConnector, GDriveSourceConnector, Any, BaseConnector, register, Download or export a Drive file and return its content as a string., Return metadata for a single Drive file., BaseConnector adapter routing Google Drive files through the real pipeline.… (+3 more)

### Community 731 - "LLMJudge"
Cohesion: 0.21
Nodes (16): LLMJudge, Score the goal execution result on multiple dimensions., LLM-as-judge scorer for semantic quality of goal execution. Evaluates:…, asyncio, Phase 17: LLM-as-judge eval tests., run_with_llm_judge should include LLM judge scores in the output., test_eval_suite_has_llm_judge(), test_eval_suite_runner_set_llm_judge() (+8 more)

### Community 732 - "api/analytics.py"
Cohesion: 0.26
Nodes (18): GoalMetrics, agent_analytics(), cost_analytics(), eval_analytics(), _get_aggregator(), get_spans(), goal_analytics(), list_traces() (+10 more)

### Community 733 - "Any"
Cohesion: 0.13
Nodes (9): Any, Resolve a single expression like 'steps.foo.output.bar'., Traverse nested dict/list by dot-separated parts., Resolve a single value. If not a string, return as-is., Recursively resolve all string values in a dict., Resolve any nested structure (dict, list, string)., Resolve a template string. If the entire string is a single {{...}} expression…, Any (+1 more)

### Community 734 - "_ctx"
Cohesion: 0.15
Nodes (9): _ctx(), Line 1158: _track_db_task on goal_failed., Lines 1086-1150: eval scoring on goal_complete., Lines 1126-1148: low score triggers self_optimizer., Lines 1179-1185: publish to Redis on goal_complete., Line 1192: events pushed to subscriber queues., Line 1840-1841: exception → 0.0., Line 1072: _track_db_task called when DB wired on goal_complete. (+1 more)

### Community 735 - "upsert_google_user"
Cohesion: 0.22
Nodes (10): Any, User management service — upsert, lookup, membership management., Upsert a Google-authenticated user and create a personal tenant. Returns…, upsert_google_user(), Base, User and TenantMembership ORM models., Global user identity (email-unique, cross-tenant)., User ↔ Tenant membership with role. (+2 more)

### Community 736 - "GraphFactory"
Cohesion: 0.28
Nodes (14): GraphFactory, Any, Canonical profile-before-compile factory for the local AgentGraph kernel., profile(), asyncio, D-2: a LOCAL-tier profile that selects the supervisor/debate strategies must…, services(), test_all_selected_existing_reasoning_patterns_compile_before_run() (+6 more)

### Community 737 - "decide_rollout"
Cohesion: 0.27
Nodes (14): CanaryEvidence, CertificationPolicy, decide_rollout(), BaseModel, datetime, field_validator, Quantitative, fail-closed promotion policy for agent-pattern canaries., Return a deterministic promotion decision; absent trust evidence always holds. (+6 more)

### Community 738 - "_make_guardrails_app"
Cohesion: 0.14
Nodes (10): _check_test_rate(), _make_guardrails_app(), Targets uncovered paths in app/api/guardrails.py., Lines 78, 93, 120-127, 170-179 in guardrails.py., Line 78: _require_tenant raises 401 when called without middleware tenant state., Line 93: _check_test_rate resets count when window has expired., Lines 120-127: list_guardrail_configs DB success → returns rows., Lines 170-179: create_guardrail_config DB success → returns new record. (+2 more)

### Community 739 - "test_guardrail_rules_persistence.py"
Cohesion: 0.16
Nodes (15): PostgresGuardrailRuleRepository, async_sessionmaker, AsyncSession, Durable, tenant-scoped store for GuardrailRule objects., Load rules. With ``tenant_id`` set, scope to that tenant under its RLS context;…, _alembic(), _app_role_url(), app_url() (+7 more)

### Community 740 - "test_production_safety.py"
Cohesion: 0.10
Nodes (19): Tests that production safety guards are in place., tool_inverses module accepts MCP client injection., get_inverse_fn returns no-op lambda for unknown tools., RedisCostController is importable., Slack uses env var SLACK_TENANT_ID, not hardcoded string., embed_texts() never returns random vectors — returns [] when no provider., SimulationRunner class is defined exactly once (no duplicate)., CostController accepts Redis client for cross-replica cost tracking. (+11 more)

### Community 741 - "AutonomyEnforcer"
Cohesion: 0.12
Nodes (10): AutonomyEnforcer, AutonomyLevel, AutonomyLevelConfig, SUPPLEMENT A — Autonomy Levels L0-L5 (Detailed). Enforces the autonomy level…, SUPPLEMENT A — Enforces autonomy level constraints on every action. Resolution…, Resolve effective autonomy level. Most specific wins., Check if an action is permitted at the given autonomy level. Returns (allowed,…, Return True if this action needs human approval at the given level. (+2 more)

### Community 742 - "test_untested_modules2.py"
Cohesion: 0.08
Nodes (21): AlertSeverity, DriftAlert, DriftType, EvalDataset, EvalResult, LLMJudge, StrEnum, AI Ops - observability, evals, regression, and drift models. (+13 more)

### Community 743 - "get_org_event_publisher"
Cohesion: 0.17
Nodes (16): get_org_event_publisher(), approve_org_request(), _OrgApprovalDecision, G-24: Approve an org approval gate. ``approval_id`` is the durable approval-…, G-24: Reject an org approval gate (durable, task-backed). Rejecting cancels the…, reject_org_request(), _MockService, asyncio (+8 more)

### Community 744 - "test_supervisor_debate_nodes.py"
Cohesion: 0.22
Nodes (18): DebateResult, _graph(), Coverage-Matrix row 1 (D-1/D-2): supervisor & debate patterns on the live…, No goal_service wired → supervisor cannot recurse; node no-ops gracefully., Guard against re-running the heavy supervisor pattern on replan loops., _state(), test_debate_enabled_via_runtime_profile_strategy(), test_debate_node_absent_by_default() (+10 more)

### Community 745 - "test_composer_llm_wiring.py"
Cohesion: 0.20
Nodes (14): Resolve the real LLM provider from the request's wired ``app.state``. The org…, resolve_llm_provider(), _CannedProvider, _composer_service(), Any, asyncio, Regression + behaviour tests for the org composer's LLM wiring (WS-2 last…, Minimal LLMProvider stub returning a fixed completion body. (+6 more)

### Community 746 - "test_live_platform.py"
Cohesion: 0.12
Nodes (16): live_client(), Phase 17: Live Platform Testing. Run against a real running backend with:…, Skills can be executed on real backend., Comprehensive check that no secrets appear in any API response., Backend health check passes., Model registry returns catalog without secrets., RAG query returns structured result., Can add and retrieve knowledge graph nodes. (+8 more)

### Community 747 - "test_vault.py"
Cohesion: 0.18
Nodes (18): _get_master_key(), Return the vault master key from the environment. - Raises ``RuntimeError`` if…, LogCaptureFixture, MonkeyPatch, Tests for the Fernet credential vault., test_ciphertext_is_different_from_plaintext(), test_dev_vault_emits_warning_without_allow_dev(), test_encrypt_and_decrypt_round_trip() (+10 more)

### Community 748 - "test_oauth.py"
Cohesion: 0.13
Nodes (16): asyncio, Tests for OAuthFlowManager., OAuthFlowManager must encrypt access tokens before storage., Without vault, tokens are stored as plaintext (dev mode)., test_exchange_code_cleans_up_state(), test_exchange_code_invalid_state_returns_none(), test_exchange_code_stores_token_for_tenant(), test_get_token_after_exchange() (+8 more)

### Community 749 - "test_step_enforcement.py"
Cohesion: 0.38
Nodes (12): RetryConfig, _clean_registry(), _compiler(), Any, 2.W-7: the compiler node wrapper must enforce the DSL's ``retry``, per-step…, _register(), _step(), test_on_failure_abort_raises() (+4 more)

### Community 750 - "api/coordination.py"
Cohesion: 0.17
Nodes (23): cancel_coordination_session(), _canonical_transition(), CanonicalTransitionRequest, complete_session(), create_coordination_session(), create_session(), CreateSessionRequest, get_coordination_session() (+15 more)

### Community 751 - "EvalSuite"
Cohesion: 0.12
Nodes (11): EvalSuite, EvalSuiteRunResult, Base, SQLAlchemy ORM models for eval suites and run results., An eval suite containing golden test tasks., Results of running an eval suite against live agents., test_eval_suite_instantiation(), test_eval_suite_run_result_instantiation() (+3 more)

### Community 752 - "SubAgentTask"
Cohesion: 0.16
Nodes (18): SubAgentTask, test_sub_agent_task_custom_goal(), test_sub_agent_task_defaults(), asyncio, Tests for SupervisorAgent multi-agent pattern., When all tasks failed, _synthesize returns without calling the LLM., When the LLM call raises, _synthesize falls back to structured text., When LLM decompose fails, falls back to single task. (+10 more)

### Community 753 - "TestDomainPolicies"
Cohesion: 0.20
Nodes (6): apply_domain_policy(), DomainPolicy, get_domain_policy(), Per-Domain Content Policies ============================== Domain-specific…, Apply domain policy to content. Returns (processed_content, violations)., TestDomainPolicies

### Community 754 - "GraphAccessControl"
Cohesion: 0.15
Nodes (10): get_graph_access_control(), GraphAccessControl, Any, Knowledge Graph Access Control — SUPPLEMENT U5. Controls who can see, query,…, Enforces role-based access to knowledge graphs. Usage: gac =…, Return the list of operations allowed for a role., Return True if the given role is allowed to perform operation., Raise PermissionError if role cannot perform operation. (+2 more)

### Community 755 - "SourceConfig"
Cohesion: 0.02
Nodes (143): Return allowed principals for a document (LAW-07). Default: empty list (tenant-…, Signal that a source document was deleted. Called when source-side deletion is…, Estimate total document count for progress reporting. Returns None if unknown…, AgentGeneratedConnector, BaseConnector, register, Ingest agent goal outputs and HITL decisions as knowledge. sync_mode=streaming:…, Yield high-quality goal outputs since cursor timestamp. (+135 more)

### Community 756 - "test_ingestors_coverage.py"
Cohesion: 0.10
Nodes (4): DOCX document ingestor using python-docx., PDF document ingestor using pypdf (open-source, no cloud dependencies)., Comprehensive coverage for all app/knowledge/ingestors/*. Mocks all external…, TestGitHubShouldIngest

### Community 757 - "MFAStore"
Cohesion: 0.13
Nodes (13): decrypt_secret(), encrypt_secret(), _get_fernet_key(), MFA secret encryption/decryption using Fernet symmetric encryption. The Fernet…, Derive a URL-safe base64-encoded 32-byte Fernet key from SECRET_KEY., Encrypt a TOTP secret for database storage. Returns a Fernet token (URL-safe…, Decrypt a TOTP secret retrieved from the database. Handles both the Fernet path…, MFAStore (+5 more)

### Community 758 - "org/metrics.py"
Cohesion: 0.12
Nodes (3): Any, PART 22 — Org-level Prometheus Metrics. Exposes org-level Prometheus…, _Stub

### Community 759 - "CorpusSample"
Cohesion: 0.16
Nodes (15): CorpusSample, GuardrailEffectivenessReport, GuardrailTuner, Any, GuardrailTuner — corpus-driven effectiveness analysis for guardrail rules. Uses…, One labelled example. ``should_block`` is the ground truth., _FakeEngine, Any (+7 more)

### Community 760 - "test_phase12_13_skills_frontend.py"
Cohesion: 0.23
Nodes (15): _make_app(), Phase 12+13: Skills Runtime tests., test_create_tenant_skill(), test_disable_skill(), test_enable_platform_skill(), test_enable_status_is_per_tenant(), test_execute_skill(), test_get_nonexistent_skill_returns_404() (+7 more)

### Community 761 - "test_migrations.py"
Cohesion: 0.12
Nodes (14): parametrize, Tests that verify migration files are syntactically valid and chain correctly.…, Each migration module exposes revision, down_revision, upgrade, downgrade., Each migration references the correct down_revision., All migration modules can be imported without errors., Avoid defining the same api_keys.tenant_id index implicitly and explicitly., 0010 adds workflow metadata columns with database defaults., agent_id and ix_goals_tenant_agent already exist from 0004_goals. (+6 more)

### Community 762 - "TestTenantServiceCachedLookup"
Cohesion: 0.16
Nodes (10): asyncio, get_tenant_cached must read from Redis on cache hit., Updating a tenant must invalidate the Redis cache., invalidate_tenant_cache is a no-op when redis=None., invalidate_tenant_cache swallows Redis errors gracefully., On a Redis miss, get_tenant_cached writes in-memory result to Redis., get_tenant_cached falls back to in-memory on Redis cache miss., get_tenant_cached returns None when tenant is not found anywhere. (+2 more)

### Community 763 - "SourceConfigStore"
Cohesion: 0.16
Nodes (9): _iso(), Any, SourceConfigStore — durable persistence for ingestion Sources (item 6). Backs…, Advance sync stats after a run — also sets last_synced_at so the beat due-scan…, Cross-tenant get for the worker/scheduler (bypasses RLS)., System-wide scan of enabled, non-streaming sources whose next sync is due…, Map a source_configs row (SQLAlchemy mapping) to a SourceConfig., _row_to_config() (+1 more)

### Community 764 - "BulkheadRegistry"
Cohesion: 0.16
Nodes (12): BulkheadRegistry, Per-tenant bulkhead semaphores for concurrent tool call limits., Per-tenant asyncio.Semaphore to prevent one tenant monopolizing workers. Each…, Set per-tenant concurrency limit., How many concurrent calls this tenant can still make., BulkheadRegistry creates independent semaphores per tenant., test_bulkhead_registry_per_tenant_semaphore(), asyncio (+4 more)

### Community 765 - "test_gap_integrations.py"
Cohesion: 0.13
Nodes (13): asyncio, Tests for critical wiring gaps - guardrails in graph, AI router in goal service., Verify AI Router selection is captured in execution context., Ensure the guardrails 2.0 import block in graph.py loads cleanly., The _GUARDRAILS_AVAILABLE flag must be True when modules are present., _select_models_for_tenant should return provider/model_id strings., test_ai_router_selection_stored_in_execution_context(), test_goal_service_select_models_for_tenant() (+5 more)

### Community 766 - "OrgHealthScore"
Cohesion: 0.07
Nodes (31): compute_cost_efficiency(), compute_escalation_rate(), compute_quality_avg(), compute_security_compliance(), OrgHealthScore, Any, Compute the full 8-factor OrgHealthScore for an organization., Org-level overview: missions, costs, agents, bottlenecks. (+23 more)

### Community 767 - "OrgEventPublisher"
Cohesion: 0.21
Nodes (7): _make_envelope(), OrgEventPublisher, Any, PART 21 + PART 29 — Org Audit Event Publisher. PART 21: Org-level audit event…, PART 29 — Publishes org events to Redis pub/sub and persists to audit trail.…, Publish an org event. Returns the correlation_id. Validates event_type against…, Build a spec-compliant event envelope (PART 29).

### Community 768 - "_make_db_mock"
Cohesion: 0.12
Nodes (16): _make_db_mock(), Build a mock DB session factory., Line 921: DB available → runs insert (succeeds or fails gracefully)., Line 921: DB fails gracefully → still returns consent_id., Lines 944-947: DB available → runs UPDATE., Lines 847-849: DB insert succeeds → job_id returned., Lines 1076-1077: DB insert succeeds → returns contract details., Lines 1130-1152: DB raises exception → returns []. (+8 more)

### Community 769 - "api/artifacts.py"
Cohesion: 0.27
Nodes (13): delete_artifact(), get_artifact(), list_artifacts(), Any, delete, get, Request, Artifact REST API — list, get, download, delete agent-produced files. (+5 more)

### Community 770 - "ocr.py"
Cohesion: 0.21
Nodes (16): BatchOcrRequest, BatchOcrResponse, extract_document(), extract_documents_batch(), OcrFieldResult, OcrRequest, OcrResponse, _persist_ocr_to_kb() (+8 more)

### Community 771 - ".probe_rag_strategy_contract"
Cohesion: 0.13
Nodes (9): RAGStrategyContractProbe, Static adapter identity/contract evidence, not operational readiness proof., Compatibility alias for static contract evidence., Compatibility wrapper for the static identity/contract probe., Validate adapter identity and contract without claiming operational evidence., _readiness_from_available_dependencies(), StrategyResolution, LookupError (+1 more)

### Community 772 - "GuardrailEngine"
Cohesion: 0.14
Nodes (13): GuardrailEngine, LLMJudge, Uses a fast LLM to semantically evaluate risk that regex cannot catch.…, Orchestrates all six guardrail layers and returns a single GuardrailResult.…, asyncio, When provider raises, judge must return HIGH-severity violation (fail-closed)., No violation when score is below threshold, assuming provider works., GuardrailEngine.evaluate_output must redact PII via Layer 6. (+5 more)

### Community 773 - "verify_goal_token"
Cohesion: 0.24
Nodes (8): _b64url(), mint_goal_token(), Short-Lived Goal Execution Tokens =================================== When a…, Mint a short-lived signed token for a goal execution., Verify and decode a goal token. Returns payload or None if invalid., verify_goal_token(), Each token must have a unique ID for anti-replay., TestGoalTokens

### Community 774 - "models/auth.py"
Cohesion: 0.21
Nodes (12): APIKeyScope, CustomRole, Base, SQLAlchemy ORM models for scopes, custom roles, role assignments, and IP…, Canonical registry of all scopes supported by the platform. Seeded at startup…, Explicit scope delegation from one principal to another. Supports least-…, Custom or builtin role definition for a tenant. Builtin roles have ``tenant_id…, Assigns a role (custom or system) to a user / API key within a tenant.… (+4 more)

### Community 775 - "ChannelAuthGuard"
Cohesion: 0.05
Nodes (20): ChannelAuthGuard, AsyncSession, Per-channel authentication guard — Q9 of spec. Each channel has its own auth…, Verifies channel-specific authentication for every inbound command. All…, Verify a tenant API key (hash comparison)., # TODO: Look up hashed key in DB, Verify HMAC-SHA256 signature., Check if any of the provided scopes grants the required action. (+12 more)

### Community 776 - "test_rollback_experiment.py"
Cohesion: 0.18
Nodes (18): _make_app(), _make_opt_v2(), Any, FastAPI, Tests for POST /experiments/{experiment_id}/rollback API endpoint., When self_optimizer_v2 not wired on app.state → 503., Empty body → default reason string applied., Missing API key → 401. (+10 more)

### Community 777 - "test_token_metrics.py"
Cohesion: 0.20
Nodes (15): asyncio, Tests for FakeProvider streaming and supports_streaming., stream_complete yields at least one non-empty token., FakeProvider.supports_streaming returns True., Joined tokens from stream_complete are non-empty., Each call to stream_complete advances the response index., Concatenated stream tokens reconstruct the full response (modulo spacing)., stream_complete behaves as a proper async generator (supports async for). (+7 more)

### Community 778 - "StructuredLogStore"
Cohesion: 0.16
Nodes (9): Any, Run multi-agent debate and return winning proposal., Redis Streams-backed structured log store. Falls back to an in-memory ring…, Write a structured log entry. Called from goal_service, agent loop, etc., StructuredLogStore, asyncio, StructuredLogStore falls back to in-memory ring buffer when Redis is…, set_redis() wires the Redis client; subsequent emits use Redis path. (+1 more)

### Community 779 - "api/policy_rules.py"
Cohesion: 0.27
Nodes (13): create_policy_rule(), delete_policy_rule(), evaluate_rules_dry_run(), list_policy_rules(), PolicyRuleUpsert, Any, BaseModel, delete (+5 more)

### Community 780 - "test_workflow_builder.py"
Cohesion: 0.15
Nodes (18): _make_app(), Any, FastAPI, Tests for POST /workflows/generate and the fixed POST /workflows/{id}/run.…, POST /workflows/{id}/run?dry_run=true must return status='dry_run'., POST /workflows/{id}/run must always return a non-empty 'run_id'., Every generated workflow must have a 'trigger' and an 'end' node., generate falls back to heuristic plan when no LLM provider is configured. (+10 more)

### Community 781 - "test_durable_execution.py"
Cohesion: 0.05
Nodes (53): check_pause_cancel(), clear_signals(), GoalCancelledError, is_cancelled_sync(), is_paused_sync(), Any, Exception, Cross-process goal lifecycle signals via Redis pub/sub + flag keys. Allows API… (+45 more)

### Community 782 - "RedisBulkheadRegistry"
Cohesion: 0.15
Nodes (7): Any, Semaphore, Redis-backed registry of per-tenant distributed bulkheads. Falls back to…, Set per-tenant concurrency limit., Get a bulkhead for a tenant (Redis if available, local otherwise)., Get or create semaphore for tenant., RedisBulkheadRegistry

### Community 783 - "test_dr_drill.py"
Cohesion: 0.13
Nodes (14): skipif, DR drill validation — tests backup/restore capability., DR script must document RPO and RTO targets., DR script must cover the 5 required drill steps., docker-compose.yml must have a pgbackup service for automated backups., pgbackup service must configure retention periods., Shell script must be syntactically valid (bash -n check)., DR drill script must exist and be executable. (+6 more)

### Community 784 - "Any"
Cohesion: 0.20
Nodes (5): Any, GDPR compliance. Amendment 8.3: All controls read from real DB records — no…, SOC 2 Type II — audit completeness check., Run all compliance checks and return combined report., HIPAA compliance: all required controls must pass.

### Community 785 - "test_a2a.py"
Cohesion: 0.22
Nodes (13): _make_app(), FastAPI, Tests for A2A protocol endpoints — updated for DB-backed + HMAC implementation., Set A2A_TENANT_ID for all tests in this module., When A2A_SHARED_SECRET not set, any request is accepted., When A2A_SHARED_SECRET is set, bad signature returns 401., _set_a2a_tenant(), test_agent_card_returns_json() (+5 more)

### Community 786 - "test_agent_patterns.py"
Cohesion: 0.12
Nodes (12): ReflectionPattern, Tests for all 13 agent pattern adapters and ALL_PATTERNS list., All 13 pattern classes can be imported without error., All patterns have unique IDs., All patterns have valid PatternState values., test_all_patterns_importable(), test_plan_execute_is_implemented(), test_reflection_is_implemented() (+4 more)

### Community 787 - "GoalPersistenceEngine"
Cohesion: 0.05
Nodes (71): AttemptRecord, GoalPersistenceEngine, PersistenceConfig, Any, StrEnum, Agent goal persistence — keeps trying until goal is achieved or explicitly…, Manages persistent goal execution with intelligent retry strategies. Wraps an…, Count trailing consecutive failures. (+63 more)

### Community 788 - "LLMQueryTransformer"
Cohesion: 0.14
Nodes (17): LLMQueryTransformer, Return [rewritten_query] or [original] on failure., Apply all strategies and return a de-duplicated union of queries., Transforms queries using an LLM to improve retrieval recall and precision., Extract non-empty, non-boilerplate lines from LLM output., Return [original_query, step_back_query]., Return [original_query, sub_q1, sub_q2, …] (de-duplicated)., FakeProvider (+9 more)

### Community 789 - "test_cli.py"
Cohesion: 0.13
Nodes (9): Tests for the agentverse CLI (app/cli/main.py)., submit without its required positional arg should exit non-zero., logs without its required positional arg should exit non-zero., connectors --help shows help before any network call., status without its required positional arg should exit non-zero., test_connectors_help_exits_without_key(), test_logs_requires_goal_id(), test_status_requires_goal_id() (+1 more)

### Community 790 - "test_state_runtime.py"
Cohesion: 0.04
Nodes (69): _frame_untrusted(), PromptBuilder, PromptContextBundle, Any, PromptBuilder — builds model-specific prompts from PromptContextBundle., Build context string for the planner LLM — includes all 9 sources., Build per-step context for the executor LLM., Build context for the verifier LLM — focus on citations and confidence. (+61 more)

### Community 791 - "test_goal_hitl_lifecycle_e2e.py"
Cohesion: 0.25
Nodes (12): _create_supervised_agent(), _HighRiskPlanProvider, _pinned_high_risk_provider(), Any, FakeProvider, e2e_full: full goal lifecycle through a real HITL approval gate. The Raccoon-…, Deterministic provider that plans one explicit high-risk step. Branches on the…, Poll GET /governance/approvals until a pending request for goal_id shows. (+4 more)

### Community 792 - "grant_elevation"
Cohesion: 0.24
Nodes (8): ElevationToken, grant_elevation(), Any, Temporary Elevated Scope ========================= Time-boxed impersonation and…, Verify and decode an elevation token., Grant temporary elevated access. Returns a signed token., verify_elevation(), TestTemporalElevation

### Community 793 - "_get_client_ip"
Cohesion: 0.08
Nodes (22): _get_client_ip(), Extract client IP with trusted-proxy validation. Only trusts ``X-Forwarded-…, asyncio, Regression tests for 0C.2 API authorization hardening. Covers: H2 — A2A cross-…, Requests denied count should align with the effective limit., H2: A2A in-memory fallback must not return other tenants' tasks., In-memory task store should only return tasks owned by the calling tenant., _get_task must return None if tenant_id doesn't match. (+14 more)

### Community 794 - "google_oauth.py"
Cohesion: 0.16
Nodes (17): _generate_pkce(), google_callback(), google_login(), _pkce_redis_key(), _pkce_store_pop(), _pkce_store_set(), Any, get (+9 more)

### Community 795 - "TestToolReliabilityStoreWithDBError"
Cohesion: 0.14
Nodes (8): _FailingDB, When DB fails, get_reliability must fall back to in-process cache., get_unreliable_tools without DB always returns empty list., DB failure in get_unreliable_tools returns empty list., record() with explicit db_session_factory=None skips DB path., Tests that exercise the DB fallback paths., DB failure must not prevent in-memory update., TestToolReliabilityStoreWithDBError

### Community 796 - "decision_store.py"
Cohesion: 0.17
Nodes (11): Base, ORM rows for canonical routing decisions and outcomes., RoutingDecisionRow, RoutingOutcomeRow, _decision(), PostgresDecisionStore, Any, async_sessionmaker (+3 more)

### Community 797 - "test_system_comprehensive.py"
Cohesion: 0.24
Nodes (14): _make_app(), FastAPI, Comprehensive tests for /system endpoints — targets 30% → 70%+ coverage., Redis errors should be caught — fallback to building keys., test_health_all_down(), test_health_all_healthy(), test_health_partial_failure(), test_jwks_no_redis_no_service() (+6 more)

### Community 798 - "PromptCompressor"
Cohesion: 0.10
Nodes (20): PromptCompressor, Any, Compress a list of {role, content} message dicts., Truncate only [Relevant context] / [Knowledge base context] / [Visual context]…, Truncate [context] / [Relevant context] blocks that exceed max token size., If [Available tools] section has > _MAX_TOOL_LIST_ITEMS, trim it., Stateless heuristic prompt compressor. Usage: compressor = PromptCompressor()…, Return a compressed version of text with ~15-30% fewer tokens. (+12 more)

### Community 799 - "Any"
Cohesion: 0.18
Nodes (7): Any, Register a new prompt variant for A/B testing. If *db* is provided (an async…, Set Redis client for cache invalidation between replicas., Persist a variant to the prompt_variants table., Update win/loss counts in DB after A/B test result., Load all active variants from DB into in-process cache. Call at startup and…, Register a pre-built PromptVariant for the given tenant. If *db* is None, the…

### Community 800 - "CSVParser"
Cohesion: 0.20
Nodes (8): CSVParser, ExcelParser, CSV and Excel parser — schema-aware extraction for structured data., Parse CSV/TSV files into readable text chunks. Each row becomes: "Table:…, Parse XLS/XLSX files into readable text, one sheet per section., test_csv_parser_basic(), test_csv_parser_empty(), test_csv_parser_truncates_large()

### Community 801 - "test_critical_fixes.py"
Cohesion: 0.18
Nodes (12): _agent_source(), asyncio, Tests for critical bug fixes in AgentVerse. Covers: CRITICAL-1:…, Read combined source of graph.py and all node mixin files., RedisCostController must have check_and_record (not just…, graph.py must not call model_router.route() — use model_for() or similar., Concurrent wave step mutations must not corrupt AgentState., HITLGateway must not use deprecated asyncio.get_event_loop(). (+4 more)

### Community 802 - "_make_integrations_app"
Cohesion: 0.24
Nodes (8): _make_integrations_app(), MonkeyPatch, Targets uncovered paths in app/api/integrations.py., Lines 185-188, 223-225 in integrations.py., Lines 185-188: invalid JSON payload → 400., Lines 223-225: resume_goal raises → logged., TestIntegrationsExtra, TestIntegrationsWave5

### Community 803 - "test_rate_limiter.py"
Cohesion: 0.21
Nodes (16): Per-tenant, per-endpoint sliding-window rate limiter. Primary path: atomic Lua…, SlidingWindowRateLimiter, asyncio, Tests for SlidingWindowRateLimiter and RateLimiter., Concurrent requests must not exceed the rate limit (in-memory path)., The _WORKER_CHECKPOINTER module-level var must exist in tasks., _store(), test_different_endpoints_have_separate_counters() (+8 more)

### Community 804 - "_MockSession"
Cohesion: 0.20
Nodes (3): _MockSession, Any, Minimal async SQLAlchemy session mock.

### Community 805 - "GoalDeduplicator"
Cohesion: 0.18
Nodes (8): _dedup_key(), GoalDeduplicator, Any, Goal-level request deduplication. When two tenants submit identical goals…, Redis-backed goal-level deduplication. Usage: dedup =…, Return the in-flight goal_id for this (tenant, goal) pair, or None., Register a new goal. Returns True if this is the first registration (i.e. no…, Delete the dedup key so future identical goals can be submitted.

### Community 806 - "test_phase5_knowledge_graph.py"
Cohesion: 0.20
Nodes (17): _make_app(), Phase 5: Tenant Knowledge Graph tests., Tenant A cannot see Tenant B's nodes., test_add_edge_between_nodes(), test_add_node_manually(), test_entity_extraction_deterministic(), test_extract_text_creates_nodes(), test_find_path_between_nodes() (+9 more)

### Community 807 - "extract_roles_from_jwt"
Cohesion: 0.14
Nodes (12): extract_roles_from_jwt(), Extract AgentVerse roles from a Keycloak JWT payload. Looks for roles in: 1.…, TestExtractRolesFromJWT, test_extract_deduplicates_roles(), test_extract_empty_payload(), test_extract_from_all_sources(), test_extract_from_realm_access(), test_extract_from_resource_access() (+4 more)

### Community 808 - "TestAdminRouter"
Cohesion: 0.15
Nodes (10): PlanChangeRequest, BaseModel, Validate platform admin key. Raises 401 if invalid, 503 if unconfigured., _require_admin(), _require_admin must use hmac.compare_digest, not == operator. Finding: direct…, test_admin_key_comparison_is_constant_time(), Tests for Phase 1c — durable TenantService with Redis cache., Admin endpoints must reject invalid X-Admin-Key with 401. (+2 more)

### Community 809 - "test_marketplace_v2_extra.py"
Cohesion: 0.13
Nodes (12): _make_mock_db(), Extra coverage for marketplace_v2.py. Targets uncovered lines: 82, 88-89,…, Build a fake async DB session factory., Lines 1242-1254: DB exception → falls back to memory., Lines 1394-1438: DB exception on publish → stored in memory., Lines 1573-1578: DB failure during install → success=False., Lines 1673-1674: DB exception on add_review → success=False., list_templates DB exception → in-memory fallback. (+4 more)

### Community 810 - "test_user_models.py"
Cohesion: 0.15
Nodes (5): Tests for Phase 1a — User + TenantMembership models., Migration must enable RLS on tenant_memberships., TestEntitlementsModule, TestGoogleOAuthRouter, TestUserModel

### Community 811 - "TestRunGoalPaths"
Cohesion: 0.19
Nodes (8): Lines 337-346, 364-365, 533-539, 630-636., Context that makes the distributed lock always succeed., Lines 345-346: invalid plan string → PROFESSIONAL., Lines 358-363: emergency stop returns blocked., Lines 630-636: fake provider blocked in production., Lines 533-535: ANTHROPIC_API_KEY path., Lines 537-539: OPENAI_API_KEY path., TestRunGoalPaths

### Community 813 - "ReActPattern"
Cohesion: 0.20
Nodes (5): ReActPattern, test_react_pattern_is_compatible(), test_react_pattern_state(), test_react_is_implemented(), TestReActPattern

### Community 814 - "coordination_group_chat.py"
Cohesion: 0.27
Nodes (11): _api_key(), _authenticate(), describe_group_chat_websocket(), group_chat_websocket(), GroupChatWebSocketHandshake, _origin_allowed(), Any, BaseModel (+3 more)

### Community 815 - "system.py"
Cohesion: 0.13
Nodes (18): get_provider_catalog_endpoint(), health(), jwks_endpoint(), metrics(), Any, get, JSONResponse, Request (+10 more)

### Community 816 - "TestCostOptimizerSuggestions"
Cohesion: 0.12
Nodes (7): Covers line 119: no cheaper model in MODEL_DOWNGRADE_PATH., Covers line 125: cheaper_stats is None (no data for cheaper model at all)., Covers lines 151-153: auto-apply when confidence >= auto_apply_confidence., After auto-apply, get_model_override should return the cheaper model., Suggestions must be returned sorted by estimated_savings_usd_per_100 descending., Covers line 125: cheaper_stats goal_count < min_goals., TestCostOptimizerSuggestions

### Community 817 - "DecisionTrace"
Cohesion: 0.15
Nodes (7): DecisionTrace, Any, Explainability — DecisionTrace captures why an action was taken. Every tool…, test_decision_trace_records_reasoning(), test_decision_trace_serializes(), Comprehensive tests for app/intelligence/explainability.py — targeting 100%…, TestDecisionTrace

### Community 818 - "multi_turn_eval.py"
Cohesion: 0.23
Nodes (9): MultiTurnCase, MultiTurnEvaluator, MultiTurnResult, Any, Multi-turn dialogue evaluator. Evaluates agents on multi-turn conversations…, Evaluate agent behaviour across multi-turn conversations. Parameters ----------…, Run the case against *agent_fn* and score the conversation. Parameters…, Turn (+1 more)

### Community 819 - "coordination_magentic.py"
Cohesion: 0.38
Nodes (11): get_ledger(), HumanReviewRequest, _ledger(), list_revisions(), Any, BaseModel, get, Request (+3 more)

### Community 820 - "coordination_moa.py"
Cohesion: 0.36
Nodes (9): get_layer(), _layer_public(), list_layers(), Any, get, Request, Safe tenant-scoped Mixture-of-Agents layer explanations., _repository() (+1 more)

### Community 821 - "test_guardrails_v3.py"
Cohesion: 0.17
Nodes (9): IndirectInjectionResult, Any, Indirect Injection Scanner ============================ Scans tool outputs and…, Scan RAG-retrieved chunks for injection attempts before LLM injection., Wrap tool output in untrusted delimiters to signal LLM it's external data., scan_rag_chunks(), wrap_in_untrusted(), Output Anomaly Detection ========================= Detects statistical… (+1 more)

### Community 822 - "scan_output_for_anomalies"
Cohesion: 0.26
Nodes (4): Any, Scan LLM output for anomalies. Returns dict: {clean, anomalies, severity}, scan_output_for_anomalies(), TestOutputAnomaly

### Community 823 - "verify_stream_token"
Cohesion: 0.23
Nodes (15): _b64url(), mint_stream_token(), Any, Short-lived SSE stream tokens. EventSource cannot send request headers, so…, Mint a short-lived signed token authorizing SSE reads for one tenant., Verify and decode a stream token. Returns the payload or None if invalid., verify_stream_token(), Tests for short-lived SSE stream tokens (app/auth/stream_tokens.py). (+7 more)

### Community 824 - "test_magentic_api.py"
Cohesion: 0.26
Nodes (7): HumanReviewDecision, MagenticHumanReviewService, One-time, session-scoped Magentic human-review responses., _app(), FastAPI, test_human_review_token_is_one_time_and_session_scoped(), test_ledger_reads_are_paginated_immutable_and_tenant_scoped()

### Community 825 - "GuardrailsEngine"
Cohesion: 0.08
Nodes (17): GuardrailsEngine, Evaluates content against guardrail rules., Best-effort background persistence (only when an event loop runs)., Persist any rules added since the last flush. Returns the count saved., Rehydrate rules from the bound repository into memory. Returns count., Seed baseline BLOCK rules (+ compliance-bundle rules) for a tenant. Fixes…, asyncio, _tenant() (+9 more)

### Community 826 - "test_graph_persistence_wiring.py"
Cohesion: 0.21
Nodes (12): _agent_source(), graph.py must use on_goal_completed(), not the non-existent record_result()., graph.py must reference persist_tool_outcome for cross-restart trust., graph.py must persist scorecards via OrchestrationPersistence., graph.py must call RegressionGate for low-scoring goals., SelfOptimizerV2 must NOT have record_result() (only on_goal_completed)., Read combined source of graph.py and all node mixin files., test_graph_calls_on_goal_completed_not_record_result() (+4 more)

### Community 827 - "_StatefulMockSession"
Cohesion: 0.20
Nodes (4): Mock session that can return different rows per execute() call., Set rows to return for each successive execute() call., _StatefulMockDB, _StatefulMockSession

### Community 828 - "_make_eval_runner"
Cohesion: 0.15
Nodes (13): _make_eval_runner(), Lines 752-760: creates suite and returns suite_id., Lines 785-787: lists suites from runner., Line 802: returns 404 for unknown suite_id., Lines 802-803: returns suite metadata., Lines 813-826: adds task to suite., Lines 847-849: get suite results., test_add_golden_task_with_runner() (+5 more)

### Community 829 - "test_workflow_definition_bridge.py"
Cohesion: 0.21
Nodes (12): app_factory(), _app_url(), postgres_url(), async_sessionmaker, Real-Postgres coverage for the workflows → workflow_definitions bridge. The…, A DB-mode create must make the workflow triggerable by the run engine., Tenant B must not see tenant A's bridged workflow definition (RLS)., Editing the visual builder must keep the run-engine DSL in sync. (+4 more)

### Community 830 - "agent_runtime.py"
Cohesion: 0.27
Nodes (16): create_execution_plan(), create_run_trace(), get_execution_plan(), get_run_trace(), list_agent_roles(), list_strategies(), Any, get (+8 more)

### Community 831 - "test_workflow_trigger_e2e.py"
Cohesion: 0.30
Nodes (11): _create_workflow(), _inline_runner(), Any, e2e_full: workflow create → trigger → persisted run → queryable. Proves the…, The create/read path is genuinely wired (DB-backed, RLS-scoped)., Two API-created workflows must each run their own definition. The run engine…, Run workflow triggers inline for one test (no Celery worker in the harness).…, test_distinct_workflows_run_their_own_graph() (+3 more)

### Community 832 - "summarize_pattern_selection"
Cohesion: 0.21
Nodes (15): _available_agent_patterns(), humanize(), Any, Pattern-selection summary — the always-on, human-readable record of which agent…, Best-effort plain-language name for a pattern id., The registry's agent-pattern catalog, so a picker is driven by ONE registry., Return a serializable, plain-language summary of the goal's pattern choice.…, summarize_pattern_selection() (+7 more)

### Community 833 - "has_permission"
Cohesion: 0.18
Nodes (8): assert_permission(), has_permission(), OrgRole, StrEnum, Raise PermissionError if role doesn't have permission., Check if an org role grants a specific permission., PART 18 security test: Org RBAC roles enforce correct permissions., TestOrgRBAC

### Community 834 - "test_prompt_variants_api.py"
Cohesion: 0.21
Nodes (12): anyio, Behavioral tests for /intelligence/prompt-variants API endpoints. All tests use…, GET /intelligence/prompt-variants must return 401 without an API key., POST creates a new challenger variant and it appears in the listing., POST /{id}/promote marks the variant as control and sets promoted_at., GET /{id}/report returns score fields for a known variant., DELETE /{id} removes the variant; subsequent list no longer contains it., test_create_variant_registers_with_optimizer() (+4 more)

### Community 836 - "SlackIngestor"
Cohesion: 0.20
Nodes (5): Any, Slack channel message ingestor via Web API., SlackIngestor, Extra coverage for all knowledge ingestors — mock all external HTTP/lib calls., TestSlackIngestor

### Community 837 - "test_family_a_time.py"
Cohesion: 0.18
Nodes (16): _business_calendar_slots(), _deadline_due_utc(), _is_business_time(), RELATIVE_DELAY: fire once at (base + offset). ``base`` is ``fire_at_iso``; a…, DEADLINE: fire once ``warning_seconds`` before the deadline in ``fire_at_iso``., True when the instant is Mon-Fri, 09:00-17:00 (local wall-clock in tz)., BUSINESS_CALENDAR: cron slots that fall within business hours only., _relative_delay_due_utc() (+8 more)

### Community 838 - "list_active_sessions"
Cohesion: 0.21
Nodes (11): list_active_sessions(), Any, delete, get, Request, User auth session management — list active sessions, revoke, idle timeout., List all active login sessions for the current user., Revoke a specific session (remote logout). (+3 more)

### Community 839 - "test_oauth_security.py"
Cohesion: 0.17
Nodes (11): Tests that OAuth security fixes work correctly., HTTP 400/401 from OAuth server returns None, not a mock token., Unreachable OAuth server returns None., OAuth server returns 200 but no access_token — should return None., Expired or forged state parameter returns None., Successful exchange stores the real token., test_exchange_code_returns_none_on_connection_error(), test_exchange_code_returns_none_on_empty_access_token() (+3 more)

### Community 840 - "test_supervisor_debate_wiring.py"
Cohesion: 0.32
Nodes (9): _FakeAgentStore, _node_names(), Any, D-2: the real supervisor/debate reasoning nodes must be reachable from an…, Regression guard: without the flags, the nodes stay absent (opt-in only)., _svc_with_agent_config(), test_default_path_enables_debate_from_agent_config(), test_default_path_enables_supervisor_from_agent_config() (+1 more)

### Community 841 - "TestFireDueSchedules"
Cohesion: 0.20
Nodes (4): Lines 1261, 1291-1384: schedule types and exception path., Line 1261: advance_and_dispatch returns None when goal_kwargs is None., Line 1372-1373: exception processing a schedule is logged and continued., TestFireDueSchedules

### Community 842 - "test_routing.py"
Cohesion: 0.32
Nodes (14): _make_graph(), Unit tests for the RoutingMixin — _route and _route_after_execute., _state(), _tenant(), test_max_reflection_rounds_default(), test_route_after_execute_continue_on_success(), test_route_after_execute_failed_on_failed_status(), test_route_complete_when_verification_success() (+6 more)

### Community 843 - "test_goals_debate.py"
Cohesion: 0.20
Nodes (13): _make_app(), Any, FastAPI, Tests for debate workflow_mode in goal submission., Non-debate workflow_mode is unaffected by the debate block., Minimal app wired with goals router and optional debate provider., Submit a goal with workflow_mode=debate returns 202 (normal path)., Debate mode still calls submit_goal on the underlying service. (+5 more)

### Community 844 - "scan_tool_output"
Cohesion: 0.29
Nodes (3): Scan tool output for indirect injection attempts. Always wraps content in…, scan_tool_output(), TestIndirectInjection

### Community 845 - "notebook_parser.py"
Cohesion: 0.40
Nodes (3): ParquetParser, Jupyter Notebook parser — cell-pair extraction (code + output)., Parse Parquet files — sample rows and schema as text.

### Community 846 - "TestMultimodalPipeline"
Cohesion: 0.17
Nodes (6): asyncio, get_job must enforce tenant isolation., Empty text should still create a job (returns empty spans)., Without a vision provider the job should fail gracefully., set_provider should not raise., TestMultimodalPipeline

### Community 847 - "test_comprehensive_coverage.py"
Cohesion: 0.02
Nodes (140): AgentLoop, Backward-compatible import for the canonical :class:`AgentGraph` runtime.…, circuit_breaker_check(), cost_check(), dedup_check(), exec_memory_lookup(), governance_check(), hitl_gate() (+132 more)

### Community 848 - "MemoryRecord"
Cohesion: 0.10
Nodes (24): CanonicalMemoryRecord, ImprovementActionRecord, MemoryFeedback, MemoryRecallHit, MemoryRecallRequest, MemoryRecord, BaseModel, model_validator (+16 more)

### Community 849 - "RoutingOptimizer"
Cohesion: 0.29
Nodes (6): MeasuredOutcome, OptimizationRecommendation, Bounded rolling optimization recommendations., RoutingOptimizer, test_hedging_requires_idempotency_budget_and_deadline_pressure(), test_optimizer_requires_samples_and_never_weakens_hard_limits()

### Community 850 - ".create_workflow_approval"
Cohesion: 0.12
Nodes (13): make_context_item(), make_number_context(), Any, Build + persist a :class:`WorkflowHITLRequest` for a suspended step. Bridges…, Validate and consume a magic link token (single-use). Returns the payload dict…, Return inbox stats for a tenant., Build a rich context item dict for a HITL request., Create a number display context item with optional risk thresholds. (+5 more)

### Community 851 - "test_agent_teams_e2e.py"
Cohesion: 0.23
Nodes (11): _prompt_text(), Any, FakeProvider, e2e_full: AI Agent Team -- supervisor decomposition + real sub-agent execution.…, A supervisor-mode goal decomposes into a real 2-agent team; each sub-goal runs…, Deterministic provider driving both halves of the team flow: -…, Fresh tenant per test so cumulative plan/concurrency caps don't leak., team_client() (+3 more)

### Community 852 - "TestCheckAuthRateLimit"
Cohesion: 0.23
Nodes (7): asyncio, Redis without .pipeline() uses direct zadd/zcard/expire., No Redis wired → no-op, request allowed., Under rate limit with Redis pipeline → request allowed., The rate limit HTTPException is caught by the outer except block and logged as…, Redis error → allow request (availability over blocking)., TestCheckAuthRateLimit

### Community 853 - "test_builder_preview.py"
Cohesion: 0.17
Nodes (11): Test builder preview hosting endpoints., Must show 'Building' status when no index.html artifact found., Must serve actual index.html content when artifact exists., Created project must have a /builder/preview/ URL., GET /builder/preview/{id} must return HTML, not JSON., Preview must not crash if artifact_store.list_artifacts raises AttributeError., test_builder_preview_building_message_when_no_artifacts(), test_builder_preview_handles_missing_list_artifacts() (+3 more)

### Community 854 - "agent_credentials_api.py"
Cohesion: 0.22
Nodes (12): AgentKeyCreateRequest, create_agent_key(), get_agent_manifest(), list_agent_keys(), BaseModel, delete, get, Request (+4 more)

### Community 855 - "TestToolReliabilityDBPaths"
Cohesion: 0.14
Nodes (8): Tests for DB-backed record/get paths (lines 50, 74-81, 113-123)., Line 50: DB write inside record()., DB write for failure case., Lines 74-81: DB returns a row → parse it., DB returns no row → falls back to in-memory cache., Lines 113-123: get_unreliable_tools with DB returning results., DB returns no unreliable tools., TestToolReliabilityDBPaths

### Community 856 - "evaluate_rule"
Cohesion: 0.21
Nodes (17): evaluate_rule(), evaluate_rules(), _get_field(), _matches(), PolicyRuleResult, Any, Declarative policy-as-code evaluator. Rule format (JSON): { "name": "block-…, Test that policy rules are actually evaluated during tool dispatch. (+9 more)

### Community 857 - "_inject_goal"
Cohesion: 0.17
Nodes (7): _inject_goal(), Lines 1950-1979: checkpoint resume via graph instance., Lines 1980-1981: exception in graph resume → legacy fallback., Line 2103: unknown action → ok=False., TestHandleApproval, TestResumeGoal, TestRunWorkflow

### Community 858 - "requires_consensus"
Cohesion: 0.24
Nodes (4): Any, Return True when this goal warrants 3-way consensus verification. Two calling…, requires_consensus(), TestRequiresConsensus

### Community 859 - "test_rbac.py"
Cohesion: 0.23
Nodes (14): has_role(), Return True if ctx has the given role (with hierarchy expansion)., _ctx(), asyncio, Tests for app/tenancy/rbac.py — RBAC role hierarchy and helpers., test_admin_effective_roles_contains_all(), test_admin_has_all(), test_empty_roles() (+6 more)

### Community 860 - "firebase_server.py"
Cohesion: 0.33
Nodes (9): call_tool(), _from_firestore_doc(), _fs_base(), _headers(), Any, Firebase / Firestore MCP server — document database and push notifications.…, Convert a Python value to Firestore REST API typed value., Flatten a Firestore REST document into plain Python dict. (+1 more)

### Community 861 - "EmailChannelAdapter"
Cohesion: 0.24
Nodes (5): EmailChannelAdapter, Any, Email inbound command parser — org@commands.agentverse.io., Verify sender is in allowed list., Format OrgResponse as an email payload.

### Community 862 - "integrations.py"
Cohesion: 0.04
Nodes (61): AlertmanagerPayload, confluence_page_webhook(), DatadogWebhookPayload, _get_slack_tenant_id(), _get_zapier_tenant_id(), github_push_webhook(), notion_page_webhook(), Any (+53 more)

### Community 863 - "TelegramChannelAdapter"
Cohesion: 0.22
Nodes (7): Any, Format OrgResponse for Telegram (text + optional inline keyboard)., Send formatted response to a Telegram chat., Get download URL for a Telegram file_id., Verify Telegram webhook secret token header., Full Telegram Bot API integration., TelegramChannelAdapter

### Community 864 - "test_keycloak.py"
Cohesion: 0.08
Nodes (31): extract_roles(), map_roles_to_plan(), Extract realm-level roles from Keycloak JWT claims., Map Keycloak roles to AgentVerse plan tiers., Force reload from disk (used in tests / hot reload)., test_extract_roles_empty_payload(), test_extract_roles_from_realm_access(), test_extract_roles_missing_roles_key() (+23 more)

### Community 865 - "ConversationManager"
Cohesion: 0.25
Nodes (6): Conversation, ConversationManager, ConversationTurn, AsyncSession, Multi-turn ConversationManager — maintains context across channels., Maintains conversation state across multiple turns, regardless of channel. Uses…

### Community 866 - "record_desired_workers"
Cohesion: 0.19
Nodes (8): Emit desired worker count per plan for autoscaling. The *plan* label is passed…, record_desired_workers(), Tests for per-plan autoscale desired-worker gauge., DESIRED_WORKERS gauge reflects the last set value., All four standard plan names can be recorded without error., DESIRED_WORKERS Gauge is registered and exportable., TestAutoscaleGauge, TestDesiredWorkersGaugeExists

### Community 867 - "TenantScopedStore"
Cohesion: 0.22
Nodes (4): Any, Tenant-isolated Redis interface. Wraps any redis.asyncio.Redis-compatible…, Execute a Lua script, prefixing the first *numkeys* positional arguments (the…, TenantScopedStore

### Community 868 - "make_resp"
Cohesion: 0.23
Nodes (16): make_resp(), mk_client(), Any, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_elasticsearch_bulk_index(), test_elasticsearch_create_index(), test_elasticsearch_delete_document(), test_elasticsearch_get_document() (+8 more)

### Community 869 - "TenantMiddleware"
Cohesion: 0.02
Nodes (120): BaseHTTPMiddleware, Authenticate API key → inject TenantContext into request.state.tenant. When…, Add OWASP security headers to every response., SecurityHeadersMiddleware, TenantMiddleware, ASGIApp, KeyResolver, _make_enterprise_app() (+112 more)

### Community 870 - "Agent pattern canary rollback"
Cohesion: 0.18
Nodes (10): Agent pattern canary rollback, Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Rollback (+2 more)

### Community 871 - "Agent pattern runaway limits"
Cohesion: 0.18
Nodes (10): Agent pattern runaway limits, Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Rollback (+2 more)

### Community 872 - "Auction anomalies"
Cohesion: 0.18
Nodes (10): Auction anomalies, Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Rollback (+2 more)

### Community 873 - "Coordination delivery lag"
Cohesion: 0.18
Nodes (10): Coordination delivery lag, Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Rollback (+2 more)

### Community 874 - "Coordination lease reclaims"
Cohesion: 0.18
Nodes (10): Coordination lease reclaims, Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Rollback (+2 more)

### Community 875 - "Magentic stalls and fallbacks"
Cohesion: 0.18
Nodes (10): Dashboard queries, Diagnosis, Escalation, Impact, Magentic stalls and fallbacks, Mitigation, Recovery, Rollback (+2 more)

### Community 876 - "Reflexion quality poisoning"
Cohesion: 0.18
Nodes (10): Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Reflexion quality poisoning, Rollback (+2 more)

### Community 877 - "Sandbox denial outage"
Cohesion: 0.18
Nodes (10): Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Rollback, Sandbox denial outage (+2 more)

### Community 878 - "Tenant policy anomalies"
Cohesion: 0.18
Nodes (10): Dashboard queries, Diagnosis, Escalation, Impact, Mitigation, Recovery, Rollback, Symptoms (+2 more)

### Community 879 - "generate_api_key"
Cohesion: 0.21
Nodes (7): generate_api_key(), Generate a NIST SP 800-131A compliant API key. Was: uuid4() — 122 bits, not…, test_generate_api_key_custom_prefix(), test_generate_api_key_default_prefix(), test_generate_api_key_hash_deterministic(), test_generate_api_key_unique(), TestAPIKeyGeneration

### Community 880 - "PluginRegistry"
Cohesion: 0.18
Nodes (6): PluginType, StrEnum, PluginRegistry, Any, Plugin registry — manages installed plugins at runtime., Central registry for all installed AgentVerse plugins. Plugins are registered…

### Community 881 - "test_insights.py"
Cohesion: 0.28
Nodes (12): _make_app(), FastAPI, Tests for /insights endpoints., test_agent_health_returns_defaults_without_db(), test_analysis_returns_404_for_unknown_goal(), test_analysis_returns_heuristic_suggestions(), test_benchmarks_returns_platform_data(), test_estimate_requires_auth() (+4 more)

### Community 882 - "._delivery"
Cohesion: 0.22
Nodes (3): TestWebhookDeliveryIsDead, TestWebhookDeliverySignature, WebhookDelivery

### Community 883 - "test_ocr_persist_kb.py"
Cohesion: 0.35
Nodes (12): app(), client(), _img_body(), _make_app(), _mocks(), FastAPI, TestClient, WS-13: standalone /ocr/extract → optional persist-to-KB. The standalone OCR… (+4 more)

### Community 884 - ".exchange_code"
Cohesion: 0.20
Nodes (5): Exchange authorization code for tokens (PKCE flow)., Persist an OAuth token to the database for cross-restart recovery., Encrypt *value* using the vault if available, else return as-is., Remove OAuth state tokens older than 10 minutes., Get and validate a pending OAuth flow by state token.

### Community 885 - "_default_marketplace"
Cohesion: 0.13
Nodes (15): _default_marketplace(), Lines 520-530: template exists, no version history → returns current version., Lines 520+: template not found → 404., Lines 596-597: deploy raises ValueError → 404., Lines 361-362: deploy_bundle calls marketplace.create_bundle., Lines 349-353: agent_store.get raises exception → propagates as 500 (no…, Lines 349-353: publish_template enriches connector_ids from agent_store., Lines 361-362: agent not found in store — continues without enrichment. (+7 more)

### Community 886 - "FeatureFlagService"
Cohesion: 0.18
Nodes (6): FeatureFlagService, Simple feature flag service backed by in-memory dict. In production: backed by…, Check if a feature flag is enabled., Enable a feature flag., Disable a feature flag., Enable all flags for a deployment phase. Returns enabled flags.

### Community 887 - "test_org_mission_hitl_e2e.py"
Cohesion: 0.21
Nodes (13): OrchestrationPlan, _fake_plan_mission_factory(), _HighRiskPlanProvider, _pinned_high_risk_provider(), Any, FakeProvider, e2e_full: AI-org mission HITL — a mission's agent hits a gated action, pauses,…, Deterministic provider that plans one explicit high-risk step. Identical in… (+5 more)

### Community 888 - "TestPdfIngestor"
Cohesion: 0.18
Nodes (6): Happy path: pypdf is available with real-ish page content., Pages with < 30 chars of text are skipped., PdfReader exception produces empty list (not crash)., None/empty extract_text is skipped., When pypdf not installed, returns placeholder chunk., TestPdfIngestor

### Community 889 - "get_my_sla"
Cohesion: 0.33
Nodes (7): get_my_sla(), list_sla_plans(), Any, get, Request, Return the SLA terms for the authenticated tenant's plan., List all plan SLA tiers (public — for pricing page).

### Community 890 - "test_phase_metrics.py"
Cohesion: 0.22
Nodes (11): record_plan_duration(), record_queue_wait(), record_verify_duration(), Tests for phase-level Prometheus histograms., Iterations above 15 should be clamped to '15' label., Negative durations should be clamped to 0., test_record_plan_duration(), test_record_plan_duration_clamps_iteration() (+3 more)

### Community 891 - "chat/templates.py"
Cohesion: 0.24
Nodes (5): _now(), datetime, Prompt templates (personas) for the chat system. Built-in personas + user-saved…, Template, TemplateStore

### Community 892 - "TestSelectVariant"
Cohesion: 0.18
Nodes (6): Line 262: tenant not found → falls back to 'global'., No variants registered → None., No challengers → always control., Lines 269-270: 30% of the time → challenger returned., random.random >= 0.70 → fallback to control., TestSelectVariant

### Community 893 - "evaluate_progress"
Cohesion: 0.40
Nodes (8): evaluate_progress(), _new(), Deterministic validation of material Magentic progress., parametrize, _revision(), test_evidence_removal_cannot_count_as_progress(), test_progress_accepts_only_material_typed_deltas(), test_progress_rejects_cosmetic_repeated_or_unverified_claims()

### Community 894 - "resize_image_b64"
Cohesion: 0.14
Nodes (13): Resize a base64 image if it exceeds max_size bytes. Returns new base64., resize_image_b64(), test_resize_image_b64_handles_invalid(), test_resize_image_b64_small_image_unchanged(), TestResizeImageB64, Small image (under max_size) is returned unchanged., Invalid base64 input returns original string without raising., Large image without Pillow installed returns original base64. (+5 more)

### Community 895 - "test_benchmarking.py"
Cohesion: 0.23
Nodes (10): EvalScorecard, Tests for agent performance benchmarking., _scorecard(), test_benchmark_store_has_db_persistence(), test_compare_agents(), test_record_eval_accumulates(), test_record_eval_creates_benchmark(), test_tenant_isolation() (+2 more)

### Community 896 - "BenchmarkRun"
Cohesion: 0.13
Nodes (12): BenchmarkRun, Persist benchmark run to DB and update in-memory cache., A single benchmark run record., asyncio, Tests for record_run_async and load_history_from_db (lines 128-171)., record_run_async without DB still updates in-memory cache., DB failure must not prevent in-memory cache update., load_history_from_db with no DB returns []. (+4 more)

### Community 897 - "test_gemini_provider_comprehensive.py"
Cohesion: 0.17
Nodes (9): EmbedContentConfig, GenerateContentConfig, _Models, _provider(), Current Google Gen AI async provider coverage., test_capability_flags_follow_model(), test_complete_uses_async_models_and_usage(), test_embed_maps_query_and_document_tasks() (+1 more)

### Community 898 - "TestMaybePromote"
Cohesion: 0.15
Nodes (7): Lines 296-297: key not found → None., Line 304: no challengers → None., Line 298: challenger.run_count < min_runs → None., Line 326: no best_challenger found → None., Lines 315: best_challenger > best_score + significant → promoted., Line 307-308: control.run_count < min_runs → None., TestMaybePromote

### Community 899 - "certify_sandbox"
Cohesion: 0.29
Nodes (7): certify_sandbox(), BaseModel, datetime, timedelta, Evidence-backed production sandbox readiness certification., SandboxCertificationEvidence, test_sandbox_certification_fails_closed_for_missing_or_expired_evidence()

### Community 900 - "_mock_boto3_ses"
Cohesion: 0.15
Nodes (13): _mock_boto3_ses(), _mock_boto3_sqs(), Any, Return a mock boto3 SES client whose methods return the given dicts., test_ses_get_send_statistics(), test_ses_list_identities(), test_ses_send_email(), test_ses_unknown_tool() (+5 more)

### Community 901 - "KokoroTTS"
Cohesion: 0.27
Nodes (3): KokoroTTS, Any, Kokoro TTS — MIT-licensed, 82 M params, high quality, no API key. Requires: pip…

### Community 902 - "OmniVoiceTTS"
Cohesion: 0.29
Nodes (3): OmniVoiceTTS, Any, OmniVoice TTS — k2-fsa/OmniVoice, local, 600+ languages, RTF 0.025. Requires:…

### Community 903 - "test_tool_aware_planning.py"
Cohesion: 0.24
Nodes (9): _agent_source(), Tests for Phase 5: tool-aware planning in graph.py., graph.py _node_plan must inject tool schemas into system prompt., Plan validation should warn about unknown tools., Executor prompt must match the JSON-only tool-call parser contract., Read combined source of graph.py and all node mixin files., test_executor_prompt_requires_structured_tool_call_json(), test_graph_injects_tool_schemas_into_planner() (+1 more)

### Community 904 - "test_kubernetes_runner.py"
Cohesion: 0.21
Nodes (7): build_workload_manifests(), Build a digest-pinned Job and matching default-deny NetworkPolicy., FakeKubernetesClient, request(), test_fake_client_lifecycle_always_cleans_up(), test_manifest_enforces_pod_and_network_isolation(), test_manifest_rejects_mutable_or_privileged_workloads()

### Community 905 - "test_trigger_fire_e2e.py"
Cohesion: 0.27
Nodes (9): _create_webhook_trigger(), Any, e2e_full: trigger → dispatcher → real goal, proven against live Postgres+Redis.…, Sanity: firing a non-existent trigger is a clean 404, not a 503/500., Create a webhook trigger with a goal_template; return its schedule_id., # NOTE: goal_template currently lives on the store record, not on the, # NOTE: reading back the persisted trigger_events audit row is intentionally, test_fire_creates_real_goal_and_dedups_second_fire() (+1 more)

### Community 906 - "RedisDeduplicationCache"
Cohesion: 0.14
Nodes (10): Any, Redis-backed cross-replica deduplication cache. Prevents duplicate in-flight…, Check if identical goal is already in-flight. Returns goal_id or None., Register a goal to prevent duplicates during its execution window., Remove dedup entry after goal completes., RedisDeduplicationCache, RedisDeduplicationCache must be importable., RedisDeduplicationCache must not crash when Redis is unavailable. (+2 more)

### Community 907 - "load/goal_submission.js"
Cohesion: 0.20
Nodes (8): errorRate, GOALS, goalsFailed, goalsSubmitted, options, pollLatency, submitLatency, VUS

### Community 908 - "test_celery_critical.py"
Cohesion: 0.20
Nodes (9): Tests for Celery infrastructure critical fixes., Keycloak realm-export.json must exist for docker-compose to start., OTel collector config must exist for the otel-collector service., fire_due_schedules must use continue not raise on per-schedule errors., goals_dlq must be consumed by the worker — check docker-compose., test_fire_due_schedules_continues_on_error(), test_goals_dlq_queue_in_worker_queues(), test_keycloak_realm_file_exists() (+1 more)

### Community 909 - "test_hitl_db_persistence.py"
Cohesion: 0.22
Nodes (13): _make_tenant(), asyncio, Tests that HITL approvals are persisted to DB when db_session_factory is set., HITLGateway._db_session_factory starts as None (no DB by default)., When _db_session_factory is set, new approval requests fire DB persistence task., When _db_session_factory is None (default), no DB call is attempted., request_approval must return immediately even if DB persist is slow., Verify main.py sets _hitl._db_session_factory = db_factory in lifespan. (+5 more)

### Community 910 - "test_sse_bridge_sentinel.py"
Cohesion: 0.24
Nodes (8): asyncio, Test SSE bridge sends sentinel on terminal events (FIX H9/H10)., subscribe_events must create a bounded queue to prevent OOM., _subscribe_celery_goal_events must send _SENTINEL after terminal events., test_bridge_sends_sentinel_for_terminal_events(), test_sentinel_sent_on_goal_complete(), test_sentinel_sent_on_goal_failed(), test_subscribe_events_uses_bounded_queue()

### Community 911 - "get_sandbox_config"
Cohesion: 0.28
Nodes (8): get_sandbox_config(), Any, get, Request, Per-tenant staging/sandbox environment. Provides a sandboxed copy of the…, Submit a goal in sandbox mode — uses SimulationRunner, no real tools called., Return sandbox configuration for the tenant., submit_sandbox_goal()

### Community 912 - "has_feature"
Cohesion: 0.39
Nodes (3): has_feature(), Return True if the tenant's plan includes the given feature., TestHasFeature

### Community 913 - "meta_agent.py"
Cohesion: 0.27
Nodes (11): _cap_name(), _clean_llm_name(), _connector_id_from_value(), _derive_agent_name(), _normalize_connectors(), Any, Meta-agent — decomposes one NL command into a complete agent configuration.…, Accept the LLM's name only if it's real; otherwise derive one. (+3 more)

### Community 914 - "agent_credentials.py"
Cohesion: 0.29
Nodes (5): generate_agent_api_key(), is_agent_key(), Per-Agent Credential System ============================ Every agent can have…, Generate (raw_key, key_hash) for an agent-scoped API key., Return True if this key is an agent-scoped key.

### Community 915 - "AgentBenchmark"
Cohesion: 0.18
Nodes (5): AgentBenchmark, Any, EvalScorecard, Return comparison dict for multiple agents, sorted by avg score., Record an eval scorecard and update the agent's benchmark.

### Community 916 - "Reranker"
Cohesion: 0.36
Nodes (3): Any, Legacy LLM reranker retained for non-model-specific callers., Reranker

### Community 917 - "test_prompted_tool_fallback.py"
Cohesion: 0.26
Nodes (12): _extract_json_objects(), parse_prompted_tool_calls(), Extract top-level JSON objects from free text via a balanced-brace scan.…, Parse prompted-format tool calls from a model's text response. Recognises…, Prompted tool-calling fallback for OpenAI-compatible servers. When a server has…, test_extract_json_handles_nested_and_braces_in_strings(), test_parse_accepts_name_and_input_aliases(), test_parse_handles_prose_around_json() (+4 more)

### Community 918 - "test_cost_atomic.py"
Cohesion: 0.22
Nodes (12): asyncio, Test atomic cost check-and-increment prevents budget overrun under concurrency., Concurrent requests must not both succeed when combined cost exceeds budget., Sequential requests within budget all succeed., Cost equal to budget limit is allowed; one cent over is denied., With no Redis client, try_record_and_check always returns True (fail-open)., Budget counters for separate tenants are independent., test_cost_controller_allows_within_budget() (+4 more)

### Community 919 - "TestMakeAgentGraphForTenant"
Cohesion: 0.15
Nodes (6): Lines 619-636, 647-657, 701-709, 719-722, 733-744., Lines 650-651: AnthropicProvider raise → fallback., Lines 656-657: OpenAICompatibleProvider raise → fallback., Lines 701-709: agent store config loading., Lines 733-744: circuit breakers wired per connector., TestMakeAgentGraphForTenant

### Community 920 - "TestRedisCache"
Cohesion: 0.22
Nodes (5): Lines 150-155: publish called on redis., No redis → returns immediately without error., Lines 154-155: redis.publish raises → silenced., Line 146: _redis attribute set., TestRedisCache

### Community 921 - "TestIsSignificant"
Cohesion: 0.22
Nodes (5): Lines 339-340: < 10 samples → False., Lines 347-349: scipy not available → mean comparison., Lines 348-349: challenger NOT > control * 1.05 → False., Lines 340-346: scipy available → mannwhitneyu used., TestIsSignificant

### Community 922 - "test_http_fallback.py"
Cohesion: 0.38
Nodes (10): _mock_httpx(), Any, asyncio, WS-13: RPAExecutor real-HTTP fallback (browser-less REAL page text). When…, _sim_executor(), test_extract_with_inline_url_fetches_directly(), test_flag_off_keeps_simulation(), test_open_then_extract_returns_real_text() (+2 more)

### Community 923 - "ToolTrace"
Cohesion: 0.25
Nodes (4): Any, ToolTrace — per-goal observability trace for tool calls., ToolCallRecord, ToolTrace

### Community 924 - "MacOSSayTTS"
Cohesion: 0.25
Nodes (3): MacOSSayTTS, macOS System TTS — uses the built-in `say` command, no downloads required.…, macOS built-in TTS via the `say` command. No model files required.

### Community 925 - "AuditLog"
Cohesion: 0.20
Nodes (11): ApprovalRequest, AuditLog, PolicyVersion, Base, SQLAlchemy ORM models for governance: audit log, approval requests, policy…, Append-only audit trail — immutability enforced by DB trigger., Human-in-the-loop approval gate for high-risk agent actions., Immutable snapshot of a policy at a given version number (migration 0056). (+3 more)

### Community 926 - "LLMConfigStore"
Cohesion: 0.07
Nodes (29): LLMConfigStore, Any, Redis-backed LLM configuration store. Stores per-tenant LLM provider…, Reads and writes per-tenant LLM provider config to/from Redis. Args:…, Store the LLM config for *tenant_id* in Redis., Return the LLM config for *tenant_id*, or *None* if not configured., Remove the LLM config for *tenant_id* from Redis., Wire the process-wide singleton (called once from ``create_app``). (+21 more)

### Community 927 - "todoist_server.py"
Cohesion: 0.39
Nodes (7): call_tool(), _call_tool_inner(), Any, Todoist MCP server — Todoist REST API v2 integration. Environment variables:…, Resolve the Todoist API token from connector credentials (set via the…, _resolve_token(), _todoist_headers()

### Community 928 - "api/test_memory_api.py"
Cohesion: 0.31
Nodes (8): _get_knowledge_routes(), _get_memory_routes(), Tests for Phase 10 memory inspection + delete endpoints. These tests verify…, Directly inspect the knowledge router without creating a full app., Directly inspect the memory router without creating a full app., test_knowledge_url_ingest_endpoint_exists(), test_memory_list_endpoint_exists(), test_memory_recall_endpoint_exists()

### Community 929 - "test_mfa_module.py"
Cohesion: 0.22
Nodes (5): Test MFA module structure and TOTP logic., Correct TOTP code must pass verification., Wrong TOTP code must fail., test_mfa_verify_correct_totp_code(), test_mfa_verify_wrong_code()

### Community 930 - "advanced_services.py"
Cohesion: 0.05
Nodes (33): ChannelRouter, CollectiveIntelligence, ConversationTurn, get_channel_router(), get_collective_intel(), get_mcp_full_spec(), get_sub_tenant_manager(), MCPFullSpec (+25 more)

### Community 931 - "Bulkhead"
Cohesion: 0.17
Nodes (8): Bulkhead, Async context manager that tracks concurrency without reading private semaphore…, Bulkhead with max=3 allows 3 simultaneous callers., Bulkhead.available_slots() decrements while slots are held., Bulkhead with max=1 blocks second caller until first releases., test_bulkhead_allows_concurrent_calls_within_limit(), test_bulkhead_available_slots_decrements(), test_bulkhead_blocks_when_limit_reached()

### Community 932 - "api/test_replay.py"
Cohesion: 0.27
Nodes (11): _get_routes(), _make_app(), Return all registered paths by walking FastAPI's OpenAPI schema. app.routes…, GET /goals/{id}/replay must be registered., GET /goals/{id}/timeline must be registered., Tracing must work even without OTLP endpoint., test_in_process_tracing_configured(), test_replay_endpoint_exists() (+3 more)

### Community 933 - "StoreGateway"
Cohesion: 0.17
Nodes (10): Test gateway that keeps core-execution tests on the canonical boundary., Per-step retrieval must return KB context for relevant queries., Per-step retrieval with empty KB must return structured result, not empty…, When KB is empty and web is available, RetrieverTool uses web fallback., ContextPipeline.run() must produce a non-empty planner context when KB has data., StoreGateway, test_context_pipeline_builds_planner_context(), test_per_step_retrieval_returns_context() (+2 more)

### Community 935 - "test_k8s_manifests.py"
Cohesion: 0.42
Nodes (8): kustomization_resources(), read_manifest(), test_backend_and_worker_pdbs_protect_rollouts(), test_external_secret_replaces_raw_secret_placeholder(), test_kustomization_includes_phase_12_resources(), test_migration_job_runs_alembic_upgrade_head_before_rollout(), test_networkpolicy_limits_backend_worker_data_store_access(), test_worker_hpa_uses_queue_depth_external_metric()

### Community 936 - "test_message_editing.py"
Cohesion: 0.17
Nodes (11): Tests for message editing and branching — 8 cases., session(), svc(), test_delete_message(), test_edit_cannot_edit_assistant_message(), test_edit_only_prunes_subsequent(), test_edit_preserves_remaining_messages(), test_edit_prunes_subsequent_messages() (+3 more)

### Community 937 - "TestIsIPAllowed"
Cohesion: 0.15
Nodes (4): Malformed CIDR entries are skipped without raising., TestIsIPAllowed, IP allowlist CIDR matching must work correctly., test_ip_in_cidr_check()

### Community 938 - "._bayesian_prob_better"
Cohesion: 0.08
Nodes (22): Fix 6: Thread-safe Thompson sampling using numpy.default_rng(). Was: global…, When candidate mean >> control mean, probability should be > 0.9., When control mean >> candidate mean, probability should be < 0.2., Equal means should yield probability near 0.5., Should fall back to pure-Python implementation when numpy is unavailable., test_bayesian_prob_better_candidate_clearly_better(), test_bayesian_prob_better_control_clearly_better(), test_bayesian_prob_better_equal_means_around_half() (+14 more)

### Community 939 - "VoiceWebhookAdapter"
Cohesion: 0.24
Nodes (6): Any, Trim text to TTS-friendly length, ending at sentence boundary., Voice command adapter — receives pre-transcribed voice input. Typical flow: 1.…, Verify HMAC-SHA256 signature on the payload., Return a TTS-optimised response (shorter, conversational)., VoiceWebhookAdapter

### Community 940 - "test_self_optimizer_auto_apply_flag.py"
Cohesion: 0.26
Nodes (13): _conclude_db_with_winning_candidate(), _db_returning(), asyncio, Auto-apply gating for SelfOptimizerV2 — the closed self-improvement loop. When…, A mock async-session factory whose experiment JOIN yields a candidate win., auto_apply=False: winner is recorded, but apply_suggestion is NOT called., auto_apply=True (default): the winning candidate is applied automatically., test_apply_pending_applies_candidate_winner() (+5 more)

### Community 941 - "SupervisorPattern"
Cohesion: 0.24
Nodes (4): SupervisorPattern, SupervisorPattern has correct pattern_id., test_supervisor_pattern_registered(), TestSupervisorPattern

### Community 942 - "_decode_header_value"
Cohesion: 0.27
Nodes (4): _decode_header_value(), Decode email header (handles encoded headers like =?UTF-8?...)., TestDecodeHeaderValue, TestImapListenerDecodeHeader

### Community 943 - "roles.py"
Cohesion: 0.24
Nodes (10): _add(), count_roles(), get_role_by_name(), get_roles_for_dept(), PART 4 — Complete Role Taxonomy (456 roles across 22 departments). Provides: -…, Full role definition per PART 5 spec., Return all role definitions for a department., Case-insensitive role lookup by name. (+2 more)

### Community 944 - "ModelStats"
Cohesion: 0.23
Nodes (4): ModelStats, CostOptimizer — tracks LLM cost per goal type and suggests model downgrades.…, Comprehensive tests for app/intelligence/cost_optimizer.py — targeting 95%+…, TestModelStats

### Community 945 - "codeact.py"
Cohesion: 0.16
Nodes (15): CodeActionState, CodeActPhase, CodeActRuntime, CodeActState, Any, BaseModel, StrEnum, Bounded governed CodeAct strategy with deterministic stall detection. (+7 more)

### Community 946 - ".list_async"
Cohesion: 0.22
Nodes (4): Load active DB agents into memory on startup., Convert an Agent ORM row to a plain dict (same shape as in-memory store)., Read a single agent directly from DB; fall back to memory cache., Read all agents for a tenant directly from DB; fall back to memory cache.

### Community 947 - "TestInputClamping"
Cohesion: 0.15
Nodes (7): Input validation: top_k clamped to [1, 100], query length capped at 10 000., top_k > 100 must be clamped to 100., top_k < 1 must be raised to 1., Queries longer than 10 000 chars must be truncated., Queries at or below 10 000 chars must not be modified., search_knowledge endpoint code applies the clamp inline., TestInputClamping

### Community 948 - "OAuthToken"
Cohesion: 0.11
Nodes (15): OAuthToken, Token is considered expired 60 seconds before actual expiry., test_oauth_token_defaults(), test_oauth_token_expired_old(), test_oauth_token_expiry_boundary_60s_buffer(), test_oauth_token_not_expired_fresh(), Lines 168-172: positional call style., Lines 173-177: keyword call style. (+7 more)

### Community 949 - "WebCrawlConnector"
Cohesion: 0.24
Nodes (6): BaseConnector, register, Web crawl connector with sitemap and robots.txt support., Crawl seed URLs and discover new/changed pages., WebCrawlConnector, test_web_crawl_connector_source_type()

### Community 950 - "mailchimp_server.py"
Cohesion: 0.39
Nodes (7): _auth(), _base(), call_tool(), Any, Mailchimp MCP server — lists, campaigns, and members via Mailchimp API v3.…, _server_prefix(), _subscriber_hash()

### Community 951 - "test_billing_real.py"
Cohesion: 0.18
Nodes (9): Test billing returns 503 when not configured (not placeholder URL) (FIX…, Settings must have typed stripe_api_key field., checkout endpoint must raise 503 when STRIPE_API_KEY is not set., checkout must use stripe.checkout.Session.create(), not a hardcoded URL., When STRIPE_API_KEY not set, must 503, not return placeholder URL., test_billing_returns_503_not_placeholder(), test_checkout_raises_503_when_stripe_not_configured(), test_checkout_uses_real_stripe_session_create() (+1 more)

### Community 952 - "test_eval_judge_output.py"
Cohesion: 0.28
Nodes (8): GoldenTaskResult, Test LLM judge uses actual agent output not goal prompt (FIX 0.10)., run_with_llm_judge must use task_result.actual_output., _run_task return must include actual_output=all_output., test_actual_output_defaults_empty(), test_golden_task_result_has_actual_output(), test_judge_uses_actual_output_not_goal(), test_run_task_returns_actual_output()

### Community 953 - "test_openapi_schema.py"
Cohesion: 0.18
Nodes (6): openapi_schema(), Tests that OpenAPI schema includes all registered endpoints., Schema must have at least 65 paths (we have 70+)., Every endpoint should define at least one response., test_all_endpoints_have_response_schemas(), test_schema_has_minimum_path_count()

### Community 955 - "GoalTokenStore"
Cohesion: 0.33
Nodes (3): GoalTokenStore, Any, Tracks active goal tokens and supports revocation.

### Community 956 - "test_tts_engine.py"
Cohesion: 0.08
Nodes (35): get_tts(), Return the configured TTS provider singleton. Falls back through kokoro →…, reset_providers(), BrowserFallbackTTS, Browser fallback TTS — returns empty WAV; browser uses Web Speech API instead.…, get_model(), Any, Native TTS engine — thin shim over the abstract provider system. Public API… (+27 more)

### Community 957 - "RoleResolver"
Cohesion: 0.22
Nodes (7): Any, Resolve the complete permission set for a role, traversing parent chain., RoleResolver, A role that references itself should not infinite loop., test_role_resolver_cycle_guard(), test_role_resolver_role_not_found(), test_role_resolver_simple_role()

### Community 958 - "asyncio"
Cohesion: 0.18
Nodes (11): asyncio, Export payload contains structured data section with tenant_profile., Export payload data section contains all expected collection keys., Export payload always includes tenant_id, plan, and timestamp at top level., Download URL follows the /compliance/export/{id}/download pattern., Created export request is immediately retrievable by ID., test_compliance_export_download_url_format(), test_compliance_export_has_structured_payload() (+3 more)

### Community 959 - "test_hitl_gate.py"
Cohesion: 0.29
Nodes (5): _agent_source(), Test HITL gate is enforced by default in fully-autonomous mode (FIX C1/C7)., graph.py must gate the write_high bypass behind…, Read combined source of graph.py and all node mixin files., test_hitl_gate_source_contains_env_flag()

### Community 960 - "TestSubmitGoal"
Cohesion: 0.18
Nodes (4): Lines 1669-1674: dry-run decrements Redis counter., Line 1632: persistence_mode spawns _run_agent_loop_persistent., Lines 1467-1520: auto-routing when agent_id is None., TestSubmitGoal

### Community 961 - "_build_goal_kwargs_for_alert"
Cohesion: 0.24
Nodes (11): _build_goal_kwargs_for_alert(), Build goal submission kwargs for an external alert trigger. Returns a dict…, TempPathFactory, FILE_DROP, ALERTMANAGER, DATADOG, PAGERDUTY trigger handlers., _build_goal_kwargs_for_alert must build goal for alertmanager., FILE_DROP handler must submit a goal for each new file found., test_build_goal_kwargs_for_alert_alertmanager(), test_build_goal_kwargs_for_alert_datadog() (+3 more)

### Community 962 - "TestDocxIngestor"
Cohesion: 0.25
Nodes (4): Happy path with mocked python-docx., Paragraphs shorter than 20 chars are filtered out., Exception returns empty list., TestDocxIngestor

### Community 963 - "agent/consensus.py"
Cohesion: 0.24
Nodes (6): ConsensusResult, 3-Way Consensus Verification for high-stakes goals. For goals touching…, Run all configured verifiers and compute majority verdict., Run the LLM judge with a rubric-scored prompt., _run_judge(), VerifierVote

### Community 964 - "get_public_status"
Cohesion: 0.29
Nodes (6): get_public_status(), Any, get, Request, Public status page API — no authentication required., Public system health — used by the status page, no auth required.

### Community 965 - "test_knowledge_graph_e2e.py"
Cohesion: 0.33
Nodes (10): _build_source_and_doc(), _fake_embedder(), _make_collection(), Any, e2e_full: knowledge graph auto-population from real document ingestion. Proves…, A second tenant must not see the first tenant's auto-populated graph., Swap the wired ingestion pipeline's embedder for a deterministic 768-dim fake.…, Ingesting a document through the wired pipeline creates real KG nodes. The… (+2 more)

### Community 966 - "mattermost_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Mattermost MCP server — interact with Mattermost REST API v4. Environment:…

### Community 967 - "0097_coordination_runtime.py"
Cohesion: 0.38
Nodes (5): _domain_column(), Column, Add the canonical durable coordination runtime. Revision ID:…, _rls_policy(), upgrade()

### Community 968 - "Any"
Cohesion: 0.20
Nodes (5): Any, Upgrade the DB session factory (called during lifespan startup)., Upgrade the Redis client (called during lifespan startup)., Generate an RSA keypair and store the public key in DB. The private key is…, List all credentials for an agent. Private keys are never returned.

### Community 969 - "_name_tokens"
Cohesion: 0.20
Nodes (9): _name_tokens(), Tool risk classification for governed real tool calls. Covers all major…, Split a camelCase / snake_case / PascalCase name into lowercase tokens., test_name_tokens_all_caps(), test_name_tokens_camel_case(), test_name_tokens_empty(), test_name_tokens_mixed(), test_name_tokens_pascal_case() (+1 more)

### Community 970 - ".score"
Cohesion: 0.17
Nodes (8): Any, EvalScorecard, Use LLM to rate how logically coherent the steps are relative to the goal.…, Use LLM to rate how accurately the agent achieved the goal. Falls back to the…, Score asynchronously, replacing heuristic coherence AND accuracy with LLM…, Score AND persist results to the evaluations table. Writes to the actual DB…, Score tool call efficiency: redundant/failed calls lower the score. Returns a…, Produce a scorecard for a completed goal run.

### Community 971 - "_setup_sigterm"
Cohesion: 0.20
Nodes (10): Register a SIGTERM handler so Celery workers shut down gracefully. LangGraph…, _setup_sigterm(), _setup_sigterm can be called without raising., The SIGTERM handler raises SystemExit(0) when triggered., _setup_sigterm catches OSError/ValueError from invalid signal contexts., test_setup_sigterm_handler_raises_system_exit(), test_setup_sigterm_handles_os_error_gracefully(), test_setup_sigterm_registers_handler_without_error() (+2 more)

### Community 972 - "MemoryWriteRequest"
Cohesion: 0.17
Nodes (17): MemoryWriteRequest, mem_tenant(), Any, e2e_full: canonical memory inspector read API + uniform TTL purge on the live…, Fresh tenant per test → deterministic counts; yields (tenant_id, http client)., test_memory_inspector_returns_categorized_records(), test_ttl_purge_removes_all_kinds_against_postgres(), _write() (+9 more)

### Community 973 - "test_guardrail_kwarg.py"
Cohesion: 0.22
Nodes (9): _agent_source(), Test that GuardrailChecker.check_output is called with keyword argument., GuardrailChecker.check_output must accept 'output=' kwarg., Positional arg must raise TypeError (proving the old bug was real)., graph.py source must use output= keyword for check_output., Read combined source of graph.py and all node mixin files., test_guardrail_check_output_accepts_keyword(), test_guardrail_check_output_fails_without_keyword() (+1 more)

### Community 974 - "freshsales_server.py"
Cohesion: 0.40
Nodes (4): call_tool(), _headers(), Any, Freshsales CRM MCP server — contacts, deals, and accounts management.…

### Community 975 - "test_supervisor_goal_id_threading.py"
Cohesion: 0.24
Nodes (6): _FakeGoalService, Any, asyncio, Regression: SubAgentTask must carry the REAL goal_id from submit_goal so the…, test_run_threads_real_goal_id_onto_each_task(), test_sub_agent_task_has_goal_id_field()

### Community 976 - "looker_server.py"
Cohesion: 0.33
Nodes (6): call_tool(), _get_token(), Any, AsyncClient, Looker MCP server — Looker Business Intelligence. Environment: LOOKER_BASE_URL:…, Obtain a Looker API bearer token (cached).

### Community 977 - "_FakeDb"
Cohesion: 0.18
Nodes (7): _FakeDb, Any, asyncio, Self-improvement robustness: applying an optimization must persist the live…, Async-context DB stand-in; optionally raises on execute (simulated outage)., test_critical_apply_failure_reports_false(), test_history_failure_does_not_negate_live_apply()

### Community 978 - "models/__init__.py"
Cohesion: 0.05
Nodes (44): Artifact DB model for storing RPA outputs, screenshots, reports, etc., SQLAlchemy ORM model for durably-stored guardrail rules (P1-4). Rules used to…, Base, SQLAlchemy declarative base shared across all models. All domain models are re-…, KnowledgeEdge, KnowledgeNode, Base, Knowledge Graph DB models. (+36 more)

### Community 979 - "yotpo_server.py"
Cohesion: 0.33
Nodes (6): call_tool(), _get_utoken(), Any, AsyncClient, Yotpo MCP server — Yotpo reviews, loyalty points, and marketing campaigns.…, Exchange app_key+secret for a short-lived uToken.

### Community 980 - "Any"
Cohesion: 0.28
Nodes (3): EvalSuiteResult, Any, Run an eval suite with LLM-as-judge scoring and optionally persist results to…

### Community 981 - "_cleanup_expired_crdt_tokens"
Cohesion: 0.24
Nodes (6): _cleanup_expired_crdt_tokens(), Evict tokens past their TTL from the in-memory store., Yjs CRDT WebSocket — binary message fan-out with Redis pub/sub. Authentication…, yjs_crdt_sync(), Short-lived CRDT tokens are cleaned up when expired., TestCollabCRDTTokenStore

### Community 982 - ".refresh_token"
Cohesion: 0.33
Nodes (3): Any, Flexible token lookup. Supports two call styles: - Keyword:…, Refresh an expired access token.

### Community 983 - "parametrize"
Cohesion: 0.20
Nodes (10): _assert_tool_definition(), parametrize, Every server exposes a non-empty TOOL_DEFINITIONS list., Every server exposes an async call_tool() callable., Tool names within a server must be unique., All 'required' fields in parameters must reference existing properties., test_all_required_params_are_listed(), test_server_has_callable_call_tool() (+2 more)

### Community 984 - "main_services.py"
Cohesion: 0.28
Nodes (8): get_service_health(), init_core_services(), init_intelligence_services(), Any, Service initialization helpers extracted from main.py. Reduces main.py…, Initialize core platform services after DB+Redis are available., Initialize LLM providers, embedders, and evaluation services., Check health of all initialized services for /health endpoint.

### Community 985 - "query_reformulator.py"
Cohesion: 0.25
Nodes (5): Any, QueryReformulation, QueryReformulator — generates alternative query phrasings on empty results., LLM-driven reformulation. Falls back to rule-based., Return one explicit reformulation without hiding provider failures.

### Community 987 - ".check"
Cohesion: 0.22
Nodes (4): Any, Lock, Return ``True`` if the request is within the rate limit, else ``False``., Check if a request is within rate limits and record it if allowed. Returns:…

### Community 988 - "test_folders.py"
Cohesion: 0.22
Nodes (8): Tests for session folders CRUD — 6 cases., svc(), test_create_folder(), test_delete_folder(), test_delete_folder_cascades_to_null(), test_list_folders_isolated(), test_move_session_to_folder(), test_session_count_in_folder()

### Community 989 - "AgentVerse Load Tests"
Cohesion: 0.29
Nodes (6): AgentVerse Load Tests, Autoscale signal, CI smoke profile, Requirements, Running k6, Running locust

### Community 990 - "qa-agent.md"
Cohesion: 0.29
Nodes (6): Common failure patterns, Coverage thresholds, Key security checks, Known pre-existing failures, Quick commands, Test matrix — what to run for each PR

### Community 991 - "wave_server.py"
Cohesion: 0.47
Nodes (5): call_tool(), _gql(), Any, AsyncClient, Wave MCP server — accounting, invoices, customers, and transactions via…

### Community 992 - "SlackChannelAdapter"
Cohesion: 0.33
Nodes (4): Any, Slack channel adapter — handles slash commands and @mention events., Format as Slack Block Kit message., SlackChannelAdapter

### Community 993 - "smoke.js"
Cohesion: 0.33
Nodes (6): errorRate, handleSummary(), healthLatency, metricsLatency, options, textSummary()

### Community 994 - "test_mission_execute_wired_services.py"
Cohesion: 0.38
Nodes (6): _make_app(), Any, asyncio, FastAPI, Regression: the mission-execute path must use the REQUEST's wired app.state…, test_mission_execute_threads_request_app_state()

### Community 995 - "WebhookChannelAdapter"
Cohesion: 0.31
Nodes (5): _infer_command_from_trigger(), Any, Generic HMAC-signed webhook receiver., Infer a natural-language command from a webhook trigger event., WebhookChannelAdapter

### Community 996 - "activecampaign_server.py"
Cohesion: 0.40
Nodes (4): call_tool(), _headers(), Any, ActiveCampaign MCP server — email marketing & CRM contacts, lists, and…

### Community 997 - "test_append_operation_in_memory_version_conflict"
Cohesion: 0.22
Nodes (9): asyncio, VersionConflictError must be importable and raised on conflict., In-memory mode raises VersionConflictError when expected_version is wrong., In-memory mode succeeds when expected_version matches current., No expected_version means no conflict check — always succeeds., test_append_operation_in_memory_correct_version(), test_append_operation_in_memory_no_version_check(), test_append_operation_in_memory_version_conflict() (+1 more)

### Community 998 - "customerio_server.py"
Cohesion: 0.47
Nodes (5): _app_headers(), call_tool(), Any, Customer.io MCP server — customers, events, and campaigns. Environment:…, _track_headers()

### Community 999 - "facebook_conversions_server.py"
Cohesion: 0.31
Nodes (8): call_tool(), Any, AsyncClient, Facebook Conversions MCP server — Facebook Conversions API server-side event…, SHA256 hash a string for PII fields., Send events to the Facebook Conversions API., _send_events(), _sha256()

### Community 1000 - "error_response"
Cohesion: 0.33
Nodes (5): error_response(), JSONResponse, Request, Standard error response utilities., Return a standardized error response with a correlation_id. The correlation_id…

### Community 1001 - "generate_api_key"
Cohesion: 0.25
Nodes (8): generate_api_key(), Generate a NIST-compliant API key (256 bits from os.urandom). Returns a URL-…, test_generate_api_key_minimum_length(), test_generate_api_key_prefix(), test_generate_api_key_unique(), test_generate_api_key_url_safe(), generate_api_key() must produce high-entropy keys (≥ 44 chars, av_ prefix)., test_cryptographic_key_passes_entropy_check()

### Community 1002 - "0101_magentic_moa.py"
Cohesion: 0.47
Nodes (4): _base_columns(), Column, _rls(), upgrade()

### Community 1003 - "0102_camel_generative_swarm_auction.py"
Cohesion: 0.47
Nodes (4): _base(), Column, _rls(), upgrade()

### Community 1004 - "0104_memory_learning.py"
Cohesion: 0.47
Nodes (4): Column, _rls(), _timestamps(), upgrade()

### Community 1005 - "_Message"
Cohesion: 0.25
Nodes (4): _Message, Edit a user message and return (updated_message, pruned_message_ids). All…, Simple substring search — production uses Postgres FTS index., Return a brief summary of the session conversation.

### Community 1007 - "test_ingestion_pipeline_e2e.py"
Cohesion: 0.32
Nodes (7): _fake_embedder(), Any, e2e_full: document ingestion → pgvector → retrievable by query. The plan's…, A query against an empty sibling collection must not return the document., Pin a deterministic 768-dim embedder for both ingest and search. Ingest reads…, test_document_ingests_and_is_retrievable(), test_search_isolated_by_collection()

### Community 1008 - ".publish_target"
Cohesion: 0.29
Nodes (5): Any, Receipt from a completed publish step (connector, link/output, time)., True when a finished deliverable is waiting at the publish approval gate., The mission's publish destination (no argument template — shape only)., computed_field

### Community 1009 - "pandadoc_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, PandaDoc MCP server — document creation, templates, and eSignature.…

### Community 1012 - "TestPdfIngestor"
Cohesion: 0.18
Nodes (5): Mocked pypdf returns text chunks., Pages with < 30 chars of text are skipped., Page returning None for text is handled., Exception during extraction returns empty list., TestPdfIngestor

### Community 1013 - "TestDocxIngestor"
Cohesion: 0.22
Nodes (5): When python-docx not installed, returns placeholder chunk., Happy path: python-docx is available and parses paragraphs., If Document() raises, returns []., Paragraphs shorter than 20 chars are filtered out., TestDocxIngestor

### Community 1014 - "test_mission_decomposition.py"
Cohesion: 0.52
Nodes (6): asyncio, WS-2b: real mission decomposition, assignment, handoff and progress events.…, _service(), test_decompose_mission_heuristic_multiple_subtasks(), test_decompose_mission_single_department_uses_phases(), test_decomposition_emits_assignment_handoff_progress_events()

### Community 1015 - "test_capabilities.py"
Cohesion: 0.46
Nodes (6): _auth_header(), _make_app(), Tests for Phase 2: Capability Registry API., test_capabilities_endpoint_returns_list(), test_capabilities_search_endpoint_exists(), test_missing_capabilities_endpoint_exists()

### Community 1016 - "TestEvalSuiteWave4"
Cohesion: 0.25
Nodes (5): Lines 270-275, 372-375 — exception paths in eval_suite., Lines 270-275: TimeoutError in _run_task subscribe_events., Lines 372-375: DB persist exception → logged, result still returned., Lines 497-503: check_agent_rollout_gate with empty DB result., TestEvalSuiteWave4

### Community 1017 - "docusign_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, DocuSign MCP server — eSignature envelopes and signing workflows. Environment:…

### Community 1018 - "TestPersistOutcome"
Cohesion: 0.25
Nodes (4): Line 191: col = 'win_count' when won=True., col = 'loss_count' when won=False., Lines 200-202: DB exception → logged, no raise., TestPersistOutcome

### Community 1019 - "test_tool_reliability.py"
Cohesion: 0.32
Nodes (6): asyncio, Tests for P2.3 tool reliability memory., test_tool_reliability_api_endpoint_exists(), test_tool_reliability_no_db_get_unreliable_empty(), test_tool_reliability_record_failure(), test_tool_reliability_record_success()

### Community 1020 - "TestLockReleaseFix"
Cohesion: 0.25
Nodes (5): H12: Distributed lock must be released successfully after goal completion., H12: _SyncGoalLock class must be defined in tasks module., H12: _SyncGoalLock.acquire and release must work with a mock sync Redis., H12: run_goal must use _SyncGoalLock (or single asyncio.run) instead of async…, TestLockReleaseFix

### Community 1021 - "WhatsAppChannelAdapter"
Cohesion: 0.39
Nodes (3): Any, WhatsApp Business Cloud API adapter., WhatsAppChannelAdapter

### Community 1022 - "clickup_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _clickup_headers(), Any, ClickUp MCP server — ClickUp REST API v2 integration. Environment variables:…

### Community 1023 - "agent/supervisor.py"
Cohesion: 0.29
Nodes (6): Supervisor agent — coordinates multiple sub-agents to achieve complex goals.…, SupervisionResult, test_supervision_result_defaults(), test_supervision_result(), Lines 97-118: supervisor mode returns multi_agent result., test_submit_goal_supervisor_mode_success()

### Community 1024 - "linear_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _gql(), Any, Linear MCP server — Linear GraphQL API integration. Environment variables:…

### Community 1025 - "_FakeLuaScript"
Cohesion: 0.29
Nodes (4): _FakeLuaScript, Return a fake Lua script executor that simulates the atomic check-and-increment., Simulates the _ATOMIC_INCREMENT_SCRIPT Lua behaviour for tests., Execute the Lua script atomically using the FakeRedis lock. Handles two script…

### Community 1026 - "load_roles_from_db"
Cohesion: 0.38
Nodes (7): load_roles_from_db(), Any, Load roles for a user from the user_roles table. Returns empty tuple if DB…, asyncio, test_load_roles_db_exception_returns_empty(), test_load_roles_no_db_returns_empty(), test_load_roles_with_db()

### Community 1028 - "._assign"
Cohesion: 0.29
Nodes (3): Simple round-robin — for now returns None (DB-backed in production)., Picks reviewer with fewest pending requests., Stub — hooks into a skills registry in production.

### Community 1030 - "smartsuite_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), Any, SmartSuite MCP server — SmartSuite REST API v1 integration. Environment…, _smartsuite_headers()

### Community 1031 - "ApprovalChainEngine"
Cohesion: 0.03
Nodes (65): ApprovalChain, ApprovalChainEngine, ApprovalChainRegistry, ApprovalRequest, get_approval_engine(), Any, Cross-department approval chain engine. Defines approval chains for high-risk…, Runtime engine for approval chain matching and request management. G-20:… (+57 more)

### Community 1032 - "monday_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _monday_gql(), Any, Monday.com MCP server — monday.com GraphQL API v2 integration. Environment…

### Community 1033 - "notion_server.py"
Cohesion: 0.39
Nodes (7): call_tool(), _call_tool_inner(), _format_page(), _notion_headers(), Any, Notion MCP server — Notion REST API v1 integration. Environment variables:…, Extract a clean summary from a raw Notion page object.

### Community 1034 - "teamwork_server.py"
Cohesion: 0.50
Nodes (4): _base(), call_tool(), Any, Teamwork MCP server — project management, tasks, and milestones. Environment:…

### Community 1035 - "NotionSourceConnector"
Cohesion: 0.38
Nodes (4): NotionSourceConnector, BaseConnector, register, BaseConnector adapter that routes Notion pages through the real pipeline. Reads…

### Community 1036 - "expensify_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Expensify MCP server — expense management. Environment:…

### Community 1037 - "smoke_e2e.sh"
Cohesion: 0.43
Nodes (6): hdr(), mint(), no(), ok(), runstatus(), smoke_e2e.sh script

### Community 1038 - "grafana_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Grafana MCP server — monitoring and observability. Environment: GRAFANA_URL:…

### Community 1039 - "test_platform_wiring_e2e.py"
Cohesion: 0.40
Nodes (5): Any, e2e_full: the anti-disconnect gate (Phase-3 Row 18 — core platform workflows).…, A real DB+Redis round-trip: signup persists a tenant and returns a key., test_backends_reachable_via_signup_roundtrip(), test_lifespan_upgrades_services_to_db_redis_backed()

### Community 1040 - "tool_policy.py"
Cohesion: 0.40
Nodes (3): ToolPolicy — per-tool access policy based on tenant, risk, and capability., ToolAccessPolicy, ToolPolicy

### Community 1041 - "pytest_collection_modifyitems"
Cohesion: 0.40
Nodes (5): Config, Item, pytest_collection_modifyitems(), Opt-in guard for the live-backend integration tests.…, _server_reachable()

### Community 1042 - "compliance_endpoints.js"
Cohesion: 0.33
Nodes (5): errorRate, gstLatency, headers, options, policyLatency

### Community 1043 - "Runbook: Backend Instance Down"
Cohesion: 0.33
Nodes (5): Alert, Diagnosis Steps, Resolution, Runbook: Backend Instance Down, Severity

### Community 1044 - "Runbook: High Goal Failure Rate"
Cohesion: 0.33
Nodes (5): Alert, Diagnosis Steps, Resolution, Runbook: High Goal Failure Rate, Severity

### Community 1045 - "security-reviewer.md"
Cohesion: 0.33
Nodes (5): Files to check for security issues, Quick security scan commands, Security checklist — run before every PR merge, Security test commands, Severity escalation

### Community 1048 - "evernote_server.py"
Cohesion: 0.38
Nodes (6): call_tool(), _enml_wrap(), _headers(), Any, Evernote MCP server — note-taking, notebooks, and search via Evernote API.…, Wrap plain text in minimal ENML.

### Community 1049 - "test_org_db_e2e.py"
Cohesion: 0.40
Nodes (5): Any, e2e_full: the AI Organization OS DB path must work end-to-end. Regression guard…, POST /v1/org/compose must build a real org with departments + missions.…, test_org_compose_from_nl_builds_a_real_org(), test_org_list_and_create_roundtrip()

### Community 1050 - "gmail_server.py"
Cohesion: 0.38
Nodes (6): _build_mime_message(), call_tool(), _headers(), Any, Gmail MCP server — email reading, sending, drafts, and label management via…, Build a base64url-encoded RFC 2822 message.

### Community 1051 - "test_internal_url_is_blocked"
Cohesion: 0.47
Nodes (5): _collection(), Any, parametrize, e2e_full: the SSRF egress guard is enabled on the live URL-ingestion path. The…, test_internal_url_is_blocked()

### Community 1052 - "convertkit_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, ConvertKit MCP server — subscribers, forms, sequences, and tags. Environment:…

### Community 1053 - "wrike_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), Any, Wrike MCP server — Wrike REST API v4 integration. Environment variables:…, _wrike_headers()

### Community 1054 - "test_redbeat_config.py"
Cohesion: 0.33
Nodes (5): Verify RedBeat scheduler configuration is correctly wired., Beat scheduler should be RedBeat when configured., Beat schedule must be a dict with at least one task., test_celery_app_has_beat_schedule(), test_celery_app_redbeat_scheduler()

### Community 1055 - "TestRateLimiterTenantIsolation"
Cohesion: 0.29
Nodes (4): Verify that rate-limit buckets are per-tenant and cannot bleed across., Redis keys must be prefixed per-tenant — verify raw key structure., Two tenants on the same Redis instance cannot see each other's counters., TestRateLimiterTenantIsolation

### Community 1056 - "mandrill_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Mandrill (Mailchimp Transactional) MCP server. Environment: MANDRILL_API_KEY:…

### Community 1057 - "cloudinary_server.py"
Cohesion: 0.43
Nodes (6): _base(), call_tool(), Any, Cloudinary MCP server — media upload, transformation, and management.…, Generate Cloudinary API signature., _sign()

### Community 1058 - "TestSubscribeEvents"
Cohesion: 0.29
Nodes (3): Lines 2021-2027: terminal goal returns without queuing., Lines 2030-2040: live queue receives None sentinel → exits., TestSubscribeEvents

### Community 1059 - "v1/router.py"
Cohesion: 0.50
Nodes (4): api_info(), health_v1(), get, AgentVerse v1 API router.

### Community 1060 - "stripe_server.py"
Cohesion: 0.38
Nodes (5): call_tool(), _flatten(), Any, Stripe MCP server — comprehensive payments, subscriptions, and billing.…, Flatten nested dict to Stripe-style form encoding.

### Community 1061 - "0091_rag_ingestion_structures.py"
Cohesion: 0.50
Nodes (3): _create_index_concurrently(), Retry an interrupted concurrent build without replacing a valid index., upgrade()

### Community 1062 - "0100_handoffs_group_chat.py"
Cohesion: 0.50
Nodes (3): _json(), Column, upgrade()

### Community 1063 - "slack_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), Any, Slack MCP server — interact with Slack API. Environment: SLACK_BOT_TOKEN: Bot…, _token()

### Community 1064 - "mailerlite_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, MailerLite MCP server — subscribers, groups, and campaigns. Environment:…

### Community 1065 - "test_aggregator.py"
Cohesion: 0.48
Nodes (6): _make_mock_goal(), Tests for GoalAnalyticsAggregator., test_agent_metrics_groups_by_agent(), test_cost_trends_buckets_by_day(), test_goal_metrics_success_rate(), test_tool_metrics_empty_when_no_events()

### Community 1066 - "woocommerce_server.py"
Cohesion: 0.47
Nodes (5): _auth(), _base(), call_tool(), Any, WooCommerce MCP server — WordPress e-commerce store management. Environment:…

### Community 1067 - "youtube_server.py"
Cohesion: 0.47
Nodes (5): _api_key(), call_tool(), _headers(), Any, YouTube MCP server — YouTube Data API v3 for video and channel data.…

### Community 1068 - "TestGuardrailInternalHelpers"
Cohesion: 0.12
Nodes (13): _detect_base64_injection(), _detect_homoglyph_injection(), _detect_indirect_injection(), _detect_rot13_injection(), _normalize_text(), Normalize Unicode to NFKC and lower-case for injection detection., Detect injection phrases encoded as base64., Detect injection phrases encoded with ROT13. (+5 more)

### Community 1069 - "zendesk_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Zendesk MCP server — tickets, users, organizations, and search. Environment:…

### Community 1070 - "TestPhase3Wiring"
Cohesion: 0.29
Nodes (3): C1/C2/C3: Phase 3 services must be wired in goal_service, main.py, and…, The google-oauth router is imported and included by the router registrar…, TestPhase3Wiring

### Community 1072 - "zoom_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_token(), Any, Zoom MCP server — meetings, recordings, and user management. Environment:…, Get OAuth token, supporting both direct token and Server-to-Server OAuth.

### Community 1073 - "RedisCapabilityTracker"
Cohesion: 0.17
Nodes (9): Any, Redis-backed rolling success-rate tracker per (tenant, model, kind). Stores two…, RedisCapabilityTracker, _FakeRedis, asyncio, test_tracker_none_for_missing_key(), test_tracker_records_and_reports_rate(), test_tracker_redis_error_is_safe() (+1 more)

### Community 1074 - "get_goal_cost_metrics"
Cohesion: 0.33
Nodes (5): get_goal_cost_metrics(), get, Request, Cost breakdown API endpoint — per-role token/cost attribution per goal. Exposes…, Return per-role (planner/executor/verifier) token and cost breakdown for a goal.

### Community 1075 - "IngestionPipeline"
Cohesion: 0.03
Nodes (78): _Noop, Prometheus metrics for the ingestion pipeline (LAW-12). Mirrors the graceful-…, _cosine_similarity(), IngestionPipeline, Any, IngestionPipeline — 13-stage unified document processing pipeline. LAW-01: ALL…, Adapter for callers holding a RawDocument (e.g. the DLQ-retry path). P0-11: a…, Run all 13 pipeline stages for one document. LAW-17: correlation_id set if not… (+70 more)

### Community 1077 - "discord_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Discord MCP server — interact with Discord API v10. Environment:…

### Community 1078 - "TestScopeEnforcementBypass"
Cohesion: 0.50
Nodes (3): 0B.9: No-roles API keys must NOT bypass scope enforcement for writes., API keys without roles are denied on write endpoints by default., TestScopeEnforcementBypass

### Community 1079 - "acoustic_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Acoustic (IBM) Marketing Cloud MCP server — campaigns, contacts, and email…

### Community 1080 - "amadeus_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Amadeus MCP server — travel search, flight booking, and hotel discovery.…

### Community 1081 - "apache_kafka_server.py"
Cohesion: 0.40
Nodes (4): _auth(), call_tool(), Any, Apache Kafka MCP server — topic and consumer-group management via Confluent…

### Community 1082 - "microsoft_teams_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft Teams MCP server — interact via MS Graph API. Environment:…

### Community 1083 - "long_term_extractor.py"
Cohesion: 0.50
Nodes (4): LongTermCandidate, BaseModel, Typed evidence-only long-term memory extraction., validate_extraction()

### Community 1084 - "CompletionRequest"
Cohesion: 0.02
Nodes (165): Toxicity classifier — pattern-based first pass + optional LLM second pass.…, Use the injected LLMProvider Protocol to describe the image. Builds a…, Extract entities/relations from each chunk and persist them; return counts.…, Anthropic (Claude) provider implementation. Default provider for AgentVerse.…, Stream tokens from the Anthropic API, calling on_token for each text chunk.…, CompletionRequest, CompletionResponse, embed_texts() (+157 more)

### Community 1085 - "dr-drill.sh"
Cohesion: 0.90
Nodes (4): fail(), log(), pass(), dr-drill.sh script

### Community 1086 - "asana_server.py"
Cohesion: 0.53
Nodes (5): _asana_headers(), call_tool(), _call_tool_inner(), Any, Asana MCP server — Asana REST API v1.0 integration. Environment variables:…

### Community 1088 - "test_goal_service_integration.py"
Cohesion: 0.33
Nodes (5): GoalService must build and attach GoalRuntimeProfile when flag is enabled., Without flag, goal submission works exactly as before., With DYNAMIC_ORCHESTRATION=true, goal submission attaches a runtime profile., test_goal_service_builds_profile_when_flag_enabled(), test_goal_service_works_without_flag()

### Community 1091 - "doordash_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _make_jwt(), Any, DoorDash Drive MCP server — on-demand delivery creation and management.…, Create JWT for DoorDash authentication.

### Community 1092 - "AgentVerse Load Tests"
Cohesion: 0.40
Nodes (4): AgentVerse Load Tests, Environment Variables, Prerequisites, Run

### Community 1093 - "soak.js"
Cohesion: 0.40
Nodes (4): ENDPOINTS, errorRate, latencyTrend, options

### Community 1094 - "ecwid_server.py"
Cohesion: 0.33
Nodes (3): call_tool(), Any, Ecwid MCP server — Ecwid e-commerce store products, orders, and statistics.…

### Community 1095 - "TestSubscribeHITLRejections"
Cohesion: 0.33
Nodes (4): Lines 317-353: inner subscriber loop., Redis connect failure → except block → sleep (cancelled immediately)., Lines 326-349: PMessa processing updates goal record., TestSubscribeHITLRejections

### Community 1099 - "test_keycloak_sso.py"
Cohesion: 0.47
Nodes (5): asyncio, Tests for SSO JIT tenant provisioning., test_create_tenant_from_sso_returns_api_key(), test_get_tenant_by_sso_sub_in_memory(), test_get_tenant_by_sso_sub_not_found()

### Community 1101 - "env.py"
Cohesion: 0.50
Nodes (3): Alembic environment — async engine, DB URL sourced from application Settings., _run_async(), _run_migrations()

### Community 1102 - "0120_knowledge_chunks_binary_index.py"
Cohesion: 0.33
Nodes (8): _create_index_concurrently(), downgrade(), _index_name(), _pgvector_supports_binary(), Binary-quantized Hamming HNSW indexes on knowledge_chunks_<dim>. Adds a…, True when the installed pgvector is >= 0.7.0 (has binary_quantize)., Retry an interrupted concurrent build without replacing a valid index., upgrade()

### Community 1103 - "PromptVariantSelector"
Cohesion: 0.33
Nodes (4): PromptVariant, PromptVariantSelector, PromptVariantSelector — deterministic A/B variant selection per goal_id., Get the currently active prompt variant ID for A/B testing via…

### Community 1104 - "pipedrive_server.py"
Cohesion: 0.53
Nodes (5): _base_url(), call_tool(), _params(), Any, Pipedrive MCP server — deals, persons, organizations, activities, stages.…

### Community 1105 - "snovio_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_access_token(), Any, Snov.io MCP server — lead generation: email finding, verification, and prospect…, Obtain OAuth 2.0 access token from Snov.io.

### Community 1106 - "toast_pos_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Toast POS MCP server — restaurant orders, menu management, and payments.…

### Community 1107 - "appsheet_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, AppSheet MCP server — no-code app data management and action invocation.…

### Community 1108 - "zoho_crm_server.py"
Cohesion: 0.47
Nodes (5): _base_url(), call_tool(), _headers(), Any, Zoho CRM MCP server — records CRUD and search across CRM modules. Environment…

### Community 1109 - "Plan: Concise-planner nudge for verbose reasoning models"
Cohesion: 0.22
Nodes (8): Acceptance criteria, Approach, Goal, Out of scope, Plan: Concise-planner nudge for verbose reasoning models, Tasks, Tests, Why

### Community 1110 - "capsule_crm_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Capsule CRM MCP server — contacts, opportunities, and notes management.…

### Community 1111 - "clearbit_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Clearbit MCP server — person/company enrichment, email lookup, and IP reveal.…

### Community 1112 - "dynamics365_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft Dynamics 365 CRM MCP server — accounts, contacts, and leads…

### Community 1114 - "TestEngineLeakFix"
Cohesion: 0.33
Nodes (4): C5: run_goal must use get_session_factory (singleton), not…, C5: _make_session_factory must not be called directly inside run_goal., C5: run_goal should rely on get_session_factory, not _make_session_factory., TestEngineLeakFix

### Community 1116 - "affinity_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Affinity CRM MCP server — lists, list entries, persons, organizations.…

### Community 1117 - "amazon_ses_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _client(), Any, Amazon SES MCP server — email delivery via SES API. Environment:…

### Community 1118 - "netsuite_server.py"
Cohesion: 0.50
Nodes (4): _base(), call_tool(), Any, NetSuite MCP server — ERP records, search, and saved searches via REST API.…

### Community 1119 - "amazon_sqs_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _client(), Any, Amazon SQS MCP server — message queue operations via SQS API. Environment:…

### Community 1120 - "apollo_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Apollo.io MCP server — people & company search/enrichment, email lookup.…

### Community 1121 - "emma_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Emma email marketing MCP server — contacts, groups, mailings, and analytics.…

### Community 1122 - "recruitee_server.py"
Cohesion: 0.50
Nodes (4): _base(), call_tool(), Any, Recruitee MCP server — applicant tracking, candidates, offers, and stages.…

### Community 1123 - "zuora_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _get_token(), Any, Zuora MCP server — subscription billing, accounts, invoices, and subscriptions.…

### Community 1124 - "loadtest/goal_submission.js"
Cohesion: 0.50
Nodes (3): options, submitDuration, submitErrors

### Community 1125 - "AgentVerse — Backend"
Cohesion: 0.50
Nodes (3): AgentVerse — Backend, Development, Stack

### Community 1126 - "_digest"
Cohesion: 0.67
Nodes (3): _digest(), main(), Any

### Community 1127 - "attio_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Attio MCP server — records, notes on a modern CRM platform. Environment…

### Community 1128 - "aweber_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, AWeber MCP server — email marketing subscribers, lists, and broadcasts.…

### Community 1129 - "auth_throughput.js"
Cohesion: 0.50
Nodes (3): authLatency, errorRate, options

### Community 1131 - "test_structlog_migration.py"
Cohesion: 0.50
Nodes (3): Verify critical modules use structlog instead of stdlib logging., Verify that migrated modules do not import stdlib logging directly., test_migrated_modules_use_get_logger()

### Community 1132 - "bigquery_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google BigQuery MCP server — data warehouse queries and management.…

### Community 1133 - "bitly_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Bitly MCP server — URL shortening and click analytics. Environment:…

### Community 1135 - "braintree_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Braintree MCP server — payment transactions, customers, and subscriptions.…

### Community 1136 - "test_spec_module_importable"
Cohesion: 0.50
Nodes (3): parametrize, Every spec-required module must be importable without error., test_spec_module_importable()

### Community 1137 - "encharge_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Encharge MCP server — marketing automation: users, tags, events, and segments.…

### Community 1243 - "clockify_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Clockify MCP server — time tracking, projects, workspaces, and reports.…

### Community 1251 - "basecamp_server.py"
Cohesion: 0.53
Nodes (5): _basecamp_headers(), call_tool(), _call_tool_inner(), Any, Basecamp MCP server — Basecamp 3 REST API integration. Environment variables:…

### Community 1255 - "freshbooks_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, FreshBooks MCP server — accounting, invoices, clients, and expenses.…

### Community 1266 - "greenhouse_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Greenhouse MCP server — applicant tracking, jobs, candidates, and applications.…

### Community 1267 - "gusto_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Gusto MCP server — payroll, HR, employees, pay periods, and benefits.…

### Community 1268 - "campaign_monitor_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Campaign Monitor MCP server — email campaigns, subscriber lists, and delivery…

### Community 1269 - "harvest_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Harvest MCP server — time tracking, expense management, projects, and…

### Community 1275 - "hive_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Hive MCP server — project and action management, workspaces, and collaboration.…

### Community 1280 - "microsoft_outlook_server.py"
Cohesion: 0.53
Nodes (5): _build_recipients(), call_tool(), _headers(), Any, Microsoft Outlook MCP server — email management via Microsoft Graph API.…

### Community 1282 - "invoice_ninja_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Invoice Ninja MCP server — invoicing, clients, and payments. Environment:…

### Community 1283 - "knack_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Knack MCP server — no-code database records, objects, and views. Environment:…

### Community 1284 - "miro_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Miro MCP server — visual collaboration boards, sticky notes, frames, and items.…

### Community 1285 - "ninox_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Ninox MCP server — database management, tables, and records. Environment:…

### Community 1286 - "fullcontact_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, FullContact MCP server — person/company enrichment, tag management, and contact…

### Community 1287 - "_stub_ocr_engine"
Cohesion: 0.40
Nodes (4): Deterministic OcrEngine stand-in — no Tesseract / provider needed., Inject a deterministic OcrEngine into the utility server's OCR tool., _stub_ocr_engine(), _StubOcrEngine

### Community 1296 - "pivotal_tracker_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Pivotal Tracker MCP server — agile stories, projects, and iterations.…

### Community 1297 - "close_crm_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Close CRM MCP server — leads, contacts, and activities. Environment variables:…

### Community 1299 - "procore_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Procore MCP server — construction management, projects, RFIs, submittals, and…

### Community 1309 - "cloudflare_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Cloudflare MCP server — CDN, DNS, and Workers management via Cloudflare API v4.…

### Community 1310 - "_mint_viewer_client"
Cohesion: 0.38
Nodes (6): _mint_viewer_client(), Any, AsyncClient, e2e_full: org RBAC must be fail-closed end-to-end. A key that was never granted…, Create a second API key on the same tenant and return a client using it. ``POST…, test_viewer_key_is_denied_admin_org_endpoints()

### Community 1311 - "profitwell_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, ProfitWell MCP server — subscription metrics, MRR, churn, and customer…

### Community 1313 - "redmine_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Redmine MCP server — issue tracking, projects, users, and time entries.…

### Community 1315 - "TestCostOptimizerSummaryReport"
Cohesion: 0.29
Nodes (3): Tests for summary_report() — previously uncovered (lines 174-188)., override_applied must be True for the auto-applied model., TestCostOptimizerSummaryReport

### Community 1316 - "samcart_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, SamCart MCP server — checkout platform products, orders, and customers.…

### Community 1317 - "smartsheets_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Smartsheet MCP server — project management sheets, rows, and reports.…

### Community 1319 - "toggl_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Toggl Track MCP server — time tracking, projects, clients, and reports.…

### Community 1327 - "constant_contact_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Constant Contact MCP server — email marketing contacts, lists, and campaigns.…

### Community 1328 - "copper_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Copper CRM MCP server — people, companies, and opportunities. Environment…

### Community 1329 - "._categorise"
Cohesion: 0.33
Nodes (3): Record a goal run's cost and quality metrics., Return the cost-optimised model for a goal category, if any., Extract goal category from first 3 significant words.

### Community 1332 - "core/errors.py"
Cohesion: 0.04
Nodes (53): AuthenticationError, AuthorizationError, BudgetExceededError, CircuitOpenError, ConflictError, ExternalServiceError, InternalError, _is_retryable() (+45 more)

### Community 1336 - "zoho_books_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Zoho Books MCP server — accounting, invoices, contacts, and expenses.…

### Community 1490 - "zoho_invoice_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Zoho Invoice MCP server — invoicing, customers, and invoice lifecycle.…

### Community 1491 - "trello_server.py"
Cohesion: 0.47
Nodes (4): call_tool(), _call_tool_inner(), Any, Trello MCP server — Trello REST API v1 integration. Environment variables:…

### Community 1492 - "drip_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Drip MCP server — ecommerce CRM, subscriber management, and email automation.…

### Community 1493 - "gainsight_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Gainsight MCP server — customer success: accounts, CSMs, CTAs, and scorecards.…

### Community 1494 - "figma_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Figma MCP server — design file access and collaboration via Figma API.…

### Community 1495 - "formstack_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Formstack MCP server — forms, submissions, and documents. Environment:…

### Community 1496 - "getresponse_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, GetResponse MCP server — email marketing contacts, campaigns, and statistics.…

### Community 1499 - "gong_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Gong MCP server — call recordings, transcripts, users, and call statistics.…

### Community 1500 - "google_forms_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google Forms MCP server — form creation and response management via Google…

### Community 1501 - "google_slides_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google Slides MCP server — presentation management via Google Slides API v1.…

### Community 1503 - "google_tasks_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google Tasks MCP server — task list and task management via Google Tasks API…

### Community 1504 - "gravity_forms_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Gravity Forms MCP server — WordPress form builder data and submissions.…

### Community 1505 - "hubspot_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, HubSpot MCP server — contacts, companies, deals, notes, CRM search. Environment…

### Community 1506 - "jotform_server.py"
Cohesion: 0.60
Nodes (4): call_tool(), _params(), Any, JotForm MCP server — form building and submission management. Environment:…

### Community 1507 - "linkedin_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, LinkedIn MCP server — profile, people/company search, and posting. Environment…

### Community 1508 - "loom_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Loom MCP server — video messaging via Loom API v1. Environment: LOOM_API_KEY:…

### Community 1509 - "loops_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Loops MCP server — transactional and marketing email for SaaS products.…

### Community 1510 - "mailgun_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Mailgun MCP server — transactional email sending, domain management, and event…

### Community 1511 - "manychat_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, ManyChat MCP server — chat marketing subscriber management and content…

### Community 1512 - "test_rpa_pdf_e2e.py"
Cohesion: 0.40
Nodes (5): Any, e2e_full (WS-5): RPA scrape → structured report → downloadable PDF. Drives the…, The report endpoint fetches the URL server-side, so internal/metadata targets…, test_rpa_report_blocks_ssrf_targets(), test_rpa_report_returns_real_pdf()

### Community 1513 - "_create_cron_trigger"
Cohesion: 0.47
Nodes (5): _create_cron_trigger(), Any, e2e_full (WS-4): a scheduled (cron) trigger fire is dispatcher-governed.…, Create a cron (time-based / scheduled) trigger; return its schedule_id., test_scheduled_fire_dedups_on_replay()

### Community 1514 - "maropost_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Maropost MCP server — email marketing, contact management, and campaign…

### Community 1515 - "microsoft_excel_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft Excel MCP server — workbook and spreadsheet management via Microsoft…

### Community 1516 - "microsoft_onenote_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft OneNote MCP server — notebook and page management via Microsoft Graph…

### Community 1517 - "microsoft_todo_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft To Do MCP server — task list management via Microsoft Graph API.…

### Community 1518 - "highlevel_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, GoHighLevel CRM MCP server — contacts, campaigns, pipelines, opportunities, and…

### Community 1519 - "moosend_server.py"
Cohesion: 0.60
Nodes (4): call_tool(), _params_with_key(), Any, Moosend MCP server — email marketing lists, subscribers, and campaigns.…

### Community 1520 - "omnisend_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Omnisend MCP server — omnichannel marketing contacts, segments, campaigns, and…

### Community 1521 - "onesignal_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, OneSignal MCP server — push notifications, device management, and segments.…

### Community 1522 - "order_desk_server.py"
Cohesion: 0.40
Nodes (3): call_tool(), Any, Order Desk MCP server — Order Desk order management, inventory, and shipments.…

### Community 1523 - "planhat_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Planhat MCP server — companies, end-users, and activity logging. Environment…

### Community 1524 - "plivo_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Plivo MCP server — cloud communications: SMS, voice calls, and phone number…

### Community 1525 - "postmark_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Postmark MCP server — transactional email sending, templates, streams, bounces,…

### Community 1527 - "pushbullet_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Pushbullet MCP server — push notifications, links, notes, and file sharing to…

### Community 1528 - "ringcentral_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, RingCentral MCP server — cloud communications: SMS, voice calls, and message…

### Community 1529 - "salesforce_server.py"
Cohesion: 0.50
Nodes (4): _auth_headers(), call_tool(), Any, Salesforce MCP server — SOQL queries, records CRUD, metadata, SOSL search.…

### Community 1530 - "shipstation_server.py"
Cohesion: 0.40
Nodes (3): call_tool(), Any, ShipStation MCP server — ShipStation shipping, orders, shipments, and labels.…

### Community 1531 - "TestAuditChainSeeding"
Cohesion: 0.33
Nodes (4): H15: Audit chain must be seeded from DB on startup., H15: AuditWriter must have chain initialization from DB., H15: Audit hash must include tool_name in the hash input., TestAuditChainSeeding

### Community 1532 - "signnow_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, SignNow MCP server — electronic signature management. Environment:…

### Community 1533 - "sugarcrm_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, SugarCRM MCP server — accounts, contacts, and leads management. Environment…

### Community 1534 - "surveymonkey_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, SurveyMonkey MCP server — survey creation and response analysis. Environment:…

### Community 1535 - "twitch_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Twitch MCP server — streaming platform: streams, users, followers, videos, and…

### Community 1536 - "typeform_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Typeform MCP server — form building and response collection. Environment:…

### Community 1537 - "wufoo_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Wufoo MCP server — form building, entries, and report data. Environment:…

### Community 1538 - "hubspot_marketing_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, HubSpot Marketing Hub MCP server — forms, email campaigns, lists, and…

### Community 1539 - "TestSSEEndpointLastEventId"
Cohesion: 0.40
Nodes (3): SSE endpoint must parse Last-Event-ID header., SSE response must include id: lines for resume., TestSSEEndpointLastEventId

### Community 1540 - "TestWorkerWiring"
Cohesion: 0.40
Nodes (3): 0B.2/0B.6: tasks.py worker must pass all services and use correct API., 0B.6: Worker must call .get_config() not .get() on LLMConfigStore., TestWorkerWiring

### Community 1541 - "insightly_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Insightly CRM MCP server — contacts, opportunities, projects, and tasks.…

### Community 1542 - "buffer_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Buffer MCP server — Buffer social media post scheduling, analytics, and profile…

### Community 1543 - "instagram_server.py"
Cohesion: 0.53
Nodes (5): _account_id(), call_tool(), _params(), Any, Instagram MCP server — Graph API for business account management. Environment:…

### Community 1544 - "ebay_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, eBay MCP server — eBay marketplace item search, orders, and selling statistics.…

### Community 1545 - "etsy_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Etsy MCP server — Etsy marketplace shops, listings, and orders management.…

### Community 1546 - "facebook_lead_ads_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Facebook Lead Ads MCP server — Facebook Lead Ad forms, leads, ad accounts, and…

### Community 1547 - "facebook_pages_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Facebook Pages MCP server — Facebook Page posts, insights, comments, and…

### Community 1548 - "filestack_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Filestack MCP server — file upload, transformation, and management.…

### Community 1549 - "gumroad_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Gumroad MCP server — Gumroad digital product sales, subscriptions, and license…

### Community 1550 - "whatsapp_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, WhatsApp Business MCP server — send messages via Meta Cloud API. Environment:…

### Community 1551 - "hootsuite_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Hootsuite MCP server — Hootsuite social media profile management, scheduling,…

### Community 1552 - "kajabi_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Kajabi MCP server — Kajabi online courses, members, offers, and pipeline…

### Community 1553 - "lightspeed_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Lightspeed MCP server — Lightspeed Retail POS products, sales, customers, and…

### Community 1554 - "magento_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Magento MCP server — Magento e-commerce products, orders, customers, and…

### Community 1555 - "pinterest_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Pinterest MCP server — Pinterest boards, pins, analytics, and search.…

### Community 1556 - "pushover_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Pushover MCP server — push notifications to mobile devices and desktop clients.…

### Community 1557 - "sonarqube_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, SonarQube MCP server — code quality and security analysis. Environment:…

### Community 1559 - "spotify_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Spotify MCP server — Spotify music track search, playlists, and artist info.…

### Community 1560 - "sprout_social_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Sprout Social MCP server — Sprout Social social media management, analytics,…

### Community 1561 - "squarespace_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Squarespace MCP server — Squarespace website pages, products, orders, and…

### Community 1562 - "storyblok_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Storyblok MCP server — Storyblok headless CMS stories, components, and…

### Community 1564 - "substack_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Substack MCP server — Substack newsletter posts, subscribers, stats, and email…

### Community 1566 - "teachable_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Teachable MCP server — Teachable online course users, enrollments, coupons, and…

### Community 1568 - "thinkific_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Thinkific MCP server — Thinkific online courses, users, enrollments, and stats.…

### Community 1569 - "vimeo_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Vimeo MCP server — Vimeo video hosting, uploads, folders, and analytics.…

### Community 1570 - "vonage_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Vonage (Nexmo) MCP server — SMS messaging, voice calls, and account management.…

### Community 1571 - "wistia_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Wistia MCP server — Wistia video hosting, media management, and analytics.…

### Community 1572 - "klaviyo_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Klaviyo MCP server — profiles, lists, events, and campaigns. Environment:…

### Community 1573 - "klenty_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Klenty MCP server — sales engagement: prospects, cadences, and email analytics.…

### Community 1574 - "_extract_text"
Cohesion: 0.67
Nodes (3): _extract_text(), Extract plain-text body from a multipart email., Message

### Community 1575 - "konnektive_server.py"
Cohesion: 0.50
Nodes (4): _auth_params(), call_tool(), Any, Konnektive CRM MCP server — orders, customers, and subscriptions management.…

### Community 1576 - "leadpages_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Leadpages MCP server — landing pages, leads, lead boxes, and conversion…

### Community 1578 - "lemlist_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Lemlist MCP server — sales outreach: campaigns, leads, and engagement…

### Community 1579 - "orbit_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Orbit MCP server — community management: members, activities, and workspace…

### Community 1580 - "outreach_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Outreach MCP server — sales engagement: prospects, sequences, and analytics.…

### Community 1582 - "overloop_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Overloop (ex-Prospect.io) MCP server — outreach prospecting and sequence…

### Community 1583 - "podio_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Podio MCP server — project management: apps, items, spaces, and tasks.…

### Community 1584 - "postgres_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), get_tools(), Any, PostgreSQL MCP server — execute queries against a configured database.…

### Community 1585 - "reply_io_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Reply.io MCP server — sales automation: people, sequences, and email…

### Community 1586 - "salesloft_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Salesloft MCP server — sales engagement: people, cadences, calls, and…

### Community 1587 - "segment_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Segment MCP server — customer data platform: identify, track, page, group, and…

### Community 1588 - "sendgrid_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, SendGrid MCP server — transactional & marketing email via SendGrid v3 API.…

### Community 1589 - "twilio_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Twilio MCP server — SMS, WhatsApp, voice calls, and number lookup. Environment:…

### Community 1590 - "test_goals_batch_submit_route_exists"
Cohesion: 0.40
Nodes (5): asyncio, Goals app schema test — checks /goals/batch or similar exists., Verify a streaming endpoint is registered in the app., test_goals_batch_submit_route_exists(), test_goals_stream_endpoint_exists()

### Community 1591 - "asyncio"
Cohesion: 0.40
Nodes (5): asyncio, Second resolve_api_key call must return from Redis without hitting in-memory., API keys without explicit roles must default to 'operator', not 'admin'., test_default_role_is_operator_not_admin(), test_redis_cache_avoids_db_on_second_lookup()

### Community 1593 - ".test_db_exception_falls_back_to_memory"
Cohesion: 0.40
Nodes (3): Lines 2069-2073: fallback to in-memory., Lines 2069-2070: DB exception → in-memory., TestGetAuditEntries

### Community 1594 - "aws_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, AWS MCP server — unified AWS operations across S3, Lambda, EC2, CloudWatch,…

### Community 1596 - ".__init__"
Cohesion: 0.50
Nodes (3): async_sessionmaker, AsyncSession, Embedder

### Community 1599 - ".list_api_keys"
Cohesion: 0.33
Nodes (3): Return all keys for *tenant_id* — raw keys and hashes are **never** included., Revoking a key makes resolve_api_key return None for that key., test_revoke_key_deactivates_it()

### Community 1600 - "test_toolcall_import_exists"
Cohesion: 0.50
Nodes (4): _agent_source(), ToolCall must be importable from tool_calls — no NameError in graph.py, Read combined source of graph.py and all node mixin files., test_toolcall_import_exists()

### Community 1601 - "test_tenancy_integration.py"
Cohesion: 0.50
Nodes (3): Integration test: three-layer tenant isolation (Postgres + Redis + app layer).…, Tenant A's resources are invisible to Tenant B at every isolation layer., test_tenant_isolation_across_all_three_layers()

### Community 1606 - "test_persistence_attempt_written_to_db"
Cohesion: 0.67
Nodes (3): asyncio, GoalPersistenceEngine should call _write_attempt_start and _write_attempt_end., test_persistence_attempt_written_to_db()

## Knowledge Gaps
- **189 isolated node(s):** `Turn`, `ResponseAction`, `ArtifactRef`, `MCPTool`, `MCPPromptArgument` (+184 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 15497 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **121 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TenantContext` connect `TenantContext` to `RollbackEngine`, `EmbeddingOrchestrator`, `rag/gateway.py`, `GoalService`, `test_agents_comprehensive2.py`, `test_graph_comprehensive_coverage.py`, `AgentStore`, `EvalRunner`, `RAGStrategy`, `RedTeamRunner`, `test_all_memory_types.py`, `HITLGateway`, `ToolContext`, `test_agent_graph_build_gaps.py`, `test_scheduler.py`, `KnowledgeCollection`, `GoalRuntimeProfile`, `PolicyEngine`, `tenants.py`, `test_workflows.py`, `build_default_registry`, `test_civilization_extra4.py`, `.find_completed_model`, `CredentialVault`, `test_store_final_coverage.py`, `api/knowledge.py`, `RetrievalResult`, `test_mcp_circuit_breaker.py`, `org/router.py`, `test_truly_live_everything.py`, `test_workflows_comprehensive2.py`, `CostController`, `test_tool_reliability_store_get_unreliable`, `test_evals_comprehensive.py`, `test_knowledge_api.py`, `workflow/test_router.py`, `test_agents_extra4.py`, `test_governance_extra3.py`, `registry_wiring.py`, `generate_gst_invoice`, `test_workflows_comprehensive.py`, `test_knowledge_rpa.py`, `IngestionPipeline`, `test_chunkers.py`, `MCPServerConfig`, `test_configured_model_registry_api.py`, `test_a2a_extra2.py`, `OAuthState`, `get_connector`, `.purge_expired`, `workflow_executor.py`, `SelfOptimizer`, `AnthropicProvider`, `tasks.py`, `ContextPipeline`, `test_phase2_model_registry.py`, `CompletionRequest`, `test_retrieval_gateway.py`, `test_persistence_attempt_written_to_db`, `asyncio`, `ContentType`, `OAuthFlowManager`, `ColBERTPattern`, `_TemplateStore`, `chunk_by_tokens`, `test_knowledge_extra4.py`, `RedisCircuitBreaker`, `LongTermMemoryStore`, `IngestionOrchestrator`, `test_phase5_api.py`, `MockMCPClient`, `test_eval_suite_offline_discrimination.py`, `rag/raft.py`, `test_rbac_comprehensive2.py`, `SIEMType`, `api/test_collab.py`, `StepDefinition`, `test_celery_agentgraph.py`, `SystemTemplateStore`, `test_gateway_entrypoints.py`, `test_tenants_extra4.py`, `RetrievalEvaluator`, `.test_require_role_sync_dependency_raises_403_directly`, `test_governor.py`, `test_catalog_comprehensive.py`, `test_rpa_comprehensive.py`, `test_goal_tree_comprehensive.py`, `test_audit_v2.py`, `api/governance.py`, `Constitution`, `marketplace_monetization.py`, `test_modular_rag.py`, `create_app`, `test_optimizer_wired.py`, `CivilizationOrchestrator`, `FakeProvider`, `WorkflowExecutor`, `test_rag_patterns_real_openai.py`, `test_program09_coordination_api.py`, `api/civilization.py`, `HandoffRecord`, `test_backend_fixes.py`, `test_civilization_api_comprehensive2.py`, `test_connectors_catalog.py`, `KnowledgeStore`, `StrategyRunner`, `OrgService`, `test_phase10_11_ai_ops_memory.py`, `coordination/store.py`, `test_spawn_tool.py`, `test_dynamic_orchestration_e2e.py`, `CRDTRoomManager`, `CollaborationStore`, `PlanMode`, `test_semantic_cache_world_class.py`, `test_runtime_scorecard.py`, `ReflexionStore`, `test_tools_api_comprehensive.py`, `goals.py`, `api/test_artifacts_comprehensive.py`, `test_policies_extra.py`, `test_middleware_full.py`, `TestWorkflowExecutorDispatch`, `test_enterprise.py`, `test_goals.py`, `test_insights_extra.py`, `AgentState`, `CredentialInjector`, `test_security_runtime.py`, `test_layer4_complete.py`, `agent/test_router.py`, `test_connectors_extra2.py`, `Classification`, `test_ghost_run.py`, `chat/router.py`, `api/test_governance_comprehensive.py`, `test_enterprise_intelligence_gaps.py`, `insights.py`, `CapabilitySearch`, `test_safe_web_capability.py`, `test_tenants_comprehensive2.py`, `.run`, `test_training_export_comprehensive2.py`, `test_collab_extra3.py`, `test_router_hitl.py`, `test_phase6_7_rag_runtime.py`, `civilization/test_events.py`, `test_replay_comprehensive.py`, `test_learning.py`, `SupervisorAgent`, `agents.py`, `observability.py`, `test_middleware_comprehensive.py`, `ScheduleStore`, `test_entitlements.py`, `ComplianceController`, `test_identity_action_safety.py`, `PromptVariant`, `test_analytics_comprehensive.py`, `test_workflows_extra2.py`, `test_raft_lifecycle.py`, `gateway/router.py`, `test_retrieval_strategies_comprehensive.py`, `test_knowledge_store_comprehensive.py`, `test_security_audit_findings.py`, `Any`, `api/a2a.py`, `test_real_simulation.py`, `.register_builtin_handler`, `SemanticCache`, `_fixture`, `triggers.py`, `LLMJudge`, `MetaAgentPlanner`, `_ctx`, `test_reasoning_retrieval_strategies.py`, `Any`, `test_supervisor_debate_nodes.py`, `test_scope_enforcement_comprehensive.py`, `AgentCollabSession`, `test_schedules_extra.py`, `test_oauth.py`, `PolicyResult`, `SourceConfig`, `secrets.py`, `test_phase12_13_skills_frontend.py`, `test_org_router.py`, `test_goals_comprehensive.py`, `test_society.py`, `test_rollback_experiment.py`, `test_workflow_builder.py`, `test_openapi_importer_comprehensive.py`, `GoalPersistenceEngine`, `Governor`, `test_state_runtime.py`, `get_inverse_fn`, `_make_service`, `test_critical_fixes.py`, `test_rate_limiter.py`, `RoutingDecision`, `test_phase5_knowledge_graph.py`, `RuntimeProfileBuilder`, `test_marketplace_endpoint_gaps.py`, `test_enterprise_comprehensive2.py`, `test_marketplace_v2_extra.py`, `SimulationRunner`, `MCPClient`, `test_workflow_planner_comprehensive.py`, `core/errors.py`, `test_magentic_api.py`, `test_enterprise_api.py`, `KnowledgeGraphStore`, `test_oauth_flow.py`, `ExecutionMemory`, `test_default_path_rerank.py`, `_make_agents_app`, `test_gateway_gap_closure.py`, `test_oauth_security.py`, `test_supervisor_debate_wiring.py`, `test_routing.py`, `test_goals_debate.py`, `test_tenants.py`, `test_comprehensive_coverage.py`, `test_router_runs.py`, `test_goals_final.py`, `test_governance_integration.py`, `test_insights_comprehensive.py`, `test_phase14_deep_coverage.py`, `OrgMCPServer`, `schedules.py`, `test_rbac.py`, `integrations.py`, `test_compliance_endpoint_gaps.py`, `test_collab_api_comprehensive.py`, `test_knowledge_base_pipeline.py`, `test_knowledge_comprehensive.py`, `TenantMiddleware`, `test_a2a_dispatch.py`, `test_agents_api.py`, `test_mfa.py`, `test_connectors_comprehensive2.py`, `_vec`, `test_insights.py`, `test_ocr_persist_kb.py`, `MarketplaceV2`, `.exchange_code`, `test_agents_extra.py`, `test_orchestrator.py`, `api/billing.py`, `test_scopes_rbac.py`, `asyncio`, `test_benchmarking.py`, `api/test_connectors.py`, `workflows.py`, `AgentRouter`, `test_costs_comprehensive.py`, `rag/engine.py`, `has_feature`, `meta_agent.py`, `AgentBenchmark`, `test_guardrails_comprehensive2.py`, `test_agent_patterns_real_openai.py`, `api/auth.py`, `test_enterprise_extra3.py`, `test_memory_comprehensive.py`, `test_schedules_api.py`, `api/mfa.py`, `test_marketplace_v2.py`, `RAFTDatasetRecord`, `test_tenants_comprehensive.py`, `.list_async`, `test_governance_comprehensive2.py`, `test_connectors_comprehensive.py`, `MoAProposal`, `api/model_registry.py`, `collab.py`, `FastAPI`, `test_phase3_4_multimodal.py`, `execution_environment/models.py`, `test_phase8_9_guardrails_trust.py`, `.score`, `test_knowledge_comprehensive2.py`, `test_enterprise_v2.py`, `test_templates_comprehensive2.py`, `.refresh_token`, `test_schedules_comprehensive.py`, `test_store_comprehensive3.py`, `test_perception_comprehensive.py`, `dpdp.py`, `estimate_cost`?**
  _High betweenness centrality (0.188) - this node is a cross-community bridge._
- **Why does `get_logger()` connect `get_logger` to `rag/gateway.py`, `GoalService`, `AgentStore`, `test_all_memory_types.py`, `HITLGateway`, `KnowledgeCollection`, `TenantContext`, `CredentialVault`, `RiskLevel`, `api/knowledge.py`, `org/router.py`, `test_truly_live_everything.py`, `CostController`, `observability/metrics.py`, `airtable_server.py`, `registry_wiring.py`, `test_batch7_servers.py`, `test_remaining_connectors.py`, `MCPServerConfig`, `SelfOptimizer`, `test_google_storage_payment_connectors.py`, `test_database_analytics_connectors.py`, `ColBERTPattern`, `test_batch6_servers.py`, `LongTermMemoryStore`, `IngestionOrchestrator`, `StepDefinition`, `SystemTemplateStore`, `test_extra_coverage_servers5.py`, `ToolReliabilityStore`, `Constitution`, `create_app`, `FakeProvider`, `api/civilization.py`, `rpa/test_artifacts_comprehensive.py`, `OrgService`, `civilization/test_metrics.py`, `test_batch2_servers.py`, `test_dynamic_orchestration_e2e.py`, `_ingest_repo_background`, `test_runtime_scorecard.py`, `test_semantic_cache_world_class.py`, `RPAArtifactStore`, `BrowserSessionManager`, `ReflexionStore`, `goals.py`, `WorkflowTestRunner`, `Message`, `AgentState`, `RuntimeSSEEmitter`, `ModelRouter`, `QualityGateSystem`, `test_bus.py`, `test_finance_servers_dispatch.py`, `check_grounding`, `civilization/test_events.py`, `test_extra_coverage_servers4.py`, `agents.py`, `PromptVariant`, `WorkflowState`, `analytics/aggregator.py`, `WorkflowDefinition`, `api/a2a.py`, `NotificationService`, `AuditEvent`, `WebhookDeliverySystem`, `.register_builtin_handler`, `DebateOrchestrator`, `test_extra_coverage_servers6.py`, `test_blackboard.py`, `NLTriggerResolver`, `workflow/router.py`, `test_tool_cache.py`, `test_tracing_comprehensive.py`, `test_new_tools.py`, `org/events.py`, `assert_public_url`, `test_society.py`, `BrowserAgent`, `test_saml_provider.py`, `ExperimentRegistry`, `test_servers_comprehensive.py`, `test_auth_api_coverage.py`, `OutboundWebhookService`, `DeletionOrchestrator`, `ComplianceBundleManager`, `Tokenizer`, `ComplianceChecker`, `WorkflowHITLRequest`, `ChannelRateLimiter`, `memory_v2.py`, `ExecutionMemory`, `test_gateway_gap_closure.py`, `ToolSelector`, `org/connectors/__init__.py`, `check_tool_args_for_exfil`, `InMemoryCacheBackend`, `LLMResponseCache`, `test_context_pipeline.py`, `test_a2a_dispatch.py`, `test_communication_connectors.py`, `test_expression_engine.py`, `test_hosted_reranker.py`, `test_ip_allowlist_comprehensive.py`, `rag/engine.py`, `JiraIngestor`, `test_all_providers.py`, `test_tasks_coverage_gaps.py`, `a2a/__init__.py`, `api/auth.py`, `SIEMAdapter`, `test_scope_seeder.py`, `org/test_security.py`, `_sign`, `LargePayloadStore`, `api/model_registry.py`, `collab.py`, `execution_environment/models.py`, `test_otel.py`, `router_runs.py`, `test_audit_scopes_limits.py`, `ConsensusVerifier`, `OrgMCPResources`, `audit_v3.py`, `test_telegram_server.py`, `OrgLoopDetector`, `UsageService`, `tenancy/billing.py`, `ArtifactTool`, `router_hitl.py`, `Any`, `test_devops_connectors.py`, `OpenAIFineTuneProvider`, `test_webhook_trigger_activation.py`, `org/rbac.py`, `TenantUserService`, `router_versions.py`, `SCIMHandler`, `start_policy_subscriber`, `GitHubIngestor`, `emarsys_server.py`, `test_configured_model_registry_api.py`, `OAuthState`, `test_default_path_rerank.py`, `tasks.py`, `TestUniversalArgumentResolver`, `logging.py`, `LimitsV2Checker`, `test_batch8_servers.py`, `check_and_process_emails`, `test_project_management_connectors.py`, `get_builtin_server_configs`, `RetrievalEvaluator`, `PostgresWorkflowRunStore`, `test_audit_v2.py`, `TestAnswerSynthesizer`, `CommandScheduler`, `GoalRefinementPipeline`, `SubTenantService`, `._node_plan`, `test_spawn_tool.py`, `guardrail_engine.py`, `scan_for_encoding_attacks`, `OcrDocumentTool`, `test_replay_comprehensive.py`, `ConfluenceIngestor`, `warm_permission_cache`, `test_cost_dashboard_api.py`, `admin.py`, `upsert_google_user`, `AutonomyEnforcer`, `TestDomainPolicies`, `test_ingestors_coverage.py`, `SourceConfigStore`, `OrgEventPublisher`, `ocr.py`, `verify_goal_token`, `ChannelAuthGuard`, `test_durable_execution.py`, `GoalPersistenceEngine`, `grant_elevation`, `google_oauth.py`, `Any`, `GoalDeduplicator`, `test_guardrails_v3.py`, `verify_stream_token`, `SlackIngestor`, `firebase_server.py`, `integrations.py`, `.exchange_code`, `agent_credentials.py`, `todoist_server.py`, `ModelStats`, `.list_async`, `mailchimp_server.py`, `agent/consensus.py`, `mattermost_server.py`, `.score`, `freshsales_server.py`, `looker_server.py`, `yotpo_server.py`, `wave_server.py`, `activecampaign_server.py`, `customerio_server.py`, `facebook_conversions_server.py`, `pandadoc_server.py`, `docusign_server.py`, `clickup_server.py`, `agent/supervisor.py`, `linear_server.py`, `smartsuite_server.py`, `monday_server.py`, `notion_server.py`, `teamwork_server.py`, `expensify_server.py`, `grafana_server.py`, `evernote_server.py`, `gmail_server.py`, `convertkit_server.py`, `wrike_server.py`, `mandrill_server.py`, `cloudinary_server.py`, `stripe_server.py`, `slack_server.py`, `mailerlite_server.py`, `woocommerce_server.py`, `youtube_server.py`, `zendesk_server.py`, `zoom_server.py`, `discord_server.py`, `acoustic_server.py`, `amadeus_server.py`, `apache_kafka_server.py`, `microsoft_teams_server.py`, `asana_server.py`, `doordash_server.py`, `ecwid_server.py`, `pipedrive_server.py`, `snovio_server.py`, `toast_pos_server.py`, `appsheet_server.py`, `zoho_crm_server.py`, `capsule_crm_server.py`, `clearbit_server.py`, `dynamics365_server.py`, `affinity_server.py`, `amazon_ses_server.py`, `netsuite_server.py`, `amazon_sqs_server.py`, `apollo_server.py`, `emma_server.py`, `recruitee_server.py`, `zuora_server.py`, `attio_server.py`, `aweber_server.py`, `bigquery_server.py`, `bitly_server.py`, `braintree_server.py`, `encharge_server.py`, `clockify_server.py`, `basecamp_server.py`, `freshbooks_server.py`, `greenhouse_server.py`, `gusto_server.py`, `campaign_monitor_server.py`, `harvest_server.py`, `hive_server.py`, `microsoft_outlook_server.py`, `invoice_ninja_server.py`, `knack_server.py`, `miro_server.py`, `ninox_server.py`, `fullcontact_server.py`, `pivotal_tracker_server.py`, `close_crm_server.py`, `procore_server.py`, `cloudflare_server.py`, `profitwell_server.py`, `redmine_server.py`, `samcart_server.py`, `smartsheets_server.py`, `toggl_server.py`, `constant_contact_server.py`, `copper_server.py`, `zoho_books_server.py`, `zoho_invoice_server.py`, `trello_server.py`, `drip_server.py`, `gainsight_server.py`, `figma_server.py`, `formstack_server.py`, `getresponse_server.py`, `gong_server.py`, `google_forms_server.py`, `google_slides_server.py`, `.load_tokens_from_db`, `google_tasks_server.py`, `gravity_forms_server.py`, `hubspot_server.py`, `jotform_server.py`, `linkedin_server.py`, `loom_server.py`, `loops_server.py`, `mailgun_server.py`, `manychat_server.py`, `maropost_server.py`, `microsoft_excel_server.py`, `microsoft_onenote_server.py`, `microsoft_todo_server.py`, `highlevel_server.py`, `moosend_server.py`, `omnisend_server.py`, `onesignal_server.py`, `order_desk_server.py`, `planhat_server.py`, `plivo_server.py`, `postmark_server.py`, `pushbullet_server.py`, `ringcentral_server.py`, `salesforce_server.py`, `shipstation_server.py`, `signnow_server.py`, `sugarcrm_server.py`, `surveymonkey_server.py`, `twitch_server.py`, `typeform_server.py`, `wufoo_server.py`, `hubspot_marketing_server.py`, `insightly_server.py`, `buffer_server.py`, `instagram_server.py`, `ebay_server.py`, `etsy_server.py`, `facebook_lead_ads_server.py`, `facebook_pages_server.py`, `filestack_server.py`, `gumroad_server.py`, `whatsapp_server.py`, `hootsuite_server.py`, `kajabi_server.py`, `lightspeed_server.py`, `magento_server.py`, `pinterest_server.py`, `pushover_server.py`, `sonarqube_server.py`, `spotify_server.py`, `sprout_social_server.py`, `squarespace_server.py`, `storyblok_server.py`, `substack_server.py`, `teachable_server.py`, `thinkific_server.py`, `vimeo_server.py`, `vonage_server.py`, `wistia_server.py`, `klaviyo_server.py`, `klenty_server.py`, `konnektive_server.py`, `leadpages_server.py`, `lemlist_server.py`, `orbit_server.py`, `outreach_server.py`, `overloop_server.py`, `podio_server.py`, `postgres_server.py`, `reply_io_server.py`, `salesloft_server.py`, `segment_server.py`, `sendgrid_server.py`, `twilio_server.py`, `aws_server.py`?**
  _High betweenness centrality (0.128) - this node is a cross-community bridge._
- **Why does `create_app()` connect `create_app` to `RollbackEngine`, `ProspectiveMemoryService`, `rag/gateway.py`, `GoalService`, `EvalRunner`, `AgentStore`, `ApprovalChainEngine`, `RAGStrategy`, `RedTeamRunner`, `test_all_memory_types.py`, `HITLGateway`, `OAuthFlowManager`, `test_main_create_app.py`, `test_scheduler.py`, `test_tool_reliability.py`, `GoalRuntimeProfile`, `PolicyEngine`, `tenants.py`, `build_default_registry`, `TenantContext`, `CredentialVault`, `RetrievalResult`, `test_dispatcher.py`, `start_policy_subscriber`, `CostController`, `RPAExecutor`, `TriggerConsumerSupervisor`, `registry_wiring.py`, `IngestionPipeline`, `TenantService`, `test_goals_batch_submit_route_exists`, `MCPServerConfig`, `test_configured_model_registry_api.py`, `test_p3_phases.py`, `RerankPolicy`, `get_connector`, `CompletionRequest`, `SelfOptimizer`, `tasks.py`, `test_retrieval_gateway.py`, `EvalSuiteRunner`, `LongTermMemoryStore`, `IngestionOrchestrator`, `test_perception_api.py`, `SIEMType`, `api/test_collab.py`, `StepDefinition`, `SystemTemplateStore`, `PostgresWorkflowRunStore`, `test_agent_identity_comprehensive.py`, `test_audit_v2.py`, `ToolReliabilityStore`, `FakeProvider`, `test_routers.py`, `test_program09_coordination_api.py`, `HandoffRecord`, `rpa/test_artifacts_comprehensive.py`, `KnowledgeStore`, `StrategyRunner`, `HITLWorkflowGateway`, `coordination/store.py`, `CollaborationStore`, `test_ingestion_pipeline.py`, `BrowserSessionManager`, `ReflexionStore`, `Message`, `test_enterprise.py`, `WorkflowCompiler`, `AgentState`, `pools.py`, `CredentialInjector`, `ModelRouter`, `SIEMConfig`, `Classification`, `ReflexionService`, `VoiceAlertManager`, `CapabilitySearch`, `test_session_store_comprehensive.py`, `test_tenant_service_db.py`, `CostTracker`, `PostgresWorkflowApprovalStore`, `warm_permission_cache`, `ScheduleStore`, `ComplianceController`, `test_security_audit_findings.py`, `test_raft_lifecycle.py`, `InMemoryMemoryRepository`, `WorkflowDefinition`, `test_real_simulation.py`, `NotificationService`, `CeleryGoalTaskQueue`, `test_agent_advanced.py`, `SemanticCache`, `_fixture`, `DebateOrchestrator`, `test_memory_learning_services.py`, `wait_for_status`, `MetaAgentPlanner`, `GraphFactory`, `test_guardrail_rules_persistence.py`, `_FakeRedis`, `routers.py`, `get_org_event_publisher`, `test_scope_enforcement_comprehensive.py`, `NLTriggerResolver`, `SourceConfig`, `test_tool_cache.py`, `secrets.py`, `MultimodalPipeline`, `SourceConfigStore`, `GoalCostBreakdown`, `GuardrailEngine`, `WorkflowService`, `_WorkflowStore`, `RedisBulkheadRegistry`, `BrowserAgent`, `factory`, `get_inverse_fn`, `decision_store.py`, `test_auth_api_coverage.py`, `Event`, `PageAnalyzer`, `LedgerRevision`, `SimulationRunner`, `MCPClient`, `DeletionOrchestrator`, `ComplianceChecker`, `test_magentic_api.py`, `test_redis_factory.py`, `KnowledgeGraphStore`, `ExecutionMemory`, `test_gateway_gap_closure.py`, `ToolSelector`, `test_comprehensive_coverage.py`, `MemoryRecord`, `test_goals_final.py`, `InMemoryCacheBackend`, `test_rbac.py`, `LLMResponseCache`, `integrations.py`, `test_keycloak.py`, `TenantMiddleware`, `test_fakeredis_gaps.py`, `test_agents_api.py`, `test_full_stack_e2e.py`, `MarketplaceV2`, `test_chat_api.py`, `AuditV3`, `test_scopes_rbac.py`, `AgentRouter`, `PermissionCache`, `VoyageProvider`, `test_all_providers.py`, `HealthCheck`, `LLMConfigStore`, `SIEMAdapter`, `api/test_replay.py`, `test_phase4_connectors.py`, `test_agent_builder.py`, `test_scope_seeder.py`, `OcrEngine`, `test_civilization_api.py`, `test_openapi_schema.py`, `MoAProposal`, `api/model_registry.py`, `test_raft_repository_integration.py`, `MemoryWriteRequest`, `test_nl_scheduler_comprehensive.py`, `audit_v3.py`, `IdempotencyStore`, `UsageService`, `test_capabilities.py`, `SelfOptimizerV2`, `OpenAIFineTuneProvider`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Are the 183 inferred relationships involving `FakeProvider` (e.g. with `FakeRunner` and `run_goal()`) actually correct?**
  _`FakeProvider` has 183 INFERRED edges - model-reasoned connections that need verification._
- **Are the 93 inferred relationships involving `AgentGraph` (e.g. with `ExecutionStrategy` and `GraphState`) actually correct?**
  _`AgentGraph` has 93 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Turn`, `ResponseAction`, `ArtifactRef` to the rest of the system?**
  _189 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `RollbackEngine` be split into smaller, more focused modules?**
  _Cohesion score 0.01562565455992627 - nodes in this community are weakly interconnected._