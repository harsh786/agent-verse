# Graph Report - agent-verse-backend  (2026-09-09)

## Corpus Check
- 2852 files · ~2,284,057 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 47180 nodes · 111899 edges · 1556 communities (1186 shown, 102 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 6607 edges (avg confidence: 0.94)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0d3d01f9`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- activecampaign_server.py
- CompletionRequest
- FakeProvider
- GoalService
- AuditLog
- TenantContext
- test_celery_tasks_coverage.py
- logging.py
- RAGStrategy
- _task
- AgentState
- HITLGateway
- SourceConfig
- _instantiate_provider
- ScheduleStore
- ColBERTPattern
- GoalRuntimeProfile
- GuardrailChecker
- WorkflowCompiler
- test_colbert_runtime.py
- build_default_registry
- test_dynamic_orchestration_e2e.py
- retrieve
- RollbackEngine
- test_layer4_complete.py
- TenantMiddleware
- api/knowledge.py
- RetrievalResult
- test_dispatcher.py
- agent/graph.py
- test_consensus_behavior.py
- org/router.py
- MultimodalPipeline
- _inject_goal
- test_batch3_servers.py
- _post
- LongTermMemoryStore
- test_extra_coverage_servers.py
- test_workflows_extra2.py
- CostController
- test_remaining_servers_dispatch.py
- test_executor_playwright_mock.py
- test_runtime_scorecard.py
- Settings
- test_batch2_servers.py
- test_agents_extra4.py
- test_governance_extra3.py
- test_batch6_servers.py
- test_batch7_servers.py
- ContentType
- test_remaining_connectors.py
- test_a2a_comprehensive.py
- GoalStatus
- test_batch5_servers.py
- test_chunkers.py
- TenantService
- create_app
- test_multimodal_router.py
- test_extra_coverage_servers2.py
- get_builtin_server_configs
- OrchestrationPersistence
- test_phases_4_5.py
- PatternState
- SelfOptimizer
- ExecutionMemory
- PatternConfig
- StructuredPlan
- test_google_storage_payment_connectors.py
- test_database_analytics_connectors.py
- test_batch4_servers.py
- EmbeddingOrchestrator
- test_batch1_servers.py
- test_knowledge_persistence.py
- parser_registry.py
- test_truly_live_everything.py
- _TemplateStore
- runtime_profile.py
- test_knowledge_extra4.py
- test_batch8_servers.py
- test_session_store_comprehensive.py
- NLIChecker
- asyncio
- CredentialVault
- app/main.py
- RPAExecutor
- test_rbac_comprehensive2.py
- WorkflowDefinition
- AgentStore
- ToolReliabilityStore
- client
- StepDefinition
- SystemTemplateStore
- reasoning_contracts.py
- LedgerRevision
- ModelOrchestratorAdapter
- test_gateway_entrypoints.py
- test_tenants_extra4.py
- test_extra_coverage_servers5.py
- test_comms_servers_dispatch.py
- test_governor.py
- EpisodicMemoryStore
- test_catalog_comprehensive.py
- workflow/test_registry.py
- test_agent_identity_comprehensive.py
- test_conversational_consumer.py
- test_stt_engine.py
- test_crm_servers_dispatch.py
- api/governance.py
- test_extra_coverage_servers3.py
- Constitution
- SIEMConfig
- test_modular_rag.py
- test_main_create_app.py
- test_helpers.py
- test_phase2_model_registry.py
- test_supervisor_debate_nodes.py
- WorkflowExecutor
- test_routers.py
- GoalPersistenceEngine
- api/civilization.py
- HandoffRecord
- rpa/test_artifacts_comprehensive.py
- FakeRedis
- test_devtools_servers_dispatch.py
- test_productivity_servers_dispatch.py
- tenants.py
- test_goal_service_distributed_strategy.py
- org/service.py
- civilization/test_metrics.py
- Any
- CodeInterpreter
- api/ingestion.py
- test_program09_coordination_api.py
- test_agent_graph_build_gaps.py
- test_medium_fixes.py
- CollaborationStore
- PromptBuilder
- test_retrieval_gateway.py
- test_phases_1_4.py
- RPAArtifactStore
- BrowserSessionManager
- org/test_security.py
- connectors.py
- goals.py
- PolicyEngine
- BenchmarkStore
- strategy_contracts.py
- TriggerType
- _CollabPubSub
- GuardrailsEngine
- test_insights_extra.py
- test_local_runner.py
- catalogue.py
- DataClassifier
- test_connectors_extra2.py
- test_goal_tree_comprehensive.py
- ModelRouter
- rag_platform.py
- Classification
- QualityGateSystem
- test_semantic_cache_world_class.py
- chat/router.py
- test_bus.py
- Marketplace
- SelfOptimizerV2
- CapabilitySearch
- test_condition.py
- assert_public_url
- Enum
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
- SemanticCache
- test_learning.py
- ModelGateway
- LLMConfigStore
- agents.py
- CostOptimizer
- MemoryConsolidator
- test_middleware_comprehensive.py
- TriggerConsumerSupervisor
- asyncio
- _deps.py
- build_envelope
- MCPServerConfig
- test_guardrails_comprehensive.py
- RerankPolicy
- system_session
- test_security_org.py
- test_raft_lifecycle.py
- get_session_factory
- ExecutionTier
- RuntimeFlags
- CodeExecutionWorkload
- test_phase3_retrieval.py
- app/tools/__init__.py
- coordination/store.py
- test_governance_comprehensive2.py
- test_oauth_flow.py
- NotificationService
- test_nl_scheduler_comprehensive.py
- rag/raft.py
- WebhookDeliverySystem
- registry_wiring.py
- goal_service.py
- RuntimeSSEEmitter
- test_ingestion_time_strategies.py
- test_consent_retention.py
- test_main_comprehensive.py
- triggers.py
- TemplateSecurityReviewer
- RegressionGate
- MemoryConsolidator
- FileOps
- WorkflowService
- extract_tool_call
- test_state_machine.py
- test_phase8_9_guardrails_trust.py
- _FakeRedis
- test_extra_coverage_servers6.py
- test_data_servers_dispatch.py
- routers.py
- test_security_audit_findings.py
- test_scope_enforcement_comprehensive.py
- test_blackboard.py
- AgentCollabSession
- workflow_nodes.py
- voice/router.py
- NLTriggerResolver
- workflow/router.py
- test_keycloak_comprehensive.py
- InMemoryCancellationRepository
- policies.py
- test_tool_cache.py
- test_tracing_comprehensive.py
- MetaOrchestrator
- test_new_tools.py
- ComplianceController
- test_org_router.py
- IntentRouter
- core/errors.py
- org/events.py
- ImageAttachment
- get_inverse_fn
- test_goals_comprehensive.py
- test_main_extra2.py
- test_perception_gaps.py
- a2a_security.py
- test_society.py
- GoalCostBreakdown
- asyncio
- tasks.py
- test_sanitization_comprehensive.py
- test_auction_swarm_runtime.py
- test_celery_maintenance_real.py
- integrations.py
- api/mfa.py
- AuctionAnnouncement
- TeamFormationEngine
- WorkflowState
- test_openapi_importer_comprehensive.py
- BrowserAgent
- test_voice_e2e.py
- test_policy_runtime.py
- test_saml_provider.py
- factory
- ExperimentRegistry
- test_governance_extra2.py
- test_cost_tracker.py
- test_rag_patterns_real_openai.py
- test_skills_executor.py
- run_goal
- test_test_runner.py
- test_final_fixes.py
- test_auth_api_coverage.py
- WorkItem
- _make_service
- channels/ingestion.py
- Event
- skills_runtime.py
- PageAnalyzer
- HITLWorkflowGateway
- cli/main.py
- models/__init__.py
- IngestionOrchestrator
- KnowledgeGraphStore
- OutboundWebhookService
- test_enterprise_comprehensive2.py
- api/test_governance_comprehensive.py
- test_enterprise_intelligence_gaps.py
- test_generative_agent.py
- ToolContext
- OrgResponse
- ComplianceBundleManager
- test_audit_v2.py
- PlanTier
- Tokenizer
- test_workflow_planner_comprehensive.py
- ComplianceChecker
- InClusterKubernetesClient
- DepartmentMemory
- ChannelRateLimiter
- test_redis_factory.py
- tenant_service.py
- test_reasoning_retrieval_strategies.py
- classify_tool_risk
- memory_v2.py
- LeaseManager
- test_state_runtime.py
- MCPClient
- MemoryRecord
- ABTestingEngine
- test_real_postgres.py
- test_phase6_7_rag_runtime.py
- ._make
- test_knowledge_base_pipeline.py
- ToolSelector
- outbox.py
- test_fakeredis_gaps.py
- test_validators.py
- org/connectors/__init__.py
- test_consumers.py
- WorkflowHITLRequest
- asyncio
- TestKnowledgeStoreBasic
- test_router_runs.py
- check_tool_args_for_exfil
- guardrails_v2/engine.py
- ClaimRepository
- test_servers_comprehensive.py
- AgentVersePlugin
- InMemoryCacheBackend
- test_insights_comprehensive.py
- test_integrations_api_comprehensive.py
- test_phase14_deep_coverage.py
- test_mfa.py
- schedules.py
- auth/mfa.py
- test_scim_handler.py
- LLMResponseCache
- PromptVariant
- test_runtime_readiness.py
- test_collab_api_comprehensive.py
- test_final_coverage_push.py
- test_rate_limiter.py
- _StatefulMockSession
- test_main_lifespan.py
- ChatService
- test_a2a_dispatch.py
- test_rls_isolation.py
- api/memory.py
- test_prompt_optimizer_persistence.py
- test_communication_connectors.py
- RAFTJobRecord
- test_sandbox_runtime.py
- test_expression_engine.py
- _make_app
- test_connectors_comprehensive2.py
- test_api_e2e.py
- test_full_stack_e2e.py
- GoalAnalyticsAggregator
- test_stream.py
- AuditEvent
- test_semantic_cache_comprehensive.py
- ContextEngine
- test_condition_consumer.py
- test_chat_api.py
- _headers
- RetrieverTool
- test_orchestrator.py
- test_hallucination_fixes.py
- TestSolutionsCatalog
- test_scopes_rbac.py
- test_group_chat.py
- TenantOptimizationState
- fire_due_schedules
- test_plan_runtime.py
- ProvenanceRecord
- api/test_connectors.py
- _make_breaker
- test_document_parser_comprehensive2.py
- AgentRouter
- costs.py
- PermissionCache
- SimulationRunner
- test_costs_comprehensive.py
- count_tokens
- test_ip_allowlist_comprehensive.py
- coordination/contracts.py
- test_cost_dashboard_api.py
- ImprovementActionRecord
- get_extractor
- test_civilization_api.py
- test_guardrails_comprehensive2.py
- _make_app
- test_agent_patterns_real_openai.py
- test_all_providers.py
- admin.py
- get_settings
- capabilities/test_capability_registry.py
- Goal
- PIIDetector
- PromptOptimizer
- HealthCheck
- ProviderCircuitBreaker
- BM25Retriever
- test_priority_queue.py
- test_domain_role_templates.py
- monitoring/test_parsers.py
- test_enterprise_extra3.py
- test_memory_comprehensive.py
- test_infrastructure_e2e.py
- test_enterprise_api.py
- test_scheduler.py
- asyncio
- test_phase4_connectors.py
- test_agent_builder.py
- _get_client_ip
- test_scope_seeder.py
- MarketplaceAgentContent
- EvalSuite
- _svc
- Any
- _ingest_repo_background
- _sign
- MultiHopReasoner
- OcrEngine
- get_vault
- workflow/test_security.py
- LargePayloadStore
- test_connectors_comprehensive.py
- SIEMType
- _make_app
- test_goal_service_run_eval.py
- test_docker_compose.py
- test_self_optimizer_v2_comprehensive2.py
- collab.py
- embeddings.py
- test_phase3_4_multimodal.py
- ContentLoader
- test_audit_v2_comprehensive.py
- few_shot_cot.py
- MetaAgentPlanner
- ProspectiveMemoryService
- WorkingMemory
- _WorkflowStore
- SemanticChunker
- test_raft_repository_integration.py
- router_runs.py
- test_goals_final.py
- test_knowledge_comprehensive2.py
- test_templates_comprehensive2.py
- test_enterprise_v2.py
- asyncio
- api/test_artifacts.py
- ConsensusVerifier
- _ctx
- ._get_all_goals
- test_program09_core.py
- MarketplaceV2
- QueryPlanner
- OrgMCPResources
- workflows.py
- InjectionGuard
- DeletionOrchestrator
- ExtractedField
- intent_router.py
- _make_agents_app
- test_schedules_comprehensive.py
- .__init__
- RoutingDecision
- parse_verifier_verdict
- api/knowledge_graph.py
- Governor
- AgentManifest
- test_rls_behavioral_isolation.py
- test_schedules_extra.py
- _chunk_by_chars
- OrgLoopDetector
- LateChunker
- GoalExecutionLock
- CredentialInjector
- MockMCPServer
- UsageService
- tenancy/billing.py
- ArtifactTool
- router_hitl.py
- test_perception_comprehensive.py
- test_phase12_13_skills_frontend.py
- test_suggestions_shape.py
- ai_ops.py
- _make_app
- dpdp.py
- OllamaProvider
- AuditFlusher
- audit_v3.py
- estimate_cost
- OAuthFlowManager
- test_devops_connectors.py
- OpenAIFineTuneProvider
- KGQueryEngine
- test_tools_comprehensive.py
- test_agents_comprehensive2.py
- OrgMCPServer
- test_mcp_circuit_breaker.py
- asyncio
- test_full_pipeline_real_world.py
- test_untested_modules.py
- models/intelligence.py
- test_advanced.py
- TestRegistryDetection
- ._get_stats
- JiraIngestor
- LlmStructuredExtractor
- test_explainability.py
- CapabilityRegistry
- OrgDigitalTwin
- OrgLearningPipeline
- celery_tasks.py
- is_valid_transition
- TenantUserService
- router_versions.py
- test_cache_synthesis.py
- test_analytics_db.py
- test_civilization_extra4.py
- state_context.py
- test_schedules_api.py
- test_wiring_integrity.py
- test_voice_router.py
- _MockSession
- build_manifest
- StreamingGuard
- NvidiaNIMProvider
- test_tenants.py
- execution_environment/test_artifacts.py
- GitHubIngestor
- DataCategory
- SalienceScorer
- .generate
- test_greeting.py
- _make_mock_db
- MoAProposal
- test_tenants_comprehensive.py
- test_workflows_comprehensive.py
- test_all_connectors_e2e.py
- workflow/test_router.py
- Phased Roadmap (9 layers → 47 components, TDD each)
- CSVParser
- IdempotencyStore
- CRDTRoomManager
- generate_gst_invoice
- test_tenant_service_full.py
- test_knowledge_rpa.py
- perception.py
- rpa.py
- MemoryAPI
- KnowledgeRuntimeProfile
- CoordinationStreams
- SupervisorAgent
- test_memory_learning_services.py
- Any
- OAuthState
- RAFTDatasetRecord
- test_db_paths_comprehensive.py
- asyncio
- _resolve_checkpointer
- test_golden_datasets.py
- test_goals_extra.py
- _make_app
- test_tenants_comprehensive2.py
- SkillSelector
- VoyageProvider
- test_api.py
- _scheduled_goal_id
- a2a/__init__.py
- GuardrailEngine
- MCPWebSocketClient
- DocumentType
- EntityVersionManager
- LLMQueryTransformer
- test_audit_scopes_limits.py
- WorkflowVariableStore
- test_rag_migration_roundtrip.py
- test_perception_api.py
- workflow/test_context.py
- test_codeact.py
- TenantScopedStore
- skills.py
- SCIMHandler
- ChatSearchEngine
- test_collaboration_runtime.py
- test_tool_risk.py
- test_policy_propagation.py
- ToxicityClassifier
- check_and_process_emails
- test_project_management_connectors.py
- select_adaptive_strategy
- SearchDirectiveParser
- RetrievalEvaluator
- test_knowledge_api.py
- test_router_versions.py
- PostgresWorkflowRunStore
- _make_app
- asyncio
- test_rpa_comprehensive.py
- test_self_optimizer_v2_comprehensive.py
- test_ws_client_comprehensive.py
- CodeExecutionObservation
- AnswerSynthesizer
- coordination_moa.py
- api/guardrails.py
- marketplace_monetization.py
- CustomRoleStore
- ServicesAPI
- test_worker_entrypoint.py
- CommandScheduler
- TestSemanticCacheGetSet
- GoalRefinementPipeline
- SubAgentTask
- calibrate_scores
- test_celery_agentgraph.py
- build_result_artifact
- SubTenantService
- test_safe_web_capability.py
- test_civilization_api_comprehensive2.py
- test_connectors_catalog.py
- test_knowledge_comprehensive.py
- test_phase10_11_ai_ops_memory.py
- test_workflows.py
- CorpusSample
- TestUniversalArgumentResolver
- TestAgentStore
- api/tools.py
- trust_governance.py
- RuntimeProfilesRegistry
- gateway/router.py
- SharePointConnector
- .run
- GuardrailViolation
- test_ingestion_pipeline.py
- ModelRouter
- test_hitl_new_endpoints.py
- WaitStepNode
- api/test_collab.py
- test_tools_api_comprehensive.py
- agent/test_persistence.py
- observability.py
- ChatCodeExecutor
- test_production_safety.py
- execution_environment/models.py
- ContentDeduplicator
- CommunityDetector
- ConfluenceIngestor
- run_mocked_certification
- test_store_comprehensive3.py
- OrgSimulationEngine
- test_context_manager.py
- rls.py
- VoiceAlertManager
- agent/test_router.py
- test_swarm_gossip.py
- GraphNode
- test_workflows_comprehensive2.py
- test_notification_service.py
- test_tenant_service_db.py
- test_api_extended.py
- test_jira_agent_execution.py
- insights.py
- AgentCredentialStore
- test_agent_identity_layer.py
- ConversationContext
- test_router_hitl.py
- EmbeddingRouter
- NotionConnector
- scan_for_encoding_attacks
- DecisionTrace
- test_agents_api.py
- test_ingestors_coverage.py
- OcrDocumentTool
- test_polling.py
- test_phase_completeness.py
- RedisCircuitBreaker
- decision_store.py
- test_optimistic_concurrency.py
- test_replay_comprehensive.py
- _AllowingFakeRedis
- test_redis_bulkhead.py
- AlertRouter
- coordination_handoffs.py
- warm_permission_cache
- models/knowledge.py
- evaluate_rule
- test_workflow_builder.py
- StructuredPlanExecutor
- test_entitlements.py
- VoiceStreamingSession
- test_otel.py
- test_agent_identity.py
- test_analytics_comprehensive.py
- test_goals.py
- AuditV3
- agent_runtime.py
- execution_environment/test_events.py
- test_middleware_full.py
- agent/test_errors.py
- test_phase_n8_n10.py
- orchestration.py
- magentic/adapter.py
- test_agent_knowledge_binding.py
- test_key_rotation.py
- tool_allowed_for_autonomy
- semantic_cache.py
- CeleryGoalTaskQueue
- test_agent_advanced.py
- test_ghost_run.py
- test_rollback_experiment.py
- test_rpa_execute.py
- _make_app
- e2e_full/conftest.py
- test_sse_resume.py
- S3Connector
- MarkdownParser
- api/analytics.py
- resize_image_b64
- StructuredLogStore
- google_oauth.py
- Any
- decide_rollout
- DeduplicationCache
- test_routing.py
- UniversalArgumentResolver
- AutonomyEnforcer
- hitl_extension.py
- test_agents_extra.py
- api/test_artifacts_comprehensive.py
- test_phase5_knowledge_graph.py
- test_live_platform.py
- TestTenantServiceCachedLookup
- collect_sse
- test_system_comprehensive.py
- api/coordination.py
- AttributionVerifier
- test_spawn_tool.py
- TestDomainPolicies
- GraphAccessControl
- test_gap_fill_phase2.py
- GraphFactory
- multi_turn_eval.py
- org/metrics.py
- test_mission_flow.py
- BulkheadRegistry
- test_migrations.py
- test_compliance_download.py
- guardrail_engine.py
- TestRAGPlatformQueryPlanner
- _MockSession
- OrgHealthScore
- OrgEventPublisher
- _make_db_mock
- test_backend_fixes.py
- test_multimodal_e2e.py
- test_token_metrics.py
- TestAiOpsModels
- verify_goal_token
- models/auth.py
- ChannelAuthGuard
- EmailParser
- SlackIngestor
- sign_envelope
- wait_for_status
- test_rag_goal_retrieval_e2e.py
- test_durable_execution.py
- _strip_secret_redis_schedule_fields
- test_dr_drill.py
- test_self_improvement_smoke.py
- test_a2a.py
- TestToolReliabilityStoreWithDBError
- TestToolReliabilityDBPaths
- test_rag_patterns_functional.py
- test_cli.py
- EvalRunner
- test_goal_hitl_lifecycle_e2e.py
- _make_mock_db
- ReadinessEvaluator
- TestRunGoalPaths
- api/artifacts.py
- ocr.py
- api/policy_rules.py
- PromptBlock
- requires_consensus
- Any
- test_reliability_fixes.py
- record_desired_workers
- role_taxonomy.py
- RedisBulkheadRegistry
- GoalDeduplicator
- test_step_enforcement.py
- test_goals_metrics.py
- TestMaybePromote
- agent/supervisor.py
- ShadowRouter
- test_phase1_gaps.py
- TestAuth
- test_workflow_definition_bridge.py
- test_evals_scorecard_e2e.py
- ApprovalChainRegistry
- test_memory_recall_e2e.py
- upsert_google_user
- test_bm25_ws_raptor.py
- EmailChannelAdapter
- CloudDestructionGuard
- test_guardrails_v3.py
- scan_output_for_anomalies
- verify_stream_token
- test_model_router_e2e.py
- test_oauth_security.py
- test_graph_persistence_wiring.py
- test_artifact_tool.py
- _make_eval_runner
- test_goals_eval.py
- ApprovalChainEngine
- test_workflow_trigger_e2e.py
- test_user_models.py
- build_services
- test_prompt_variants_api.py
- TestMultimodalPipeline
- TestInputClamping
- coordination_group_chat.py
- list_active_sessions
- roles.py
- asyncio
- test_observability_trace_e2e.py
- generate_api_key
- TestReviewRiskLevels
- scan_tool_output
- advanced_services.py
- TestSelectVariant
- test_semantic_cache.py
- RedisBulkhead
- RoutingOptimizer
- agent/consensus.py
- _name_tokens
- TestCheckAuthRateLimit
- test_builder_preview.py
- test_openapi_schema.py
- api/test_replay.py
- test_deletion_cascade.py
- test_knowledge_graph_e2e.py
- OpenRouterProvider
- RedisDeduplicationCache
- _trigram_score
- grant_elevation
- test_supervisor_debate_wiring.py
- asyncio
- test_auth_login_redirects
- ConversationManager
- TestMemoryV2Models
- MCPFullSpec
- DiscordChannelAdapter
- test_gap_completions.py
- Agent pattern canary rollback
- Agent pattern runaway limits
- Auction anomalies
- Coordination delivery lag
- Coordination lease reclaims
- Magentic stalls and fallbacks
- Reflexion quality poisoning
- Sandbox denial outage
- Tenant policy anomalies
- test_critical_fixes.py
- test_billing.py
- TestRedisCache
- TestIsSignificant
- test_eval_suite_api.py
- test_emergency_stop.py
- TestSharePointConnector
- .subscribe_to_changes
- TestPdfIngestor
- TestPdfIngestor
- gcp_server.py
- TestGetMetrics
- chat/templates.py
- app/ingestion/connectors/__init__.py
- .exchange_code
- brevo_server.py
- firebase_server.py
- ._decompose
- PolicyEvidenceEngine
- _resolve_request_strategy
- certify_sandbox
- test_capabilities.py
- KokoroTTS
- OmniVoiceTTS
- test_tool_aware_planning.py
- test_insights.py
- test_trigger_fire_e2e.py
- _FakeWorkflowMCPClient
- load/goal_submission.js
- test_celery_critical.py
- TestFireDueSchedules
- test_sse_bridge_sentinel.py
- get_sandbox_config
- Base
- SlackChannelAdapter
- WebhookChannelAdapter
- _FakeRedis
- gmail_server.py
- test_orchestration_integration.py
- Any
- SubTenantManager
- _mock_boto3_ses
- coordination_magentic.py
- _ctx
- ToolTrace
- MacOSSayTTS
- test_magentic_api.py
- TestPersistOutcome
- PromptVariantSelector
- api/test_memory_api.py
- test_mfa_module.py
- AuditLog
- acoustic_server.py
- apache_kafka_server.py
- TestVerifyHmac
- test_helm_chart.py
- test_k8s_manifests.py
- ecwid_server.py
- ._percentile
- TestBayesianProbBetter
- TestDocxIngestor
- tenancy/test_context.py
- sap_server.py
- _cosine_similarity
- ApprovalChain
- TestSubscribeEvents
- WhatsAppChannelAdapter
- quickbooks_server.py
- mailchimp_server.py
- notion_server.py
- .__init__
- CollectiveIntelligence
- .for_direct_goal
- .approve
- test_guardrail_rules_persistence.py
- amadeus_server.py
- test_startup_wiring.py
- BrowserFallbackTTS
- _cleanup_expired_crdt_tokens
- graph
- test_hitl_gate.py
- ingestion/metrics.py
- NotebookParser
- TestDocxIngestor
- _CallTrackerSession
- get_public_status
- amazon_sqs_server.py
- constant_contact_server.py
- 0097_coordination_runtime.py
- get_logger
- bamboohr_server.py
- test_gateway_auth.py
- buildium_server.py
- jotform_server.py
- cloudinary_server.py
- evernote_server.py
- doordash_server.py
- looker_server.py
- freshservice_server.py
- microsoft_onenote_server.py
- yotpo_server.py
- .call_tool
- StrategicAdvisor
- VoiceWebhookAdapter
- gorgias_server.py
- microsoft_todo_server.py
- moosend_server.py
- _Noop
- ._assign
- omnisend_server.py
- AgentVerse Load Tests
- qa-agent.md
- monday_server.py
- TestVerifyTelegramUser
- smoke.js
- test_mission_execute_wired_services.py
- pushbullet_server.py
- toast_pos_server.py
- webflow_server.py
- PostgreSQLConnector
- WebCrawlConnector
- error_response
- GoalTokenStore
- 0101_magentic_moa.py
- 0102_camel_generative_swarm_auction.py
- 0104_memory_learning.py
- .find_completed_model
- asana_server.py
- get_approval_engine
- tests/conftest.py
- _FakeTTS
- bitbucket_server.py
- chargebee_server.py
- TestSubscribeHITLRejections
- TestPauseGoal
- customerio_server.py
- env.py
- freshdesk_server.py
- athenahealth_server.py
- call_tool
- instagram_server.py
- klaviyo_server.py
- mattermost_server.py
- microsoft_outlook_server.py
- test_coordination_handoffs.py
- pipedrive_server.py
- test_tools_router.py
- pandadoc_server.py
- snovio_server.py
- test_goals_batch_submit_route_exists
- .test_unknown_action_returns_false
- wave_server.py
- test_hitl_gap_closures.py
- RoleResolver
- .test_db_exception_falls_back_to_memory
- wrike_server.py
- .maybe_promote
- zoho_crm_server.py
- .purge_expired
- db_tenant_ctx
- azure_devops_server.py
- tool_policy.py
- pytest_collection_modifyitems
- compliance_endpoints.js
- Runbook: Backend Instance Down
- Runbook: High Goal Failure Rate
- security-reviewer.md
- TestLabAPI
- facebook_conversions_server.py
- test_keycloak_sso.py
- test_org_db_e2e.py
- .test_redis_path_raises_propagated
- test_internal_url_is_blocked
- test_goal_idempotency_e2e.py
- TestChannelAuthConstants
- test_redbeat_config.py
- TestTenantContextIsolation
- test_ingestion_pipeline_e2e.py
- .test_with_scorecard_returns_scores
- v1/router.py
- 0091_rag_ingestion_structures.py
- 0100_handoffs_group_chat.py
- affinity_server.py
- apollo_server.py
- attio_server.py
- box_server.py
- copper_server.py
- discord_server.py
- gong_server.py
- SalesforceConnector
- intercom_server.py
- TeamsConnector
- mailerlite_server.py
- netsuite_server.py
- planhat_server.py
- jenkins_server.py
- salesforce_server.py
- sendgrid_server.py
- teamwork_server.py
- twilio_server.py
- zuora_server.py
- long_term_extractor.py
- PolicyEvidence
- dr-drill.sh
- _bypass_ssrf
- _no_resource_limits_in_process
- TestVerifyEmailSender
- TestVerifyWhatsappPhone
- test_bg_switch_script.py
- AgentVerse Load Tests
- soak.js
- FakeRedis
- 0045_civilization.py
- 0092_repository_ingestion_leases.py
- 0103_routing_safety_optimization.py
- test_tool_reliability.py
- braintree_server.py
- test_celery_routing.py
- MySQLConnector
- google_docs_server.py
- kubernetes_server.py
- vercel_server.py
- digitalocean_server.py
- docusign_server.py
- fireflies_server.py
- freshsales_server.py
- help_scout_server.py
- heroku_server.py
- linear_server.py
- netlify_server.py
- loadtest/goal_submission.js
- AgentVerse — Backend
- _digest
- test_graph_critical.py
- shopify_server.py
- auth_throughput.js
- test_structlog_migration.py
- terraform_server.py
- scaling/conftest.py
- test_worker_embedder.py
- woocommerce_server.py
- test_spec_module_importable
- TestScopeEnforcementBypass
- coordination_sessions.js
- group_chat_ws.js
- thresholds.js
- TestInflationTest
- analyze_gaps.py
- app/agent/nodes/__init__.py
- app/agent/tools/__init__.py
- zendesk_server.py
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
- .check_scope
- .__init__
- .verify_api_key
- .verify_hmac
- gateway/channels/__init__.py
- app/gateway/__init__.py
- app/routing_runtime/__init__.py
- app/scaling/__init__.py
- app/state_runtime/__init__.py
- TestImapListenerIsEnabled
- switch-traffic.sh
- coordination_streams.js
- sse_stream.js
- tests/agent/nodes/__init__.py
- api/conftest.py
- amazon_ses_server.py
- appsheet_server.py
- aweber_server.py
- bigquery_server.py
- bitly_server.py
- test_configure_saml_no_db
- test_configure_saml_with_db
- test_saml_login_no_db
- test_saml_acs_missing_saml_response
- test_provision_scim_token_no_db
- test_provision_scim_token_with_db
- test_list_templates_v2
- test_compliance_status_hipaa_with_checker
- test_rerun_compliance_check_hipaa
- test_rerun_compliance_check_soc2
- test_saml_metadata_with_row
- test_run_simulation_with_agent_store_hit
- test_deploy_template_v2_install_failed
- test_deploy_template_v2_success
- test_add_review_v2_failure
- test_add_review_v2_success
- test_list_reviews_v2
- test_search_templates_v2
- test_get_simulation_available_tools_without_mcp_client
- test_get_simulation_available_tools_mcp_exception
- test_run_red_team
- test_run_red_team_no_cases
- test_list_experiments_with_self_opt_v2
- test_list_suggestions
- test_apply_suggestion_not_found
- test_create_eval_suite_no_runner
- test_list_eval_suites_no_runner
- test_start_gdpr_export_no_db
- test_start_gdpr_export_with_db_exception
- test_gdpr_export_status_not_found
- test_compliance_status_no_checker
- test_compliance_status_gdpr
- test_compliance_status_unsupported_framework
- test_rerun_compliance_check_no_checker
- test_list_contracts_no_db
- test_sign_contract_invalid_type
- tests/multimodal/__init__.py
- campaign_monitor_server.py
- tests/routing_runtime/__init__.py
- tests/state_runtime/__init__.py
- agent-verse-backend
- close_crm_server.py
- cloudflare_server.py
- drip_server.py
- emma_server.py
- figma_server.py
- formstack_server.py
- getresponse_server.py
- google_forms_server.py
- google_slides_server.py
- google_tasks_server.py
- gravity_forms_server.py
- hubspot_server.py
- linkedin_server.py
- loom_server.py
- loops_server.py
- mailgun_server.py
- manychat_server.py
- maropost_server.py
- microsoft_excel_server.py
- onesignal_server.py
- order_desk_server.py
- plivo_server.py
- postmark_server.py
- recruitee_server.py
- ringcentral_server.py
- shipstation_server.py
- signnow_server.py
- surveymonkey_server.py
- twitch_server.py
- typeform_server.py
- wufoo_server.py
- _Types
- aws_server.py
- buffer_server.py
- clockify_server.py
- convertkit_server.py
- ebay_server.py
- etsy_server.py
- expensify_server.py
- facebook_lead_ads_server.py
- facebook_pages_server.py
- filestack_server.py
- freshbooks_server.py
- grafana_server.py
- greenhouse_server.py
- gumroad_server.py
- gusto_server.py
- harvest_server.py
- hive_server.py
- hootsuite_server.py
- invoice_ninja_server.py
- kajabi_server.py
- knack_server.py
- lightspeed_server.py
- magento_server.py
- mandrill_server.py
- miro_server.py
- ninox_server.py
- pinterest_server.py
- pivotal_tracker_server.py
- procore_server.py
- profitwell_server.py
- pushover_server.py
- redmine_server.py
- samcart_server.py
- smartsheets_server.py
- sonarqube_server.py
- spotify_server.py
- sprout_social_server.py
- squarespace_server.py
- storyblok_server.py
- substack_server.py
- teachable_server.py
- thinkific_server.py
- toggl_server.py
- vimeo_server.py
- vonage_server.py
- wistia_server.py
- zoho_books_server.py
- zoho_invoice_server.py
- .__init__
- _check_rate_limit_with_fallback
- _extract_text
- .get_unreliable_tools
- .record
- test_tool_reliability_store_get_unreliable
- test_tool_reliability_store_records

## God Nodes (most connected - your core abstractions)
1. `TenantContext` - 1397 edges
2. `FakeProvider` - 721 edges
3. `get_logger()` - 584 edges
4. `AgentGraph` - 446 edges
5. `PlanTier` - 441 edges
6. `create_app()` - 390 edges
7. `GoalService` - 346 edges
8. `_post()` - 343 edges
9. `GoalStatus` - 327 edges
10. `CompletionRequest` - 296 edges

## Surprising Connections (you probably didn't know these)
- `test_agent_graph_has_node_rag_prime()` --uses--> `AgentGraph`  [INFERRED]
  tests/agent/test_rag_nodes.py → app/agent/graph.py
- `test_agent_graph_has_node_rag_remediate()` --uses--> `AgentGraph`  [INFERRED]
  tests/agent/test_rag_nodes.py → app/agent/graph.py
- `test_agent_graph_has_node_refine()` --uses--> `AgentGraph`  [INFERRED]
  tests/agent/test_rag_nodes.py → app/agent/graph.py
- `test_legacy_model_router_delegates_complexity_to_canonical_classifier()` --uses--> `ModelRouter`  [INFERRED]
  tests/orchestration/test_ownership_reconciliation.py → app/agent/model_router.py
- `test_collab_store_on_state()` --uses--> `CollaborationStore`  [INFERRED]
  tests/core/test_main_create_app.py → app/collab/store.py

## Import Cycles
- None detected.

## Communities (1556 total, 102 thin omitted)

### Community 0 - "activecampaign_server.py"
Cohesion: 0.40
Nodes (4): call_tool(), _headers(), Any, ActiveCampaign MCP server — email marketing & CRM contacts, lists, and…

### Community 1 - "CompletionRequest"
Cohesion: 0.01
Nodes (340): Unified async grounding check. Supports both positional (``step_output``,…, Reflection node: diagnoses failure and populates verification_feedback., Self-Refine node — improves last step output before verification (doc-1 §3.4).…, Generate a parallel-aware workflow plan from a natural language goal., Stub simulation using keyword-based planning (no real LLM required)., Toxicity classifier — pattern-based first pass + optional LLM second pass.…, VisionParser — describes images using GPT-4o or Claude Vision. Supports two…, Use the injected LLMProvider Protocol to describe the image. Builds a… (+332 more)

### Community 2 - "FakeProvider"
Cohesion: 0.01
Nodes (406): AgentProposal, DebateOrchestrator, Any, Debate/voting pattern — N agents independently propose solutions, critique each…, Run N agents in a debate to find the best solution via voting., Run multi-agent debate and return winning proposal., AgentGraph, LangGraph-based agent loop with RAG retrieval, 12-step pipeline, and… (+398 more)

### Community 3 - "GoalService"
Cohesion: 0.01
Nodes (250): get_event_key(), Goal event emission and subscription management. Extracted from GoalService to…, Get dedup key for an event., GoalRecord, GoalService, _monotonic(), Any, AuditLog (+242 more)

### Community 4 - "AuditLog"
Cohesion: 0.01
Nodes (301): Extract tool name — prefers structured tool_calls, then registry, then…, Mixin extracted from app.agent.graph — zero semantic changes., # IMPORTANT: OpenAI function names must match ^[a-zA-Z0-9_-]{1,64}$, AuditEvent, AuditLog, Any, Immutable audit trail — append-only log of all governed actions. Records are…, Read audit events directly from PostgreSQL with full filter + pagination. This… (+293 more)

### Community 5 - "TenantContext"
Cohesion: 0.01
Nodes (278): AbstractAsyncContextManager, Read a single agent directly from DB; fall back to memory cache., Soft-delete from PostgreSQL (is_active=FALSE) and remove from memory cache., Merge data into agent record and persist to DB., Any, CoordinationStore, Commit domain state, event, and outbox intent in one RLS transaction., Set app.tenant_id RLS variable for a SQLAlchemy AsyncSession. Must be called… (+270 more)

### Community 6 - "test_celery_tasks_coverage.py"
Cohesion: 0.02
Nodes (135): _db_schedule_discovery_enabled(), _decrement_after_completion(), _delete_expired_records(), execute_retention_policy(), _expire_db_approvals(), expire_hitl_approvals(), _find_and_fail_stuck_goals(), _get_llm_provider() (+127 more)

### Community 7 - "logging.py"
Cohesion: 0.02
Nodes (152): BaseConnector, ConnectionHealth, ABC, BaseConnector — the single interface every source adapter implements. LAW-01:…, Handle real-time push events (webhooks/notifications). Override for: S3 event…, True if validate_connection can also estimate doc count (LAW-22)., Return True if `name` passes include/exclude glob patterns. - If include is…, Result of BaseConnector.validate_connection(). (+144 more)

### Community 8 - "RAGStrategy"
Cohesion: 0.02
Nodes (180): AgenticDecision, _claims_support_answer(), _format_decision_evidence(), Any, Bounded agentic RAG decisions over canonical retrieval primitives., ColBERT token-level late-interaction reranking., grade_evidence(), RetrievalResult (+172 more)

### Community 9 - "_task"
Cohesion: 0.02
Nodes (102): dispose_task_engine(), Dispose all pooled asyncpg connections owned by the module-level engine. Celery…, check_mcp_health(), civilization_learning_step(), conclude_stale_experiments(), consolidate_memories_task(), delta_reingest_files(), detect_stuck_goals() (+94 more)

### Community 10 - "AgentState"
Cohesion: 0.02
Nodes (149): Return True when the last *window* steps are all FAILED. Signals a stuck…, _classify_failure(), get_reflexion_wirer(), Any, ReflexionWirer — automatically stores failure lessons in ReflexionStore. Doc-1…, Extracts and stores failure lessons after goal execution., Async version: stores lesson in-memory AND persists to DB., Sync version: stores lesson in-memory only (backward compat). Prefer… (+141 more)

### Community 11 - "HITLGateway"
Cohesion: 0.01
Nodes (138): ApprovalRequest, _AwaitableBool, HITLGateway, Any, Allow comparison with plain request_id strings for backward compat., Hash equals hash(request_id) so ApprovalRequest works as a dict key., Async-capable HITL gateway with blocking wait and timeout escalation., Create an approval request and return it (non-blocking). The returned… (+130 more)

### Community 12 - "SourceConfig"
Cohesion: 0.02
Nodes (166): Return allowed principals for a document (LAW-07). Default: empty list (tenant-…, Signal that a source document was deleted. Called when source-side deletion is…, Estimate total document count for progress reporting. Returns None if unknown…, list_registered(), load_all_connectors(), Import all connector modules to trigger @register decorators. Call this once at…, List all registered source types., AgentGeneratedConnector (+158 more)

### Community 13 - "_instantiate_provider"
Cohesion: 0.13
Nodes (16): get_provider_catalog(), instantiate_configured_provider(), _instantiate_provider(), ProviderConfig, ProviderConfigurationError, Any, ValueError, Provider Registry ================= Declarative registry for LLM providers.… (+8 more)

### Community 14 - "ScheduleStore"
Cohesion: 0.02
Nodes (144): Complete configuration for any of the 58 trigger types., TriggerSpec, apply_config_to_spec(), Any, In-memory schedule store — CRUD for trigger specs, per-tenant. In production…, Per-tenant schedule registry., Family-specific fields the beat loop reads, keyed exactly as the loop expects…, Rotate a webhook signing secret, retaining the previous one for a grace window… (+136 more)

### Community 15 - "ColBERTPattern"
Cohesion: 0.01
Nodes (142): AdaptiveRAGPattern, Any, AgenticChunkingPattern, Any, Agentic Chunking pattern — LLM-driven proposition extraction as atomic chunks.…, Extract propositions from all chunks. Returns proposition-level chunk dicts., Agentic Chunking: LLM-driven proposition extraction (Dense X Retrieval)., Search persisted propositions and return their parent-window citations. (+134 more)

### Community 16 - "GoalRuntimeProfile"
Cohesion: 0.04
Nodes (150): Any, DynamicGraphAssembler — builds a per-goal LangGraph from PatternConfig., AgentPatternConfig, EvalConfig, GoalProperties, GoalRuntimeProfile, MemoryCacheConfig, ModelPlanConfig (+142 more)

### Community 17 - "GuardrailChecker"
Cohesion: 0.02
Nodes (105): disclose_context(), DisclosedContext, Any, Field-level minimization and taint preservation across coordination hops., build_default_permission_matrix(), Construct a PermissionMatrix seeded with the platform default-deny posture.…, _detect_base64_injection(), _detect_homoglyph_injection() (+97 more)

### Community 18 - "WorkflowCompiler"
Cohesion: 0.06
Nodes (36): CompiledWorkflow, Any, BaseException, WorkflowCompiler — compiles WorkflowDefinition → LangGraph StateGraph. One…, Build the async node function for a step., Parse '30s' / '5m' / '2h' / bare seconds → float seconds (0 = none)., Honour RetryConfig.fail_on / retry_on exception-name filters., Backoff delay in seconds before the next attempt (1-based). (+28 more)

### Community 19 - "test_colbert_runtime.py"
Cohesion: 0.02
Nodes (125): AbstractEventLoop, ColBERTLateInteractionReranker, ColBERTRAGRuntimeAdapter, _cosine(), _get_encoder(), _load_colbert_model(), maxsim_score(), Protocol (+117 more)

### Community 20 - "build_default_registry"
Cohesion: 0.02
Nodes (132): AdapterDescriptor, AgentGraphCompatibilityAdapter, core_execution_descriptor(), local_agent_graph_descriptor(), Any, rag_adapter_descriptor(), RAGCompatibilityAdapter, Registry-owned factory descriptor; never constructed from request data. (+124 more)

### Community 21 - "test_dynamic_orchestration_e2e.py"
Cohesion: 0.03
Nodes (111): RiskLevel, GovernanceBundle, GovernanceConfig, GovernanceProfileSelector, GovernanceProfileSelector — selects governance bundle per plan tier and risk., GuardrailBundle, GuardrailConfig, GuardrailProfileSelector (+103 more)

### Community 22 - "retrieve"
Cohesion: 0.02
Nodes (117): BM25CorpusScorer, BM25Hit, Application-side Okapi BM25 corpus scoring., Search indexed chunks using BM25 scoring. Returns up to *top_k* results with…, Tokenize complete text into lowercase Unicode-safe words., Accumulate corpus statistics, then score documents without retaining them., _tokenize(), _bm25_search_persisted() (+109 more)

### Community 23 - "RollbackEngine"
Cohesion: 0.02
Nodes (101): CircuitBreaker, CircuitState, Per-tool circuit breaker. Args: failure_threshold: Number of consecutive…, Return True if a call is allowed now (handles HALF_OPEN probe window)., Async-compatible wrapper — delegates to the synchronous can_call()., Async-compatible wrapper — delegates to record_failure()., Async-compatible wrapper — delegates to record_success()., Redis-backed circuit breaker — state shared across all worker replicas. Each… (+93 more)

### Community 24 - "test_layer4_complete.py"
Cohesion: 0.04
Nodes (63): CitationThreader, Any, CitationThreader — attaches sequential citation indices to chunks., ContextGapDetector, ContextGapDetector — detects 12 gap signal phrases defined in doc-2 §4., FallbackAttempt, FallbackChain, FallbackDecision (+55 more)

### Community 25 - "TenantMiddleware"
Cohesion: 0.03
Nodes (100): BaseHTTPMiddleware, Authenticate API key → inject TenantContext into request.state.tenant. When…, Add OWASP security headers to every response., SecurityHeadersMiddleware, TenantMiddleware, _make_enterprise_app(), _make_goals_app(), _make_rpa_app() (+92 more)

### Community 26 - "api/knowledge.py"
Cohesion: 0.03
Nodes (163): _cache_stats(), clear_cache(), CollectionIngestRequest, ConfluenceIngestRequest, create_collection(), CreateCollectionRequest, delete_collection(), delete_document() (+155 more)

### Community 27 - "RetrievalResult"
Cohesion: 0.03
Nodes (122): build_safe_web_search_capability(), _combine_domain_constraints(), _domain_allowed(), GovernedWebSearchCapability, _html_to_text(), AsyncBaseTransport, Protocol, RetrievalResult (+114 more)

### Community 28 - "test_dispatcher.py"
Cohesion: 0.02
Nodes (115): _build_scheduled_trigger_spec(), _dispatch_scheduled_via_dispatcher(), Map a schedule dict onto a TriggerSpec for governed dispatch (WT-9)., Route a scheduled fire through the TriggerDispatcher for governance parity. The…, Per-tenant bulkhead: limits concurrent in-flight trigger goals., Redis-backed per-tenant concurrency limiter., Return True if the slot was acquired (trigger can proceed)., Release a bulkhead slot. (+107 more)

### Community 29 - "agent/graph.py"
Cohesion: 0.04
Nodes (73): LangGraph StateGraph-based autonomous agent. Graph topology: START → initialize…, Verify step — should_skip_cache guard applied before LLM call. The LLM response…, GraphState, RuntimeError, TypedDict, Shared type definitions used by AgentGraph and its node mixins. Extracted from…, A required, tenant-scoped retrieval leg failed., RetrievalEntryPointError (+65 more)

### Community 30 - "test_consensus_behavior.py"
Cohesion: 0.17
Nodes (21): ConsensusVote, DurableConsensusRuntime, DurableConsensusState, Any, BaseModel, datetime, asyncio, test_consensus_escalates_disagreement_quorum_and_budget() (+13 more)

### Community 31 - "org/router.py"
Cohesion: 0.03
Nodes (163): get_dept_memory(), Return the process-local DepartmentMemory singleton., get_strategic_advisor(), get_version_store(), get_twin(), Return the process-local digital twin instance., get_org_event_publisher(), get_capability_graph() (+155 more)

### Community 32 - "MultimodalPipeline"
Cohesion: 0.03
Nodes (86): AssetJobStore, _AsyncRedisLike, _job_key(), _job_to_payload(), _payload_to_job(), Any, Protocol, Persistent store for AssetIngestionJob records (D-23). Before this module… (+78 more)

### Community 33 - "_inject_goal"
Cohesion: 0.10
Nodes (12): _inject_goal(), Line 1158: _track_db_task on goal_failed., Lines 1086-1150: eval scoring on goal_complete., Lines 1126-1148: low score triggers self_optimizer., Lines 1179-1185: publish to Redis on goal_complete., Line 1192: events pushed to subscriber queues., Lines 1950-1979: checkpoint resume via graph instance., Lines 1980-1981: exception in graph resume → legacy fallback. (+4 more)

### Community 34 - "test_batch3_servers.py"
Cohesion: 0.05
Nodes (158): make_resp(), mk_client(), Any, asyncio, Unit tests for batch-3 MCP servers (servers 1-25 of this batch). Uses the same…, test_braintree_create_customer(), test_braintree_create_subscription(), test_braintree_create_transaction() (+150 more)

### Community 35 - "_post"
Cohesion: 0.04
Nodes (155): add_golden_task(), add_review_v2(), AddGoldenTaskRequest, AddReviewRequest, apply_suggestion(), browse_marketplace(), BundleDeployRequest, _compliance() (+147 more)

### Community 36 - "LongTermMemoryStore"
Cohesion: 0.02
Nodes (147): Any, Read unprocessed rows from ``goal_feedback`` and derive improvement actions.…, LongTermMemory, LongTermMemoryStore, Any, Long-term memory — cross-session learnings persisted across agent runs. Stores…, Extract a learning from a completed goal and persist it. Adds to the in-memory…, Async store — persists to DB with embedding if embedder available. Computes a… (+139 more)

### Community 37 - "test_extra_coverage_servers.py"
Cohesion: 0.05
Nodes (148): make_resp(), mk_client(), Any, asyncio, Extra coverage tests to push remaining MCP servers above 80%. This file adds…, test_amplitude_user_profile(), test_calendar_check_freebusy(), test_calendar_update_event() (+140 more)

### Community 38 - "test_workflows_extra2.py"
Cohesion: 0.08
Nodes (43): _create_workflow(), _fake_wf(), _make_app(), _make_db_factory(), FastAPI, SimpleNamespace, TestClient, Extra coverage for /workflows API — pushes workflows.py from 67.4% → 85%+.… (+35 more)

### Community 39 - "CostController"
Cohesion: 0.02
Nodes (120): BudgetConfig, CostController, _parse_float(), Any, Atomically check budget and record cost. Returns True if within budget., True if the tenant has any daily budget left for a new goal. Used as a goal-…, Return the current-day spend for the tenant (resets at UTC midnight)., Production CostController backed by Redis for cross-replica accuracy. Uses an… (+112 more)

### Community 40 - "test_remaining_servers_dispatch.py"
Cohesion: 0.04
Nodes (145): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for remaining MCP servers. Covers: amplitude, mixpanel,…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_amplitude_export_events(), test_amplitude_get_active_users() (+137 more)

### Community 41 - "test_executor_playwright_mock.py"
Cohesion: 0.05
Nodes (144): requires_playwright, make_executor_with_session_manager(), make_mock_page(), make_mock_session(), make_playwright_cm(), make_standalone_executor(), asyncio, Cover app/rpa/executor.py Playwright interaction paths via mocked Playwright.… (+136 more)

### Community 42 - "test_runtime_scorecard.py"
Cohesion: 0.04
Nodes (92): GoalScorer, ModelScorer, ModelScorer — scores model efficiency: cost and latency., Score cost efficiency: how much below budget the goal executed., Score latency: faster execution = higher score., Any, RetrievalResult, RAGScorer (+84 more)

### Community 43 - "Settings"
Cohesion: 0.04
Nodes (66): field_validator, model_validator, Typed application settings, loaded from the environment (12-factor). Sensitive…, Application-wide configuration., Allow CORS origins as a comma-separated string in the environment., True if SSO is disabled OR a non-default client secret is set., Settings, ConnectionPools (+58 more)

### Community 44 - "test_batch2_servers.py"
Cohesion: 0.07
Nodes (86): make_resp(), mk_client(), Any, asyncio, Batch 2 MCP server unit tests — all 25 new servers mocked via httpx. Tests use…, test_airtable_create_record(), test_airtable_list_records_happy(), test_airtable_missing_api_key() (+78 more)

### Community 45 - "test_agents_extra4.py"
Cohesion: 0.03
Nodes (138): _create_agent(), _make_app(), _make_redis_mock(), Any, FastAPI, TestClient, Extra tests for /agents API — push from 59% to 85%+ coverage. Targets uncovered…, Lines 1171-1175: key_id read from request body. (+130 more)

### Community 46 - "test_governance_extra3.py"
Cohesion: 0.03
Nodes (138): _headers(), _make_app(), _make_gov_db_mock(), Any, AuditLog, FastAPI, Extra governance tests — pushes coverage from 65% to 85%+. Targets missing…, Lines 1208-1241: rollback policy to target version. (+130 more)

### Community 47 - "test_batch6_servers.py"
Cohesion: 0.03
Nodes (112): call_tool(), Any, Databox MCP server — business analytics dashboards and KPI tracking.…, call_tool(), Any, Delighted MCP server — customer satisfaction surveys and NPS tracking.…, call_tool(), Any (+104 more)

### Community 48 - "test_batch7_servers.py"
Cohesion: 0.03
Nodes (116): _base_url(), call_tool(), Any, Alchemy MCP server — blockchain data, NFTs, and Web3 development. Environment:…, call_tool(), Any, Alpaca MCP server — commission-free trading, market data, and portfolio…, call_tool() (+108 more)

### Community 49 - "ContentType"
Cohesion: 0.03
Nodes (84): ChunkingStrategySelector, Any, ChunkingStrategySelector — selects chunking strategy by content type., Return default strategy for a content type., Return collection-level override if valid, else default for content type., Return True for strategies that need special orchestrator dispatch., Select strategy and chunk text. Returns list of non-empty text chunks. Used by…, Sentence-boundary semantic chunking. (+76 more)

### Community 50 - "test_remaining_connectors.py"
Cohesion: 0.02
Nodes (93): call_tool(), _headers(), Any, Brave Search MCP server — privacy-focused web and news search. Environment:…, call_tool(), _headers(), Any, Deel MCP server — global payroll, contracts, and compliance. Environment:… (+85 more)

### Community 51 - "test_a2a_comprehensive.py"
Cohesion: 0.03
Nodes (91): A2ATaskRequest, agent_card(), _get_a2a_secret(), get_a2a_task(), _get_task(), list_a2a_tasks(), _persist_task(), Any (+83 more)

### Community 52 - "GoalStatus"
Cohesion: 0.02
Nodes (219): AgentLoop, __getattr__(), Any, Agent package - canonical LangGraph-based autonomous execution kernel., Load the graph lazily so state-only imports cannot form a cycle., Backward-compatible import for the canonical :class:`AgentGraph` runtime.…, GoalStatus, Result of executing a single planned step. (+211 more)

### Community 53 - "test_batch5_servers.py"
Cohesion: 0.06
Nodes (111): http_err(), make_resp(), mk_client(), asyncio, Unit tests for the batch-5 MCP server integrations (servers 1–25). Covers:…, test_bitly_get_click_metrics(), test_bitly_no_token(), test_bitly_shorten_url() (+103 more)

### Community 54 - "test_chunkers.py"
Cohesion: 0.04
Nodes (66): ASTChunker, Chunk, Chunk, ChunkerBase, ABC, HeadingChunker, Chunk, get_chunker_for_strategy() (+58 more)

### Community 55 - "TenantService"
Cohesion: 0.03
Nodes (103): In-memory implementation of tenant and API key management. All state is scoped…, Load tenants and API keys from PostgreSQL into memory on startup. Returns…, TenantService, Email duplicate detection is case-insensitive., TenantService.get_tenant returns tenant profile., Expired API keys are rejected by resolve_api_key., Valid non-expired API keys are resolved correctly., TenantService.list_api_keys returns all keys for the tenant. (+95 more)

### Community 56 - "create_app"
Cohesion: 0.03
Nodes (86): create_app(), configure_logging(), Configure structlog + stdlib logging once at startup., main(), _render(), asyncio, test_analytics_costs_returns_shape(), test_analytics_goals_requires_auth() (+78 more)

### Community 57 - "test_multimodal_router.py"
Cohesion: 0.03
Nodes (87): AudioParser, AudioParseResult, AudioSegment, _fmt(), Any, AudioParser — transcribes audio using OpenAI Whisper API with timestamp…, ParsedChunk, Any (+79 more)

### Community 58 - "test_extra_coverage_servers2.py"
Cohesion: 0.06
Nodes (115): make_resp(), mk_client(), Any, asyncio, Second batch of extra coverage tests to push remaining servers above 80%.…, test_affinity_create_list_entry(), test_affinity_list_list_entries(), test_azure_create_pull_request() (+107 more)

### Community 59 - "get_builtin_server_configs"
Cohesion: 0.02
Nodes (76): get_builtin_server_configs(), Return configurations for all built-in MCP server wrappers., get_builtin_server_configs() must return a config for builtin-jira with all 11…, test_get_builtin_server_configs_includes_jira(), Production-readiness tests for critical and high-severity fixes., MinIO healthcheck must use mc not curl (curl not in minio image)., AWS, Azure, K8s, Docker, Heroku, DigitalOcean servers must be registered., registry_wiring.py must not have duplicate server_id entries. (+68 more)

### Community 60 - "OrchestrationPersistence"
Cohesion: 0.06
Nodes (41): OrchestrationPersistence, Any, OrchestrationPersistence — persists all orchestration state to Postgres. Called…, Persist a regression case candidate for the eval dataset., Persist tool trust outcome to in-memory store + Postgres., Load tool trust history from Postgres into in-memory store on startup., Persist RuntimeScorecard to eval_scorecards table., ToolRanker (+33 more)

### Community 61 - "test_phases_4_5.py"
Cohesion: 0.03
Nodes (84): GeofenceRegion, GeofenceTriggerEvaluator, haversine_meters(), LatLng, point_in_polygon(), Any, Geofence trigger — detects when a device enters or exits a polygon., Ray-casting algorithm for point-in-polygon test. (+76 more)

### Community 62 - "PatternState"
Cohesion: 0.01
Nodes (128): AgentPattern, PatternState, ABC, Any, Base classes for agent pattern adapters., ConsensusPattern, Consensus verification pattern adapter., DebatePattern (+120 more)

### Community 63 - "SelfOptimizer"
Cohesion: 0.04
Nodes (102): EvalResult, EvalScorecard, Agent evaluation — 5-dimension scorecard with 70% pass threshold. Dimensions:…, OptimizationSuggestion, Any, EvalScorecard, Self-optimization — analyzes failed evaluations and produces improvement…, Generate RPA-specific suggestions based on tool failure pattern. Covers four… (+94 more)

### Community 64 - "ExecutionMemory"
Cohesion: 0.03
Nodes (75): ExecutionMemory, Any, Persist failed attempt to DB for cross-session pattern learning., Per-tenant store of past executions (successful plans and failures)., Seed in-memory _plans from DB on startup. Returns count loaded., Recall relevant execution plans from DB for a given goal. Falls back to in-…, Record to both in-memory dict and PostgreSQL. Uses ``tenant_id`` (str) directly…, _agent_source() (+67 more)

### Community 65 - "PatternConfig"
Cohesion: 0.02
Nodes (191): DynamicGraphAssembler, Translates PatternConfig into an AgentGraph instance., GoalClassifier, _phrase_in(), Any, GoalProperties, GoalClassifier — two-tier goal classification (doc-4 exact implementation).…, Word-boundary-safe phrase membership test. (+183 more)

### Community 66 - "StructuredPlan"
Cohesion: 0.03
Nodes (112): PlanValidationError, Any, ValueError, Structured execution plan — parses LLM output into topologically sortable steps., A single step in a structured execution plan., Raised before execution when a structured plan is unsafe or inconsistent., Evaluate condition field. Returns True if step should run., An ordered set of :class:`StructuredStep` objects with dependency information. (+104 more)

### Community 67 - "test_google_storage_payment_connectors.py"
Cohesion: 0.03
Nodes (68): call_tool(), _headers(), Any, Dropbox MCP server — file and folder management via Dropbox API v2. Environment…, call_tool(), _gaql_search(), _headers(), Any (+60 more)

### Community 68 - "test_database_analytics_connectors.py"
Cohesion: 0.03
Nodes (74): _auth(), call_tool(), Any, Amplitude MCP server — query events, cohorts, and user profiles. Environment:…, call_tool(), _client(), Any, AsyncClient (+66 more)

### Community 69 - "test_batch4_servers.py"
Cohesion: 0.06
Nodes (106): make_resp(), mk_client(), Any, asyncio, parametrize, Unit tests for batch-4 MCP servers (servers 1-25). Covers: Etsy, eBay, Ecwid,…, test_buffer_get_profile_analytics(), test_buffer_list_profiles() (+98 more)

### Community 70 - "EmbeddingOrchestrator"
Cohesion: 0.03
Nodes (64): DimensionPolicy, DimensionPolicy — maps model IDs to standard vector dimensions., EmbeddingModelRegistry, EmbeddingModelSpec, EmbeddingModelRegistry — catalogue of available embedding models., BatchEmbeddingResult, EmbeddingOrchestrator, EmbeddingSelectionResult (+56 more)

### Community 71 - "test_batch1_servers.py"
Cohesion: 0.06
Nodes (105): make_resp(), mk_client(), Any, asyncio, Unit tests for batch-1 MCP servers (20 new integrations). Exercises every…, Return a mock AsyncClient context manager with all HTTP methods set., test_activecampaign_add_to_list(), test_activecampaign_create_contact() (+97 more)

### Community 72 - "test_knowledge_persistence.py"
Cohesion: 0.08
Nodes (43): _app(), _AwaitedStore, _FailingStore, Any, Chunk, FastAPI, KnowledgeCollection, MonkeyPatch (+35 more)

### Community 73 - "parser_registry.py"
Cohesion: 0.03
Nodes (59): AudioTranscriptParser, _AvroBridge, CodeParser, CSVParser, DOCXParser, _ExcelBridge, HTMLParser, JSONParser (+51 more)

### Community 74 - "test_truly_live_everything.py"
Cohesion: 0.07
Nodes (54): _chunks_as_dicts(), _db_factory(), _embed(), _jira(), _provider(), Truly live end-to-end tests — ZERO mocking, ZERO stubs, ZERO FakeProvider. ✅…, Ingest 12 docs with real OpenAI embeddings → real pgvector cosine search., BM25 + trigram + vector 4-leg RRF on real Postgres. (+46 more)

### Community 75 - "_TemplateStore"
Cohesion: 0.04
Nodes (73): create_template(), delete_template(), _extract_parameters(), get_template(), _instantiate_template(), InstantiateRequest, list_templates(), _load_yaml_goal_templates() (+65 more)

### Community 76 - "runtime_profile.py"
Cohesion: 0.04
Nodes (94): CompatibilityDecision, CompatibilityEvaluator, Deterministic composition of one primary strategy and auxiliary capabilities., GoalClassifier, _phrase_in(), Any, GoalProperties, GoalClassifier — two-tier classification of incoming goals. Tier 1: Fast… (+86 more)

### Community 77 - "test_knowledge_extra4.py"
Cohesion: 0.06
Nodes (97): _create_collection(), _make_app(), _make_embed_texts_mock(), _make_embedder(), Any, FastAPI, TestClient, Extra tests for /knowledge API — push from 51% to 85%+ coverage. Targets… (+89 more)

### Community 78 - "test_batch8_servers.py"
Cohesion: 0.03
Nodes (136): call_tool(), Any, Anvil MCP server — PDF generation, form filling, and e-signature workflows.…, call_tool(), Any, Autopilot (Ortto) MCP server — marketing automation, journeys, and contact…, call_tool(), Any (+128 more)

### Community 79 - "test_session_store_comprehensive.py"
Cohesion: 0.04
Nodes (92): _safe_name(), Any, Return all active sessions for a tenant., Mark a session as closed., Lightweight session record tracked by RPASessionStore for API consumers., Redis-backed RPA session store with 24-hour TTL. Per-session key:…, Create and persist a new active RPA session., Retrieve a session by ID, scoped to the given tenant. (+84 more)

### Community 80 - "NLIChecker"
Cohesion: 0.05
Nodes (33): ClaimDecomposer, Decompose text into atomic claims and verify each against evidence., Sentence-split fallback for use without an LLM., GroundingVerdict, Structured hallucination verdict for an answer against its evidence., Verify that *answer* is grounded in *evidence_chunks*. Parameters ----------…, verify_grounding(), NLIChecker (+25 more)

### Community 81 - "asyncio"
Cohesion: 0.12
Nodes (17): asyncio, get_request() retrieves a request from Redis when not in local memory., list_pending includes both in-memory and Redis-backed requests., Recording an approval persists the updated state to Redis., create_approval_request persists in-memory and returns a fully-formed request., escalate_timeout logs + publishes org.approval.timeout + creates escalation…, escalate_timeout no-ops on already-resolved requests., The /governance/approvals endpoint signature accepts an optional org_id. (+9 more)

### Community 82 - "CredentialVault"
Cohesion: 0.02
Nodes (118): connector_secret_ref(), _connector_secret_ref_parts(), CredentialVault, _derive_fernet_key(), is_connector_secret_ref(), Any, Credential vault — AES-256-GCM encryption via Fernet. All LLM API keys and MCP…, Store a connector secret in either a tenant-aware store or mapping fallback. (+110 more)

### Community 83 - "app/main.py"
Cohesion: 0.03
Nodes (79): BehavioralRedTeamRunner, Any, Red-team runner — adversarial testing for agent safety and robustness., Submits adversarial payloads to the live agent and checks behavioral response.…, Returns True if agent correctly rejected the adversarial input., Runs adversarial test cases against the agent using real guardrail detection., Run red-team cases through real guardrail detection. Returns a report., RedTeamReport (+71 more)

### Community 84 - "RPAExecutor"
Cohesion: 0.04
Nodes (89): Any, RPA executor — executes browser automation commands via Playwright or…, Execute using a stateful Playwright session from session_manager., Executes RPA tool calls. Uses Playwright when available, falls back to…, Execute using a short-lived Playwright browser (no session manager). All 5 RPA…, Execute an RPA tool command., Simulated execution when Playwright is not available., RPAExecutor (+81 more)

### Community 85 - "test_rbac_comprehensive2.py"
Cohesion: 0.03
Nodes (95): effective_roles(), extract_roles_from_jwt(), has_any_role(), has_role(), is_ip_allowed(), load_roles_from_db(), Any, Role-Based Access Control helpers for AgentVerse. Roles (most privileged… (+87 more)

### Community 86 - "WorkflowDefinition"
Cohesion: 0.04
Nodes (84): InputDefinition, Any, model_validator, Validate step IDs unique, depends_on refs valid, no cycles., Parse a canonical YAML workflow definition., Parse from JSON string or dict (API payload)., Export canonical YAML., WorkflowDefinition (+76 more)

### Community 87 - "AgentStore"
Cohesion: 0.04
Nodes (88): AgentStore, Load active DB agents into memory on startup., Convert an Agent ORM row to a plain dict (same shape as in-memory store)., Read all agents for a tenant directly from DB; fall back to memory cache., Synchronous in-memory delete (used by tests / no-DB mode)., Per-tenant in-memory agent registry. Key: (tenant_id, agent_id) → agent record…, _make_app(), Any (+80 more)

### Community 88 - "ToolReliabilityStore"
Cohesion: 0.13
Nodes (13): Any, Per-tool reliability tracking — success rates and latency across all agent…, Track per-tool success/failure rates and latency in PostgreSQL. Table:…, Get reliability stats for a specific tool., ToolReliabilityStore, asyncio, Comprehensive tests for app/memory/tool_reliability.py — targeting 90%+…, get_unreliable_tools without DB always returns empty list. (+5 more)

### Community 89 - "client"
Cohesion: 0.04
Nodes (20): api_key(), client(), collection_id(), _new_client(), fixture, Real no-mock integration tests — AgentVerse full platform. Hits the LIVE…, Use a pre-provisioned key to avoid signup rate limits., TestAgents (+12 more)

### Community 90 - "StepDefinition"
Cohesion: 0.04
Nodes (111): ContextResolver, ContextResolverError, ValueError, ContextResolver — resolves {{...}} template expressions in workflow steps.…, Resolves {{...}} expressions against the current WorkflowState., AlertTriggerConfig, AssigneeConfig, CallbackConfig (+103 more)

### Community 91 - "SystemTemplateStore"
Cohesion: 0.04
Nodes (67): fork_template(), ForkRequest, get_template(), list_categories(), list_templates(), preview_run(), Any, BaseModel (+59 more)

### Community 92 - "reasoning_contracts.py"
Cohesion: 0.05
Nodes (72): GraphOfThoughtsRuntime, Any, Bounded Graph-of-Thoughts search with safe checkpoint metadata., LATSRuntime, Any, Bounded Language Agent Tree Search (LATS) adapter., LeastToMostRuntime, Any (+64 more)

### Community 93 - "LedgerRevision"
Cohesion: 0.09
Nodes (27): Immutable Magentic progress-ledger contracts., LedgerRevision, BaseModel, Typed immutable progress-ledger revision., InMemoryProgressLedgerRepository, PostgresProgressLedgerRepository, Any, async_sessionmaker (+19 more)

### Community 94 - "ModelOrchestratorAdapter"
Cohesion: 0.07
Nodes (27): ModelOrchestratorAdapter, Any, Adapter that wraps ModelOrchestrator to implement the model_for() interface…, Update model assignment from a GoalRuntimeProfile. Called by _node_plan when…, Return the best model for a task type, using orchestrator's tier selection., Alias for model_for() with goal context (unused in orchestrator path)., D-13: feed a live provider-call outcome into the health policy so failover…, When budget > 90%, adapter must return low-tier models. (+19 more)

### Community 95 - "test_gateway_entrypoints.py"
Cohesion: 0.06
Nodes (68): RAGCitation, Evidence cited by a grounded RAG answer., _RAGCostGuard, _BudgetContext, CitationVerification, MinimalCitationVerifier, Any, Protocol (+60 more)

### Community 96 - "test_tenants_extra4.py"
Cohesion: 0.04
Nodes (90): _generate_raw_key(), Generate a cryptographically random API key with a recognisable prefix., _make_app(), _make_db_mock(), _make_svc(), Any, Exception, FastAPI (+82 more)

### Community 97 - "test_extra_coverage_servers5.py"
Cohesion: 0.04
Nodes (87): call_tool(), _cw_client(), get_tools(), _logs_client(), Any, AWS CloudWatch MCP server — query metrics, alarms, and logs via boto3.…, call_tool(), _client() (+79 more)

### Community 98 - "test_comms_servers_dispatch.py"
Cohesion: 0.08
Nodes (92): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for communications MCP servers. Exercises every…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_brevo_create_contact(), test_brevo_delete_contact() (+84 more)

### Community 99 - "test_governor.py"
Cohesion: 0.06
Nodes (84): SpawnVerdict, _FakeSession, _make_governor(), _make_tenant_ctx(), _noop_ctx, asyncio, Tests for Governor — central authority for the civilization., When only min_viable_roster members remain, none should be retired. (+76 more)

### Community 100 - "EpisodicMemoryStore"
Cohesion: 0.05
Nodes (46): Episode, EpisodicMemoryStore, Any, EpisodicMemoryStore — DB-backed storage for past goal experiences. Episodic…, Recall similar past episodes for a given goal., Format episodes as a context block for planner prompt., A single past experience., Format for injection into planner context. (+38 more)

### Community 101 - "test_catalog_comprehensive.py"
Cohesion: 0.04
Nodes (56): A2ATask, A2ATaskResult, AgentCard, BaseModel, Agent-to-Agent (A2A) protocol types. The AgentCard is served at /.well-…, Publicly discoverable capability declaration for this agent., A task sent from one agent to another., Result returned from an A2A task execution. (+48 more)

### Community 102 - "workflow/test_registry.py"
Cohesion: 0.05
Nodes (38): Workflow Automation Engine — predefined multi-step workflow execution. This…, KeyError, Singleton registry of all available workflow step types., Register a step type. Safe to call multiple times (idempotent)., Get a step node class by type name., List all registered step types (drives Visual Builder palette)., Test helper — reset registry to empty state., StepTypeMeta (+30 more)

### Community 103 - "test_agent_identity_comprehensive.py"
Cohesion: 0.06
Nodes (61): AgentIdentityService, _build_jwks(), generate_agent_keypair(), generate_api_key(), issue_agent_token(), Any, Agent Identity Service — cryptographic service-account credentials for agents.…, Verify an agent JWT. Raises JWTError or ValueError on failure. Per Amendment… (+53 more)

### Community 104 - "test_conversational_consumer.py"
Cohesion: 0.05
Nodes (55): conversational_matches(), ConversationalTriggerConsumer, normalize_conversational_event(), publish_conversational_event(), Any, Conversational trigger consumer — Family C (2.W-1). Semantic ruling (user-…, Publish a normalized conversational event onto the EVENT bus (tenant stamped)., True if pattern is empty (no filter) or matches value; bad regex → False. (+47 more)

### Community 105 - "test_stt_engine.py"
Cohesion: 0.05
Nodes (31): Any, Normalised STT output — same shape regardless of provider., TranscriptResult, AssemblyAISTT, AssemblyAI STT — requires ASSEMBLY_AI_KEY., _decode_audio(), FasterWhisperSTT, Any (+23 more)

### Community 106 - "test_crm_servers_dispatch.py"
Cohesion: 0.08
Nodes (87): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for CRM MCP servers. Exercises every call_tool() branch by…, Return a mock httpx.Response with the given status and JSON payload., Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_affinity_create_list_entry() (+79 more)

### Community 107 - "api/governance.py"
Cohesion: 0.07
Nodes (78): approve_request(), ApproveRejectRequest, _audit(), _budget_config(), clear_emergency_stop(), _cost(), create_legal_hold(), create_notification_channel() (+70 more)

### Community 108 - "test_extra_coverage_servers3.py"
Cohesion: 0.07
Nodes (86): call_tool(), Any, make_http_error_resp(), make_resp(), mk_client(), Any, asyncio, Final targeted tests to push remaining servers above 80%. These tests… (+78 more)

### Community 109 - "Constitution"
Cohesion: 0.05
Nodes (89): evaluate_breach(), evaluate_spawn(), Constitution — pure policy evaluator. Zero I/O. Fully unit-testable., Evaluate whether a spawn request satisfies the Constitution. Returns…, Check if the civilization is in a Constitutional breach state., Governor — central authority for the civilization. The ONLY component that may…, Check Constitution breach. Called by Celery beat every 30s., BreachContext (+81 more)

### Community 110 - "SIEMConfig"
Cohesion: 0.05
Nodes (57): build_siem_adapter(), CEFAdapter, DatadogAdapter, ElasticsearchAdapter, LEEFAdapter, NullSIEMAdapter, Any, SIEM integration adapters for the AgentVerse audit system. Supported SIEM… (+49 more)

### Community 111 - "test_modular_rag.py"
Cohesion: 0.05
Nodes (75): ModularRAGRuntimeAdapter, Any, Execute a safe default or validated per-agent Modular RAG pipeline., _bounded_integer(), default_modular_pipeline(), _document_rank_key(), _freeze_mapping(), _freeze_value() (+67 more)

### Community 112 - "test_main_create_app.py"
Cohesion: 0.03
Nodes (46): fake_redis(), asyncio, fixture, Coverage tests for app/main.py — create_app() factory, _FakeRedis,…, CORS and security middleware are registered., At least 3 middleware layers registered., App has routes registered (health route is deeply nested)., Expired sorted set returns 0. (+38 more)

### Community 113 - "test_helpers.py"
Cohesion: 0.03
Nodes (98): _build_verifier_summary(), _extract_scope_value(), _extract_tool_name(), _guardrail_should_fail_closed(), _is_high_risk_step(), _is_ungrounded_status(), _parse_json(), _parse_verifier_response() (+90 more)

### Community 114 - "test_phase2_model_registry.py"
Cohesion: 0.05
Nodes (58): ModelCapability, ModelEndpoint, ModelRoutePolicy, ProviderHealth, StrEnum, AI Router data models - Model Registry primitives., A specific model endpoint with its configuration., Routing policy for a specific task type. (+50 more)

### Community 115 - "test_supervisor_debate_nodes.py"
Cohesion: 0.20
Nodes (20): DebateResult, SupervisionResult, test_supervision_result_defaults(), _graph(), Coverage-Matrix row 1 (D-1/D-2): supervisor & debate patterns on the live…, No goal_service wired → supervisor cannot recurse; node no-ops gracefully., Guard against re-running the heavy supervisor pattern on replan loops., _state() (+12 more)

### Community 116 - "WorkflowExecutor"
Cohesion: 0.06
Nodes (66): _arguments_for_step(), Any, Workflow executor: legacy sequential runner and new parallel DAG executor., Execute a single workflow step, falling back LLM → stub., Compatibility forwarding method over the canonical bounded DAG executor., Parallel workflow executor using asyncio.gather() for independent steps. The…, Execute a workflow plan with parallel waves. Returns a result dict with keys:…, _summarize_inputs() (+58 more)

### Community 117 - "test_routers.py"
Cohesion: 0.08
Nodes (44): OptimizationOutcome, BaseModel, model_validator, Immutable routing decisions and measured outcomes., RoutingCandidate, RoutingDecision, RoutingSignalSet, InMemoryDecisionStore (+36 more)

### Community 118 - "GoalPersistenceEngine"
Cohesion: 0.08
Nodes (55): AttemptRecord, GoalPersistenceEngine, PersistenceConfig, StrEnum, Agent goal persistence — keeps trying until goal is achieved or explicitly…, Manages persistent goal execution with intelligent retry strategies. Wraps an…, Count trailing consecutive failures., Configuration for the persistent retry engine. (+47 more)

### Community 119 - "api/civilization.py"
Cohesion: 0.05
Nodes (71): add_civilization_member(), AddMemberRequest, _build_orchestrator(), civilization_ws(), ConstitutionUpdateRequest, control_civilization(), ControlRequest, create_civilization() (+63 more)

### Community 120 - "HandoffRecord"
Cohesion: 0.07
Nodes (34): Durable same-civilization handoff protocol., HandoffRecord, HandoffState, HandoffTransition, BaseModel, model_validator, StrEnum, Typed handoff commands and immutable state. (+26 more)

### Community 121 - "rpa/test_artifacts_comprehensive.py"
Cohesion: 0.04
Nodes (66): ArtifactStoreProtocol, get_artifact_store(), MinIOArtifactStore, Any, Path, Protocol, Create an aioboto3 S3 client configured for MinIO., Common interface for all artifact backends. (+58 more)

### Community 122 - "FakeRedis"
Cohesion: 0.09
Nodes (18): Verify that rate-limit buckets are per-tenant and cannot bleed across., Redis keys must be prefixed per-tenant — verify raw key structure., Two tenants on the same Redis instance cannot see each other's counters., TestRateLimiterTenantIsolation, FakeRedis, Any, Tests for TenantScopedStore — Redis key prefixing ensures tenant isolation., Simulate the rate-limiter Lua script atomically. Expects: KEYS[1]=key,… (+10 more)

### Community 123 - "test_devtools_servers_dispatch.py"
Cohesion: 0.09
Nodes (82): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for dev-tools MCP servers. Exercises every call_tool()…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_azure_create_work_item(), test_azure_list_pipelines() (+74 more)

### Community 124 - "test_productivity_servers_dispatch.py"
Cohesion: 0.08
Nodes (82): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for productivity/project-management MCP servers. Targets:…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_asana_add_comment(), test_asana_create_project() (+74 more)

### Community 125 - "tenants.py"
Cohesion: 0.03
Nodes (109): create_ip_allowlist_entry(), create_key(), create_role(), CreateIPAllowlistRequest, CreateKeyRequest, CreateRoleRequest, delete_ip_allowlist_entry(), delete_role() (+101 more)

### Community 126 - "test_goal_service_distributed_strategy.py"
Cohesion: 0.07
Nodes (71): DistributedStrategyLoop, Any, Makes a DISTRIBUTED-tier ``GoalRuntimeProfile`` executable via…, AgentGraph-shaped adapter that dispatches a goal through ``StrategyRunner``.…, Bridges free-form goal text/context to the durable StrategyExecutionRequest…, Everything the wired Executor needs to actually run a strategy for one goal., Process-local registry of :class:`StrategyGoalContext` keyed by…, StrategyGoalContext (+63 more)

### Community 127 - "org/service.py"
Cohesion: 0.04
Nodes (46): Org-level analytics service — PART 22/23 of spec. Provides: -…, # TODO: integrate with cost tracking service, # TODO: integrate with model gateway telemetry, OrgDepartmentNotFoundError, OrgInvalidStatusError, OrgMissionNotFoundError, OrgNotFoundError, OrgTaskDepthExceededError (+38 more)

### Community 128 - "civilization/test_metrics.py"
Cohesion: 0.05
Nodes (72): civ_agents_active(), civ_budget_spent_usd(), civ_debates_total(), civ_learnings_promoted_total(), civ_learnings_rejected_total(), civ_spawn_denied_total(), civ_spawns_total(), _get() (+64 more)

### Community 129 - "Any"
Cohesion: 0.18
Nodes (7): Any, Resolve a single expression like 'steps.foo.output.bar'., Traverse nested dict/list by dot-separated parts., Resolve a single value. If not a string, return as-is., Recursively resolve all string values in a dict., Resolve any nested structure (dict, list, string)., Resolve a template string. If the entire string is a single {{...}} expression…

### Community 130 - "CodeInterpreter"
Cohesion: 0.06
Nodes (49): CodeInterpreter, CodeResult, get_interpreter(), Any, Sandboxed code execution via Docker. Execution constraints: - No network access…, Return True if Docker is available on this host., Execute code in a sandboxed Docker container. Falls back to restricted…, Execute code in Docker container with strict isolation. Writes code to a host… (+41 more)

### Community 131 - "api/ingestion.py"
Cohesion: 0.03
Nodes (86): create_source(), CreateSourceRequest, delete_source(), get_catalogue(), get_cost(), _get_pipeline(), get_quota(), get_source() (+78 more)

### Community 132 - "test_program09_coordination_api.py"
Cohesion: 0.07
Nodes (37): InMemoryAuctionRepository, InMemorySealedBidInbox, PostgresAuctionRepository, PostgresSealedBidInbox, Any, async_sessionmaker, AsyncSession, BaseModel (+29 more)

### Community 133 - "test_agent_graph_build_gaps.py"
Cohesion: 0.04
Nodes (78): _fake_provider(), asyncio, FakeProvider, Tests for app/agent/graph.py AgentGraph._build branches and helper methods that…, _build adds the refine branch edges when self_refine enabled., _build adds reflect node when reflection enabled., _build adds peer_review node when enable_peer_review enabled., _build adds supervisor node when _enable_supervisor set via kwargs (H8). (+70 more)

### Community 134 - "test_medium_fixes.py"
Cohesion: 0.05
Nodes (42): Sandboxed shell command execution tool. Uses Docker for isolation when…, Validate working_dir is safe. Returns normalized path or /tmp fallback., ShellResult, _validate_working_dir(), Tests for medium and low severity fixes., Celery beat must use the actual registered task name for stuck-goal detection., Mock server must auto-complete goals for SDK testing., Migration file for RLS fix must be named 0034_*.py. (+34 more)

### Community 135 - "CollaborationStore"
Cohesion: 0.06
Nodes (72): CollaborationStore, _now_iso(), _operation_to_dict(), Exception, Tenant-scoped collaboration session store., Raised when an optimistic concurrency check fails., PostgreSQL-backed collaboration store with in-memory fallback semantics in…, _session_to_dict() (+64 more)

### Community 136 - "PromptBuilder"
Cohesion: 0.08
Nodes (28): CitationManager, Any, _frame_untrusted(), PromptBuilder, PromptContextBundle, Any, PromptBuilder — builds model-specific prompts from PromptContextBundle., Build context string for the planner LLM — includes all 9 sources. (+20 more)

### Community 137 - "test_retrieval_gateway.py"
Cohesion: 0.04
Nodes (83): Create graph capabilities without exposing a store or session factory., Provider and model selected for one tenant-scoped execution., A concrete adapter and the capabilities it requires to execute., Dependencies scoped to one authenticated retrieval execution., ResolvedLLM, RetrievalExecutionContext, RetrievalStrategyCapability, TenantScopedGraphCapabilityAdapter (+75 more)

### Community 138 - "test_phases_1_4.py"
Cohesion: 0.03
Nodes (88): ChannelIngestionGateway, NLIntentClassifier, Any, Channel Ingestion Gateway — routes inbound channel events to the dispatcher., Routes channel events to matching trigger types and dispatches them., Process an inbound channel event and fire matching triggers. Returns list of…, Classify natural-language messages to trigger types using embeddings + LLM…, Return the most likely trigger_type string for the given message. (+80 more)

### Community 139 - "RPAArtifactStore"
Cohesion: 0.05
Nodes (59): Artifact storage for RPA outputs — filesystem fallback and MinIO/S3 backend., Store artifacts under /tmp for CI-safe local RPA workflows., RPAArtifactStore, RPA automation primitives and CI-safe local runner., execute_rpa_tool(), _failure(), _format_items(), LocalRPARunner (+51 more)

### Community 140 - "BrowserSessionManager"
Cohesion: 0.05
Nodes (60): BrowserSession, BrowserSessionManager, Any, Browser session manager — keeps Playwright sessions alive across multiple RPA…, Close a specific session., Close sessions idle longer than max_idle_seconds., List all active sessions, optionally filtered by tenant., Persist session metadata to Redis for visibility across restarts. (+52 more)

### Community 141 - "org/test_security.py"
Cohesion: 0.04
Nodes (43): CollectionPolicy, KnowledgeAccessPolicy, Any, PART 15 — Knowledge Access Control (KnowledgeAccessPolicy). Controls which…, Return all collection IDs accessible to a department., Map a collection to its owning department., Filter RAG search results to only permitted collections., Access policy for a single knowledge collection. (+35 more)

### Community 142 - "connectors.py"
Cohesion: 0.07
Nodes (73): _auth_config_requires_secret_storage(), _build_auth_headers(), _cleanup_oauth_states(), complete_oauth_popup(), _connector_secret_store(), _default_redirect_uri(), discover_connector_tools(), _get_builtin_handler_for_name() (+65 more)

### Community 143 - "goals.py"
Cohesion: 0.07
Nodes (75): abort_persistence(), approve_goal(), ApproveRequest, BatchGoalRequest, _build_multimodal_goal_text(), cancel_goal(), explain_goal(), _extract_multimodal_context() (+67 more)

### Community 144 - "PolicyEngine"
Cohesion: 0.05
Nodes (61): Policy, PolicyEngine, PolicyResult, Returns True if current time (in policy.timezone) is within policy's allowed…, Evaluate tool access. parent_policy_ids allows sub-agents to inherit parent…, Reload policies from DB. If tenant_id given, reload only that tenant's…, Evaluates tool calls against a set of policies. Policies are intentionally…, PolicyEngine.evaluate must skip policies from other tenants. (+53 more)

### Community 145 - "BenchmarkStore"
Cohesion: 0.05
Nodes (37): AgentBenchmark, BenchmarkRun, BenchmarkStore, Any, EvalScorecard, Agent performance benchmarking — aggregate eval trends across runs., Return comparison dict for multiple agents, sorted by avg score., Persist benchmark run to DB and update in-memory cache. (+29 more)

### Community 146 - "strategy_contracts.py"
Cohesion: 0.09
Nodes (66): ArtifactKind, ArtifactReference, CertificationEvidence, CertificationKind, CertificationStatus, CheckpointMigration, EvidenceKind, EvidenceReference (+58 more)

### Community 147 - "TriggerType"
Cohesion: 0.10
Nodes (34): _build_dispatch(), dispatch_mechanism(), DispatchMechanism, is_supported(), Single source of truth for how every ``TriggerType`` reaches the runtime…, Return the dispatch mechanism for a TriggerType (accepts the enum or its string…, True when the trigger type has a real runtime dispatch path., Trigger type definitions — 58 trigger types across 9 families. Families: A.… (+26 more)

### Community 148 - "_CollabPubSub"
Cohesion: 0.04
Nodes (37): _CollabPubSub, Increment cross-replica participant counter in Redis., Decrement cross-replica participant counter in Redis., Return participant count, preferring Redis for cross-replica accuracy., Subscribe to ``collab:*`` and forward messages to local WS connections., Redis pub/sub fanout for cross-replica WebSocket broadcast. When a message…, Lazily start the subscriber task on first WebSocket connection., Publish a message to all replicas for the given session. No-op (silently) when… (+29 more)

### Community 149 - "GuardrailsEngine"
Cohesion: 0.06
Nodes (25): GuardrailsEngine, Any, Evaluates content against guardrail rules., Attach a persistence repository. When ``auto_persist`` is set, rules added at…, Best-effort background persistence (only when an event loop runs)., Persist any rules added since the last flush. Returns the count saved., Rehydrate rules from the bound repository into memory. Returns count., Evaluate content against all active rules for the given layer. (+17 more)

### Community 150 - "test_insights_extra.py"
Cohesion: 0.04
Nodes (73): _make_app(), _make_db_factory(), _make_db_factory_from_session(), Any, FastAPI, Extra coverage tests for app/api/insights.py — targeting 85%+ coverage., estimate falls back to defaults when embedder raises exception., Graph correctly builds nodes from step_start events. (+65 more)

### Community 151 - "test_local_runner.py"
Cohesion: 0.03
Nodes (58): AlwaysHealthyCheck, AlwaysUnhealthyCheck, HealthStatus, ABC, Health-check interface for execution-environment runners. Each concrete runner…, Result of a single health-check probe., Abstract health-check interface for a runner backend., Trivially healthy check — used by the fake runner. (+50 more)

### Community 152 - "catalogue.py"
Cohesion: 0.06
Nodes (50): StrEnum, RAGAdapterConfiguration, RAGCapabilityCatalogueEntry, RAGRuntimeDependency, Authoritative runtime catalogue for the 20 canonical RAG strategies., Trusted process configuration supplied to concrete runtime adapters., One strategy's adapter, dependencies, readiness rule, and probe owner., Named runtime capabilities that can make a strategy unavailable. (+42 more)

### Community 153 - "DataClassifier"
Cohesion: 0.08
Nodes (35): DataClassifier, DataClassifier — regex-based classification. No data enters prompts until…, Redactor — removes sensitive entities from text., Redactor, DataClass, DataClassification, Data classification schema., clf() (+27 more)

### Community 154 - "test_connectors_extra2.py"
Cohesion: 0.07
Nodes (64): _make_app(), _make_db_factory(), _make_registry(), FastAPI, MonkeyPatch, TestClient, Extra coverage for /connectors API — pushes connectors.py from 77.6% → 88%+.…, _require_tenant raises HTTPException(401) when state.tenant is None (line 52). (+56 more)

### Community 155 - "test_goal_tree_comprehensive.py"
Cohesion: 0.07
Nodes (56): decompose_goal(), DecompositionResult, execute_goal_tree(), execute_sub_goal(), Any, Semaphore, Goal-tree decomposition and parallel sub-agent execution. The GoalTreeExecutor:…, Synthesize sub-goal results into a coherent final answer using LLM. Falls back… (+48 more)

### Community 156 - "ModelRouter"
Cohesion: 0.05
Nodes (57): get_router_for_tenant(), ModelRouter, ModelRouterConfig, Any, Multi-model router — selects the optimal model for each task type. Strategy: -…, Compatibility facade over the canonical orchestration classifier., Like model_for() but downgrades to a cheaper model for simple goals. For…, Return a NEW ModelRouter (copy-on-write) with all task types overridden to… (+49 more)

### Community 157 - "rag_platform.py"
Cohesion: 0.20
Nodes (29): create_raft_dataset(), evaluate_raft_job(), get_raft_job(), _job_response(), list_strategies(), preview_raft_job(), Any, BaseModel (+21 more)

### Community 158 - "Classification"
Cohesion: 0.06
Nodes (46): Classification, StrEnum, Append-only transcript compaction preserving evidence and dissent., TranscriptCompactor, Canonical append-only coordination transcript., BaseModel, model_validator, Safe, ordered transcript contracts. (+38 more)

### Community 159 - "QualityGateSystem"
Cohesion: 0.09
Nodes (29): GateOutcome, GateResult, Any, StrEnum, QualityGateSystem, QualityScore, Quality Gate System — SUPPLEMENT K (6-gate system). GATE 1: AGENT_SELF_CHECK —…, Run all configured quality gates and return composite score. (+21 more)

### Community 160 - "test_semantic_cache_world_class.py"
Cohesion: 0.07
Nodes (62): _cosine(), _find_best_match(), _LRUCache, Cosine similarity between two float vectors. Returns 0.0 for zero vectors., Find the highest-similarity entry above threshold. Returns (response,…, Per-tenant in-process LRU cache with cosine similarity. Provides sub-…, _ctx(), _mock_redis() (+54 more)

### Community 161 - "chat/router.py"
Cohesion: 0.11
Nodes (70): connect_service(), ConnectServiceRequest, create_artifact(), create_folder(), create_memory(), create_session(), create_template(), CreateArtifactRequest (+62 more)

### Community 162 - "test_bus.py"
Cohesion: 0.06
Nodes (51): CivilizationBus, _nullctx, Any, datetime, CivilizationBus — Redis pub/sub event bus with PostgreSQL persistence. Topics:…, Fetch persisted messages from DB for replay., Null async context manager to handle redis clients directly., Redis pub/sub bus for civilization events with durable persistence. (+43 more)

### Community 163 - "Marketplace"
Cohesion: 0.04
Nodes (53): DeployedTemplate, Marketplace, Any, Marketplace — agent template gallery for browse/deploy/publish. V1 (this…, Template gallery + deploy functionality., Deploy a template as a live agent for the tenant. If *registry* is provided,…, Publish a custom agent template to the marketplace., Deploy multiple templates as a group (a 'bundle'). (+45 more)

### Community 164 - "SelfOptimizerV2"
Cohesion: 0.06
Nodes (63): Production-grade self-improvement engine with Bayesian A/B testing. Fix…, Roll back to the control config from the experiment., Fix 1 (part 2): Apply suggestion to config dict. Was: called API with empty…, Fix 6: Thread-safe Thompson sampling using numpy.default_rng(). Was: global…, SelfOptimizerV2, _make_optimizer(), asyncio, Comprehensive tests for app/intelligence/self_optimizer_v2.py — the 51% gap.… (+55 more)

### Community 165 - "CapabilitySearch"
Cohesion: 0.06
Nodes (60): CapabilitySearch, Any, Semantic and keyword-based tool capability search. Falls back to keyword…, Return the top-*k* tools matching *query*. Parameters ---------- query:…, A tool that matched a capability query., Find tools matching a natural-language capability query. Usage:: search =…, Coerce a mixed list of dicts or ToolDefinition objects to dicts., Return the cosine similarity between two vectors. (+52 more)

### Community 166 - "test_condition.py"
Cohesion: 0.05
Nodes (48): CELEvaluator, CompoundTriggerEvaluator, CounterThresholdEvaluator, CEL condition evaluator and Jinja2 sandboxed template renderer., Sliding-window counter backed by a dict (production: use Redis INCR+EXPIRE).…, Record an event occurrence and return the current window count., Evaluate CEL expressions against a payload dict. Falls back to a permissive…, Return True if count in window >= threshold. (+40 more)

### Community 167 - "assert_public_url"
Cohesion: 0.04
Nodes (62): Outbound A2A call tool — lets agents call external A2A/MCP agents as tools., assert_public_url(), _is_blocked_ip(), is_public_url(), is_ssrf_blocked(), ValueError, SSRF egress guard — prevents Server-Side Request Forgery.…, Non-raising version of assert_public_url. Returns False if blocked. (+54 more)

### Community 168 - "Enum"
Cohesion: 0.08
Nodes (41): EscalationDecision, EscalationPolicy, EscalationPolicy — determines when to escalate a failed goal to human., FailureClass, FailureClassifier, FailureResult, RecoveryAction, RecoveryPolicy (+33 more)

### Community 169 - "test_org_advanced_endpoints.py"
Cohesion: 0.06
Nodes (69): anyio_backend(), client(), _fake_dept(), _fake_health(), _fake_mission(), _fake_org(), _fake_team(), _make_app() (+61 more)

### Community 170 - "test_training_export_comprehensive2.py"
Cohesion: 0.06
Nodes (66): _collect_training_examples_db(), _collect_training_examples_memory(), export_training_data(), preview_training_data(), Any, get, Request, StreamingResponse (+58 more)

### Community 171 - "test_all_models.py"
Cohesion: 0.06
Nodes (56): Agent, AgentPermission, Base, SQLAlchemy ORM models for agents and agent permissions., BlackboardEntry, BusMessage, Civilization, CivilizationAgent (+48 more)

### Community 172 - "EmailTool"
Cohesion: 0.07
Nodes (53): email_send(), EmailTool, IMAPConfig, Any, Email sending and reading tool. Sending: aiosmtplib (async SMTP) Reading:…, Read emails from IMAP inbox. Returns list of message dicts with from, subject,…, Create EmailTool from a vault/secrets config dict. Expected keys: smtp_host,…, Send an email via aiosmtplib using environment-variable SMTP config. For local… (+45 more)

### Community 173 - "test_collab_extra3.py"
Cohesion: 0.05
Nodes (61): FakeCollabStore, _make_app(), Any, FastAPI, Extra collab tests — pushes coverage from 48% to 85%+. Targets missing lines:…, Lines 398-401: presence_join broadcast runs when other WS exists in session., Lines 433-436, 438, 459-462: broadcast to other WS + presence_leave broadcast., Line 214: no API key → 401. (+53 more)

### Community 174 - "test_cloud_servers_dispatch.py"
Cohesion: 0.08
Nodes (68): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for cloud/infra MCP servers. Covers: AWS S3, IAM, Lambda,…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_calendar_create_event(), test_calendar_delete_event() (+60 more)

### Community 175 - "test_finance_servers_dispatch.py"
Cohesion: 0.10
Nodes (68): make_resp(), mk_client(), Any, asyncio, Dispatch-level tests for finance/payments/support MCP servers. Targets: stripe,…, Return a mock AsyncClient context manager. All HTTP method mocks are explicitly…, test_chargebee_create_customer(), test_chargebee_list_subscriptions() (+60 more)

### Community 176 - "test_executor_all_tools.py"
Cohesion: 0.07
Nodes (68): _make_playwright_executor(), _mock_session(), asyncio, Comprehensive coverage tests for app/rpa/executor.py. Forces…, Ephemeral sessions (no session_id given) are closed via session_manager., Provided session_id → NOT cleaned up automatically., Credential injector.resolve_arguments() is awaited when set., Credential injector exception is logged; execution continues with original args. (+60 more)

### Community 177 - "check_grounding"
Cohesion: 0.05
Nodes (35): annotate_ungrounded(), check_grounding(), Claim, deterministic_ground(), extract_claims(), extract_claims_structured(), GroundingResult, Claim Grounding Checker ======================= After each executor step,… (+27 more)

### Community 178 - "civilization/test_events.py"
Cohesion: 0.06
Nodes (51): CivEventType, emit_event(), get_events_since(), Any, datetime, Civilization event types and dispatch helpers., Emit a civilization event to DB and Redis SSE channel., Fetch events from the durable log for SSE reconnect catch-up. (+43 more)

### Community 179 - "CostTracker"
Cohesion: 0.08
Nodes (53): BudgetLimits, CostTracker, Any, Central cost-tracking service wired with Redis + DB. Redis keys ----------…, Load budget limits from DB; return defaults if unavailable., Pure READ operation — never modifies Redis counters. This is the safe method to…, Record a real LLM call's token usage and compute/persist the cost. Returns the…, Update EWMA state and return a CostAnomaly if a spike is detected. (+45 more)

### Community 180 - "test_extra_coverage_servers4.py"
Cohesion: 0.06
Nodes (69): call_tool(), _client(), get_tools(), Any, AWS S3 MCP server — manage S3 buckets and objects via boto3. Environment…, call_tool(), _headers(), Any (+61 more)

### Community 181 - "test_executor_standalone_paths.py"
Cohesion: 0.09
Nodes (67): _build_executor(), _inject(), _make_download_cm(), _make_locator(), _make_page(), _make_pw_stack(), asyncio, Standalone Playwright path coverage for app/rpa/executor.py lines 396-652.… (+59 more)

### Community 182 - "SemanticCache"
Cohesion: 0.06
Nodes (26): Any, World-class semantic cache with true cosine-similarity matching. Layer 1 (L1):…, Store a step execution result in L1 + L2. Silently ignores all storage errors —…, Backward-compatible wrapper for old hash-based API., Backward-compatible sync clear. Clears L1 and resets stats (no Redis flush)., Pre-populate the cache with known step→response patterns. Two calling…, Return rich stats including hit rate, bytes saved, and L1/L2 breakdown. Tracks…, Clear ALL cache entries for a tenant: L1 in-process AND Redis. Returns the… (+18 more)

### Community 183 - "test_learning.py"
Cohesion: 0.06
Nodes (57): _FakeScoringState, Minimal state stub for EvalRunner.score_and_persist when no real state…, _FakeSession, _make_pipeline(), _noop_ctx, asyncio, Tests for LearningPipeline — curated collective learning (Phase D)., High-scoring validated candidates must be promoted to LTM. (+49 more)

### Community 184 - "ModelGateway"
Cohesion: 0.05
Nodes (42): DecisionIntelligence, DecisionRecord, get_decision_intelligence(), Any, SUPP-J Decision Intelligence + SUPP-L Versioning Strategy. Decision…, Aggregate quality metrics for an org's decisions., Recommend which LLM should reason about a decision of this type. Delegates to…, A point-in-time snapshot of a versioned entity. (+34 more)

### Community 185 - "LLMConfigStore"
Cohesion: 0.06
Nodes (31): get_llm_config_store(), LLMConfigStore, Any, Redis-backed LLM configuration store. Stores per-tenant LLM provider…, Reads and writes per-tenant LLM provider config to/from Redis. Args:…, Store the LLM config for *tenant_id* in Redis., Return the LLM config for *tenant_id*, or *None* if not configured., Remove the LLM config for *tenant_id* from Redis. (+23 more)

### Community 186 - "agents.py"
Cohesion: 0.09
Nodes (62): _agent_store(), assign_knowledge_collection(), check_readiness(), check_rollout_gate(), clone_agent(), CloneAgentRequest, _connector_id_from_value(), _connector_lookup_key() (+54 more)

### Community 187 - "CostOptimizer"
Cohesion: 0.05
Nodes (30): CostOptimizer, DowngradeSuggestion, ModelStats, Any, CostOptimizer — tracks LLM cost per goal type and suggests model downgrades.…, Record a goal run's cost and quality metrics., Return downgrade suggestions for categories with sufficient data., Return the cost-optimised model for a goal category, if any. (+22 more)

### Community 188 - "MemoryConsolidator"
Cohesion: 0.08
Nodes (22): MemoryConsolidator, Any, datetime, Memory 2.0 consolidation — lifecycle management over a dict-backed store.…, Consolidate a tenant's memories: age-based lifecycle + canonical dedup., Run consolidation for a tenant's memories. 1. Mark active entries older than…, consolidator_run(), _now() (+14 more)

### Community 189 - "test_middleware_comprehensive.py"
Cohesion: 0.09
Nodes (44): _auth_error_response(), _extract_key(), _is_cors_preflight(), JSONResponse, Request, _rate_limit_response(), Attempt to resolve a Keycloak JWT Bearer token to a TenantContext. Returns None…, _try_resolve_sso() (+36 more)

### Community 190 - "TriggerConsumerSupervisor"
Cohesion: 0.04
Nodes (36): HITLTriggerConsumer, Any, HITL (Human-in-the-Loop) trigger consumer., Subscribe to HITL approval/rejection events and dispatch matching triggers., MemoryTriggerConsumer, Any, Memory creation trigger consumer., Subscribe to memory creation events and dispatch matching triggers. (+28 more)

### Community 191 - "asyncio"
Cohesion: 0.05
Nodes (33): _make_cron_spec(), asyncio, Extra coverage for app/triggers/store.py. Targets uncovered lines: 69-74,…, Lines 121-123: exception + strict=True → re-raised., Lines 117-120: exception + strict=False → logs, no raise., Lines 136-138: exception + strict=True → re-raised., Lines 133-135: exception + strict=False → no raise., Lines 161-176: create() fires DB create task when loop running. (+25 more)

### Community 192 - "_deps.py"
Cohesion: 0.07
Nodes (63): get_agent_store(), get_auction_bid_inbox(), get_auction_repository(), get_audit_log(), get_budget_config(), get_cache_stats(), get_camel_repository(), get_collab_store() (+55 more)

### Community 193 - "build_envelope"
Cohesion: 0.08
Nodes (50): build_envelope(), Any, Return True if the envelope signature is valid AND not expired. Args: envelope:…, Construct, sign, and return a complete :class:`ExecutionEnvelope`., verify_envelope(), evaluate_policy(), PolicyDecision, Execution-environment policy evaluation. Determines whether an… (+42 more)

### Community 194 - "MCPServerConfig"
Cohesion: 0.04
Nodes (115): MCPRegistry, MCPServerConfig, BaseModel, model_validator, Register a new MCP server and return its ID. If config.server_id is already set…, Fetch a server config by ID; returns None if not found or cross-tenant. Re-…, Return all servers registered for this tenant., Return all servers with their registry IDs for this tenant. (+107 more)

### Community 195 - "test_guardrails_comprehensive.py"
Cohesion: 0.06
Nodes (52): ActionSafetyLevel, ActionSafetyProfile, ActionSafetyProfileSelector, Any, ActionSafetyProfile — per-action risk assessment (spec §Layer 1). Determines…, Per-action safety determination., Selects ActionSafetyProfile based on tool name, args, and risk level., IdentityProfile (+44 more)

### Community 196 - "RerankPolicy"
Cohesion: 0.04
Nodes (80): Any, Citation, CitationManager — threads source citations through retrieved context., ContextPipeline, PipelineResult, Any, ContextPipeline — orchestrates the 7-step context processing pipeline. Spec…, Any (+72 more)

### Community 197 - "system_session"
Cohesion: 0.05
Nodes (49): AsyncSession, Set session for system-level maintenance, bypassing tenant RLS. Issues ``SET…, system_session(), _update_goal_dlq(), asyncio, Tests for system_session RLS bypass in app/db/rls.py., system_session must be importable from app.db.rls., system_session must issue SET LOCAL row_security = off (or equivalent). (+41 more)

### Community 198 - "test_security_org.py"
Cohesion: 0.04
Nodes (31): assert_permission(), has_permission(), OrgRole, StrEnum, Raise PermissionError if role doesn't have permission., Check if an org role grants a specific permission., OrgLearningSystem, OrgLesson (+23 more)

### Community 199 - "test_raft_lifecycle.py"
Cohesion: 0.09
Nodes (42): RAFTRAGRuntimeAdapter, Retrieve only after a compatible completed RAFT model is durable., _compatibility_key(), InMemoryRAFTRepository, PersistedRAFTChunk, RAFTDatasetConfig, RAFTService, Deterministic test repository with the same tenant boundaries as SQL storage. (+34 more)

### Community 200 - "get_session_factory"
Cohesion: 0.09
Nodes (24): get_db(), get_db_session(), get_session_factory(), _make_engine(), AsyncSession, Async SQLAlchemy session factory and FastAPI dependency., Context-manager that yields an AsyncSession and commits/rolls back., FastAPI dependency — yields one session per request. (+16 more)

### Community 201 - "ExecutionTier"
Cohesion: 0.03
Nodes (119): AutoGPTAdapter, AutoGPTRuntime, AutoGPTState, Any, BaseModel, Bounded AutoGPT controller with mandatory pre-execution gates., BabyAGIAdapter, BabyAGIRuntime (+111 more)

### Community 202 - "RuntimeFlags"
Cohesion: 0.09
Nodes (24): _bool_env(), _env_bool(), _env_set(), Runtime feature flags loaded from environment variables. All new orchestration…, RuntimeFlags, Any, datetime, RolloutController (+16 more)

### Community 203 - "CodeExecutionWorkload"
Cohesion: 0.07
Nodes (39): CodeWorkloadLimits, CodeWorkloadValidator, Control-plane validation for bounded Python code workloads., _emit(), main(), _matches_schema(), Any, Dedicated minimal worker for validated code-interpreter workloads. (+31 more)

### Community 204 - "test_phase3_retrieval.py"
Cohesion: 0.07
Nodes (25): Any, QueryExpander, Generate multiple query phrasings for Fusion RAG (RRF across multiple queries)., LLM-driven query expansion for Fusion RAG. Falls back to rule-based., Any, QueryReformulation, QueryReformulator, QueryReformulator — generates alternative query phrasings on empty results. (+17 more)

### Community 205 - "app/tools/__init__.py"
Cohesion: 0.17
Nodes (10): Native tool implementations for AgentVerse. These tools are built-in and do not…, Any, AsyncBaseTransport, Web search tool using SearXNG (self-hosted open-source search aggregator).…, DuckDuckGo Instant Answer API — no key required., Web search via SearXNG (self-hosted) or DuckDuckGo fallback. SearXNG:…, Search the web. Returns up to num_results results., SearchResult (+2 more)

### Community 206 - "coordination/store.py"
Cohesion: 0.05
Nodes (38): AuthorizationContext, CoordinationCommandStore, CoordinationService, Any, BaseModel, Protocol, Authorized command boundary for canonical coordination state., Validate authority and immutable admission inputs before persistence. (+30 more)

### Community 207 - "test_governance_comprehensive2.py"
Cohesion: 0.11
Nodes (32): _make_app(), Any, AuditLog, FastAPI, Extended governance tests — covers endpoints not tested in…, Test simulate returns dict with simulation_results., test_approve_and_reject_flow(), test_approve_unknown_request_returns_404() (+24 more)

### Community 208 - "test_oauth_flow.py"
Cohesion: 0.19
Nodes (14): _make_app(), Tests for real OAuth callback implementation., A matching pending flow returns connected with token metadata., OAuth start returns state and code_challenge embedded in auth_url., Connectors that are not OAuth type return 400., Missing/empty state param returns pending_config (no token_url configured)., When server config has no token_url, endpoint returns pending_config., A state that doesn't match any pending flow returns error. (+6 more)

### Community 209 - "NotificationService"
Cohesion: 0.06
Nodes (31): NotificationChannel, NotificationService, Any, Notification service — sends alerts when HITL approval is required. Supports…, Remove a channel from the DB (fire-and-forget)., Send notification to all tenant channels., G-12: Notify when an approval request has timed out. Called from…, Notify when a goal reaches a terminal state. (+23 more)

### Community 210 - "test_nl_scheduler_comprehensive.py"
Cohesion: 0.10
Nodes (29): NLScheduler, _parse_single(), Converts NL trigger descriptions to TriggerSpecs. Primary path: LLM provider…, Comprehensive tests for app/triggers/nl_scheduler.py — targets the 48% baseline., Invalid JSON response should fall back to a ONCE TriggerSpec., LLM sometimes wraps JSON in markdown code blocks., Empty schedules array should produce empty list., Provider.complete should be called exactly once per parse(). (+21 more)

### Community 211 - "rag/raft.py"
Cohesion: 0.10
Nodes (25): Base, RAFTConfirmationGrant, RAFTDataset, RAFTFineTuneJob, Durable tenant-scoped records for the RAFT lifecycle., _confirmation_binding_digest(), _ConfirmationGrant, ConfirmationRequiredError (+17 more)

### Community 212 - "WebhookDeliverySystem"
Cohesion: 0.06
Nodes (21): Any, QA7 — Webhook Delivery Guarantees (at-least-once delivery). Guarantees: - At-…, Register a new outbound webhook., Queue a webhook delivery. Returns a WebhookDelivery that will be retried until…, Attempt one delivery. Returns True on success., A registered outbound webhook., Per spec QA7 — a single webhook delivery attempt., Compute HMAC-SHA256 signature for payload. (+13 more)

### Community 213 - "registry_wiring.py"
Cohesion: 0.04
Nodes (57): Any, Register a handler callable for a built-in server. Process-local only., Return the process-local built-in handler for server_id, or None., call_tool(), Any, Emarsys MCP server — marketing platform with contacts, campaigns, and…, _wsse_header(), _absolute_http_url() (+49 more)

### Community 214 - "goal_service.py"
Cohesion: 0.02
Nodes (120): check_tool_output_for_injection(), Scan tool output for indirect prompt injection attempts. Returns a warning…, GroundingChecker, Any, Checks whether step output claims are grounded in tool outputs. Two-pass:…, ExecutorMixin, Any, Execute a step using the world-class semantic cache. True cosine-similarity… (+112 more)

### Community 215 - "RuntimeSSEEmitter"
Cohesion: 0.06
Nodes (45): Any, RuntimeSSEEmitter — creates structured SSE events for all orchestration…, RuntimeSSEEmitter, SSEEventType, Phase N5-N7: ABTesting wiring + SSE events + action dispatch., BLACKLIST_TOOL_PATTERN must record failed tools in ToolReliabilityStore., Module-level ab_testing_engine must be wired with db_factory in main.py., test_ab_testing_engine_has_record_result_async() (+37 more)

### Community 216 - "test_ingestion_time_strategies.py"
Cohesion: 0.07
Nodes (42): build_parent_windows(), ParentWindow, Token-aware text chunking using tiktoken. Produces chunks with a guaranteed…, A stable sentence/chunk window independent of embedding dimensions., Build one citation window around each source chunk., expand_agentic_parent_results(), ParentWindowCitation, Expand propositions, preserve provenance, and dedupe before final top-k. (+34 more)

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
Cohesion: 0.06
Nodes (37): Automated security review pipeline for marketplace templates. Checks: 1. Scope…, TemplateSecurityReviewer, Normal connectors (gmail, document_reader) must not fail the reviewer., Templates with critical OAuth scopes are still flagged., Unknown connectors produce a low-severity finding but don't auto-reject., TestSecurityReviewerFix, Comprehensive tests for app/enterprise/marketplace_v2.py. Covers the 45% gap…, Force the fallback simple-pattern path by making the injection guard… (+29 more)

### Community 221 - "RegressionGate"
Cohesion: 0.09
Nodes (35): AggregateMetrics, BaselineKey, BaselineRepository, Immutable, version-qualified regression baselines., Small append-only repository; PostgreSQL adapters use the same contract., RegressionBaseline, PromotionDecision, Any (+27 more)

### Community 222 - "MemoryConsolidator"
Cohesion: 0.11
Nodes (15): ConsolidationResult, _jaccard(), _keywords(), MemoryConsolidator, Any, Memory consolidation — compress large episodic memory sets into concise…, Group memories by keyword (jaccard) similarity. Public entry point for callers…, Greedy keyword-similarity clustering. (+7 more)

### Community 223 - "FileOps"
Cohesion: 0.05
Nodes (28): FileOps, Path, Check if a path exists in the tenant workspace., File operations scoped to a tenant's isolated workspace directory., Resolve path and verify it stays within the tenant workspace. Raises…, Read text content from a file in the tenant workspace., Write text content to a file in the tenant workspace. Creates parent…, List files and directories in the tenant workspace path. (+20 more)

### Community 224 - "WorkflowService"
Cohesion: 0.05
Nodes (37): Any, WorkflowService — database-backed CRUD service for the workflow engine router.…, Publish a draft workflow (status draft → published)., Unpublish a workflow (status published → draft)., Return the real published version history from…, Restore a previous version: load its stored definition and write it back onto…, Return workflow templates from SystemTemplateStore if wired., (success_rate, error_rate) from a run-stats dict, div-by-zero safe. (+29 more)

### Community 225 - "extract_tool_call"
Cohesion: 0.07
Nodes (53): _canonical_tool_name(), extract_tool_call(), _jql_from_goal_or_step(), _looks_like_placeholder_jql(), _named_assignee_from_text(), Structured tool-call parsing for executor output., Attempt to parse JSON with several repair strategies., Look up a Jira user account ID by display name (async, non-blocking). Calls GET… (+45 more)

### Community 226 - "test_state_machine.py"
Cohesion: 0.07
Nodes (41): create_instance(), create_state_machine(), CreateStateMachineRequest, delete_state_machine(), get_instance(), _get_registry(), get_state_machine(), list_state_machines() (+33 more)

### Community 227 - "test_phase8_9_guardrails_trust.py"
Cohesion: 0.06
Nodes (45): GuardrailRule, A single guardrail rule., _to_row(), _make_app(), asyncio, Phase 8+9: Guardrails 2.0 + Trust & Governance 2.0 tests., test_approval_not_found(), test_audit_export_returns_json_package() (+37 more)

### Community 228 - "_FakeRedis"
Cohesion: 0.05
Nodes (38): _FakeLuaScript, _FakeRedis, Any, Thread/async-safe dict-backed Redis stub — for tests and no-pool mode. Supports…, Return the asyncio.Lock, creating it lazily on first use., Set expiry as an absolute Unix timestamp., Increment a float counter and return the new value., Return a fake Lua script executor that simulates the atomic check-and-increment. (+30 more)

### Community 229 - "test_extra_coverage_servers6.py"
Cohesion: 0.06
Nodes (55): call_tool(), _google_token(), Any, Google Cloud Storage MCP server — bucket and object management via GCS JSON…, call_tool(), _headers(), Any, TikTok MCP server — TikTok for Business API integration. Environment:… (+47 more)

### Community 230 - "test_data_servers_dispatch.py"
Cohesion: 0.07
Nodes (61): call_tool(), get_tools(), Any, Redis MCP server — interact with an external Redis instance as a data store.…, _make_async_generator(), make_resp(), mk_client(), Any (+53 more)

### Community 231 - "routers.py"
Cohesion: 0.05
Nodes (48): get_agent_card(), list_public_agents(), get, Request, A2A Agent Directory — per-agent AgentCards and queryable directory., Return AgentCard for a specific agent (A2A protocol)., List public agents in the A2A directory., get_auction_state() (+40 more)

### Community 232 - "test_security_audit_findings.py"
Cohesion: 0.04
Nodes (57): GoalRequest, Test multi-modal goal submission with image attachments., test_goal_request_accepts_image_url(), test_goal_request_defaults_none(), client(), fixture, Security audit regression tests. All tests in this file correspond to findings…, POST /rpa/execute must also require auth (control: already guarded). (+49 more)

### Community 233 - "test_scope_enforcement_comprehensive.py"
Cohesion: 0.06
Nodes (40): BaseHTTPMiddleware, Request, Enforces API key scopes and IP allowlist on every non-exempt request. Redis is…, Extract the real client IP, respecting trusted proxy headers. Delegates to the…, Return the scope required for (method, path), or None if unregistered., Load effective scopes for a key from DB + role-based fallback. Resolution…, ScopeEnforcementMiddleware, _make_test_app() (+32 more)

### Community 234 - "test_blackboard.py"
Cohesion: 0.06
Nodes (55): Blackboard, BlackboardConflictError, Any, Exception, Blackboard — tenant-scoped shared findings store. Agents post findings with…, Update an existing entry with optimistic concurrency check., Query the blackboard for relevant findings., Raised when an update fails due to version conflict. (+47 more)

### Community 235 - "AgentCollabSession"
Cohesion: 0.07
Nodes (49): AgentCollabSession, CollabRound, ConsensusResult, Any, Agent collaboration protocol — multi-round propose/critique/counter/agree loop.…, Persist a completed debate session and its proposals to PostgreSQL. Parameters…, Tracks a multi-agent collaboration session., Return ConsensusResult based on whether all recent rounds agree. (+41 more)

### Community 236 - "workflow_nodes.py"
Cohesion: 0.08
Nodes (24): execute_decision_node(), execute_delay_node(), execute_loop_node(), execute_rag_node(), execute_skill_node(), Any, Workflow Node Executors ======================= Real execution semantics for…, Execute a loop node. Returns list of per-iteration outputs. Node config:… (+16 more)

### Community 237 - "voice/router.py"
Cohesion: 0.11
Nodes (39): Voice OS module — Native STT/TTS/Streaming for AgentVerse. Providers: STT:…, _cache_persona(), _delete_persona(), _fallback_health(), _fetch_org_health(), _fetch_wywa(), _get_persona(), Any (+31 more)

### Community 238 - "NLTriggerResolver"
Cohesion: 0.08
Nodes (49): TriggerDefinition, NLTriggerParseError, NLTriggerResolver, Any, ValueError, NLTriggerResolver — maps a natural-language description to a TriggerDefinition.…, Resolves natural-language trigger descriptions to TriggerDefinition., Parse *description* and return a TriggerDefinition. (+41 more)

### Community 239 - "workflow/router.py"
Cohesion: 0.08
Nodes (66): add_permission(), analytics_summary(), create_workflow(), delete_workflow(), _get_nl_resolver(), get_permissions(), _get_runner(), get_template() (+58 more)

### Community 240 - "test_keycloak_comprehensive.py"
Cohesion: 0.06
Nodes (77): exchange_token(), get_sso_config(), Any, get, RedirectResponse, SSO authentication endpoints for Keycloak integration. Provides: - GET…, Redirect to Keycloak login page., Exchange authorization code for access + refresh tokens. (+69 more)

### Community 241 - "InMemoryCancellationRepository"
Cohesion: 0.08
Nodes (26): CancellationCoordinator, CancellationRepository, CancellationResult, IncompatibleCheckpointError, InMemoryCancellationRepository, PostgresCancellationRepository, async_sessionmaker, AsyncSession (+18 more)

### Community 242 - "policies.py"
Cohesion: 0.04
Nodes (55): evaluate_with_domain_failsafe(), GovernancePolicy, PolicyVersionManager, Any, Policy engine — evaluates tool calls against named policies. Policies layer on…, Publish a policy change event so other replicas can reload. Channel:…, Start the policy change subscriber as a background task. Call this from main.py…, Return REQUIRE_APPROVAL when no policy matches but domain is regulated. This… (+47 more)

### Community 243 - "test_tool_cache.py"
Cohesion: 0.07
Nodes (41): classify_tool(), Any, Tool Result Cache ================= Caches MCP tool call responses with smart…, Per-tenant MCP tool result cache backed by Redis. Plugs into…, Return cached tool result or None., Cache a tool result. Silently ignores errors., Return ANY cached result regardless of expiry, up to max_age_seconds old. Used…, Store both normal-TTL and long-lived stale backup. (+33 more)

### Community 244 - "test_tracing_comprehensive.py"
Cohesion: 0.06
Nodes (48): _add_console_span_processor(), configure_tracing(), get_recent_spans(), get_tracer(), _NoOpSpanContext, _NoOpTracer, Any, Exception (+40 more)

### Community 245 - "MetaOrchestrator"
Cohesion: 0.06
Nodes (40): get_model_profile_for_dept(), Return the preferred model profile name for a department kind., _compute_approval_gates(), ExecutionPhase, GoalAnalysis, GoalAnalyzer, _group_depts_into_phases(), MetaOrchestrator (+32 more)

### Community 246 - "test_new_tools.py"
Cohesion: 0.06
Nodes (45): DocumentParserTool, ParsedDocument, Document parsing tools for PDF, CSV, DOCX, and plain text files. All parsing is…, Parse documents from various formats into text. Supports: PDF, CSV, DOCX, TXT,…, HttpRequestTool, Any, Make HTTP requests to external APIs. Security: blocks requests to localhost,…, Execute shell commands in a sandboxed environment. Requires:… (+37 more)

### Community 247 - "ComplianceController"
Cohesion: 0.02
Nodes (152): ComplianceController, DataExportRequest, Any, GDPR/SOC2/PCI-DSS compliance controls. Provides: - GDPR right-to-erasure:…, GDPR right-of-access — collect and return all tenant data., GDPR right-to-erasure. Records intent and schedules DB deletion in 30 days., Sweep and mark records older than retention_days for deletion., Return the raw export payload dict for a ready export request. (+144 more)

### Community 248 - "test_org_router.py"
Cohesion: 0.06
Nodes (32): client(), _fake_dept(), _fake_mission(), _fake_org(), mock_service(), Any, AsyncClient, FastAPI (+24 more)

### Community 249 - "IntentRouter"
Cohesion: 0.08
Nodes (42): ClarifyRequest, Intent, IntentRouter, Intent router — classifies chat messages into QA / GOAL / CLARIFY / SCHEDULE.…, Classify a chat message into Intent.QA / GOAL / CLARIFY / SCHEDULE. Rules (in…, Return the intent for *message*., Return a clarifying question based on what's missing in *message*., Parse a natural-language schedule expression and return a confirmation. (+34 more)

### Community 250 - "core/errors.py"
Cohesion: 0.10
Nodes (25): AuthenticationError, AuthorizationError, BudgetExceededError, CircuitOpenError, ExternalServiceError, InternalError, _is_retryable(), PlatformError (+17 more)

### Community 251 - "org/events.py"
Cohesion: 0.04
Nodes (43): NotificationRoute, NotificationSeverity, OutboundNotification, OutboundNotificationRouter, Any, StrEnum, Outbound notification router — QA6 of spec. Routes org events outbound to the…, Routes org events outbound to the right channel(s). Respects quiet hours and… (+35 more)

### Community 252 - "ImageAttachment"
Cohesion: 0.09
Nodes (21): Perception module — multimodal inputs, browser automation, page analysis., ImageAttachment, PerceptionInput, Multimodal perception — handles image + text inputs for goals. Allows goals to…, Represents a goal with optional visual context., Format for injection into planner system prompt., Parse a data URI like 'data:image/png;base64,...'., Approximate byte size of the decoded image. (+13 more)

### Community 253 - "get_inverse_fn"
Cohesion: 0.13
Nodes (18): Execute all inverse operations in LIFO order, awaiting each one. Two modes:…, get_inverse_fn(), Tool inverse registry — maps tool names to their async undo functions. Each…, Return a callable that undoes the named tool call. Two modes depending on…, register_inverse(), Test the new-style 2-arg async inverse function path., Exercises the asyncio.run() branch (no running loop)., Exception in inverse is swallowed by the wrapper. (+10 more)

### Community 254 - "test_goals_comprehensive.py"
Cohesion: 0.08
Nodes (55): NotFoundError, _make_app(), _make_goal(), Any, FastAPI, Comprehensive tests for /goals endpoints — targets 28% → 60%+ coverage., test_abort_persistence_no_redis(), test_abort_persistence_with_redis() (+47 more)

### Community 255 - "test_main_extra2.py"
Cohesion: 0.04
Nodes (22): Extra coverage tests for app/cli/main.py — targeting 85%+ overall coverage., When API returns a dict (not list), treat as empty., When average_score is not in response, compute from scores., When events endpoint returns a dict (not list), shows nothing., Test _stream_goal processes various SSE event types., Test _stream_goal handles goal_failed events., KeyboardInterrupt during streaming is handled gracefully., Non-JSON data lines are silently skipped. (+14 more)

### Community 256 - "test_perception_gaps.py"
Cohesion: 0.05
Nodes (54): asyncio, Coverage gaps for app/perception/browser_agent.py and…, take_screenshot returns success result when playwright is available., take_screenshot returns failure on navigation exception., extract_text returns page text when playwright is available., BrowserAgent.available returns False when playwright is not importable., extract_text returns failure on exception., click_and_screenshot returns success result when playwright is available. (+46 more)

### Community 257 - "a2a_security.py"
Cohesion: 0.08
Nodes (34): A2AKey, A2AKeyMetadata, A2AKeyProvider, A2AKeyPurpose, A2ANonceStore, A2ASecretResolver, A2ASecurityError, A2ASecurityService (+26 more)

### Community 258 - "test_society.py"
Cohesion: 0.10
Nodes (45): Society — civilization membership, reputation tracking, goal routing.…, _FakeSession, _make_society(), _noop_ctx, asyncio, Tests for Society — civilization membership, reputation EWMA, routing., Reputation input clamping: scores > 1.0 treated as 1.0, < 0.0 as 0.0., get_member returns from in-memory cache without DB hit. (+37 more)

### Community 259 - "GoalCostBreakdown"
Cohesion: 0.07
Nodes (41): get_goal_cost_metrics(), get, Request, Cost breakdown API endpoint — per-role token/cost attribution per goal. Exposes…, Return per-role (planner/executor/verifier) token and cost breakdown for a goal., _backend_load(), configure_persistence(), finalize_breakdown() (+33 more)

### Community 260 - "asyncio"
Cohesion: 0.05
Nodes (28): asyncio, Lines 184: guard raises → exception caught, no finding., Lines 1261-1263: slug lookup in memory cache., Returns None when slug not found in memory., Returns None when neither id nor slug given., Line 1334: category filter in-memory., search filter in-memory., domain filter in-memory. (+20 more)

### Community 261 - "tasks.py"
Cohesion: 0.04
Nodes (78): AuditFlusher, Compat shim: v3 has no WAL to flush — records are written directly., Long-running no-op so the background task doesn't crash., No-op — v3 does not buffer in Redis WAL., beat_task_guard(), Beat task overlap guards using Redis SETNX. Wrap sensitive Celery beat tasks…, Decorator that prevents a beat task from overlapping with another instance.…, _build_worker_goal_service() (+70 more)

### Community 262 - "test_sanitization_comprehensive.py"
Cohesion: 0.07
Nodes (50): Any, Protocol, Shared event sanitization helpers for agent and workflow events., Return *value* as text with common credentials redacted., redact_sensitive_text(), ResultProcessor, sanitize_event(), sanitize_event_value() (+42 more)

### Community 263 - "test_auction_swarm_runtime.py"
Cohesion: 0.08
Nodes (42): Any, Allocation, AuctionAllocator, Any, BaseModel, datetime, Decimal, RuntimeError (+34 more)

### Community 264 - "test_celery_maintenance_real.py"
Cohesion: 0.03
Nodes (95): _build_worker_graph_capability(), create_guardrail_partitions(), _datetime_to_naive_iso(), _db_schedule_payload(), _dispatch_due_schedule(), expire_stale_documents(), Mark knowledge chunks past their freshness TTL as needing reindex., Create next 3 months of monthly partitions for guardrail_events. (+87 more)

### Community 265 - "integrations.py"
Cohesion: 0.04
Nodes (60): AlertmanagerPayload, confluence_page_webhook(), DatadogWebhookPayload, _get_zapier_tenant_id(), github_push_webhook(), notion_page_webhook(), Any, BaseModel (+52 more)

### Community 266 - "api/mfa.py"
Cohesion: 0.08
Nodes (45): begin_enrollment(), _check_rate_limit(), _check_rate_limit_global(), _check_totp_replay(), _cleanup_mfa_sessions(), complete_enrollment(), decrypt_secret(), encrypt_secret() (+37 more)

### Community 267 - "AuctionAnnouncement"
Cohesion: 0.09
Nodes (32): AuctionAnnouncement, BidPayload, BaseModel, model_validator, RankedBid, Auction contracts using fixed-point score inputs., RevealedBid, ScoreWeights (+24 more)

### Community 268 - "TeamFormationEngine"
Cohesion: 0.07
Nodes (33): _default_hours(), _estimate_duration(), _estimate_success_probability(), _priority_for_overlap(), Any, TeamFormationEngine — assembles an optimal team manifest from a mission goal.…, Assembles a TeamManifest from a mission goal., Convenience entry-point: form a team directly from a goal string. (+25 more)

### Community 269 - "WorkflowState"
Cohesion: 0.09
Nodes (30): AutoAuditMiddleware, _hash(), Any, AutoAuditMiddleware — automatically emits AuditEvents for every step…, SHA-256 hash of a JSON-serialised object (PII-safe audit)., Wraps every step node execution with automatic AuditLog writes., TypedDict, WorkflowState (+22 more)

### Community 270 - "test_openapi_importer_comprehensive.py"
Cohesion: 0.07
Nodes (49): extract_tools_from_spec(), import_and_register(), parse_openapi_spec(), persist_tools(), Any, OpenAPI 3.x spec importer — creates MCP connector registrations + tool…, Convert HTTP method + path to a valid snake_case tool name., Persist tool definitions to tool_capabilities table. Returns count of tools… (+41 more)

### Community 271 - "BrowserAgent"
Cohesion: 0.06
Nodes (51): BrowserAction, BrowserAgent, Any, Browser agent — headless Chromium automation via Playwright. Provides web…, Extract visible text from a URL., Navigate to URL, click element, return screenshot., Fill a form field and optionally submit., Dispatch a browser action. (+43 more)

### Community 272 - "test_voice_e2e.py"
Cohesion: 0.04
Nodes (67): Protocol, Abstract STT and TTS provider protocols. Every concrete provider MUST satisfy…, Speech-to-Text provider contract., Text-to-Speech provider contract., STTProvider, TTSProvider, get_capabilities(), get_stt() (+59 more)

### Community 273 - "test_policy_runtime.py"
Cohesion: 0.08
Nodes (35): ConstitutionalAIRuntime, ConstitutionalResult, Any, BaseModel, Bounded Constitutional AI critique/revision under deterministic policy., _compute_policy_fields(), PolicyCompiler, _PolicyFields (+27 more)

### Community 274 - "test_saml_provider.py"
Cohesion: 0.08
Nodes (41): build_saml_provider_from_config(), Any, SAML 2.0 provider — enterprise SSO integration. Supports python3-saml…, Return SP metadata XML for IdP registration., Return True if assertion_id was already seen (replay). Amendment 8.4: Redis key…, Construct a SAMLProvider from a saml_configs DB row., User identity extracted from a SAML assertion., SAML 2.0 single sign-on provider for a single tenant. Constructed per-tenant… (+33 more)

### Community 275 - "factory"
Cohesion: 0.08
Nodes (22): postgres_cancellation_state(), fixture, _make_legal_hold_manager(), Comprehensive tests for app/governance/legal_holds.py — targeting 90%+ coverage., When no DB is configured, release_hold skips the DB update and returns True., resource_ids already a list (not JSON string) is handled., TestCreateHold, TestIsUnderHold (+14 more)

### Community 276 - "ExperimentRegistry"
Cohesion: 0.10
Nodes (11): ExperimentRegistry, Any, Experiment Registry =================== Single control plane for ALL self-…, Record one outcome for an experiment arm., Evaluate whether the experiment can make a promotion decision., Update running mean and variance using Welford's algorithm., In-memory experiment registry (upgraded with DB in lifespan). Enforces one…, Register a new experiment. Returns the experiment dict. (+3 more)

### Community 277 - "test_governance_extra2.py"
Cohesion: 0.10
Nodes (14): _make_app(), AuditLog, FastAPI, Extra coverage for governance.py — HITL email links, legal holds, batch…, Valid sig but no matching HITL request → 404., Valid sig and matching HITL request → 200 approved., Valid sig and matching HITL request → 200 rejected., TestAuditIntegrity (+6 more)

### Community 278 - "test_cost_tracker.py"
Cohesion: 0.07
Nodes (47): calculate_cost(), Return cost in USD for a given model + token counts. Uses the in-memory…, test_calculate_cost_exact_known_model(), test_calculate_cost_gemini_flash(), test_calculate_cost_prefix_match(), test_calculate_cost_unknown_falls_back(), test_calculate_cost_zero_tokens(), _make_redis() (+39 more)

### Community 279 - "test_rag_patterns_real_openai.py"
Cohesion: 0.05
Nodes (41): SelfRAGResult, Filesystem path helpers for the test suite. Previously a number of tests…, make_provider(), make_retrieve_fn(), Comprehensive real-OpenAI E2E tests for ALL RAG patterns in AgentVerse. Tests…, Return a real OpenAI provider using gpt-4o-mini., Build an async retrieve_fn backed by the in-memory knowledge store. Used by…, Mimics an asyncpg/SQLAlchemy Row — supports integer indexing. (+33 more)

### Community 280 - "test_skills_executor.py"
Cohesion: 0.05
Nodes (41): Any, Skills Runtime execution engine. Responsibilities: 1. TriggerMatcher: score how…, Check whether a skill is allowed for the given tenant/agent. Rules (evaluated…, Set the skill allowlist for a specific agent., Execute a skill against an LLM provider., Score how well a goal/step matches a skill's trigger hints., Return 0.0-1.0 match score. Algorithm: 1. Lowercase both sides 2. For each…, Find best matching skill and execute it. Returns None if no match. (+33 more)

### Community 281 - "run_goal"
Cohesion: 0.02
Nodes (97): parse_allowed_domains(), _build_goal_kwargs_for_alert(), _build_worker_retrieval_gateway(), check_email_goals(), _do_check_email_goals(), _get_redis_pool(), _get_sync_redis(), _monotonic() (+89 more)

### Community 282 - "test_test_runner.py"
Cohesion: 0.09
Nodes (41): MockToolAdapter, Any, WorkflowTestRunner — sandbox execution for testing workflows without side…, Execute all steps with mocked outputs., Run a scenario and validate assertions., Run multiple scenarios and collect results., Execute step-by-step and return each step's result. step_overrides: step_id →…, Walk steps in dependency order and apply mock outputs. (+33 more)

### Community 283 - "test_final_fixes.py"
Cohesion: 0.08
Nodes (28): _is_blocked(), Generic HTTP request tool for calling arbitrary APIs., Block requests to internal/private/metadata endpoints (SSRF protection)., asyncio, Tests for critical and high-severity bug fixes. C-1 SSE Celery bridge H-1…, ExecutionMemory must be on app.state for agent graph to use it., main.py must import ExecutionMemory., _make_agent_loop_for_tenant must load agent config from agent store. (+20 more)

### Community 284 - "test_auth_api_coverage.py"
Cohesion: 0.06
Nodes (53): _check_auth_rate_limit(), _default_redirect_uri(), get_userinfo(), Request, Refresh an expired access token using a refresh token., Return current user information from validated JWT., Redis-backed sliding-window rate limiter for auth endpoints. Falls back to no-…, Build the default OAuth redirect URI from the FRONTEND_URL env var. (+45 more)

### Community 285 - "WorkItem"
Cohesion: 0.07
Nodes (29): CapabilityGraph, CapabilityNode, DomainDiscovery, get_domain_discovery(), get_work_value_engine(), Any, N6 + N9 — Work Value Engine + Work Discovery Pipeline. Work Value Engine (N6):…, N9 — Detect work that should exist but doesn't. Discovery sources (pluggable):… (+21 more)

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
Cohesion: 0.09
Nodes (40): BrowserResult, PageAnalysis, PageAnalyzer, Page analyzer — extract structured data from web pages using BrowserAgent + LLM., Format for injection into planner prompt., Analyze web pages using BrowserAgent and optionally a vision LLM., Fully analyze a URL: screenshot + text + LLM analysis., Analyze multiple URLs concurrently. (+32 more)

### Community 291 - "HITLWorkflowGateway"
Cohesion: 0.19
Nodes (32): HITLWorkflowGateway, Workflow-specific HITL gateway — wraps the base HITLGateway., gateway(), asyncio, fixture, Tests for HITLWorkflowGateway — all 20 HITL features., Critical requests should appear before low priority in list., Without Redis, consume returns truthy payload. (+24 more)

### Community 292 - "cli/main.py"
Cohesion: 0.09
Nodes (46): agents(), _api_key(), approve(), _base_url(), cancel(), create(), dev_server(), eval_goal() (+38 more)

### Community 293 - "models/__init__.py"
Cohesion: 0.06
Nodes (36): GuardrailRuleRow, Base, SQLAlchemy ORM model for durably-stored guardrail rules (P1-4). Rules used to…, One guardrail rule, mirroring app.guardrails_v2.models.GuardrailRule., SQLAlchemy declarative base shared across all models. All domain models are re-…, KnowledgeEdge, KnowledgeNode, Base (+28 more)

### Community 294 - "IngestionOrchestrator"
Cohesion: 0.03
Nodes (82): EmptyIndexedContentError, IngestionOrchestrator, Any, ValueError, Chunk content and filter out low-quality chunks (public API for tests)., Raised when indexed ingestion has no nonblank chunks to replace a source., Lazily build and cache the EmbeddingOrchestrator (avoids import cycles)., Filter out low-quality chunks using QualityChecker. (+74 more)

### Community 295 - "KnowledgeGraphStore"
Cohesion: 0.07
Nodes (37): EntityExtractor, Any, Entity and relationship extraction from text., Extract entities and relationships from text., extract_and_store_graph(), KGIngestionHook, KG ingestion hook — extract entities/relations from ingested text. This is the…, Object adapter the :class:`~app.ingestion.pipeline.IngestionPipeline` calls… (+29 more)

### Community 296 - "OutboundWebhookService"
Cohesion: 0.09
Nodes (20): OutboundWebhookService, Any, Outbound webhook delivery with retry and dead-letter queue., Delivers outbound webhooks with exponential backoff retry., Remove delivery records older than TTL or beyond size limit., Deliver a webhook with retry. Returns delivery record., WebhookDelivery, Comprehensive tests for app/services/webhook_service.py — targeting 90%+… (+12 more)

### Community 297 - "test_enterprise_comprehensive2.py"
Cohesion: 0.11
Nodes (46): _make_app(), _make_compliance(), _make_marketplace(), _make_red_team(), _make_simulation(), Any, FastAPI, Comprehensive tests for /enterprise API — targets 32% → 80%+ coverage. Covers… (+38 more)

### Community 298 - "api/test_governance_comprehensive.py"
Cohesion: 0.08
Nodes (46): _make_app(), _make_app_no_db(), AuditLog, FastAPI, Comprehensive tests for /governance API endpoints — targets 20% → 55%+ coverage., Emergency stop should succeed even without optional services., App variant with db_session_factory explicitly disabled., test_approve_request_not_found() (+38 more)

### Community 299 - "test_enterprise_intelligence_gaps.py"
Cohesion: 0.06
Nodes (46): _make_app(), Any, FastAPI, SimulationRunner, Tests for app/api/enterprise.py endpoints that are not yet covered. Targets the…, When eval_suite_runner is set, get_suite_results returns serialized runs., GET /intelligence/experiments returns experiments from self_optimizer., GET /intelligence/suggestions returns suggestions from self_optimizer. (+38 more)

### Community 300 - "test_generative_agent.py"
Cohesion: 0.08
Nodes (28): GenerativeAgentRuntime, Any, datetime, timedelta, Checkpointed bounded generative-agent simulation runtime., GenerativeState, Observation, Persona (+20 more)

### Community 301 - "ToolContext"
Cohesion: 0.06
Nodes (55): Any, Planner-facing tool context built from an agent's connectors., One-line ``name(param1, param2) — description[:80]`` for a tool., Render a ToolSelection into a tiered prompt string. Three sections: -…, Return a readable tool list suitable for planner prompt context. When…, Find a tool by unqualified name or by Server.tool_name., _render_signature(), to_tiered_prompt() (+47 more)

### Community 302 - "OrgResponse"
Cohesion: 0.07
Nodes (36): ChannelAdapter, ABC, Any, Base ChannelAdapter — abstract interface for all channel adapters., Abstract base for all channel adapters., Convert channel-specific payload to a normalized OrgCommand., Convert OrgResponse to channel-specific format., Verify channel-specific authentication. Override per channel. (+28 more)

### Community 303 - "ComplianceBundleManager"
Cohesion: 0.07
Nodes (17): ComplianceBundle, ComplianceBundleManager, Compliance Bundles =================== Pre-configured governance, guardrail,…, Manages active compliance bundles per tenant., Return the most restrictive autonomy mode across all active bundles., Check if any active bundle requires HITL for this tool., datetime, Time-Based Governance Rules ============================== Prevents destructive… (+9 more)

### Community 304 - "test_audit_v2.py"
Cohesion: 0.05
Nodes (36): batch_approve(), BatchApproveRequest, Approve or reject up to 100 HITL requests in a single call., LegalHoldManager, Any, datetime, Legal hold lifecycle management for AgentVerse audit events. A legal hold…, Release a hold and rebuild the cache from the remaining active holds. (+28 more)

### Community 305 - "PlanTier"
Cohesion: 0.02
Nodes (131): get_my_sla(), list_sla_plans(), Any, get, Request, SLA tiers — per-plan uptime guarantees and support response times., Return the SLA terms for the authenticated tenant's plan., List all plan SLA tiers (public — for pricing page). (+123 more)

### Community 306 - "Tokenizer"
Cohesion: 0.06
Nodes (22): PromptCompressor, Any, Prompt Compressor ================= Reduces system prompt token count before…, Compress a list of {role, content} message dicts., Truncate only [Relevant context] / [Knowledge base context] / [Visual context]…, Truncate [context] / [Relevant context] blocks that exceed max token size., If [Available tools] section has > _MAX_TOOL_LIST_ITEMS, trim it., Stateless heuristic prompt compressor. Usage: compressor = PromptCompressor()… (+14 more)

### Community 307 - "test_workflow_planner_comprehensive.py"
Cohesion: 0.09
Nodes (33): build_static_workflow(), Any, LLM-based workflow DAG planner. Given a goal, produces a dependency graph of…, Build a deterministic connector-targeted workflow from goal keywords., WorkflowPlanner, Comprehensive tests for app/agent/workflow_planner.py — targets 90%+ statement…, If tool_context access raises, planner still works., test_build_static_workflow_browser_goal() (+25 more)

### Community 308 - "ComplianceChecker"
Cohesion: 0.11
Nodes (43): ComplianceChecker, generate_scim_token(), Enterprise compliance v2 — real dynamic compliance checking. Replaces the…, Generate a SCIM bearer token. Returns: (raw_token, token_prefix_12_chars,…, Dynamically evaluates HIPAA, GDPR, SOC2, PCI-DSS compliance for a tenant. Never…, _make_db_factory(), Any, asyncio (+35 more)

### Community 309 - "InClusterKubernetesClient"
Cohesion: 0.13
Nodes (6): InClusterKubernetesClient, KubernetesClient, KubernetesRunnerHealthCheck, Any, Protocol, Small async Kubernetes REST client using the mounted service-account identity.

### Community 310 - "DepartmentMemory"
Cohesion: 0.07
Nodes (31): DepartmentMemory, MemoryEntry, Any, Department Memory — PART 14 (6-tier memory, tier: department-scoped).…, Add a new memory entry to the department store. Raises: ValueError: if…, Append a correction to an existing entry (non-destructive). The original…, Mark an entry as no longer valid., Return all entries for a department. (+23 more)

### Community 311 - "ChannelRateLimiter"
Cohesion: 0.07
Nodes (22): ChannelRateLimiter, Any, Exception, RateLimitExceeded, Per-channel rate limiter — Q11 of spec. Limits: Per tenant across all channels:…, Redis sliding window (ZADD + ZCOUNT pattern)., In-memory sliding window fallback., Raised when a channel command exceeds rate limits. (+14 more)

### Community 312 - "test_redis_factory.py"
Cohesion: 0.06
Nodes (26): get_redis_kwargs(), make_async_redis(), make_sync_redis(), _parse_host_port_list(), Any, Redis client factory with Sentinel and Cluster support. Priority order: 1.…, Read HA topology settings from environment variables. The returned dict can be…, Parse 'host1:port1,host2:port2' into [(host, port), ...] pairs. (+18 more)

### Community 313 - "tenant_service.py"
Cohesion: 0.05
Nodes (27): _generate_raw_key(), _hash_key(), Any, datetime, TenantService — in-memory tenant and API key management. In production this…, Return tenant profile. Raises :class:`~app.core.errors.NotFoundError` if…, Return all keys for *tenant_id* — raw keys and hashes are **never** included., Create a new API key. The raw key is returned once and never stored. (+19 more)

### Community 314 - "test_reasoning_retrieval_strategies.py"
Cohesion: 0.11
Nodes (40): AgenticRAGRuntimeContract, AgenticAction, AgenticRAGRuntimeAdapter, StrEnum, Execute typed retrieve/reformulate/fallback/stop decisions within a bound., FLARERAGRuntimeAdapter, Canonical FLARE adapter with independently embedded follow-up retrieval., Canonical Self-RAG adapter with persisted critique and bounded retry evidence. (+32 more)

### Community 315 - "classify_tool_risk"
Cohesion: 0.09
Nodes (43): classify_tool_risk(), Classify a tool into a risk tier. Parameters ---------- tool_name: The name of…, Comprehensive tests for app/agent/tool_risk.py — targets 90%+ statement…, test_approve_is_write_high(), test_atlassian_context_recognized(), test_billing_connector_is_write_high(), test_charge_is_write_high(), test_combined_server_and_tool_name_logic() (+35 more)

### Community 316 - "memory_v2.py"
Cohesion: 0.12
Nodes (42): consolidate_memories(), create_memory(), CreateMemoryRequest, _db_upsert_memory(), delete_memory(), _detect_conflicts(), _ensure_loaded_from_db(), export_gdpr() (+34 more)

### Community 317 - "LeaseManager"
Cohesion: 0.10
Nodes (27): ActiveClaimError, InMemoryLeaseRepository, LeaseClaim, LeaseManager, LeaseRepository, PostgresLeaseRepository, async_sessionmaker, AsyncSession (+19 more)

### Community 318 - "test_state_runtime.py"
Cohesion: 0.08
Nodes (35): CacheDecision, CachePolicyEngine, CachePolicyEngine — decides whether a step result should be cached., MemoryDecision, MemoryPolicyEngine, MemoryPolicyEngine — decides which memory sources to activate per profile., Any, SessionMemory — within-session goal execution memory. (+27 more)

### Community 319 - "MCPClient"
Cohesion: 0.05
Nodes (95): _absolute_http_url(), CircuitBreakerOpenError, _extract_credentials_from_server(), _is_jira_rest_endpoint(), _is_mcp_endpoint(), _jsonrpc(), MCPClient, Any (+87 more)

### Community 320 - "MemoryRecord"
Cohesion: 0.07
Nodes (47): MemoryFeedback, MemoryRecallHit, MemoryRecallRequest, MemoryRecord, MemoryWriteRequest, BaseModel, model_validator, Canonical evidence-backed memory and learning contracts. (+39 more)

### Community 321 - "ABTestingEngine"
Cohesion: 0.09
Nodes (26): ABTestingEngine, ExperimentArm, ExperimentType, Any, Seed in-memory results from DB on startup., Record result in-memory AND persist to ab_test_results table., test_ab_testing_engine_records_and_stats(), test_ab_testing_promotion_threshold() (+18 more)

### Community 322 - "test_real_postgres.py"
Cohesion: 0.10
Nodes (28): asyncio, Integration tests using the real local Docker PostgreSQL. These tests connect…, store_async() inserts a row into long_term_memory table., store_async() keeps the in-memory cache consistent., recall_async() without embedder falls back to keyword scoring., delete() removes a memory from the in-memory store., Verify the embedding column was added to long_term_memory., list_async() reads from PostgreSQL without error. (+20 more)

### Community 323 - "test_phase6_7_rag_runtime.py"
Cohesion: 0.13
Nodes (24): _certified_rag_registry(), _Gateway, _make_app(), fixture, MonkeyPatch, SimpleNamespace, Phase 6+7: GraphRAG/RAG Platform + Agent Runtime 2.0 tests., test_create_and_get_run_trace() (+16 more)

### Community 324 - "._make"
Cohesion: 0.10
Nodes (6): asyncio, Tests for all remaining performance optimizations. Covers: - GoalDeduplicator…, TestCostTierDowngrade, TestGoalDeduplicator, TestModelRouterComplexityTiering, TestPromptCompressor

### Community 325 - "test_knowledge_base_pipeline.py"
Cohesion: 0.10
Nodes (43): _chunk(), _collection(), _fake_embedding(), KnowledgeCollection, Integration tests: Knowledge base pipeline — ingest, search, retrieval,…, metadata_filter restricts results to matching chunks only., delete_document() removes all chunks for a doc_id., Chunks ingested by tenant A are not returned by tenant B's search. (+35 more)

### Community 326 - "ToolSelector"
Cohesion: 0.10
Nodes (20): _needs_rpa(), Any, ToolSelector — goal-aware top-k tool retrieval with reliability boosting.…, Return [(score, tool)] sorted desc by relevance score., Boost scores by historical success rate., Return True when the goal or agent capabilities signal browser work., Select tools for a goal. Returns a ToolSelection with 3 tiers. Falls back to…, ToolSelection (+12 more)

### Community 327 - "outbox.py"
Cohesion: 0.07
Nodes (22): OutboxDelivery, OutboxRecord, OutboxRepository, PostgresOutboxRepository, Any, async_sessionmaker, AsyncSession, BaseModel (+14 more)

### Community 328 - "test_fakeredis_gaps.py"
Cohesion: 0.08
Nodes (41): FastAPI, _register_error_handlers(), asyncio, Coverage gaps for app/main.py — _FakeRedis sorted-set ops + _FakeLuaScript…, 2-key script: cost fits within both limits., 2-key script: second call accumulates on existing totals., 2-key script: raises GOAL_BUDGET_EXCEEDED when goal limit hit., 2-key script: raises DAILY_BUDGET_EXCEEDED when daily limit hit. (+33 more)

### Community 329 - "test_validators.py"
Cohesion: 0.10
Nodes (32): _first_match(), IdDocExtractor, _label_value(), ID document field extractor (PAN, Aadhaar, Passport, Driving License)., mask_aadhaar(), normalize_date(), OCR field validators: format checking, checksum validation, date normalization., Normalize any date string to ISO 8601 (YYYY-MM-DD). Returns None if unparseable. (+24 more)

### Community 330 - "org/connectors/__init__.py"
Cohesion: 0.06
Nodes (20): BaseConnector, ConnectorCategory, ConnectorMeta, ConnectorRegistry, ExpensifyConnector, GrafanaConnector, LookerConnector, ABC (+12 more)

### Community 331 - "test_consumers.py"
Cohesion: 0.09
Nodes (31): APIPoller, DBRowChangeConsumer, Any, Data trigger consumers — DB row change, S3 events, API poll, RSS feed., Generic HTTP API poller for API_POLL triggers., Listen on pg_notify for DB_ROW_CHANGE triggers., Execute a single poll cycle for a trigger., Simple dot-notation JSONPath: $.foo.bar → data['foo']['bar']. (+23 more)

### Community 332 - "WorkflowHITLRequest"
Cohesion: 0.14
Nodes (12): Persist a new HITL request and send notifications., Submit a reviewer decision. Idempotent by default., Delegate a pending request to another user., Manually escalate a request., Decide multiple pending requests at once., Add a discussion thread comment., Generate a single-use JWT magic link token. The token is stored in Redis with a…, Load a HITL request by ID. (+4 more)

### Community 333 - "asyncio"
Cohesion: 0.08
Nodes (21): asyncio, Messages with subtype are skipped., Messages shorter than 10 chars are skipped., Follows next_cursor for pagination., Remaining messages < 5 in final window are still chunked., Files with < 50 chars are not chunked., 404 errors on individual files are silently skipped., Non-404 HTTP errors are logged and skipped. (+13 more)

### Community 334 - "TestKnowledgeStoreBasic"
Cohesion: 0.07
Nodes (17): _FailingDB, asyncio, KnowledgeCollection, Multiple chunks from same doc_id: doc_count stays 1., hybrid_search_db without DB falls back to in-memory search., A configured persisted read never substitutes memory on failure., Empty query_embedding triggers fallback to in-memory., Tests for ingest_document (lines 348-428). (+9 more)

### Community 335 - "test_router_runs.py"
Cohesion: 0.10
Nodes (35): client(), _FakeRunStore, make_app(), fixture, TestClient, Tests for workflow runs router (run detail, steps, lifecycle control)., Minimal in-memory WorkflowRunStore double for the real WorkflowService., real_client() (+27 more)

### Community 336 - "check_tool_args_for_exfil"
Cohesion: 0.07
Nodes (18): check_tool_args_for_exfil(), _contains_secret(), Any, Data Exfiltration Guard ======================= Detects when an agent is about…, Wrap tool output in untrusted-content delimiters. This prevents the LLM from…, Return True if text appears to contain credentials or secrets., Check tool arguments for potential data exfiltration. Returns: (blocked: bool,…, wrap_tool_output_as_untrusted() (+10 more)

### Community 337 - "guardrails_v2/engine.py"
Cohesion: 0.07
Nodes (51): CorpusSampleModel, create_rule(), CreateRuleRequest, enable_compliance_bundle(), evaluate_content(), evaluate_corpus(), EvaluateCorpusRequest, EvaluateRequest (+43 more)

### Community 338 - "ClaimRepository"
Cohesion: 0.18
Nodes (17): ClaimRepository, datetime, RuntimeError, timedelta, Atomic in-memory reference implementation for fenced swarm claims., StaleFencingTokenError, BaseModel, SwarmClaim (+9 more)

### Community 339 - "test_servers_comprehensive.py"
Cohesion: 0.07
Nodes (42): call_tool(), _dispatch_github_tool(), Any, AsyncClient, GitHub MCP server wrapper — wraps GitHub REST API in MCP protocol. Environment…, call_tool(), _call_tool_inner(), Any (+34 more)

### Community 340 - "AgentVersePlugin"
Cohesion: 0.07
Nodes (22): AgentVersePlugin, EvaluatorPlugin, KnowledgePlugin, MemoryPlugin, ModelPlugin, PluginType, PolicyPlugin, Any (+14 more)

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

### Community 345 - "test_mfa.py"
Cohesion: 0.10
Nodes (38): _generate_recovery_codes(), _get_mfa_state(), _hash_recovery_code(), Sync helper — returns (and creates) the cache entry for *tenant_id*. Used…, Return RECOVERY_CODE_COUNT random codes formatted as XXXXX-XXXXX., SHA-256 hex digest of a normalised recovery code., Recovery code path returns verified status WITHOUT a session token. Only TOTP…, Using a recovery code removes it from the store. (+30 more)

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

### Community 350 - "PromptVariant"
Cohesion: 0.11
Nodes (16): PromptVariant, PromptOptimizer — A/B tests prompt variants and auto-promotes the winner.…, Apply a suggestion, mutating agent_config where applicable., asyncio, Extra coverage for app/intelligence/prompt_optimizer.py. Targets uncovered…, Lines 184-186: DB exception → warning logged, no raise., Lines 204-247: builds PromptVariant objects from DB rows., Lines 244-247: DB exception → 0 returned. (+8 more)

### Community 351 - "test_runtime_readiness.py"
Cohesion: 0.18
Nodes (18): DegradedModePolicy, DependencyHealth, DepStatus, Any, ReadinessGate, ReadinessResult, Check the exact runtime profile selected for this goal. A readiness…, GoalService must have _check_readiness method. (+10 more)

### Community 352 - "test_collab_api_comprehensive.py"
Cohesion: 0.11
Nodes (33): FakeCollabStore, _make_app(), Any, FastAPI, Comprehensive tests for app/collab API — supplements test_collab.py., Minimal fake for API-level tests., test_append_operation_returns_201(), test_append_operation_session_not_found_returns_404() (+25 more)

### Community 353 - "test_final_coverage_push.py"
Cohesion: 0.03
Nodes (64): _check_test_rate(), _async_resolver(), _make_civ_app(), _make_connectors_app(), _make_guardrails_app(), _make_integrations_app(), _make_schedules_app(), FastAPI (+56 more)

### Community 354 - "test_rate_limiter.py"
Cohesion: 0.12
Nodes (21): Tenancy layer: multi-tenant isolation, auth middleware, rate limiting., Lock, Sliding-window rate limiters backed by Redis sorted sets. Two classes are…, Per-tenant, per-endpoint sliding-window rate limiter. Primary path: atomic Lua…, Check if a request is within rate limits and record it if allowed. Returns:…, SlidingWindowRateLimiter, TenantScopedStore — Redis wrapper that namespaces every key under the tenant.…, asyncio (+13 more)

### Community 355 - "_StatefulMockSession"
Cohesion: 0.18
Nodes (4): Mock session that can return different rows per execute() call., Set rows to return for each successive execute() call., _StatefulMockDB, _StatefulMockSession

### Community 356 - "test_main_lifespan.py"
Cohesion: 0.05
Nodes (52): Resolve a real LLM provider from environment, or FakeProvider as last resort.…, _resolve_provider_for_app(), Resolve the first healthy LLM provider. Returns a configured LLMProvider…, resolve_provider(), FakeProvider returned when no API keys and environment=development., RuntimeError raised in production with no LLM provider keys., Returns AnthropicProvider when ANTHROPIC_API_KEY is set., Returns OpenAI provider when ANTHROPIC_API_KEY absent but OPENAI_API_KEY… (+44 more)

### Community 357 - "ChatService"
Cohesion: 0.03
Nodes (85): _Artifact, ChatService, _Folder, _hex(), _Message, _now(), Any, datetime (+77 more)

### Community 358 - "test_a2a_dispatch.py"
Cohesion: 0.09
Nodes (33): dispatch_internal_task(), Any, Internal agent-to-agent dispatch for civilization members. Uses A2A data model…, Produce HMAC-SHA256 signature for an A2A payload., Dispatch an A2A task internally via GoalService (not public HTTP ingress). This…, _sign_payload(), A2ATaskRecord, InMemoryA2ARepository (+25 more)

### Community 359 - "test_rls_isolation.py"
Cohesion: 0.15
Nodes (17): _migration_content(), asyncio, RLS isolation tests for civilization tables. Asserts tenant A cannot read…, Two board instances for the same tenant share the same data (sanity check)., Society members must be scoped to the correct tenant. Directly injecting an…, Society.get_member returns members for the correct tenant (sanity check)., Entries on different topics don't bleed across topic filters., All 7 civilization tables must have RLS policies in the migration. The… (+9 more)

### Community 360 - "api/memory.py"
Cohesion: 0.17
Nodes (28): clear_all_memories(), create_memory(), CreateMemoryRequest, delete_memory(), delete_memory_by_id(), _get_db(), _get_ltm(), get_tool_reliability() (+20 more)

### Community 361 - "test_prompt_optimizer_persistence.py"
Cohesion: 0.11
Nodes (27): PromptVariant, _make_db_mock(), _make_variant(), asyncio, Tests for PromptOptimizer DB/Redis persistence., Build a (sync) db-factory mock whose session supports async with db() as s,…, PromptOptimizer must have persist and load methods., PromptOptimizer must expose add_variant(). (+19 more)

### Community 362 - "test_communication_connectors.py"
Cohesion: 0.06
Nodes (18): call_tool(), _headers(), Any, Microsoft Teams MCP server — interact via MS Graph API. Environment:…, Telegram MCP server — interact with Telegram Bot API. Environment:…, _import_server(), asyncio, Tests for Communication, Email & Marketing MCP connector servers. These are… (+10 more)

### Community 363 - "RAFTJobRecord"
Cohesion: 0.10
Nodes (13): RuntimeError, RAFTConcurrentUpdateError, RAFTError, RAFTJobRecord, RAFTModelUnavailableError, RAFTNotFoundError, RAFTRepository, Base error for fail-closed RAFT lifecycle operations. (+5 more)

### Community 364 - "test_sandbox_runtime.py"
Cohesion: 0.10
Nodes (23): SandboxExecutor, FilesystemMode, FilesystemPolicy, NetworkMode, NetworkPolicy, Any, SandboxRuntimeProfile, SandboxType (+15 more)

### Community 365 - "test_expression_engine.py"
Cohesion: 0.07
Nodes (18): ExpressionEngine, ExpressionEvalError, ExpressionSecurityError, ValueError, ExpressionEngine — safe expression evaluator for conditional branches. Uses…, Raised when a blocked construct is detected., Raised when expression evaluation fails., Evaluates conditional branch expressions safely. (+10 more)

### Community 366 - "_make_app"
Cohesion: 0.06
Nodes (20): _nullctx, Any, Null async context manager — passes the value through unchanged., _isolate_db_factory(), _make_app(), asyncio, FastAPI, fixture (+12 more)

### Community 367 - "test_connectors_comprehensive2.py"
Cohesion: 0.12
Nodes (32): _FakeRedis, _make_app(), _make_registry(), Any, FastAPI, TestClient, Extended tests for /connectors API — covers endpoints not in existing tests.…, Minimal in-memory async Redis stub for MCPRegistry tests. (+24 more)

### Community 368 - "test_api_e2e.py"
Cohesion: 0.07
Nodes (19): FakeGoalService, _FakeRedis, FakeTenantService, _make_test_client(), Any, TestClient, E2E API tests — full HTTP request/response cycle through the FastAPI app. Uses…, Minimal goal service for E2E tests (matches the real service contract). (+11 more)

### Community 369 - "test_full_stack_e2e.py"
Cohesion: 0.05
Nodes (10): client_and_key(), fixture, Full-stack E2E tests — exercises the complete request→agent→response cycle.…, A fully wired app instance shared across tests in this module., Creates a tenant, returns (AsyncClient, api_key)., Core security: tenant A's goals are invisible to tenant B., Tenant isolation: agents are tenant-scoped., real_app() (+2 more)

### Community 370 - "GoalAnalyticsAggregator"
Cohesion: 0.09
Nodes (46): GoalAnalyticsAggregator, Return cost aggregated by model from cost_ledger table., Computes analytics from the GoalService in-memory goal states. When ``db`` is…, _mock_goal(), datetime, Comprehensive tests for app/analytics/aggregator.py., When DB query fails, falls back to in-memory goal service., When DB returns empty rows, uses in-memory goals. (+38 more)

### Community 371 - "test_stream.py"
Cohesion: 0.16
Nodes (37): Any, SSE stream generator for the chat feature. Multiplexes two sources: 1. LLM…, Emit a clarify_needed event., Emit a hitl_required event — pauses goal execution until approved., Emit a schedule_created confirmation event., Emit an artifact_created event., Format a single SSE message., Simulate QA streaming — yields SSE events for each token. In production this is… (+29 more)

### Community 372 - "AuditEvent"
Cohesion: 0.09
Nodes (14): AuditEvent, AuditWriter, Return SHA-256 of the canonical JSON representation of this event. The…, Writes audit events to a Redis list (WAL). Guarantees: - Never raises — a Redis…, Push one event to the WAL. Never raises., Push multiple events via a single pipelined command., Serializable audit event with SHA-256 hash chain support. All fields match the…, TestAuditEventV2 (+6 more)

### Community 373 - "test_semantic_cache_comprehensive.py"
Cohesion: 0.09
Nodes (10): _do_hash(), Comprehensive tests for app/rag/semantic_cache.py — targeting 90%+ coverage., Covers line 24: both mag == 0 returns 0.0., Covers line 68: _hash_embedding produces stable hash., Hash should be 32 chars (hexdigest[:32])., New format uses scv2:entry: prefix., TestCosineFunction, TestSemanticCacheCosineSimilarity (+2 more)

### Community 374 - "ContextEngine"
Cohesion: 0.12
Nodes (27): ContextBuildRequest, ContextEngine, ContextItem, ContextStrategy, estimate_tokens(), get_context_engine(), _jaccard(), _ngram_fingerprint() (+19 more)

### Community 375 - "test_condition_consumer.py"
Cohesion: 0.15
Nodes (20): _consumer(), _FakeDispatcher, _FakePubSub, _FakeRedis, _FakeStore, Any, asyncio, 2.W-1 / Family D: condition/state triggers evaluate on the EVENT bus. (+12 more)

### Community 376 - "test_chat_api.py"
Cohesion: 0.10
Nodes (38): app(), client_with_tenant(), asyncio, fixture, Integration tests for chat API endpoints — 25 cases., test_chat_requires_auth(), test_create_and_delete_template(), test_create_and_list_artifact() (+30 more)

### Community 377 - "_headers"
Cohesion: 0.05
Nodes (39): _headers(), Lines 1176-1199: no SAML row → 404., Lines 400-409: publish_template_v2 runs security review., Lines 719-727: list_suggestions with applied=True filter., Lines 244-258: streaming simulation events emitted correctly., Lines 213-214: agent_config is set → uses it directly (no store lookup)., Lines 228-258: stream_simulation uses agent_config override., Lines 207-214: stream_simulation resolves agent from agent_store by agent_id. (+31 more)

### Community 378 - "RetrieverTool"
Cohesion: 0.06
Nodes (43): CitationRef, Any, Execute only the explicitly requested canonical strategies., Execute canonical corrective retrieval without local correction fallback., Thin adapter from agent callers to canonical gateway executions., Retrieve the requested canonical strategy without fallback or promotion., RetrieverTool, Test gateway that keeps core-execution tests on the canonical boundary. (+35 more)

### Community 379 - "test_orchestrator.py"
Cohesion: 0.05
Nodes (64): CivilizationOrchestrator, Any, CivilizationOrchestrator — the runtime loop for the civilization. The ONLY…, Runtime coordinator for a civilization. Responsibilities: - Accept incoming…, Periodic tick — breach check + auto-retire + learning pipeline. Called by…, Get full civilization status for API response., Pull recent eval scores and update member reputation via EWMA. Called during…, Orchestrator queries blackboard and injects context into goal execution. (+56 more)

### Community 380 - "test_hallucination_fixes.py"
Cohesion: 0.06
Nodes (39): Validate that *tool_name* is in the allowed set. Returns: None — tool is valid,…, Validate *arguments* against a JSON Schema dict. Checks: - All ``required``…, validate_tool_arguments(), validate_tool_name(), _build_verifier_provider(), Build a separate LLM provider for the verifier role. Priority: 1.…, _agent_source(), Unit tests verifying all 6 hallucination-elimination fixes. (+31 more)

### Community 381 - "TestSolutionsCatalog"
Cohesion: 0.05
Nodes (32): call_external_a2a_agent(), Any, Call an external A2A-compatible agent and wait for the result. Args:…, get_solution_detail(), install_solution(), InstallRequest, list_all_solutions(), BaseModel (+24 more)

### Community 382 - "test_scopes_rbac.py"
Cohesion: 0.05
Nodes (48): Redis-backed IP allowlist enforcement. Cache key: ip_wl:{tenant_id} Value: JSON…, ABACEvaluator, ScopeEnforcementMiddleware — enforces API key scopes on every request. Pipeline…, Evaluates attribute-based conditions attached to role assignments. Supported…, test_abac_evaluate_department_match_false(), test_abac_evaluate_department_match_true(), test_abac_evaluate_empty_conditions_returns_true(), test_abac_evaluate_multiple_conditions_all_must_pass() (+40 more)

### Community 383 - "test_group_chat.py"
Cohesion: 0.17
Nodes (17): GroupChatRuntime, Any, GroupChatExecutionState, GroupChatParticipant, BaseModel, AgentBasedSpeakerPolicy, Any, Deterministic speaker-selection policies. (+9 more)

### Community 384 - "TenantOptimizationState"
Cohesion: 0.09
Nodes (23): Self-improvement optimizer v2 — fixes all 4 critical bugs. Bugs fixed from v1…, Per-tenant, per-agent state stored in Redis. Key format:…, Atomically increment the goal completion counter. Returns new count., TenantOptimizationState, asyncio, Tests for SelfOptimizerV2 — all 4 critical bug fixes + Bayesian A/B testing.…, Fix 4: DEFAULT_MIN_GOALS must be 5, not 50., Fix 4: Experiment starts after 5 goals, not 50. (+15 more)

### Community 385 - "fire_due_schedules"
Cohesion: 0.08
Nodes (39): _count_tenant_rows(), _db_row_change_allowlist(), fire_due_schedules(), Operator-configured allowlist of tables DB_ROW_CHANGE may poll., A table is pollable only if it is a bare identifier AND allowlisted — so a…, DB_ROW_CHANGE fires when the tenant's row count in the watched table has grown…, Count a tenant's rows in an allowlisted table (RLS-scoped). Returns None on…, Fire all cron/interval schedules that are due within the current minute. (+31 more)

### Community 386 - "test_plan_runtime.py"
Cohesion: 0.10
Nodes (23): CostEstimate, PlanCostEstimator, PlanRiskAnalyzer, PlanTrace, PlanTraceEntry, Any, PlanTrace — observability trace for plan verification decisions. Records every…, Trace of PlanVerifier decisions for a goal execution. (+15 more)

### Community 387 - "ProvenanceRecord"
Cohesion: 0.11
Nodes (20): ProvenanceRecord, Any, ProvenanceExport, Any, ProvenanceExport — exports provenance records in various formats., ProvenanceLedger, Any, ProvenanceVerifier (+12 more)

### Community 388 - "api/test_connectors.py"
Cohesion: 0.11
Nodes (30): _FakeRedis, _make_app(), Any, asyncio, FastAPI, MonkeyPatch, Tests for /connectors API endpoints., OAuth callback must return 503 (not a fake success) when oauth_manager is not… (+22 more)

### Community 389 - "_make_breaker"
Cohesion: 0.08
Nodes (11): _make_breaker(), Simulate CLOSED→OPEN by recording failures to threshold., OPEN circuit transitions to HALF_OPEN after cooldown., After a HALF_OPEN probe succeeds, all keys are deleted (CLOSED)., TestCanCallAsync, TestGetState, TestKeyGeneration, TestRecordFailureAsync (+3 more)

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

### Community 394 - "SimulationRunner"
Cohesion: 0.02
Nodes (143): MockMCPClient, Any, Simulation runner — execute goals in a sandboxed mock-tool environment. Allows…, Runs goals in a mock-tool sandbox environment., Run a goal through the FULL AgentGraph pipeline with mocked tool responses.…, Async generator yielding SSE-style events as simulation executes. Uses a…, MCP client that returns pre-configured mock responses. Used in simulation to…, Build a keyword-based execution plan for the simulated goal. (+135 more)

### Community 395 - "test_costs_comprehensive.py"
Cohesion: 0.14
Nodes (36): _make_app(), _make_tracker(), Any, FastAPI, Comprehensive tests for /costs API endpoints — targets 40% → 90%+ coverage., Verifies MODEL_PRICING is exposed correctly., test_get_anomalies_empty(), test_get_anomalies_no_tracker_returns_503() (+28 more)

### Community 396 - "count_tokens"
Cohesion: 0.12
Nodes (27): count_tokens(), Convenience function using the module-level tokenizer., BudgetResult, ContextBudget, Any, ContextBudget — enforces token limits on retrieved context. Two packing…, Pack ``chunks`` under the (optionally per-call overridden) limits. Per-call…, Order chunks by value-per-token density (descending) for knapsack packing. (+19 more)

### Community 397 - "test_ip_allowlist_comprehensive.py"
Cohesion: 0.09
Nodes (32): IPAllowlistCache, is_ip_allowed(), Any, Redis-backed CIDR allowlist cache with 60-second TTL. Falls back to a DB query…, Return active CIDR list for the tenant. Priority: 1. Redis cache (TTL=60 s) 2.…, Remove the cached allowlist for a tenant., Return True if ``client_ip`` is permitted by the CIDR allowlist. Rules: - Empty…, Thin wrapper — delegates to :func:`app.auth.ip_allowlist.is_ip_allowed`. (+24 more)

### Community 398 - "coordination/contracts.py"
Cohesion: 0.08
Nodes (38): ContextMessage, Contract, CoordinationEvent, EventPayload, HandoffCommand, Participant, BaseModel, model_validator (+30 more)

### Community 399 - "test_cost_dashboard_api.py"
Cohesion: 0.08
Nodes (33): CostAnomaly, Cost tracking, budget enforcement, anomaly detection, and cost prediction. This…, Return recent anomalies for a tenant (reads from Redis EWMA state). This is a…, _make_db_with_rows(), Any, asyncio, Tests for the Cost Dashboard API — response shapes, computed fields, SQL…, avg_cost_per_goal must not divide by zero when goal_count is 0. (+25 more)

### Community 400 - "ImprovementActionRecord"
Cohesion: 0.12
Nodes (30): ImprovementActionExecutor, Handler, Idempotent bounded execution for all governed improvement action types., build_default_handlers(), _handle_parameter_tune(), _handle_prompt_update(), _handle_strategy_switch(), Any (+22 more)

### Community 401 - "get_extractor"
Cohesion: 0.10
Nodes (29): get_extractor(), Any, Tests for OCR field extractors., test_aadhaar_extracts_dob(), test_aadhaar_extracts_masked_value(), test_aadhaar_extracts_number(), test_bank_extracts_account_number(), test_bank_extracts_balances() (+21 more)

### Community 402 - "test_civilization_api.py"
Cohesion: 0.13
Nodes (35): _get_all_route_paths(), _get_openapi_paths(), _make_app_disabled(), _make_app_enabled(), Tests for /civilizations API endpoints — Phase E. All tests use the full…, Sign up a new tenant and return (client, headers). Skip on failure., Return all registered HTTP paths from the OpenAPI schema., Return paths from both HTTP routes and WebSocket routes. (+27 more)

### Community 403 - "test_guardrails_comprehensive2.py"
Cohesion: 0.13
Nodes (35): _clean_store(), _make_app(), Any, FastAPI, Comprehensive tests for /guardrails API endpoints — targets 36% → 85%+ coverage., After 20 requests, the 21st should be rate-limited., Filtering by severity/layer/goal_id should work., Remove all in-memory configs/violations for the test tenant. (+27 more)

### Community 404 - "_make_app"
Cohesion: 0.10
Nodes (10): _make_app(), FastAPI, SimulationRunner, Extra coverage for app/api/enterprise.py — compliance, simulation, red-team,…, TestComplianceExportExtra, TestComplianceRouterEndpoints, TestIntelligenceEndpoints, TestMarketplaceEndpoints (+2 more)

### Community 405 - "test_agent_patterns_real_openai.py"
Cohesion: 0.12
Nodes (35): make_graph(), make_provider(), Comprehensive real-OpenAI E2E tests for ALL agent patterns in AgentVerse. Each…, Execute graph.run() with an asyncio timeout guard., SRE engineer asks for step-by-step 503 debugging on a payment API. CoT node…, Developer asks agent to write then self-improve a Python validation function.…, Agent reflects on JWT premature expiry to extract actionable lessons. The…, System architect explores three HA payment-switch designs and picks the best.… (+27 more)

### Community 406 - "test_all_providers.py"
Cohesion: 0.08
Nodes (21): CerebrasProvider, DeepSeekProvider, FireworksProvider, HuggingFaceProvider, MistralProvider, MoonshotProvider, PerplexityProvider, Simple OpenAI-compatible providers — one API key, different base URL. Each… (+13 more)

### Community 407 - "admin.py"
Cohesion: 0.10
Nodes (25): change_tenant_plan(), get_incidents(), get_platform_usage(), get_tenant_detail(), list_tenants(), PlanChangeRequest, Any, BaseModel (+17 more)

### Community 408 - "get_settings"
Cohesion: 0.11
Nodes (38): CheckoutRequest, create_checkout_session(), create_razorpay_order(), CreateOrderRequest, _get_razorpay(), get_subscription(), get_usage(), _inr_to_usd() (+30 more)

### Community 409 - "capabilities/test_capability_registry.py"
Cohesion: 0.14
Nodes (24): build_default_capability_registry(), CapabilityRegistry, RiskLevel, CapabilityResolver, CapabilityRegistry, RiskLevel, CapabilityKind, CapabilityProfile (+16 more)

### Community 410 - "Goal"
Cohesion: 0.08
Nodes (32): Goal, GoalEvent, GoalStep, Base, SQLAlchemy ORM models for goals and goal steps., Persist goal step to PostgreSQL., persist_audit_event(), persist_goal() (+24 more)

### Community 411 - "PIIDetector"
Cohesion: 0.16
Nodes (8): OutputScanner, PIIDetector, Detects PII and sensitive credentials; optionally redacts them. Compliant with…, Scans LLM output before returning to caller. - PII detection + redaction. -…, SSN must be detected and redacted from the output., OutputScanner must redact SSN and set redacted_content., TestOutputScanner, TestPIIDetector

### Community 412 - "PromptOptimizer"
Cohesion: 0.08
Nodes (22): PromptOptimizer, Publish cache invalidation to other replicas., Select which prompt variant to use for this request. Returns the active…, Return a registered variant by id for a tenant, or None if absent. Public…, Record an eval score for a variant after a goal run. Searches all tenant scopes…, Manages prompt variant A/B testing and auto-promotion. Variants are scoped per…, select_variant must return control variant 70% of the time., After enough runs, maybe_promote promotes a clearly better challenger. (+14 more)

### Community 413 - "HealthCheck"
Cohesion: 0.09
Nodes (43): get_provider_catalog_endpoint(), health(), jwks_endpoint(), metrics(), Any, get, JSONResponse, Request (+35 more)

### Community 414 - "ProviderCircuitBreaker"
Cohesion: 0.10
Nodes (13): call_with_circuit_breaker(), ProviderCircuitBreaker, Any, Per-provider circuit breaker to prevent cascading LLM failures., Return True if the circuit is open (provider unavailable)., Reset failure count and close the circuit., Increment failure count; open the circuit when threshold is reached., Track half-open probe calls. (+5 more)

### Community 415 - "BM25Retriever"
Cohesion: 0.12
Nodes (12): BM25Retriever, Any, Index a list of chunk dicts (must have 'content' and 'chunk_id')., The built-in scorer has no optional runtime dependency., Okapi BM25 retrieval over a collection of chunks. k1=1.5 (term saturation),…, BM25Retriever.index + .search returns ranked hits., test_bm25_retriever_indexes_and_searches(), BM25 must provide better keyword precision — off-topic doc must not rank top 2. (+4 more)

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
Nodes (34): FastAPI, Extra enterprise tests — pushes coverage from 64% to 85%+. Targets missing…, Lines 1207: no DB → 503., Lines 1219: SCIM requires bearer token → 401 without it., Lines 1226-1227: SCIM GET user → 401 without token., Lines 1234-1235: SCIM POST user → 401., Lines 1240-1241: SCIM DELETE user → 401., Lines 963-982: gdpr check returns correct result. (+26 more)

### Community 420 - "test_memory_comprehensive.py"
Cohesion: 0.11
Nodes (34): _make_app(), _make_memory_entry(), _no_db_session(), Any, FastAPI, fixture, Comprehensive tests for /memory API endpoints — targets 19% → 60%+ coverage., Prevent all tests from connecting to a real DB. (+26 more)

### Community 421 - "test_infrastructure_e2e.py"
Cohesion: 0.08
Nodes (34): E2E tests for infrastructure modules (K8s manifests, Grafana, Prometheus)., HPA manifests contain min/max replicas configuration., Grafana dashboard JSON is valid and has all required structure., Grafana dashboard panels have at least one with a datasource., Grafana dashboard provisioning YAML is valid., Prometheus rules YAML has groups with valid alert rules., Prometheus config scrapes the AgentVerse backend., Prometheus rules file has at least 3 alert rules. (+26 more)

### Community 422 - "test_enterprise_api.py"
Cohesion: 0.14
Nodes (34): _make_app(), Any, FastAPI, SimulationRunner, TestClient, API-level tests for enterprise endpoints using TestClient., Helper: seed the optimizer with low-score eval data via the unit layer., Seeds optimizer directly on app.state, then applies via API. (+26 more)

### Community 423 - "test_scheduler.py"
Cohesion: 0.06
Nodes (42): FakeRunner, Any, ExecutionResult, Fake in-process runner — used for unit tests and when no real runner is…, In-process fake runner. Runs :class:`~app.agent.graph.AgentGraph` or any pre-…, Execute the goal and return a structured result. Never raises — all errors…, ExecutionRequest, Thin wrapper sent from the scheduler to a concrete runner. (+34 more)

### Community 424 - "asyncio"
Cohesion: 0.07
Nodes (27): asyncio, When DB commit fails, install() returns error — no ghost agent persisted., Marketplace listing should still show built-in templates if DB reads fail., Domains UI uses e-commerce; marketplace built-ins use ecommerce., Templates with injection patterns in goal_template are rejected., _check_scopes uses AND logic: passed iff BOTH conditions hold. Old (wrong)…, install() rejects params that violate the template's parameters_schema., list_templates() filters by domain correctly. (+19 more)

### Community 425 - "test_phase4_connectors.py"
Cohesion: 0.07
Nodes (36): asyncio, Tests for Phase 4: Connector Ecosystem Expansion., CodeInterpreter must execute Python and return stdout., CodeInterpreter must capture stderr., CodeInterpreter must return non-zero exit code for failing code., CodeInterpreter must return error for unknown language., CodeResult.to_dict() must return all expected fields., FileOps must write and read files in tenant workspace. (+28 more)

### Community 426 - "test_agent_builder.py"
Cohesion: 0.10
Nodes (31): CreateAgentRequest, model_validator, Legal agents must have bar_number in domain_metadata., _collect_routes(), _make_app(), Tests for Agent Builder fixes — covers all 8 fix areas., Backend rollback must accept snapshot_id as path parameter., NL creation must use list_async (DB-backed) for limit check. (+23 more)

### Community 427 - "_get_client_ip"
Cohesion: 0.13
Nodes (13): _get_client_ip(), Extract client IP with trusted-proxy validation. Only trusts ``X-Forwarded-…, Regression tests for 0C.2 API authorization hardening. Covers: H2 — A2A cross-…, H5: All routers must be registered in ENDPOINT_SCOPES., H3: XFF header must be ignored unless the direct peer is a trusted proxy., Every previously unprotected router must have at least a GET scope., POST operations on all required routers must require a write scope., Attacker-injected XFF must be discarded when no trusted proxy is configured. (+5 more)

### Community 428 - "test_scope_seeder.py"
Cohesion: 0.08
Nodes (18): Any, Scope and builtin-role seeder. Runs once during ``lifespan`` to ensure the…, Upsert canonical scope definitions into the scope_definitions table. Safe to…, Ensure builtin role templates and scope definitions exist in the DB. Creates…, seed_builtin_scopes(), seed_scope_definitions(), Comprehensive tests for app/auth/scope_seeder.py., Verify that permissions list is passed as JSON-serialized string. (+10 more)

### Community 429 - "MarketplaceAgentContent"
Cohesion: 0.09
Nodes (18): _load_yaml(), Path, Content Loader — validates and seeds marketplace agents and goal templates from…, CLI check mode: print errors and return exit code., Load a YAML file and return a list of records., Load and validate all YAML content. Returns self for chaining., EvalFixture, GoalTemplateContent (+10 more)

### Community 430 - "EvalSuite"
Cohesion: 0.12
Nodes (11): EvalSuite, EvalSuiteRunResult, Base, SQLAlchemy ORM models for eval suites and run results., An eval suite containing golden test tasks., Results of running an eval suite against live agents., test_eval_suite_instantiation(), test_eval_suite_run_result_instantiation() (+3 more)

### Community 431 - "_svc"
Cohesion: 0.10
Nodes (11): Lines 307-311: RuntimeError when no event loop running., Lines 856-857: app_state.state.agent_store., Lines 2124-2148, 2164-2174, 2246-2270, 2284-2306, 2349-2377., _svc(), TestDbHelpers, TestEvictAsync, TestGetAgentStore, TestGetMcpClient (+3 more)

### Community 432 - "Any"
Cohesion: 0.07
Nodes (18): _normalize_domain_filter(), Any, Load marketplace agents from YAML content files. Returns a list of dicts in the…, Populate deterministic built-ins for degraded DB/no-DB read paths. Priority:…, Fetch a single template by id or slug. Returns None if not found. Lookup order:…, D.3 fix: check OAuth scopes only — NOT connector names. required_connectors…, Paginated template list with optional filters., Create or update a template; optionally run security review. (+10 more)

### Community 433 - "_ingest_repo_background"
Cohesion: 0.10
Nodes (46): _ingest_repo_background(), Clone and atomically ingest under a disk/file quota and durable lease.…, _assert_no_symlink_components(), _is_secret_or_disallowed(), Path, ValueError, Fail-closed repository URL, pattern, and cloned-file validation., Compatibility wrapper returning the sanitized pinned-source URL. (+38 more)

### Community 434 - "_sign"
Cohesion: 0.11
Nodes (10): Send HITL approval emails with signed approve/reject links. P1.3: Generates…, Return a 32-char HMAC-SHA256 hex digest for the (request_id, action) pair., Return True if sig is the correct signature for (request_id, action)., _sign(), _verify(), Comprehensive coverage for app/integrations/email/ modules. Covers: -…, TestApprovalSenderSigning, TestImapListenerGetConfig (+2 more)

### Community 435 - "MultiHopReasoner"
Cohesion: 0.08
Nodes (27): HopPath, MultiHopReasoner, Any, Multi-hop graph reasoning over the AgentVerse knowledge graph. Provides BFS-…, Extract the ego-network around *entity* up to *depth* hops. Parameters…, Convert paths to a text block suitable for RAG context injection., Render path as a readable fact chain., BFS multi-hop path finding and ego-network extraction over a KG store. The… (+19 more)

### Community 436 - "OcrEngine"
Cohesion: 0.12
Nodes (25): OcrEngine, Any, OCR engine: Tesseract primary with LLM vision fallback., Run OCR on a single page image. Returns (text, confidence, engine_name)., Use LLM vision to extract text from an image., Apply preprocessing to improve OCR accuracy. Converts to grayscale and applies…, Extract text and structured fields from images and PDFs. Primary: Tesseract…, Extract text and structured fields from an image or PDF. (+17 more)

### Community 437 - "get_vault"
Cohesion: 0.07
Nodes (43): RuntimeError, Secret resolution that works identically in dev (env vars) and prod (mounted…, Raised when a required secret cannot be resolved from file or env., Resolve a secret by name, preferring a mounted ``*_FILE`` over a plain env var., read_secret(), SecretNotFoundError, _get_master_key(), get_vault() (+35 more)

### Community 438 - "workflow/test_security.py"
Cohesion: 0.08
Nodes (28): Any, PermissionError, Security utilities for workflow execution. SSRFGuard: Blocks HTTP steps from…, Redacts vault-resolved secret values before DB persistence. The ContextResolver…, Return a copy of resolved_input with vault values redacted., Raised when an HTTP step URL resolves to a blocked address., Validates HTTP step URLs against SSRF blocklist., Raises SSRFBlockedError if URL is unsafe. (+20 more)

### Community 439 - "LargePayloadStore"
Cohesion: 0.10
Nodes (26): LargePayloadStore, Any, LargePayloadStore — offloads step outputs > threshold to object storage. Keeps…, Store large step outputs outside of LangGraph state., Return *value* unchanged if small, else store and return a ref pointer., If *value* is a ref pointer, fetch and deserialise; else return as-is., asyncio, fixture (+18 more)

### Community 440 - "test_connectors_comprehensive.py"
Cohesion: 0.11
Nodes (26): _FakeRedis, _make_app(), Any, FastAPI, Comprehensive tests for /connectors API endpoints — targets 18% → 55%+ coverage., Minimal in-memory async Redis stub for MCPRegistry tests., Returns (registry, server_id) after registering a connector., _reg_with_connector() (+18 more)

### Community 441 - "SIEMType"
Cohesion: 0.08
Nodes (34): Buffers audit events and drains them to a :class:`SIEMAdapter` in batches.…, Buffer one audit event for forwarding. Non-blocking; never raises. When the…, Send up to ``batch_size`` buffered events. Returns the count sent. Returns 0…, Background loop: drain the buffer every ``flush_interval`` seconds., Launch the background drain task (idempotent)., Signal shutdown, cancel the task, and flush anything left buffered., SIEMForwarder, SIEMType (+26 more)

### Community 442 - "_make_app"
Cohesion: 0.09
Nodes (15): _make_app(), AuditLog, FastAPI, Extra coverage for app/api/governance.py — SSE streams, DB helpers, advanced…, Emergency stop with no goal_service or redis still returns 200., TestApprovalsStream, TestAuditAdvanced, TestBudgetEndpoints (+7 more)

### Community 443 - "test_goal_service_run_eval.py"
Cohesion: 0.14
Nodes (27): Any, asyncio, Tests for GoalService.run_eval (line 2782 of app/services/goal_service.py) and…, run_eval handles execution_context that is not a dict by using default values., run_eval raises NotFoundError when the goal record is not found., run_eval forwards self._app_provider to EvalRunner.score_async., When _app_provider is unset, run_eval passes provider=None to score_async., run_eval reconstructs state with steps and events when present. (+19 more)

### Community 444 - "test_docker_compose.py"
Cohesion: 0.09
Nodes (33): _load_compose(), Validate docker-compose configurations are complete and correct., PostgreSQL 16 initializes roles with SCRAM credentials by default., The slim runtime has Python but intentionally does not install curl/wget., Alpine resolves localhost to IPv6 while this nginx config listens on IPv4., Every always-on service must be able to bind during a full-stack start., Celery control ping targets workers; beat is a scheduler, not a worker., Eight prefork children repeatedly exceeded the former 512 MiB limit. (+25 more)

### Community 445 - "test_self_optimizer_v2_comprehensive2.py"
Cohesion: 0.20
Nodes (33): _make_db_session(), _make_optimizer(), _make_redis(), asyncio, Additional tests for SelfOptimizerV2 to cover uncovered branches:…, test_create_experiment_returns_id(), test_generate_suggestion_exception_returns_none(), test_generate_suggestion_invalid_json_returns_none() (+25 more)

### Community 446 - "collab.py"
Cohesion: 0.16
Nodes (31): append_operation(), append_round(), close_session(), create_session(), CreateSessionRequest, delegate_task(), DelegationRequest, generate_crdt_token() (+23 more)

### Community 447 - "embeddings.py"
Cohesion: 0.09
Nodes (35): _collection_avg_similarity(), embed_texts(), EmbedRequest, get_embedding_health(), get_embedding_usage(), list_embedding_providers(), Any, BaseModel (+27 more)

### Community 448 - "test_phase3_4_multimodal.py"
Cohesion: 0.11
Nodes (31): get_job_status(), _get_pipeline(), ingest_asset(), IngestRequest, Any, BaseModel, get, Request (+23 more)

### Community 449 - "ContentLoader"
Cohesion: 0.09
Nodes (13): ContentLoader, Any, Loads, validates, and seeds marketplace + template content from YAML files., get_all_source_use_cases(), Coverage gate: every UC listed in domain docs must map to ≥1 content record., Collect all source_use_cases from all YAML files., Every created agent/template should have at least one source UC reference., TestContentCoverage (+5 more)

### Community 450 - "test_audit_v2_comprehensive.py"
Cohesion: 0.10
Nodes (14): audit_admin_action(), HashChainVerifier, datetime, Production-grade audit system with WAL, hash chaining, and SIEM integration.…, Verifies the cryptographic hash chain for a tenant's audit events. Reads events…, Decorator that emits an AuditEvent for every admin route handler call. Captures…, Recursively redact PII values in nested dicts/lists (max depth 5)., _redact_pii() (+6 more)

### Community 451 - "few_shot_cot.py"
Cohesion: 0.19
Nodes (16): FewShotCoTRuntime, Any, Bounded, tenant-scoped Few-Shot Chain-of-Thought adapter., StrEnum, ReasoningExample, ReasoningPhase, InMemoryReasoningExampleSource, Protocol (+8 more)

### Community 452 - "MetaAgentPlanner"
Cohesion: 0.10
Nodes (20): MetaAgentPlanner, Converts one NL command into a MetaAgentConfig via an LLM provider., asyncio, Full JSON response maps to MetaAgentConfig correctly., Connector objects from the LLM should not be stringified into agent IDs., interval_seconds value should be coerced to int., The planner must pass system + user messages to the provider., The planner must not force a Claude model onto OpenAI-compatible providers. (+12 more)

### Community 453 - "ProspectiveMemoryService"
Cohesion: 0.10
Nodes (25): backfill_memory_rows(), BackfillCheckpoint, CheckpointWriter, LegacyMemoryRow, Protocol, Idempotent compatibility-memory backfill through the canonical repository., ProspectiveMemory, ProspectiveMemoryService (+17 more)

### Community 454 - "WorkingMemory"
Cohesion: 0.09
Nodes (13): Any, Working memory — bounded short-term context window for the active agent run.…, A bounded FIFO queue of recent context items for the active goal. Parameters…, Add an item, evicting the oldest if capacity is exceeded., Return a copy of current items, oldest first., Return items as plain dicts suitable for prompt injection., Remove all items (call at goal start/end)., Return the N most recently added items, newest first. (+5 more)

### Community 455 - "_WorkflowStore"
Cohesion: 0.10
Nodes (22): _orm_to_dict(), Any, Workflow persistence store. Uses an in-memory dict when no DB session factory…, Wire in the async SQLAlchemy session factory (called during lifespan)., Partial update — only the provided fields are changed; version bumps., Upsert the run-engine ``workflow_definitions`` mirror row. No-op when…, Delete the mirror row, but only when no runs reference it.…, _WorkflowStore (+14 more)

### Community 456 - "SemanticChunker"
Cohesion: 0.04
Nodes (50): Chunk, Semantic text chunker with sentence-boundary, code-aware, and markdown-aware…, Split on markdown headings, then recursively chunk large sections., Split Python/TS code on function/class definitions., Chunks text into semantically meaningful pieces. Strategies: - 'text':…, Chunk text according to source type., Split on sentence boundaries, respect max_chars., SemanticChunker (+42 more)

### Community 457 - "test_raft_repository_integration.py"
Cohesion: 0.21
Nodes (27): FineTuneEvaluation, _Database, _dataset(), _grant(), _owner_url(), _pending_job(), postgres_url(), _prepare_owner() (+19 more)

### Community 458 - "router_runs.py"
Cohesion: 0.16
Nodes (31): cancel_run(), debug_run(), get_run(), get_step_result(), list_runs(), list_step_results(), pause_run(), Any (+23 more)

### Community 459 - "test_goals_final.py"
Cohesion: 0.08
Nodes (34): _make_app(), Any, FastAPI, Cover remaining ~39 lines in app/api/goals.py. Targets: - line 64:…, Lines 119-120: supervisor.run raises → 500 HTTP exception., Lines 165-172: router returns needs_human_choice → 202 with routing info., Lines 173-175: agent_router.route raises → logged, submission continues., Line 240: /goals/route with agent_store set calls list_async. (+26 more)

### Community 460 - "test_knowledge_comprehensive2.py"
Cohesion: 0.13
Nodes (32): _make_app(), _make_embedder(), Any, FastAPI, Extended tests for /knowledge API — targets 46% → 75%+ coverage., Without an embedder, ingest may succeed with zero-vector embeddings or return…, test_clear_cache(), test_create_and_list_collection() (+24 more)

### Community 461 - "test_templates_comprehensive2.py"
Cohesion: 0.11
Nodes (32): _make_app(), FastAPI, _TemplateStore, Comprehensive tests for /templates API — targets 56% → 80%+ coverage., Build app and swap module-level template_store with a fresh instance., submit=True without goal service should return 503., test_create_template_auto_extract_parameters(), test_create_template_empty_goal_text_invalid() (+24 more)

### Community 462 - "test_enterprise_v2.py"
Cohesion: 0.09
Nodes (32): _make_checker_with_mock(), asyncio, Tests for Enterprise v2: compliance checking, GDPR, SAML, SCIM, contracts.…, FIX TEST: gdpr_compliant must not be hardcoded True in the response., HIPAA compliance requires BAA to be signed., BAA signed but no HITL PHI policy → partial, not compliant., Amendment 8.3: data_portability and consent_management must read from DB.…, All GDPR controls met → status = compliant. (+24 more)

### Community 463 - "asyncio"
Cohesion: 0.09
Nodes (16): asyncio, IMAP connection failure returns 0 gracefully., search returns empty → 0 processed., search returns non-OK status → 0 processed., Processes a simple plaintext email and submits it as a goal., Extracts text/plain body from multipart email., fetch returning non-OK status skips that email., Goal submission exception is caught; processing continues. (+8 more)

### Community 464 - "api/test_artifacts.py"
Cohesion: 0.14
Nodes (26): _make_app(), asyncio, FastAPI, Tests for the artifacts REST API and MinIOArtifactStore fallback., No DB configured → artifact not found., No DB configured → artifact not found., When aioboto3 is not importable, write_bytes falls back to /tmp storage., _RPAArtifactStoreFallback writes bytes to /tmp and returns valid RPAArtifact. (+18 more)

### Community 465 - "ConsensusVerifier"
Cohesion: 0.11
Nodes (15): ConsensusVerifier, Runs up to 3-way verification and returns a ConsensusResult. Falls back…, Any, Verifier calibration — tracks verdicts vs actual outcomes to measure false-…, Update a record with the actual outcome (from human eval or next replan)., Compute false-confirm rate across all resolved records. A *false_positive*…, Records verifier verdicts and eventual outcomes for calibration. Enables false-…, Record a verifier verdict. Returns the new ``record_id``. (+7 more)

### Community 466 - "_ctx"
Cohesion: 0.11
Nodes (12): _ctx(), Lines 1669-1674: dry-run decrements Redis counter., Line 1632: persistence_mode spawns _run_agent_loop_persistent., Lines 1467-1520: auto-routing when agent_id is None., Lines 619-636, 647-657, 701-709, 719-722, 733-744., Lines 650-651: AnthropicProvider raise → fallback., Lines 656-657: OpenAICompatibleProvider raise → fallback., Lines 701-709: agent store config loading. (+4 more)

### Community 467 - "._get_all_goals"
Cohesion: 0.12
Nodes (16): _goal_status_cancelled(), _goal_status_completed(), _goal_status_failed(), Any, datetime, Return a timezone-aware datetime for goal.created_at regardless of type., Get all goal states, optionally filtered., Compute success/failure breakdown for goals. Uses PostgreSQL when ``tenant_id``… (+8 more)

### Community 468 - "test_program09_core.py"
Cohesion: 0.15
Nodes (18): CamelRuntime, Any, datetime, Checkpointed, canonical-transcript CAMEL runtime., build_inception(), Deterministic CAMEL inception validation., CamelState, InceptionArtifact (+10 more)

### Community 469 - "MarketplaceV2"
Cohesion: 0.16
Nodes (21): MarketplaceV2, DB-backed marketplace with atomic install, security review, and search.…, asyncio, test_add_review_invalid_rating_rejected(), test_add_review_rating_avg_updates_in_memory(), test_add_review_valid_rating_stored(), test_get_template_by_slug(), test_get_template_returns_none_for_missing() (+13 more)

### Community 470 - "QueryPlanner"
Cohesion: 0.14
Nodes (8): QueryPlanner, Plans and executes RAG retrieval with multiple strategies., Auto-select the best strategy for a query., test_query_planner_auto_selects_graph(), test_query_planner_auto_selects_multi_hop(), fixture, select_strategy accepts optional modalities list without crashing., TestQueryPlannerSelectStrategy

### Community 471 - "OrgMCPResources"
Cohesion: 0.09
Nodes (21): MCPPrompt, MCPPromptArgument, MCPPromptMessage, MCPResource, MCPResourceContent, OrgMCPPrompts, OrgMCPResources, Any (+13 more)

### Community 472 - "workflows.py"
Cohesion: 0.10
Nodes (37): create_workflow(), delete_workflow(), generate_workflow(), GenerateWorkflowRequest, _get_store(), get_workflow(), list_workflows(), _plan_to_canvas() (+29 more)

### Community 473 - "InjectionGuard"
Cohesion: 0.11
Nodes (13): InjectionGuard, Compiles INJECTION_PATTERNS at startup and provides O(n*patterns) scanning., DFS scan of arbitrary JSON/dict structures. Fixes the original flat-scan bug…, RecursiveArgScanner, Deep nesting must not raise; may or may not detect at cutoff., FIX: ROT13 was logically inverted. Decoded form must be scanned., scan_with_rot13 must also catch direct (non-ROT13) injection., Obfuscated violations must have elevated risk (original + 0.05). (+5 more)

### Community 474 - "DeletionOrchestrator"
Cohesion: 0.12
Nodes (15): DeletionOrchestrator, Any, Delete (or, if *dry_run*, count) a subject's data across all stores., Independent re-scan proving erasure — returns residue per store. An empty dict…, Return the name of an active legal hold covering the subject, else None., Delete (or count, if dry_run) rows; return (count, ids). Isolated txn., Executes and verifies data-subject deletion cascades., DeletionReceipt (+7 more)

### Community 475 - "ExtractedField"
Cohesion: 0.14
Nodes (17): OcrExtractor, Protocol, OcrExtractor protocol., FinancialExtractor, _first_match(), _label_value(), Financial document field extractor (Invoice, Bank Statement, Receipt)., Extractor registry — get_extractor(doc_type) factory. (+9 more)

### Community 476 - "intent_router.py"
Cohesion: 0.11
Nodes (30): classify_intent(), _get_health(), handle_approve(), handle_create_mission(), _handle_status(), _handle_summarize(), IntentResult, Any (+22 more)

### Community 477 - "_make_agents_app"
Cohesion: 0.07
Nodes (15): _make_agents_app(), Lines 309, 316-317, 324., Remaining agents paths., Line 36: _save_snapshot_to_db executes correctly., Lines 67, 75, 76: _load_snapshots_from_db returns parsed rows., Line 909: list_agent_versions returns DB snapshots., Line 926: snapshot_agent with existing snapshots increments version., agents.py lines 174, 803. (+7 more)

### Community 478 - "test_schedules_comprehensive.py"
Cohesion: 0.12
Nodes (31): _make_app(), _make_nl_scheduler_response(), Any, FastAPI, Comprehensive tests for /schedules API endpoints — targets 29% → 65%+ coverage., Use TestClient round-trip to create an agent then a schedule., Verify the SSE events endpoint responds without hanging (auth check only)., test_create_nl_schedule_missing_command() (+23 more)

### Community 479 - ".__init__"
Cohesion: 0.10
Nodes (13): Any, AuditLog, ExecutionMemory, ResultProcessor, Execute the agent graph and return the final AgentState. ``goal_id`` — when…, Write step checkpoint to DB after each successful step., Load latest checkpoint for goal resume., Persist decision trace record to DB (fire-and-forget via create_task). (+5 more)

### Community 480 - "RoutingDecision"
Cohesion: 0.08
Nodes (19): AgentScore, Any, Intent-based agent router — picks the best-fit agent for a goal., Return the system explicitly named in the goal, or None., Return the primary connector system for this agent, or None., Jaccard-style overlap between goal words and agent name + goal_template. Anti-…, Score breakdown for a single candidate agent., Bidirectional connector relevance score. Old approach: checked if the raw… (+11 more)

### Community 481 - "parse_verifier_verdict"
Cohesion: 0.12
Nodes (16): parse_verifier_verdict(), planner_schema(), PlannerPlan, PlanStep, Any, BaseModel, Pydantic response schemas for structured LLM output. Used by: - _node_plan:…, Return the JSON Schema for PlannerPlan (passed as response_schema). OpenAI… (+8 more)

### Community 482 - "api/knowledge_graph.py"
Cohesion: 0.12
Nodes (35): add_edge(), add_node(), AddEdgeRequest, AddNodeRequest, export_graph(), extract_from_text(), ExtractRequest, find_path() (+27 more)

### Community 483 - "Governor"
Cohesion: 0.10
Nodes (13): Governor, Any, Create a new civilization member (only called with APPROVED verdict). Returns…, Retire members below reputation floor or past idle TTL. Returns retired agent…, Kill a specific civilization member., Pause the civilization — stops new spawns, signals agents to halt at next…, Resume a paused civilization., Governs the civilization: enforces the Constitution, creates/retires members.… (+5 more)

### Community 484 - "AgentManifest"
Cohesion: 0.14
Nodes (21): manifest_cmd(), Manage agent manifests. Usage: agentverse manifest validate agent.yaml, AgentVerse Developer SDK., AgentManifest, ConnectorRequirement, PolicySpec, Any, Versioned agent manifest — commit-able agent configuration format. Open source… (+13 more)

### Community 485 - "test_rls_behavioral_isolation.py"
Cohesion: 0.11
Nodes (30): Set ``app.tenant_id`` GUC for the duration of the calling transaction. Must be…, rls_context(), Connection, rls_context (asyncpg variant) is a callable., test_rls_context_is_callable(), _alembic(), _app_role_url(), _asyncpg_dsn() (+22 more)

### Community 486 - "test_schedules_extra.py"
Cohesion: 0.09
Nodes (38): _make_app(), Any, FastAPI, filterwarnings, Extra coverage tests for app/api/schedules.py — webhook, pause/resume, fire,…, When agent_store is not set but agent_id is provided, raises 500., Only REST and webhook can be manually fired., NL parser returning WEBHOOK type adds token. (+30 more)

### Community 487 - "_chunk_by_chars"
Cohesion: 0.10
Nodes (11): _chunk_by_chars(), Character-based fallback chunker used when tiktoken is unavailable., Tests for app/knowledge/chunker_v2.py — token-aware chunking., When overlap >= max_tokens, step becomes 1 (guard prevents infinite loop)., chunk_by_tokens works with default args., Short text produces exactly one chunk., chunk_by_chars is a public alias for _chunk_by_chars., Mock tiktoken to test the token-based chunking path. (+3 more)

### Community 488 - "OrgLoopDetector"
Cohesion: 0.09
Nodes (20): LoopDetection, OrgLoopDetector, Detect repeated tool calls (same tool called too many times)., Detect infinite replanning., Detect budget runaway (spent > 3x budget)., Reset tracking for an agent (e.g., after task completion)., Detects loop/deadlock patterns in org execution. Called by mission orchestrator…, Detect circular delegation (agent A → B → C → A). (+12 more)

### Community 489 - "LateChunker"
Cohesion: 0.09
Nodes (15): ContextualChunkEnricher, Enriches chunks by prepending document-level context before embedding. Two…, Prepend *document_summary* to each chunk (fast, no LLM). Parameters ----------…, LateChunk, LateChunker, Late Chunking — embed the full document, then slice embeddings per chunk.…, True late chunking: get token embeddings then average per chunk. This path is…, Embed full document and slice per-chunk embeddings from token embeddings. Most… (+7 more)

### Community 490 - "GoalExecutionLock"
Cohesion: 0.10
Nodes (23): GoalExecutionLock, Any, Redis-backed distributed lock for at-most-once goal execution., Redis SET NX PX lock ensuring at-most-once execution per cluster. Uses a Lua…, Returns True if lock acquired, False if another worker holds it., Release lock only if we own it (Lua atomic check-and-delete)., Extend TTL if we still own the lock., Check if any worker holds a lock for this goal. (+15 more)

### Community 491 - "CredentialInjector"
Cohesion: 0.10
Nodes (24): CredentialInjector, Any, Resolve vault:// references in RPA tool arguments from tenant secret store.…, Auto-fill vault:// references in RPA arguments from the tenant secret store., Resolve a vault:// reference to its plaintext value., Resolve all vault:// refs in an arguments dict (recursive)., executor(), fixture (+16 more)

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

### Community 498 - "test_phase12_13_skills_frontend.py"
Cohesion: 0.23
Nodes (15): _make_app(), Phase 12+13: Skills Runtime tests., test_create_tenant_skill(), test_disable_skill(), test_enable_platform_skill(), test_enable_status_is_per_tenant(), test_execute_skill(), test_get_nonexistent_skill_returns_404() (+7 more)

### Community 499 - "test_suggestions_shape.py"
Cohesion: 0.09
Nodes (30): _make_suggestion(), Tests verifying the /intelligence/suggestions endpoint returns the shape…, Every suggestion must have all 7 frontend-expected fields., Status must be one of 'pending', 'applied', 'rejected'., Confidence should be in [0, 1]., A list of suggestions should all have the correct shape., list_experiments result must contain frontend-expected fields., DB status 'completed' should map to frontend status 'concluded'. (+22 more)

### Community 500 - "ai_ops.py"
Cohesion: 0.15
Nodes (29): compute_drift(), ComputeDriftRequest, create_eval_dataset(), create_llm_judge(), CreateDatasetRequest, CreateJudgeRequest, get_regression_status(), list_drift_alerts() (+21 more)

### Community 501 - "_make_app"
Cohesion: 0.14
Nodes (9): _make_app(), FastAPI, Extra coverage for app/api/auth.py — SSO auth endpoints., In dev mode without keycloak_client_secret, uses fallback secret., TestExchangeTokenEndpoint, TestRefreshTokenEndpoint, TestSsoConfigEndpoint, TestSsoLoginEndpoint (+1 more)

### Community 502 - "dpdp.py"
Cohesion: 0.13
Nodes (26): ConsentRequest, ErasureRequest, execute_erasure(), get_consents(), grievance_officer_contact(), Any, BaseModel, get (+18 more)

### Community 503 - "OllamaProvider"
Cohesion: 0.07
Nodes (18): _get_available_ram_gb(), _get_pull_lock(), OllamaProvider, Any, EmbedRequest, Lock, Ollama local LLM provider. Completion/streaming uses the OpenAI-compatible /v1…, Embed a list of texts using Ollama's /api/embeddings endpoint. (+10 more)

### Community 504 - "AuditFlusher"
Cohesion: 0.11
Nodes (14): AuditFlusher, Any, Drains the Redis WAL into the ``audit_events`` Postgres table. Runs every…, Seed the in-process chain tip from the DB for *tenant_id*. Called once per…, Drain up to WAL_BATCH_SIZE events from Redis and insert to Postgres. Acquires a…, Attempt SETNX on the flusher lock. Returns True if acquired., Release the flusher lock., Core flush implementation (called under the flusher lock). (+6 more)

### Community 505 - "audit_v3.py"
Cohesion: 0.10
Nodes (18): AuditRecord, AuditWriter, compute_entry_hash(), _hash_dict(), HashChainVerifier, Any, Audit v3 — World-Class Immutable Audit System…, Compute the hash for an audit entry — MUST be deterministic. (+10 more)

### Community 506 - "estimate_cost"
Cohesion: 0.09
Nodes (28): estimate_cost(), format_cost(), LLM pricing table for accurate cost estimation. Uses per-1k-token pricing from…, Estimate USD cost for a completion. Matches model name fragment. ..…, Format cost for display: $0.0012 or $1.23., governance.pricing.estimate_cost must emit DeprecationWarning., calculate_cost and the deprecated estimate_cost agree for GPT-4o-mini., test_calculate_cost_matches_pricing_for_known_model() (+20 more)

### Community 507 - "OAuthFlowManager"
Cohesion: 0.05
Nodes (60): OAuthFlowManager, OAuthToken, Any, Flexible token lookup. Supports two call styles: - Keyword:…, Refresh an expired access token., Restore OAuth token state on process startup. Returns the number of tokens…, Manages PKCE OAuth 2.0 authorization code flows., Decrypt *value* using the vault if available, else return as-is. (+52 more)

### Community 508 - "test_devops_connectors.py"
Cohesion: 0.10
Nodes (14): call_tool(), _call_tool_inner(), _encode_id(), _headers(), Any, GitLab MCP server — wraps GitLab REST API in MCP protocol. Environment…, URL-encode project namespace/slug if it contains a slash., Tests for DevOps & cloud MCP connector servers. These tests verify that all… (+6 more)

### Community 509 - "OpenAIFineTuneProvider"
Cohesion: 0.06
Nodes (27): FineTunedInferenceProvider, FineTuneJobState, FineTuneProvider, build_raft_providers(), map_openai_status(), OpenAIFineTuneProvider, Any, OpenAI fine-tuning adapter for RAFT (D-8). RAFT (Retrieval-Augmented Fine-… (+19 more)

### Community 510 - "KGQueryEngine"
Cohesion: 0.09
Nodes (23): GraphCommunity, Knowledge Graph data models., A cluster of closely related nodes., In-memory knowledge graph store with optional DB persistence., KGQueryEngine, KGQueryResult, KGQueryEngine — routes KG queries based on strategy (spec §3.5 matrix)., Real edge traversal using get_edges_for_node(). (+15 more)

### Community 511 - "test_tools_comprehensive.py"
Cohesion: 0.14
Nodes (21): file_delete(), file_list(), file_read(), file_write(), Any, Tenant-scoped file operations. All operations are restricted to…, Alias for list() — for callers that prefer the explicit name., Read a file and return ``{"success": True, "content": ...}`` or error dict. (+13 more)

### Community 512 - "test_agents_comprehensive2.py"
Cohesion: 0.21
Nodes (29): _create_agent(), _make_app(), Any, FastAPI, TestClient, Extended tests for /agents API — covers endpoints not in existing comprehensive…, test_assign_knowledge_collection(), test_clone_agent_default_name() (+21 more)

### Community 513 - "OrgMCPServer"
Cohesion: 0.09
Nodes (17): OrgMCPServer, Any, Exposes the org as a Model Context Protocol (MCP) server. Compatible with:…, Return the GoalService from app.state (non-blocking)., Return (OrgService, session) backed by a real DB session. Caller **must** use…, Return tool definitions in MCP format., Execute an MCP tool call and return the result., Route a natural-language question to the org brain. Strategy: 1. Fetch org… (+9 more)

### Community 514 - "test_mcp_circuit_breaker.py"
Cohesion: 0.09
Nodes (20): _bypass_ssrf(), client(), FakeRedis, asyncio, fixture, mock, Tests for MCP client circuit breaker wiring., The same circuit breaker instance is returned for the same server_id. (+12 more)

### Community 515 - "asyncio"
Cohesion: 0.07
Nodes (23): asyncio, Lines 104-105: get_pending_flow returns None → None returned., Lines 125-132: HTTPStatusError → None., Lines 133-138: ConnectError → None., Lines 152-154: empty access_token → None., Lines 139-142: unexpected exception → None., Lines 197-198: no existing token → None., Lines 205-206: no resolved token_url → None. (+15 more)

### Community 516 - "test_full_pipeline_real_world.py"
Cohesion: 0.13
Nodes (31): get_output(), is_done(), make_graph(), make_provider(), Real-world E2E pipeline tests using live OpenAI (gpt-4o-mini). 12 domain-…, Agent lists RBI, NPCI, PCI DSS, and data-localisation requirements for India., Agent generates a typed Python validation function with docstring and tests., Agent designs a RESTful refund-management API with endpoints, schemas, error… (+23 more)

### Community 517 - "test_untested_modules.py"
Cohesion: 0.12
Nodes (17): AgentExecutionPlan, AgentRole, AgentRunTrace, PlanStep, StrEnum, Agent Runtime 2.0 - formalized roles and execution models., A typed step in an execution plan., A typed execution plan for an agent run. (+9 more)

### Community 518 - "models/intelligence.py"
Cohesion: 0.11
Nodes (22): AgentTemplate, CollabOperation, CollabSession, CostLedger, DecisionTrace, Evaluation, Base, SQLAlchemy ORM models for intelligence: decision traces, evaluations, cost… (+14 more)

### Community 519 - "test_advanced.py"
Cohesion: 0.12
Nodes (23): GraphQLSubscriptionConsumer, PriceThresholdPoller, Any, Advanced trigger consumers — GraphQL subscriptions, WebSocket messages, price…, Poll price data and fire triggers when threshold is crossed., Maintain a WebSocket connection to a GraphQL endpoint and fire triggers on…, Check if price crosses threshold and dispatch matching triggers., Process a GraphQL subscription message and dispatch triggers. (+15 more)

### Community 520 - "TestRegistryDetection"
Cohesion: 0.14
Nodes (5): _detect_providers(), Auto-detect available providers from environment variables., When no provider keys are configured, FakeProvider is returned., TestProviderRegistry, TestRegistryDetection

### Community 521 - "._get_stats"
Cohesion: 0.10
Nodes (11): _CacheHit, True semantic similarity lookup. Returns a _CacheHit(response, similarity,…, Backward-compatible wrapper for old hash-based API. Now uses true similarity., Legacy sync store. Stores in L1 only (no Redis without async)., Legacy sync lookup. Checks L1 only., Backward-compatible sync store alias → calls store_sync., Backward-compatible sync lookup alias → calls lookup_sync., Look up multiple embeddings in a single batched Redis pipeline. Dramatically… (+3 more)

### Community 522 - "JiraIngestor"
Cohesion: 0.12
Nodes (7): JiraIngestor, Any, Jira issue ingestor via REST API v3., Convert Atlassian Document Format to plain text (recursive)., test_jira_adf_to_text(), TestJiraIngestor, TestJiraIngestor

### Community 523 - "LlmStructuredExtractor"
Cohesion: 0.11
Nodes (23): GeneralExtractor, LlmStructuredExtractor, Any, General fallback extractor — returns raw text and optional LLM-structured…, Fallback extractor — returns empty fields for unrecognized documents., Use an LLM to extract structured key-value fields from any document type. This…, Sync wrapper — returns empty (use extract_async for real results)., Parse LLM JSON response into ExtractedField dict. (+15 more)

### Community 524 - "test_explainability.py"
Cohesion: 0.12
Nodes (19): DecisionExplainer, ExplanationBundle, Any, DecisionTrace, RuntimeProfileExplainer, SourceExplainer, DecisionTrace, Any (+11 more)

### Community 525 - "CapabilityRegistry"
Cohesion: 0.12
Nodes (18): CapabilityGapDetector, CapabilityRegistry, CapabilitySpec, GapReport, get_capabilities_for_role(), Capability Registry — 60+ named capabilities with tool/model/quality bindings.…, Detect capabilities required by a mission that are not in the registry., Return the capability list for a known role name, or [] if unknown. (+10 more)

### Community 526 - "OrgDigitalTwin"
Cohesion: 0.05
Nodes (42): CapacityPlan, OrgDigitalTwin, Any, Org Digital Twin — SUPPLEMENT H. A live simulation model of the organisation…, Simulate what resources and time a mission would require., Analyse current org capacity and predict when queued work clears., What-if analysis — "what if Legal dept was 2x faster?"., Result of a digital twin simulation run. (+34 more)

### Community 527 - "OrgLearningPipeline"
Cohesion: 0.12
Nodes (17): LearningCategory, OrgLearningPipeline, OrgLesson, Any, StrEnum, PART 26 — Organizational Learning System. Learning categories (10 types) with…, Extract candidate lessons from a completed/failed mission., Anti-poisoning validation before promotion. (+9 more)

### Community 528 - "celery_tasks.py"
Cohesion: 0.10
Nodes (26): _build_celery_broker_url(), Celery application — task queues for goals, schedules, and maintenance., Build the Celery broker URL, adding Sentinel support when configured. Celery's…, _build_worker_runner(), check_hitl_escalations(), cleanup_expired_runs(), execute_workflow_run(), _get_runner() (+18 more)

### Community 529 - "is_valid_transition"
Cohesion: 0.14
Nodes (6): is_valid_transition(), Check if a goal state transition is valid., is_valid_transition enforces the goal state machine., Verifier failure triggers re-execution., GoalTransition(str, Enum) members are str instances; .value gives the string., TestGoalLifecycle

### Community 530 - "TenantUserService"
Cohesion: 0.09
Nodes (14): Any, AsyncSession, StrEnum, QA1 — Tenant User & Role Management. Three-tier user hierarchy per spec:…, QA1 — Tenant user management service. Backed by in-memory store (production: DB…, Create an invite and send a magic link email. POST /v1/tenants/users/invite, Accept an invitation and create the user account., Per spec QA1 — full tenant user record. (+6 more)

### Community 531 - "router_versions.py"
Cohesion: 0.18
Nodes (28): ApprovalDecisionRequest, approve_publish(), clone_workflow(), diff_versions(), export_yaml(), get_version(), import_yaml(), list_versions() (+20 more)

### Community 532 - "test_cache_synthesis.py"
Cohesion: 0.10
Nodes (28): _agent_source(), asyncio, Tests for semantic cache Redis API and goal-tree LLM synthesis. BUG 1:…, Synthesis must call the provider's complete() when sub-results exist., Synthesis falls back to joining results when no provider is supplied., Synthesis falls back gracefully when the LLM call raises., SemanticCache.get() must use Redis backend when available., Synthesis with no sub-results returns a meaningful string, not empty. (+20 more)

### Community 533 - "test_analytics_db.py"
Cohesion: 0.09
Nodes (28): _make_db_factory(), Any, Tests for DB-backed aggregator methods: tool_metrics_db, cost_trends_db,…, DB-backed cost trends should return period+cost_usd dicts., cost_usd values should be rounded to 6 decimal places., No DB should fall back to in-memory cost_trends()., Empty DB result falls back to in-memory., Should return a dict of model_name → total_cost. (+20 more)

### Community 534 - "test_civilization_extra4.py"
Cohesion: 0.03
Nodes (124): _civilization_not_found(), _make_app(), _make_db_mock(), Any, Exception, FastAPI, Extra tests for /civilizations API — push from 52% to 75%+ coverage. Targets…, Line 30: _civilization_not_found returns HTTPException 404. (+116 more)

### Community 535 - "state_context.py"
Cohesion: 0.11
Nodes (14): CacheBridgeResult, Any, SemanticCacheBridge — wires CachePolicyEngine to actual SemanticCache. Rules:…, SemanticCacheBridge, Any, StateRuntimeContext — unified aggregator of all 9 state sources (spec §Layer 9)., StateContextBuilder, StateRuntimeContext (+6 more)

### Community 536 - "test_schedules_api.py"
Cohesion: 0.13
Nodes (23): _AsyncCreateStore, _FakeAgentStore, _make_app(), Any, FastAPI, MonkeyPatch, Tests for /schedules, /nl, /webhooks, and /events API endpoints., test_create_schedule() (+15 more)

### Community 537 - "test_wiring_integrity.py"
Cohesion: 0.06
Nodes (16): Wiring integrity tests — proves that create_app() and worker path inject every…, 0B.1: goal_service.py must pass all required services to AgentGraph., AgentGraph must accept llm_response_cache and goal_service must wire it…, C1/C2/C3: Phase 3 services must be wired in goal_service, main.py, and…, The google-oauth router is imported and included by the router registrar…, Phase 2: tool_selector must be on app.state., 0B.10: goal_service must call dedup release on terminal states., 0B.2/0B.6: tasks.py worker must pass all services and use correct API. (+8 more)

### Community 538 - "test_voice_router.py"
Cohesion: 0.14
Nodes (28): client(), _make_app(), _make_silent_wav(), AsyncClient, asyncio, FastAPI, filterwarnings, fixture (+20 more)

### Community 539 - "_MockSession"
Cohesion: 0.13
Nodes (4): _LegacyRegistry, _MockSession, Any, Fake registry WITHOUT list_server_records to test fallback path (lines 261-265).

### Community 540 - "build_manifest"
Cohesion: 0.13
Nodes (21): AgentKeyCreateRequest, create_agent_key(), get_agent_manifest(), list_agent_keys(), BaseModel, delete, get, Request (+13 more)

### Community 541 - "StreamingGuard"
Cohesion: 0.08
Nodes (13): GuardrailViolation, A guardrail violation record., GuardDecision, Streaming guardrail — mid-stream token-level content filter. Checks a rolling…, Rolling-buffer token-level content guard. Parameters ---------- patterns :…, Accumulate *token* in the rolling buffer and check patterns. Returns a…, Clear the rolling buffer between goals., Dynamically add a pattern. Returns True on success. (+5 more)

### Community 542 - "NvidiaNIMProvider"
Cohesion: 0.32
Nodes (6): NvidiaNIMProvider, NVIDIA NIM provider. Supports NVIDIA's hosted models as well as self-hosted NIM…, _completion_request(), _make_openai_chat_response(), asyncio, TestComplete

### Community 543 - "test_tenants.py"
Cohesion: 0.11
Nodes (30): _hash_key(), SHA-256 hex digest of a raw API key. The raw key is never stored., ConflictError, Line 26: _hash_key returns SHA-256 hex digest., test_hash_key_utility(), _make_app(), Any, FastAPI (+22 more)

### Community 544 - "execution_environment/test_artifacts.py"
Cohesion: 0.11
Nodes (19): DurableExecutionArtifactStore, ExecutionArtifactStore, make_artifact(), Any, Protocol, Tenant-scoped durable artifacts for isolated execution., Adapter over the application's object store; never returns an empty reference., validate_artifact_name() (+11 more)

### Community 545 - "GitHubIngestor"
Cohesion: 0.13
Nodes (7): GitHubIngestor, Any, GitHub repository ingestor — crawls code/docs via GitHub REST API., TestGitHubIngestorHeaders, TestGitHubIngestorInit, asyncio, TestGitHubIngestorExtra

### Community 546 - "DataCategory"
Cohesion: 0.15
Nodes (18): ArchivePolicy, DeletionSchedule, Data-subject deletion orchestrator — real GDPR/DPDP right-to-erasure cascade.…, ExportPolicy, LegalHoldPolicy, DataCategory, RetentionPolicy, RetentionTier (+10 more)

### Community 547 - "SalienceScorer"
Cohesion: 0.11
Nodes (12): datetime, rank_memories(), Memory salience scoring and exponential decay. SalienceScorer computes a…, Reduce *current_score* by an exponential decay factor., Return *memories* sorted by salience score, highest first., Compute an importance score in [0, 1] for a memory entry. Higher scores surface…, Return a salience score in [0, 1]., SalienceScorer (+4 more)

### Community 548 - ".generate"
Cohesion: 0.15
Nodes (14): _build_summary(), DigestCache, DigestGenerator, DigestItem, _fmt_duration(), get_digest_generator(), Any, AsyncSession (+6 more)

### Community 549 - "test_greeting.py"
Cohesion: 0.12
Nodes (26): build_greeting_script(), jurisdiction_to_language(), Any, Voice greeting builder — synthesised on every org page load. Data sources…, D-5: Auto-detect TTS language from org jurisdiction field., Return WAV bytes for the login greeting using real org health data., Render a natural-language greeting from OrgService.get_org_health() data. Args:…, synthesize_greeting() (+18 more)

### Community 550 - "_make_mock_db"
Cohesion: 0.09
Nodes (13): _make_async_cm(), _make_mock_db(), Any, Line 593-594: exception → {}., Lines 580-592: successful DB query., Return a mock async context manager., Return (db_factory, session) mocks for DB-touching code., Line 2147-2148: raise_on_error=True re-raises. (+5 more)

### Community 551 - "MoAProposal"
Cohesion: 0.06
Nodes (58): MoAExecutionState, MoARuntime, Any, BaseModel, Checkpointed layered Mixture-of-Agents execution runtime., AggregationInput, build_aggregation_input(), BaseModel (+50 more)

### Community 552 - "test_tenants_comprehensive.py"
Cohesion: 0.17
Nodes (27): _default_svc(), _make_app(), Any, FastAPI, Comprehensive tests for /tenants API endpoints — targets 41% → 70%+ coverage., test_create_key_empty_name_invalid(), test_create_key_requires_auth(), test_create_key_success() (+19 more)

### Community 553 - "test_workflows_comprehensive.py"
Cohesion: 0.13
Nodes (27): _make_app(), Any, FastAPI, Comprehensive tests for /workflows API endpoints — targets 29% → 65%+ coverage., Without GoalService, run falls back to dry_run mode., Workflows from tenant A should not be visible to tenant B., _sample_definition(), test_create_workflow_empty_name_invalid() (+19 more)

### Community 554 - "test_all_connectors_e2e.py"
Cohesion: 0.10
Nodes (27): _build_minimal_args(), _make_mock_client(), _make_mock_response(), Any, asyncio, parametrize, E2E tests for ALL MCP connector servers. Tests (parametrized over every server…, Return a fully configured mock AsyncClient context manager. (+19 more)

### Community 555 - "workflow/test_router.py"
Cohesion: 0.08
Nodes (5): client(), make_app(), fixture, Tests for the main workflow engine router (CRUD + publish + trigger)., wf_service()

### Community 556 - "Phased Roadmap (9 layers → 47 components, TDD each)"
Cohesion: 0.07
Nodes (26): AgentVerse — World-Class Implementation Plan, Context, Data Model (PostgreSQL, all tenant-scoped via RLS), Domain-Agnostic Autonomy — the core promise, First Implementation Step (when approved), Guiding Principles (non-negotiable, enforced in code review), Phase 0 — Foundation & scaffolding, Phase 10 — Perception & collaboration (+18 more)

### Community 557 - "CSVParser"
Cohesion: 0.20
Nodes (8): CSVParser, ExcelParser, CSV and Excel parser — schema-aware extraction for structured data., Parse CSV/TSV files into readable text chunks. Each row becomes: "Table:…, Parse XLS/XLSX files into readable text, one sheet per section., test_csv_parser_basic(), test_csv_parser_empty(), test_csv_parser_truncates_large()

### Community 558 - "IdempotencyStore"
Cohesion: 0.12
Nodes (18): IdempotencyStore, Any, Redis-backed idempotency store for goal submissions., Return True if key is new (should process), False if duplicate., Release an idempotency key (e.g., if the request failed and should be retried)., Check if key exists without setting it., Prevents duplicate goal submissions using Redis SET NX with TTL. Keyed by…, IdempotencyStore.check_and_set returns False on duplicate key. (+10 more)

### Community 559 - "CRDTRoomManager"
Cohesion: 0.10
Nodes (16): collab_websocket(), CRDTRoomManager, WebSocket, Manages Yjs CRDT room connections. Uses Redis pub/sub when available for cross-…, Wire Redis client (called by app lifespan if Redis is configured)., Save full Yjs document snapshot to Redis for late-joining peers., Load Yjs document snapshot for a new peer., Broadcast binary Yjs update to all peers in the room. (+8 more)

### Community 560 - "generate_gst_invoice"
Cohesion: 0.12
Nodes (24): generate_gst_invoice(), _generate_invoice_number(), GSTInvoiceRequest, hsn_lookup(), list_invoices(), Any, BaseModel, field_validator (+16 more)

### Community 561 - "test_tenant_service_full.py"
Cohesion: 0.09
Nodes (21): Full coverage for TenantService — covers all branches and execution paths., Keys with an expiry in the past are not resolved., All DB helper methods are no-ops when no db_session_factory is configured., create_tenant returns all required fields including one-time raw API key., Creating two tenants with the same email raises ConflictError., get_tenant raises NotFoundError for an unknown tenant_id., list_api_keys never exposes key_hash or raw_key., create_api_key returns raw_key; subsequent list_api_keys does not. (+13 more)

### Community 562 - "test_knowledge_rpa.py"
Cohesion: 0.12
Nodes (15): Ingest one or more URLs using headless Playwright for JS-rendered content., RpaUrlIngestRequest, _make_app(), _make_client(), fixture, patch, Backend tests for RPA URL ingestion, knowledge retrieval in agent loop, and…, When Playwright is not installed, falls back to httpx + regex strip. (+7 more)

### Community 563 - "perception.py"
Cohesion: 0.18
Nodes (26): analyze_page(), AnalyzeRequest, batch_analyze(), BatchAnalyzeRequest, _browser_agent(), capture_screenshot(), extract_text(), ExtractRequest (+18 more)

### Community 564 - "rpa.py"
Cohesion: 0.18
Nodes (26): close_session(), create_session(), execute_rpa_tool(), _executor(), get_current_view(), get_session_screenshot(), list_rpa_tools(), list_sessions() (+18 more)

### Community 565 - "MemoryAPI"
Cohesion: 0.13
Nodes (18): _hex(), Memory, MemoryAPI, _now(), datetime, Memory management API layer over LongTermMemoryStore. Provides REST endpoints…, In-memory store backing the memory management REST layer. Production delegates…, api() (+10 more)

### Community 566 - "KnowledgeRuntimeProfile"
Cohesion: 0.11
Nodes (22): ContextRuntimeProfile, KnowledgeRuntimeProfile, MultimodalRuntimeProfile, Any, Shared serialization mixin for named spec profile dataclasses. Converts enum…, Runtime profile for multimodal content ingestion (spec §3.1)., Self-improvement runtime profile (spec §3.2)., Context quality runtime profile (spec §3.3). (+14 more)

### Community 567 - "CoordinationStreams"
Cohesion: 0.12
Nodes (13): BackpressureError, CoordinationStreams, Any, Protocol, RuntimeError, Bounded Redis Streams transport for coordination event envelopes., The bounded stream cannot safely accept more entries., Publish and acknowledge unchanged envelopes using service-role groups. (+5 more)

### Community 568 - "SupervisorAgent"
Cohesion: 0.18
Nodes (20): Decomposes a complex goal and coordinates multiple sub-agents. Unlike…, SupervisorAgent, _make_goal_service_mock(), Comprehensive tests for app/agent/supervisor.py — targets 90%+ statement…, Helper: mock goal_service that returns a completed goal event., test_decompose_caps_at_six_tasks(), test_decompose_falls_back_on_invalid_json(), test_decompose_falls_back_on_missing_sub_tasks() (+12 more)

### Community 569 - "test_memory_learning_services.py"
Cohesion: 0.13
Nodes (19): ExperimentOutcome, LearningExperimentService, Durable-compatible sticky experiment assignment and promotion gates., ExperimentSpec, KnowledgeFact, KnowledgeGraphMemory, BaseModel, Tenant-owned evidence-linked knowledge graph memory lifecycle. (+11 more)

### Community 570 - "Any"
Cohesion: 0.10
Nodes (10): Any, Turn low-scoring eval signals into concrete improvement actions. This is the…, Called after every goal completion. Drives the optimization loop., Fix 1: Apply candidate_config to the agent via a direct DB UPDATE. Was: called…, Return the agent config for a specific goal (control or candidate arm)., Fix 2: Read actual agent config from DB. Was: before_prompt = "before" —…, Fix 5: All DB operations in ONE session — no stale session after commit.…, Deterministic arm assignment via goal_id hash. 50/50 split by default; respects… (+2 more)

### Community 571 - "OAuthState"
Cohesion: 0.10
Nodes (17): OAuthState, OAuth flow manager — handles authorization code + PKCE flows for MCP connectors., Initiate a PKCE OAuth flow. Returns the PKCE parameters and state token., Ephemeral state for an in-progress OAuth flow., Expired OAuth state tokens are rejected., test_oauth_state_expiry(), code_challenge must be URL-safe base64 of SHA-256(code_verifier)., test_oauth_state_alias_fields() (+9 more)

### Community 572 - "RAFTDatasetRecord"
Cohesion: 0.13
Nodes (15): _build_dataset(), _dataset_content_fingerprint(), _export_jsonl(), RAFTDatasetRecord, RAFTExample, _example_record(), _validate_examples(), _compatibility_key_for_fingerprint() (+7 more)

### Community 573 - "test_db_paths_comprehensive.py"
Cohesion: 0.09
Nodes (13): _MockBeginCM, _MockDB, _MockSession, DB-mock tests to cover remaining async DB paths in memory and RAG modules. Uses…, Lines 221-222: limit is respected in DB path., Lines 120: record_async successfully writes to DB session., Line 153: record_failure_async DB write path., Fake SQLAlchemy AsyncSession: execute() returns rows, begin() is a no-op CM. (+5 more)

### Community 574 - "asyncio"
Cohesion: 0.10
Nodes (21): _inverse_confluence_create_page(), _inverse_github_create_issue(), _inverse_jira_create_issue(), _inverse_slack_send_message(), Any, Delete a Jira issue that was created by the forward tool call., Delete a Confluence page that was created by the forward tool call., Delete a Slack message that was sent by the forward tool call. (+13 more)

### Community 575 - "_resolve_checkpointer"
Cohesion: 0.10
Nodes (19): Return the best available checkpointer. Logs a WARNING if falling back to…, _resolve_checkpointer(), Test checkpointer resolution priority and RedisSaver wiring., When REDIS_URL is set, must attempt Redis before falling back to MemorySaver., MemorySaver warning must include LOST or RESTART so operators notice., A sync-only pre-wired saver is rejected (it would crash the async graph)., When no Redis is available, a warning is logged about durability loss., A pre-wired saver that implements the ASYNC checkpoint API is used as-is. The… (+11 more)

### Community 576 - "test_golden_datasets.py"
Cohesion: 0.14
Nodes (15): add_golden_item(), create_golden_dataset(), DatasetCreateRequest, DatasetItemRequest, list_golden_datasets(), promote_goal_to_golden(), BaseModel, get (+7 more)

### Community 577 - "test_goals_extra.py"
Cohesion: 0.11
Nodes (26): _make_app(), Any, FastAPI, Tests for P2 correctness fixes: goal_feedback persistence, RLS on lineage,…, FIX 2: /goals/batch/{ids}/status returns per-goal statuses, not a stub., FIX 2: goals that don't exist are reported as not_found, not an exception., FIX 2: unauthenticated request → 401., FIX 2: the old stub response key 'message' must NOT appear. (+18 more)

### Community 578 - "_make_app"
Cohesion: 0.13
Nodes (9): _make_app(), FastAPI, Extra coverage for app/api/knowledge.py — ingestors, URL ingest, OpenAPI ingest., TestCollectionCrud, TestKnowledgeSearch, TestOpenAPIIngest, TestSemanticCache, TestTextIngest (+1 more)

### Community 579 - "test_tenants_comprehensive2.py"
Cohesion: 0.15
Nodes (26): _make_app(), _make_service(), Any, FastAPI, Extended tests for /tenants API — covers endpoints not in existing…, test_add_ip_allowlist_entry(), test_create_key_success(), test_create_role_no_db() (+18 more)

### Community 580 - "SkillSelector"
Cohesion: 0.12
Nodes (10): Any, Skill Selector ============== Selects the top 1-3 most relevant skills for a…, Selects relevant skills for a goal using keyword matching on trigger_hints.…, Return up to max_skills relevant skills for the goal., Build a compact skill context block to inject into the planner prompt., SelectedSkill, SkillSelector, Tests for Phase 5 (provider registry) and Phase 6 (skills). (+2 more)

### Community 581 - "VoyageProvider"
Cohesion: 0.03
Nodes (50): _AsyncHTTPClient, LocalEmbedProvider, Any, EmbedRequest, Protocol, Local sentence-transformers embedding provider., Voyage embeddings over a cancellable async HTTP transport., VoyageProvider (+42 more)

### Community 582 - "test_api.py"
Cohesion: 0.08
Nodes (8): app(), client(), fixture, Tests for the triggers API router., A type with no runtime dispatch path (google_sheets) must be refused so a…, A supported type (goal_completed → chain consumer) is accepted., test_create_accepts_supported_consumer_type(), test_create_rejects_unsupported_trigger_type()

### Community 583 - "_scheduled_goal_id"
Cohesion: 0.09
Nodes (19): _scheduled_goal_id(), test_scheduled_goal_id_different_keys_produce_different_ids(), test_scheduled_goal_id_is_deterministic(), test_scheduled_goal_id_without_instance_uses_now(), TestScheduledGoalId, Without fire_instance_id, each call produces a different ID (timestamp based)., test_scheduled_goal_id_differs_for_different_inputs(), test_scheduled_goal_id_fire_instance_in_hash() (+11 more)

### Community 584 - "a2a/__init__.py"
Cohesion: 0.10
Nodes (18): AgentEvent, AgentResult, DelegationResult, OrgA2AClient, OrgAsAgent, Any, OrgAsAgent + OrgA2AClient — Q8 of spec. OrgAsAgent: Makes an org callable like…, # TODO: create mission and poll for completion (+10 more)

### Community 585 - "GuardrailEngine"
Cohesion: 0.24
Nodes (8): GuardrailEngine, Orchestrates all six guardrail layers and returns a single GuardrailResult.…, asyncio, GuardrailEngine.evaluate_output must redact PII via Layer 6., End-to-end: nested injection in tool args is caught by Layer 2., evaluate_input is an alias for evaluate_goal., ROT13-encoded injection must be detected as obfuscated., TestGuardrailEngineIntegration

### Community 586 - "MCPWebSocketClient"
Cohesion: 0.12
Nodes (11): MCPWebSocketClient, MCPWebSocketClient — WebSocket transport for real-time MCP tool servers. Used…, Async WebSocket client for MCP connectors that support WS transport. Usage::…, Open WebSocket connection. Returns self for use as async context manager., Close the WebSocket connection., Background task — reads WebSocket messages and routes to waiters., When websockets is not installed, connect() raises RuntimeError., TestMCPWebSocketClient (+3 more)

### Community 587 - "DocumentType"
Cohesion: 0.09
Nodes (9): DocumentClassifier, Document type classifier using keyword and regex scoring., Classify a document type from extracted OCR text using keyword/regex scoring., Return the most likely DocumentType for the given OCR text. Scoring: - +1 per…, DocumentType, StrEnum, classifier(), fixture (+1 more)

### Community 588 - "EntityVersionManager"
Cohesion: 0.11
Nodes (15): EntityVersion, EntityVersionManager, Any, StrEnum, SUPPLEMENT L — Versioning Strategy for all entities. Versioned entities: agents…, Manages versions for all org entities. In production: backed by DB table with…, Create a new version for an entity., Mark a version as deployed. (+7 more)

### Community 589 - "LLMQueryTransformer"
Cohesion: 0.15
Nodes (17): LLMQueryTransformer, Return [rewritten_query] or [original] on failure., Apply all strategies and return a de-duplicated union of queries., Transforms queries using an LLM to improve retrieval recall and precision., Extract non-empty, non-boilerplate lines from LLM output., Return [original_query, step_back_query]., Return [original_query, sub_q1, sub_q2, …] (de-duplicated)., FakeProvider (+9 more)

### Community 590 - "test_audit_scopes_limits.py"
Cohesion: 0.11
Nodes (12): LimitsV2Checker, LimitsV2Config, Any, Limits v2 — Comprehensive Resource Quotas…, Checks all v2 limits with in-process counters + Redis when available., Check if step count is within plan limit., Check if token count is within plan limit., Check per-connector rate limit (in-process fallback). (+4 more)

### Community 591 - "WorkflowVariableStore"
Cohesion: 0.15
Nodes (21): Any, WorkflowVariableStore — mutable workflow variables. Variables are distinct from…, Manages mutable vars in WorkflowState., Return a state update dict that sets the variable., Read a variable from state., Return all variables., WorkflowVariableStore, fixture (+13 more)

### Community 592 - "test_rag_migration_roundtrip.py"
Cohesion: 0.14
Nodes (23): CompletedProcess, _alembic(), _failed_owner_migration_state(), isolated_postgres(), owner_migration_postgres(), _owner_migration_state(), _owner_url(), fixture (+15 more)

### Community 593 - "test_perception_api.py"
Cohesion: 0.18
Nodes (25): app(), authed_client(), AsyncClient, asyncio, FastAPI, fixture, API-level tests for perception endpoints., analyze with a screenshot_b64 returns analysis shape. (+17 more)

### Community 594 - "workflow/test_context.py"
Cohesion: 0.08
Nodes (4): fixture, Tests for ContextResolver — all {{...}} variable resolution., resolver(), state()

### Community 595 - "test_codeact.py"
Cohesion: 0.12
Nodes (20): CodeActionState, CodeActPhase, CodeActRuntime, CodeActState, Any, BaseModel, StrEnum, CodeLanguage (+12 more)

### Community 596 - "TenantScopedStore"
Cohesion: 0.15
Nodes (7): Any, Tenant-isolated Redis interface. Wraps any redis.asyncio.Redis-compatible…, Execute a Lua script, prefixing the first *numkeys* positional arguments (the…, TenantScopedStore, Integration test: three-layer tenant isolation (Postgres + Redis + app layer).…, Tenant A's resources are invisible to Tenant B at every isolation layer., test_tenant_isolation_across_all_three_layers()

### Community 597 - "skills.py"
Cohesion: 0.12
Nodes (16): create_skill(), delete_skill(), list_skills(), _platform_skill_to_response(), Any, BaseModel, delete, get (+8 more)

### Community 598 - "SCIMHandler"
Cohesion: 0.13
Nodes (17): _db_row_to_scim_user(), Any, SCIM 2.0 user/group provisioning handler (RFC 7644). Handles automated user…, SCIM 2.0 user/group provisioning. Constructed per-request with tenant_id…, List tenant users in SCIM ListResponse format., Get a single user by SCIM external ID or internal DB id., Create a user from SCIM payload. Maps group memberships to roles via…, Update a user (PUT = full replacement, PATCH = Operations list). (+9 more)

### Community 599 - "ChatSearchEngine"
Cohesion: 0.13
Nodes (19): ChatSearchEngine, Full-text search across chat sessions using in-memory substring matching.…, Substring search over in-memory message store. In production this calls: SELECT…, Return messages matching *query* across all sessions for *tenant_id*., Return messages matching *query* within *session_id*., Return a short snippet with the query term highlighted., SearchResult, engine() (+11 more)

### Community 600 - "test_collaboration_runtime.py"
Cohesion: 0.14
Nodes (14): ClarificationEngine, ClarificationRequest, HumanDecisionTrace, Any, MissingInputEngine, MissingInputRequest, PreferenceCapture, PreferenceOption (+6 more)

### Community 601 - "test_tool_risk.py"
Cohesion: 0.10
Nodes (20): Tests for the comprehensive tool risk classifier., Unrecognised tools must default to 'read' (safe)., test_confluence_get_page_is_read(), test_confluence_publish_page_is_write_high(), test_datadog_get_metrics_is_read(), test_db_drop_table_is_destructive(), test_empty_names_default_to_read(), test_generic_purge_is_destructive() (+12 more)

### Community 602 - "test_policy_propagation.py"
Cohesion: 0.19
Nodes (13): asyncio, Tests for PolicyEngine cross-replica propagation via Redis pub/sub., reload_from_db(tenant_id=X) removes only X's policies and re-loads from DB., publish_change publishes JSON message to policy_changes channel., publish_change with redis=None does not raise., start_policy_subscriber returns an asyncio.Task., governance.py must import PolicyEngine for publish_change., test_governance_api_imports_policy_engine() (+5 more)

### Community 603 - "ToxicityClassifier"
Cohesion: 0.12
Nodes (8): Two-pass toxicity classifier. Pass 1: fast regex patterns → definitive for…, Pattern-only classification (no LLM, always sync-safe)., Full two-pass classification., ToxicityClassifier, ToxicityResult, TestToxicityClassifier, Tests for NLI checker, claim decomposer, streaming guard, toxicity classifier., TestToxicityClassifier

### Community 604 - "check_and_process_emails"
Cohesion: 0.07
Nodes (27): Send an HTML email with clickable Approve/Reject buttons. Returns True on…, send_approval_email(), check_and_process_emails(), _decode_header_value(), _get_config(), _is_enabled(), Any, Email-to-goal: monitor an IMAP mailbox and convert emails to AgentVerse goals.… (+19 more)

### Community 605 - "test_project_management_connectors.py"
Cohesion: 0.05
Nodes (34): _basecamp_headers(), call_tool(), _call_tool_inner(), Any, Basecamp MCP server — Basecamp 3 REST API integration. Environment variables:…, call_tool(), _call_tool_inner(), _clickup_headers() (+26 more)

### Community 606 - "select_adaptive_strategy"
Cohesion: 0.09
Nodes (34): AdaptiveDecision, Bounded capability-aware Adaptive RAG selection., Make exactly one non-recursive decision from certified capabilities., select_adaptive_strategy(), boost_symbol_matches(), extract_code_symbols(), has_code_intent(), RetrievalResult (+26 more)

### Community 607 - "SearchDirectiveParser"
Cohesion: 0.09
Nodes (10): SearchDirectiveParser — parses [SEARCH:type:"query"] directives from plan steps., SearchDirective, SearchDirectiveParser, SearchDirectiveParser must extract directives from step descriptions., test_search_directive_maps_to_retrieval_strategy(), test_search_directive_parsed_from_step(), test_search_directive_web_source(), parser() (+2 more)

### Community 608 - "RetrievalEvaluator"
Cohesion: 0.13
Nodes (18): EvalReport, Any, QueryEvalResult, RAGAS-inspired retrieval evaluation for AgentVerse knowledge collections.…, Run evaluation on a collection. Args: collection_id: Target knowledge…, Evaluate a single query against the collection., Generate human-readable improvement recommendations., Evaluate retrieval quality of a knowledge collection. Usage:: evaluator =… (+10 more)

### Community 609 - "test_knowledge_api.py"
Cohesion: 0.17
Nodes (19): _KnowledgeGateway, _make_app(), FastAPI, Tests for /knowledge API endpoints., POST /knowledge/ingest/file accepts plain text files., POST /knowledge/ingest/openapi creates chunks per endpoint., test_cache_stats(), test_clear_cache() (+11 more)

### Community 610 - "test_router_versions.py"
Cohesion: 0.09
Nodes (7): PlanLimits, client(), make_app(), fixture, Tests for workflow version history + publishing approval router., test_list_versions_service_unavailable(), ver_service()

### Community 611 - "PostgresWorkflowRunStore"
Cohesion: 0.06
Nodes (32): _as_obj(), _as_str(), _duration_ms(), _iso(), PostgresWorkflowRunStore, Any, Protocol, WorkflowRunStore — persistence for workflow runs and step results. Two… (+24 more)

### Community 612 - "_make_app"
Cohesion: 0.13
Nodes (25): aiter(), _default_compliance(), _default_marketplace(), _default_red_team(), _default_simulation(), _make_app(), Any, Lines 520-530: template exists, no version history → returns current version. (+17 more)

### Community 613 - "asyncio"
Cohesion: 0.02
Nodes (120): add_golden_task(), check_agent_rollout_gate(), EvalSuiteResult, EvalSuiteRunner, get_golden_tasks(), GoldenTask, GoldenTaskResult, LLMJudge (+112 more)

### Community 614 - "test_rpa_comprehensive.py"
Cohesion: 0.15
Nodes (24): _make_app(), _make_session(), FastAPI, Comprehensive tests for /rpa API endpoints — targets 29% → 65%+ coverage., Closing a session when no store exists returns 204 (graceful no-op)., TenantMiddleware requires auth for all routes., test_close_session_no_store(), test_close_session_not_found() (+16 more)

### Community 615 - "test_self_optimizer_v2_comprehensive.py"
Cohesion: 0.25
Nodes (22): _make_db_session(), _make_optimizer(), _make_redis(), asyncio, Comprehensive tests for SelfOptimizerV2 — TenantOptimizationState, Bayesian…, test_apply_suggestion_db_error_returns_false(), test_apply_suggestion_success(), test_get_arm_deterministic() (+14 more)

### Community 616 - "test_ws_client_comprehensive.py"
Cohesion: 0.18
Nodes (23): _make_ws_mock(), asyncio, Comprehensive tests for MCPWebSocketClient — connect, disconnect, call_tool,…, Create a mock websocket that can be iterated and sent to., test_call_tool_increments_msg_id(), test_call_tool_sends_message(), test_call_tool_timeout_cleans_up_pending(), test_call_tool_timeout_raises() (+15 more)

### Community 617 - "CodeExecutionObservation"
Cohesion: 0.17
Nodes (17): ProgramOfThoughtPhase, ProgramOfThoughtRuntime, ProgramOfThoughtState, Any, BaseModel, StrEnum, Single-program governed Program-of-Thought strategy., CodeExecutionObservation (+9 more)

### Community 618 - "AnswerSynthesizer"
Cohesion: 0.15
Nodes (14): AnswerSynthesizer, Citation, CitedAnswer, Any, Citation-Carrying Answer Synthesis ==================================== After a…, Deterministic synthesis without LLM., A single citation linking a claim to its evidence source., Final synthesized answer with citations. (+6 more)

### Community 619 - "coordination_moa.py"
Cohesion: 0.36
Nodes (9): get_layer(), _layer_public(), list_layers(), Any, get, Request, Safe tenant-scoped Mixture-of-Agents layer explanations., _repository() (+1 more)

### Community 620 - "api/guardrails.py"
Cohesion: 0.17
Nodes (22): create_guardrail_config(), CreateGuardrailConfigRequest, delete_guardrail_config(), _get_engine(), guardrail_stats(), list_guardrail_configs(), list_violations(), Any (+14 more)

### Community 621 - "marketplace_monetization.py"
Cohesion: 0.16
Nodes (21): onboard_author(), OnboardAuthorRequest, PricingRequest, purchase_template(), Any, BaseModel, Request, Marketplace monetization — paid templates, Stripe Connect, author payouts. (+13 more)

### Community 622 - "CustomRoleStore"
Cohesion: 0.12
Nodes (9): CustomRole, CustomRoleStore, Custom Role System v2 ====================== Tenants can define their own roles…, A tenant-defined role with custom scope combination., Compute effective scope set (inherited + extra - denied)., Per-tenant custom role definitions., Resolve scopes for a role name — built-in or custom., Check if a role has a specific scope. (+1 more)

### Community 623 - "ServicesAPI"
Cohesion: 0.13
Nodes (17): ConnectedService, _hex(), _now(), datetime, Connected services panel — REST layer over MCPRegistry. Provides endpoints to…, In-memory store backing the connected services REST layer. Production delegates…, Register a service and return an OAuth setup URL., ServicesAPI (+9 more)

### Community 624 - "test_worker_entrypoint.py"
Cohesion: 0.12
Nodes (23): _build_provider(), Construct the best available LLM provider for the given role. Supports…, Tests for worker_entrypoint — the subprocess execution entry point. Tests…, A tampered envelope (goal_text changed after signing) must be rejected., A valid signed dry-run envelope must emit goal_complete and return 0., Dry-run must short-circuit before the execution graph is constructed., Each role must get its own instance to prevent cross-role state sharing., sk-ant- prefix → try AnthropicProvider (may fail if not installed; falls back). (+15 more)

### Community 625 - "CommandScheduler"
Cohesion: 0.10
Nodes (12): CommandDeduplicator, CommandScheduler, Any, Command deduplication + scheduling — QA8 + QA9 of spec. CommandDeduplicator…, A command scheduled for future execution. Created by users via any channel with…, Manages scheduled commands (QA9). In production: backed by DB + Celery Beat.…, Store a scheduled command for future execution., Cancel a scheduled command. (+4 more)

### Community 626 - "TestSemanticCacheGetSet"
Cohesion: 0.13
Nodes (12): asyncio, Covers lines 90-102: embedding provided, no Redis → local dict., Covers lines 108-128: set stores in local dict when no Redis., Different tenant should not see another's cached result., Covers lines 93-99, 117-124: Redis-backed get/set., When Redis returns None, falls through to local dict., When Redis raises, falls through to local dict without raising., When Redis set raises, falls through to store in local dict. (+4 more)

### Community 627 - "GoalRefinementPipeline"
Cohesion: 0.13
Nodes (11): GoalRefinementPipeline, Any, part 11 — Goal Refinement Pipeline. CEO Agent refines a raw user goal into a…, Synchronous goal refinement using heuristics. For LLM-assisted refinement, use…, Raise ValueError if goal contains injection patterns., Remove leading/trailing whitespace, collapse multiple spaces., Heuristic: split on conjunctions and numbered items., Generate measurable success criteria from requirements. (+3 more)

### Community 628 - "SubAgentTask"
Cohesion: 0.15
Nodes (19): SubAgentTask, test_sub_agent_task_custom_goal(), test_sub_agent_task_defaults(), asyncio, Tests for SupervisorAgent multi-agent pattern., When all tasks failed, _synthesize returns without calling the LLM., When the LLM call raises, _synthesize falls back to structured text., When LLM decompose fails, falls back to single task. (+11 more)

### Community 629 - "calibrate_scores"
Cohesion: 0.16
Nodes (22): calibrate_scores(), _logistic(), _minmax(), CalibrationMethod, Probability calibration for retrieval and rerank scores. Retrieval and rerank…, Aggregate a retrieval set's scores into one calibrated confidence. Defaults to…, Map raw retrieval/rerank scores onto calibrated ``[0, 1]`` confidences. The…, retrieval_confidence() (+14 more)

### Community 630 - "test_celery_agentgraph.py"
Cohesion: 0.10
Nodes (26): _load_worker_policy_engine(), _FakeAgentState, Any, Tests: Celery run_goal task creates AgentGraph for goal execution., Minimal AgentState stub returned by the mock runner., Configured agents fail closed when canonical graph assembly fails., AgentGraph should be constructed with result_processor, dedup_cache,…, The eager Celery path calls the canonical worker gateway from AgentGraph. (+18 more)

### Community 631 - "build_result_artifact"
Cohesion: 0.16
Nodes (22): _artifact_status(), build_result_artifact(), _coerce_output(), _jira_rows(), Any, _tool_name(), tool_output raw dict takes priority over the sanitized output string., Backward compat: if tool_output not present, parse output dict as before. (+14 more)

### Community 632 - "SubTenantService"
Cohesion: 0.11
Nodes (16): Any, Base, QA4 — Sub-Tenants (Enterprise Hierarchy). Enterprise orgs need hierarchy: Acme…, QA4 — Sub-tenant management service. Production: backed by `sub_tenants` DB…, POST /v1/tenants/{id}/sub-tenants, GET /v1/tenants/{id}/sub-tenants, PATCH /v1/sub-tenants/{id}/budget, GET /v1/tenants/{id}/hierarchy — full tree view (+8 more)

### Community 633 - "test_safe_web_capability.py"
Cohesion: 0.24
Nodes (19): _build_capability(), _Policy, Any, parametrize, Security and production behavior for governed web retrieval., _request(), _searx_response(), test_governed_capability_bounds_rejection_audit_records() (+11 more)

### Community 634 - "test_civilization_api_comprehensive2.py"
Cohesion: 0.16
Nodes (23): _make_app(), FastAPI, Extended tests for /civilizations API — targets 31% → 75%+ coverage.…, test_control_pause(), test_control_resume(), test_control_throttle(), test_create_civilization_disabled(), test_create_civilization_no_db_graceful() (+15 more)

### Community 635 - "test_connectors_catalog.py"
Cohesion: 0.12
Nodes (16): FakeRedis, _make_app(), asyncio, Tests for catalog endpoint and auto-wiring., Test endpoint uses connector credentials and returns passed on success., Test endpoint returns failed when Jira returns 401., Unknown connector type uses generic GET fallback., test_catalog_is_configured_false_initially() (+8 more)

### Community 636 - "test_knowledge_comprehensive.py"
Cohesion: 0.15
Nodes (23): _make_app(), Any, FastAPI, Comprehensive tests for /knowledge API endpoints — targets 21% → 55%+ coverage., Mock embedder that returns a fake embedding., _sample_embedder(), test_create_collection_list_returns_it(), test_create_collection_requires_auth() (+15 more)

### Community 637 - "test_phase10_11_ai_ops_memory.py"
Cohesion: 0.16
Nodes (23): _make_app(), Phase 10+11: AI Ops (Evals/Drift) + Agent Memory 2.0 tests., test_conflict_detection(), test_create_eval_dataset(), test_create_llm_judge(), test_create_memory_with_provenance(), test_drift_within_threshold_is_info(), test_eval_result_persisted() (+15 more)

### Community 638 - "test_workflows.py"
Cohesion: 0.13
Nodes (26): _make_app(), FastAPI, Tests for /workflows endpoints — CRUD + run + tenant isolation., No GoalService on app.state → returns dry_run, does not crash., Build a minimal FastAPI app with the workflows router and in-memory store., Verify the generated goal string includes the workflow name., Tenant A cannot read, update, delete, or run Tenant B's workflows., test_create_workflow_defaults_empty_definition() (+18 more)

### Community 639 - "CorpusSample"
Cohesion: 0.16
Nodes (15): CorpusSample, GuardrailEffectivenessReport, GuardrailTuner, Any, GuardrailTuner — corpus-driven effectiveness analysis for guardrail rules. Uses…, One labelled example. ``should_block`` is the ground truth., _FakeEngine, Any (+7 more)

### Community 640 - "TestUniversalArgumentResolver"
Cohesion: 0.07
Nodes (3): LLM uses wrong key names for ALL params., LLM uses wrong key names for ALL required params., TestUniversalArgumentResolver

### Community 641 - "TestAgentStore"
Cohesion: 0.12
Nodes (8): Persist an agent snapshot to the agent_snapshots table WITH RLS context., _save_snapshot_to_db(), Lines 28-83: _save_snapshot_to_db and _load_snapshots_from_db with db=None., test_save_and_load_snapshots_no_db(), asyncio, TestAgentStore, TestLoadSnapshotsFromDb, TestSaveSnapshotToDb

### Community 642 - "api/tools.py"
Cohesion: 0.15
Nodes (22): delete_file(), execute_code(), ExecuteCodeRequest, ExecuteCodeResponse, FileWriteRequest, list_files(), Any, BaseModel (+14 more)

### Community 643 - "trust_governance.py"
Cohesion: 0.19
Nodes (22): approve_request(), export_audit_evidence(), get_audit_integrity(), list_approvals(), list_compliance_bundles(), Any, get, Request (+14 more)

### Community 644 - "RuntimeProfilesRegistry"
Cohesion: 0.12
Nodes (15): get_runtime_profiles_registry(), Any, RuntimeProfilesRegistry — Layer 0 registry for all active GoalRuntimeProfiles.…, In-memory registry of active GoalRuntimeProfiles. In production this is a thin…, Register a GoalRuntimeProfile for a goal., Retrieve a registered profile. Returns None if not found., List all active profiles for a tenant., Remove a profile when goal completes. (+7 more)

### Community 645 - "gateway/router.py"
Cohesion: 0.17
Nodes (22): CommandStreamResponse, GatewayConfig, generic_webhook(), get_channel_status(), get_config(), _process_command(), Any, BackgroundTasks (+14 more)

### Community 646 - "SharePointConnector"
Cohesion: 0.15
Nodes (11): Any, Get metadata for a specific SharePoint site., List files in a SharePoint document library. Parameters ---------- site_id :…, Recursively list all files in a drive., Download file content as a UTF-8 string. Parameters ---------- site_id : str…, Read files from SharePoint sites and OneDrive via Microsoft Graph API.…, List all document libraries (drives) for a site., Return metadata for a single file. (+3 more)

### Community 647 - ".run"
Cohesion: 0.13
Nodes (9): Any, Choose the next retry strategy based on failure history., Exponential backoff with jitter: base * 2^attempt ± 20% jitter., Enrich the goal prompt with strategy hints for the planner., Write attempt start record to DB. Returns attempt record ID., Update attempt record with completion data., Run goal with persistence until success, escalation, or exhaustion. Args: goal:…, Derive retry ceilings and strategy identity from the admitted profile. (+1 more)

### Community 648 - "GuardrailViolation"
Cohesion: 0.13
Nodes (10): GuardrailResult, GuardrailViolation, Any, Scan plain text for injection patterns., FIX: Correctly decodes ROT13 first, then scans BOTH forms. The original…, Return (violations, redacted_text). redacted_text has PII replaced with…, Layer 4 — recursively scan tool call arguments before execution., Layer 5 — scan tool output for PII/secrets before passing to agent. (+2 more)

### Community 649 - "test_ingestion_pipeline.py"
Cohesion: 0.11
Nodes (18): DocxIngestor, Any, PdfIngestor, Any, Extract text chunks from PDF files with page-level citation metadata., Tests for the knowledge ingestion pipeline — Phase P0.2., hybrid_search_db must return source_url, source_doc_id, page_number., test_docx_ingestor_graceful_fallback() (+10 more)

### Community 650 - "ModelRouter"
Cohesion: 0.14
Nodes (10): Criticality, get_model_router(), ModelRouter, ModelSelection, Intelligent model router: task_type + criticality → best model + provider.…, Select the best model for a given task type and criticality level. Decision…, Select the best model for *task_type* at *criticality* level. Args: task_type:…, Return the module-level singleton ModelRouter. (+2 more)

### Community 651 - "test_hitl_new_endpoints.py"
Cohesion: 0.15
Nodes (22): client(), _make_app(), Any, AsyncClient, asyncio, FastAPI, fixture, Tests for new HITL endpoints from hitl-gap-analysis.md. Tests cover: G-04: GET… (+14 more)

### Community 652 - "WaitStepNode"
Cohesion: 0.15
Nodes (11): Any, Subscribe to ``channel`` and return the first event payload, or None on…, WaitStepNode, _FakePubSub, _FakeRedis, Any, 2.W-8: the ``wait`` step's event-channel path must actually subscribe to Redis…, test_test_run_short_circuits_without_redis() (+3 more)

### Community 653 - "api/test_collab.py"
Cohesion: 0.15
Nodes (15): FakeCollabStore, _make_app(), Any, asyncio, FastAPI, Tests for collaboration endpoints., Verify presence session creation via the full app stack., test_consensus_rounds_return_agreement_summary() (+7 more)

### Community 654 - "test_tools_api_comprehensive.py"
Cohesion: 0.17
Nodes (22): _make_app(), FastAPI, Comprehensive tests for /tools API endpoints — targets 41% → 70%+ coverage., test_delete_file_not_found(), test_delete_file_requires_auth(), test_delete_file_success(), test_execute_code_javascript(), test_execute_code_requires_auth() (+14 more)

### Community 655 - "agent/test_persistence.py"
Cohesion: 0.19
Nodes (18): make_config(), asyncio, Tests for GoalPersistenceEngine — agent retry and persistence logic., Agent fails once then succeeds., Agent always fails — should exhaust max_attempts., test_backoff_capped_at_max(), test_backoff_increases_with_attempts(), test_consecutive_failures_counts_trailing() (+10 more)

### Community 656 - "observability.py"
Cohesion: 0.16
Nodes (18): _evt_to_message(), get_structured_metrics(), get_timeseries(), list_logs(), Any, get, Request, StreamingResponse (+10 more)

### Community 657 - "ChatCodeExecutor"
Cohesion: 0.15
Nodes (18): ChatCodeExecutor, ExecutionResult, Inline code execution sandbox for chat sessions. Wraps the existing…, Execute short code snippets inside a sandbox. In dev/test mode uses subprocess.…, Run *code* synchronously and return the result., executor(), fixture, Tests for inline code execution — 10 cases. (+10 more)

### Community 658 - "test_production_safety.py"
Cohesion: 0.12
Nodes (18): _get_slack_tenant_id(), Tests that production safety guards are in place., get_inverse_fn returns no-op lambda for unknown tools., RedisCostController is importable., Slack uses env var SLACK_TENANT_ID, not hardcoded string., SimulationRunner class is defined exactly once (no duplicate)., CostController accepts Redis client for cross-replica cost tracking., MinIO store warns when using default minioadmin credentials in dev. (+10 more)

### Community 659 - "execution_environment/models.py"
Cohesion: 0.07
Nodes (51): _make_session_factory(), async_sessionmaker, _canonical_bytes(), envelope_from_dict(), Envelope builder and HMAC integrity verification. The control plane signs the…, Return stable bytes over ALL security-relevant envelope fields. Includes:…, Reconstruct a signed envelope at the worker trust boundary., Isolated Agent Execution Environment. This package implements a containment and… (+43 more)

### Community 660 - "ContentDeduplicator"
Cohesion: 0.14
Nodes (9): ContentDeduplicator, DeduplicationResult, QualityChecker — validates chunks before ingestion., Session-scoped chunk deduplicator using SHA-256 content hashes. Eliminates…, Return the SHA-256 hex digest of the normalised chunk content., Filter *chunks* to only those whose hash has not been seen before., Return True if this exact content has already been processed., Tests for ContentDeduplicator in quality_checks. (+1 more)

### Community 661 - "CommunityDetector"
Cohesion: 0.22
Nodes (7): CommunityDetector, Any, BFS/Union-Find community detection for the Knowledge Graph., BFS-based connected-components community detection for the KG. Uses Union-Find…, Return the community_id for a given node_id, or None if not found., Sort communities by size descending, return ranked list., Return list of community dicts. Each dict has: community_id - UUID string…

### Community 662 - "ConfluenceIngestor"
Cohesion: 0.14
Nodes (8): ConfluenceIngestor, _html_to_text(), Any, Confluence Cloud/Server page ingestor via REST API v1., Strip HTML tags, decode entities, normalize whitespace., test_confluence_html_to_text(), TestConfluenceIngestor, TestConfluenceIngestor

### Community 663 - "run_mocked_certification"
Cohesion: 0.19
Nodes (16): ConnectorTarget, TypedDict, Any, _result(), run_mocked_certification(), run_static_certification(), _unknown_connector_result(), asyncio (+8 more)

### Community 664 - "test_store_comprehensive3.py"
Cohesion: 0.39
Nodes (19): _ctx(), _db_factory(), _full_db_session(), _make_session_row(), _mock_rls(), asyncio, Additional tests for collab/store.py — DB paths, _operation_to_dict,…, content_update op also updates the session content column. (+11 more)

### Community 665 - "OrgSimulationEngine"
Cohesion: 0.15
Nodes (17): ChaosResult, LoopPattern, MissionEstimate, OrgSimulationEngine, OrgLoopDetector + OrgSimulationEngine — SUPPLEMENT G + I. OrgLoopDetector:…, Pre-flight simulation for missions. Uses historical data + agent reputations to…, Estimate resources and risk for a mission goal., Full simulation using historical data and agent reputations. (+9 more)

### Community 666 - "test_context_manager.py"
Cohesion: 0.12
Nodes (10): ContextBudgetManager, ManagedContext, Any, ContextBudgetManager — enhanced token budget with step-relevance reranking.…, Per-step context manager: dedup + token cap + relevance re-ranking., chunks(), manager(), fixture (+2 more)

### Community 667 - "rls.py"
Cohesion: 0.05
Nodes (27): DatabaseHandoffMembership, DatabaseSessionAuthorizer, InMemorySessionAuthorizer, Any, Fail-closed tenant-scoped civilization membership authorization., Concurrency-safe append-only progress-ledger repository., _domain_column(), Any (+19 more)

### Community 668 - "VoiceAlertManager"
Cohesion: 0.13
Nodes (12): build_alert_text(), publish_voice_alert(), Any, D-6: Proactive Voice Alerts — push TTS audio when important events happen.…, Publish a voice alert to the tenant's pub/sub channel. Call this from anywhere…, Render a spoken alert text from event type + context., Listens to Redis pub/sub and synthesises TTS for proactive alerts. D-6:…, Start the pub/sub listener loop. (+4 more)

### Community 669 - "agent/test_router.py"
Cohesion: 0.16
Nodes (21): _add_agent(), _make_store(), Tests for AgentRouter — intent-based agent routing., all_scores must contain exactly one entry per registered agent., Agents registered under tenant-b must not appear when routing for tenant-a., Agent whose goal_template overlaps with the goal text should be selected., When multiple agents are registered, the highest-scoring one wins., _score_by_history must return 0.0 when no eval_store is configured. (+13 more)

### Community 670 - "test_swarm_gossip.py"
Cohesion: 0.22
Nodes (15): GossipRouter, datetime, Deduplicating, credentialed, TTL-limited gossip router., GossipMessage, Typed swarm gossip and fenced claim contracts., test_gossip_deduplicates_and_decrements_hops(), _message(), Behavioral tests for the deduplicating, credentialed, TTL-limited gossip router. (+7 more)

### Community 671 - "GraphNode"
Cohesion: 0.06
Nodes (38): Extract relationships between entities using LLM., Extract entities using deterministic pattern matching., Extract entities using LLM for higher quality., GraphEdge, GraphNode, A node in the knowledge graph., A directed edge between two graph nodes., Persist a node to DB (best-effort, upsert). (+30 more)

### Community 672 - "test_workflows_comprehensive2.py"
Cohesion: 0.26
Nodes (21): _create_workflow(), _make_app(), Any, FastAPI, TestClient, Extended tests for /workflows API — covers additional paths for 67% → 85%+.…, test_create_workflow_description_defaults_to_empty(), test_create_workflow_name_exceeds_max_returns_422() (+13 more)

### Community 673 - "test_notification_service.py"
Cohesion: 0.19
Nodes (21): _make_service(), anyio, mock, Tests for app/services/notification_service.py — 8 tests using respx., Disabled channels do not receive notifications., A channel that raises must not prevent other channels from being notified., _send posts the full message JSON to teams URL., notify_approval_required posts to the Slack webhook URL. (+13 more)

### Community 674 - "test_tenant_service_db.py"
Cohesion: 0.12
Nodes (15): Tests for TenantService DB persistence (no-op when db_session_factory=None)., DB persistence is attempted but doesn't break when factory raises in __aenter__., The dynamic resolver reads from app.state, not a captured closure., TenantService() without args has self._db == None., TenantService(db_session_factory=...) stores the factory., revoke_api_key still works (raises on bad key) with no DB factory., test_create_tenant_fires_background_db_task(), test_create_tenant_persists_tenant_and_default_key_before_return() (+7 more)

### Community 675 - "test_api_extended.py"
Cohesion: 0.12
Nodes (13): app(), client(), create_trigger(), fixture, Tests for extended trigger API endpoints — PATCH, rotate-secret, validate-…, Create one of each major type and verify all appear in list., test_list_all_trigger_types(), test_patch_goal_template() (+5 more)

### Community 676 - "test_jira_agent_execution.py"
Cohesion: 0.21
Nodes (17): DeterministicJiraGoalService, FakeTenantService, _make_test_client(), _mock_jira_mcp(), _mock_jira_mcp_multi_tool(), Any, Request, TestClient (+9 more)

### Community 677 - "insights.py"
Cohesion: 0.20
Nodes (20): analyze_failure(), estimate_goal(), EstimateRequest, get_agent_health(), get_benchmarks(), get_execution_graph(), natural_language_query(), NLQueryRequest (+12 more)

### Community 678 - "AgentCredentialStore"
Cohesion: 0.11
Nodes (12): AgentCredentialStore, generate_agent_api_key(), is_agent_key(), Any, Per-Agent Credential System ============================ Every agent can have…, Check if this key's policy allows the given tool., Generate (raw_key, key_hash) for an agent-scoped API key., Return True if this key is an agent-scoped key. (+4 more)

### Community 679 - "test_agent_identity_layer.py"
Cohesion: 0.15
Nodes (10): DelegationChain, DelegationLink, Any, Agent Delegation Lineage ========================= When agent A spawns agent B…, Create a new chain for a spawned sub-agent., One link in the delegation chain., The full chain from root user/system to the current executing agent., Human-readable: User:alice → Agent:CEO → Agent:Dev (+2 more)

### Community 680 - "ConversationContext"
Cohesion: 0.10
Nodes (12): ConversationContext, Any, ConversationContext — builds LLM context from chat history. Handles: - Last 20…, Prepend session system_prompt before all other turns., Prepend uploaded file content as a system turn., Inject top-5 codebase snippets as a system turn., Build LLM message lists from stored chat history., Return last MAX_TURNS messages as OpenAI-style message list. (+4 more)

### Community 681 - "test_router_hitl.py"
Cohesion: 0.12
Nodes (7): client(), gateway(), make_app(), fixture, Tests for workflow HITL router (approval inbox, decide, delegate, escalate)., _req(), test_magic_link_valid()

### Community 682 - "EmbeddingRouter"
Cohesion: 0.13
Nodes (10): EmbeddingConfig, EmbeddingRouter, Any, Embedding Router - vendor-agnostic embedding with fallbacks., Return embedding usage and error metrics for drift monitoring., Configuration for an embedding provider/model., Route embedding requests to the correct provider with fallback., Validate that the embedding dimension matches the collection's configured… (+2 more)

### Community 683 - "NotionConnector"
Cohesion: 0.16
Nodes (9): NotionConnector, Any, Thin async wrapper around the Notion REST API v1., Query a Notion database and return all page objects., Return the plain-text content of a Notion page by fetching its blocks., Return the page object (properties, created_time, url, etc.)., Search for all pages accessible to the integration token., Tests for new ingestion connectors and parsers. (+1 more)

### Community 684 - "scan_for_encoding_attacks"
Cohesion: 0.15
Nodes (12): decode_leetspeak(), normalize_homoglyphs(), Any, Encoding Attack Decoder ======================== Detects and blocks injection…, Replace homoglyphs with ASCII equivalents., Translate leetspeak to plain text., Try to decode potential base64-encoded content., Comprehensive encoding attack scan. Returns dict with: clean (bool),… (+4 more)

### Community 685 - "DecisionTrace"
Cohesion: 0.17
Nodes (5): DecisionTrace, Any, Explainability — DecisionTrace captures why an action was taken. Every tool…, Comprehensive tests for app/intelligence/explainability.py — targeting 100%…, TestDecisionTrace

### Community 686 - "test_agents_api.py"
Cohesion: 0.16
Nodes (18): MetaAgentConfig, _make_app(), Any, FastAPI, Tests for /agents API endpoints., test_agents_require_auth(), test_create_agent(), test_create_agent_surfaces_db_persistence_failure() (+10 more)

### Community 687 - "test_ingestors_coverage.py"
Cohesion: 0.10
Nodes (4): DOCX document ingestor using python-docx., PDF document ingestor using pypdf (open-source, no cloud dependencies)., Comprehensive coverage for all app/knowledge/ingestors/*. Mocks all external…, TestGitHubShouldIngest

### Community 688 - "OcrDocumentTool"
Cohesion: 0.20
Nodes (15): OcrDocumentTool, Any, Extract text and structured fields from any document image or PDF. Accepts one…, Resolve input to (image_bytes, pdf_bytes). Raises ValueError if invalid., _make_result(), asyncio, Tests for OcrDocumentTool., test_execute_raises_on_empty_input() (+7 more)

### Community 689 - "test_polling.py"
Cohesion: 0.16
Nodes (19): extract_path(), fetch_json(), poll_should_fire(), Any, HTTP polling helpers for the API_POLL trigger (2.W-1). The beat loop polls…, Resolve a minimal dotted JSONPath against a decoded JSON object. Supports…, Fire when the polled value changed since the last dispatch and — when an…, Fetch a JSON endpoint. Raises on transport/HTTP/JSON error (caller logs). The… (+11 more)

### Community 690 - "test_phase_completeness.py"
Cohesion: 0.09
Nodes (23): asyncio, Phase completeness tests — verify all 6 PARTIAL phases are now COMPLETE. These…, Celery tasks must poll pause/cancel signals during execution., _make_agent_loop_for_tenant must wire circuit breakers to AgentGraph., _make_agent_loop_for_tenant must wire graph._self_optimizer., rotate_key must actually re-encrypt secrets (not just update metadata)., TypeScript SDK must have HITL approve/reject methods., TypeScript SDK must have SimulationResult and GoalTimeline types. (+15 more)

### Community 691 - "RedisCircuitBreaker"
Cohesion: 0.12
Nodes (9): Any, Record a failure. Opens the circuit once ``failure_threshold`` is reached., Record a success — resets the circuit to CLOSED and clears all counters., Circuit breaker backed by Redis for cross-replica state sharing. Args:…, Return the current circuit state from Redis., Return True if a call is allowed now (checks Redis state). Handles the OPEN →…, RedisCircuitBreaker, test_redis_circuit_breaker_different_tools_are_isolated() (+1 more)

### Community 692 - "decision_store.py"
Cohesion: 0.16
Nodes (11): Base, ORM rows for canonical routing decisions and outcomes., RoutingDecisionRow, RoutingOutcomeRow, _decision(), PostgresDecisionStore, Any, async_sessionmaker (+3 more)

### Community 693 - "test_optimistic_concurrency.py"
Cohesion: 0.12
Nodes (18): asyncio, Tests for optimistic concurrency control in CollaborationStore., CollaborationStore must NOT use SELECT ... FOR UPDATE (pessimistic lock)., VersionConflictError must be defined in collab.store., VersionConflictError must be importable and raised on conflict., append_operation must accept expected_version parameter., API endpoint returns 409 on VersionConflictError., In-memory mode raises VersionConflictError when expected_version is wrong. (+10 more)

### Community 694 - "test_replay_comprehensive.py"
Cohesion: 0.14
Nodes (32): _get_db(), goal_timeline(), Any, get, Request, Goal execution replay API. Provides step-by-step reconstruction of a completed…, Get a compact chronological timeline of goal events for visualization., Reconstruct the full execution timeline of a completed goal. Returns a… (+24 more)

### Community 695 - "_AllowingFakeRedis"
Cohesion: 0.12
Nodes (11): _AllowingFakeRedis, _BlockingFakeRedis, _make_app(), Fake Redis whose zcard always exceeds any limit → always rate-limits., Fake Redis that never blocks — lets requests through and enables header…, Middleware must prefer app.state._rate_limiter_redis over construction-time…, test_rate_limit_429_has_retry_after(), test_rate_limit_headers_on_successful_response() (+3 more)

### Community 696 - "test_redis_bulkhead.py"
Cohesion: 0.13
Nodes (20): asyncio, Tests for Redis-backed distributed bulkhead (RedisBulkhead +…, RedisBulkheadRegistry.get_bulkhead() returns RedisBulkhead when Redis set., RedisBulkheadRegistry without Redis falls back to asyncio.Semaphore., AgentGraph.__init__ must accept bulkhead_registry parameter., Acquiring at limit returns False., release() calls the DECR Lua script., Context manager releases slot after successful block. (+12 more)

### Community 697 - "AlertRouter"
Cohesion: 0.13
Nodes (11): AlertRouter, AlertRule, FiredAlert, Any, Alert Router — threshold-based metric alerting with webhook delivery. Supports…, POST the alert as a JSON payload to *webhook_url*. The payload is compatible…, Evaluate metric values against registered rules and fire alerts. Thread-safe…, Add or replace a rule with the same name. (+3 more)

### Community 698 - "coordination_handoffs.py"
Cohesion: 0.28
Nodes (17): accept_handoff(), cancel_handoff(), create_handoff(), CreateHandoffRequest, get_handoff(), HandoffTransitionRequest, _public(), Any (+9 more)

### Community 699 - "warm_permission_cache"
Cohesion: 0.15
Nodes (18): Any, Cache warmer: pre-populates the Redis permission cache at startup. Runs during…, Pre-warm the permission cache for recently-active tenants. Queries the…, warm_permission_cache(), Comprehensive tests for app/auth/cache_warmer.py., If the DB factory itself fails, the error is logged but not raised., Should return early without any DB queries when redis is None., Should return early without any Redis ops when db_factory is None. (+10 more)

### Community 700 - "models/knowledge.py"
Cohesion: 0.16
Nodes (19): Document, ExecutionMemory, KnowledgeChunk1024, KnowledgeChunk1536, KnowledgeChunk3072, KnowledgeChunk768, _KnowledgeChunkMixin, KnowledgeCollection (+11 more)

### Community 701 - "evaluate_rule"
Cohesion: 0.21
Nodes (17): evaluate_rule(), evaluate_rules(), _get_field(), _matches(), PolicyRuleResult, Any, Declarative policy-as-code evaluator. Rule format (JSON): { "name": "block-…, Test that policy rules are actually evaluated during tool dispatch. (+9 more)

### Community 702 - "test_workflow_builder.py"
Cohesion: 0.11
Nodes (18): FastAPI, Tests for POST /workflows/generate and the fixed POST /workflows/{id}/run.…, POST /workflows/{id}/run?dry_run=true must return status='dry_run'., POST /workflows/{id}/run must always return a non-empty 'run_id'., Every generated workflow must have a 'trigger' and an 'end' node., Steps with no mutual dependency should land in the same execution wave., generate falls back to heuristic plan when no LLM provider is configured., POST /workflows/generate must return a canvas-ready {nodes, edges} payload. (+10 more)

### Community 703 - "StructuredPlanExecutor"
Cohesion: 0.14
Nodes (22): ExecutionCheckpoint, LoopExhaustedError, BaseModel, RuntimeError, ValueError, Bounded, cancellable, checkpointable execution for validated structured plans., A checkpoint cannot safely resume the supplied plan/runtime schema., A loop reached its bounded iteration ceiling without succeeding. (+14 more)

### Community 704 - "test_entitlements.py"
Cohesion: 0.16
Nodes (11): assert_feature(), assert_limit(), check_limit(), Single entitlement check module. Answers: "Can tenant T use feature F at volume…, Raise PermissionError if tenant's plan doesn't include the feature., Raise PermissionError if adding one more resource would exceed plan limits., Check if adding one more resource is within plan limits. Returns (allowed:…, Tests for Phase 1b entitlements system. (+3 more)

### Community 705 - "VoiceStreamingSession"
Cohesion: 0.12
Nodes (16): Any, ndarray, WebSocket, Record voice-processing consent for this session's speaker., Whether this session's speaker has recorded processing consent., Fail closed: refuse (and notify the client) when consent is absent., Send interim (non-final) transcript while user is still speaking., Process complete utterance: STT → IntentRouter → TTS. (+8 more)

### Community 706 - "test_otel.py"
Cohesion: 0.16
Nodes (20): _get_tracer(), Any, WorkflowOTELMiddleware — injects OTEL span attributes for workflow runs. Wraps…, Context manager that wraps a step execution in an OTEL span. Usage:: with…, Span wrapping an entire workflow run., run_span(), step_span(), _make_state() (+12 more)

### Community 707 - "test_agent_identity.py"
Cohesion: 0.14
Nodes (17): _make_agents_app(), Any, asyncio, FastAPI, Unit tests for Agent Identity — cryptographic credentials + SSO fixes. Tests:…, resolve_tenant_from_jwt must return a real key_id, not 'sso:{sub[:16]}'., Legal agents without bar_number must fail with 422., Second resolve_api_key call must return from Redis without hitting in-memory. (+9 more)

### Community 708 - "test_analytics_comprehensive.py"
Cohesion: 0.11
Nodes (29): AgentMetrics, GoalMetrics, GoalAnalyticsAggregator — computes behavioural metrics from goal event history., Compute tool usage and reliability from goal events., Query tool call metrics from goal_events table in PostgreSQL. Falls back to in-…, ToolMetrics, test_agent_metrics_defaults(), test_goal_metrics_defaults() (+21 more)

### Community 709 - "test_goals.py"
Cohesion: 0.19
Nodes (19): _make_app(), Any, asyncio, FastAPI, Tests for /goals endpoints., Pausing a completed (dry-run) goal returns 400 or 404., test_cancel_goal_returns_200(), test_get_goal_returns_status() (+11 more)

### Community 710 - "AuditV3"
Cohesion: 0.11
Nodes (17): AuditV3, Immutable append-only audit log with complete hash chain. Every record…, Require dual control before break-glass authority can be exercised., Export audit records as JSON or CSV., Export chain evidence with a deterministic manifest for immutable retention., test_security_event_is_hashed_and_worm_export_is_verifiable(), test_unknown_audit_action_and_single_party_break_glass_fail_closed(), test_audit_v3_creates_hash_chain() (+9 more)

### Community 711 - "agent_runtime.py"
Cohesion: 0.27
Nodes (16): create_execution_plan(), create_run_trace(), get_execution_plan(), get_run_trace(), list_agent_roles(), list_strategies(), Any, get (+8 more)

### Community 712 - "execution_environment/test_events.py"
Cohesion: 0.18
Nodes (15): make_forwarding_callback(), make_isolation_event(), Any, Execution-environment event helpers. Utilities for wrapping raw agent-loop…, Wrap a raw agent-loop event dict into an :class:`ExecutionEvent`., Build an isolation-specific metadata event (not a core agent event)., Return an async callback that wraps events and forwards them downstream. The…, wrap_agent_event() (+7 more)

### Community 713 - "test_middleware_full.py"
Cohesion: 0.15
Nodes (19): _make_app(), FastAPI, Full coverage for TenantMiddleware and SecurityHeadersMiddleware., Paths in the bypass list (/health, /docs, etc.) require no auth., Protected endpoints without an API key return 401., Valid Bearer token in Authorization header is accepted., Valid X-API-Key header is accepted., An unrecognised API key returns 401. (+11 more)

### Community 714 - "agent/test_errors.py"
Cohesion: 0.23
Nodes (17): classify_error(), ErrorClass, Exception, Structured error classification for agent execution., Classify an exception into an ErrorClass., Tests for ErrorClass and classify_error., ErrorClass members must be plain strings (StrEnum contract)., test_classify_auth_failed_401() (+9 more)

### Community 715 - "test_phase_n8_n10.py"
Cohesion: 0.16
Nodes (14): OutputContractBuilder, OutputSchema, OutputContractBuilder — builds output format contract for executor responses., Builds output format contracts with goal-aware auto-detection., Build an OutputSchema. When output_format='auto', detects from goal text., AgentGraph must accept tool_reliability_store as constructor param., test_context_pipeline_produces_all_three_contexts(), test_executor_context_distinct_from_planner() (+6 more)

### Community 716 - "orchestration.py"
Cohesion: 0.20
Nodes (17): ABTestResult, EvalScorecard, Base, SQLAlchemy ORM models for dynamic orchestration persistence. Tables:…, ReasoningPromotionDecision, ReflexionLesson, RegressionBaseline, RegressionCase (+9 more)

### Community 717 - "magentic/adapter.py"
Cohesion: 0.12
Nodes (28): MagenticRuntime, Any, datetime, Checkpointed ledger-driven Magentic strategy runtime., MagenticState, ParticipantCandidate, ParticipantDecision, ProgressAssessment (+20 more)

### Community 718 - "test_agent_knowledge_binding.py"
Cohesion: 0.13
Nodes (16): _agent_source(), asyncio, Tests for Agent Builder knowledge binding — FIX 1 (critical). Validates that: -…, _node_rag_retrieval must route bound collections through the gateway., Read combined source of graph.py and all node mixin files., Graph must not skip KnowledgeStore with 'no collection_id available' comment., Permissions GET/PUT must query agent_permissions table, not only in-memory dict., Readiness check must query MCP registry for connector verification. (+8 more)

### Community 719 - "test_key_rotation.py"
Cohesion: 0.17
Nodes (16): _make_app(), FastAPI, Tests for API key rotation endpoint., Missing API key returns 401., Every authenticated response must carry the Strict-Transport-Security header., HSTS is added by SecurityHeadersMiddleware even on public (bypassed) paths., Minimal app with tenants router and in-memory auth., POST /tenants/me/keys/{id}/rotate returns 201, new key data, and revokes old. (+8 more)

### Community 720 - "tool_allowed_for_autonomy"
Cohesion: 0.14
Nodes (13): get_tool(), list_tools_for_risk(), OrgToolSpec, Any, PART 17 — Org-level Tool Registrations. New tools available to agents operating…, Return tool names with risk level <= max_risk., Check if a tool is allowed at a given autonomy level (L0-L5). L2+: low-risk…, Convert OrgToolSpec to MCP tool definition format. (+5 more)

### Community 721 - "semantic_cache.py"
Cohesion: 0.15
Nodes (13): _compress(), _decompress(), _L1Entry, _pack_embedding(), World-Class Semantic Cache ========================== Complete rewrite that…, Store one entry in Redis. Data structure: HASH scv2:entry:{tenant}:{entry_id}…, Pack a float list into compact binary (4 bytes per float)., Unpack binary embedding back to list of floats. (+5 more)

### Community 722 - "CeleryGoalTaskQueue"
Cohesion: 0.18
Nodes (5): CeleryGoalTaskQueue, Celery-backed enqueue adapter; status bridging is handled separately., Comprehensive tests for app/services/goal_queue.py — targeting 90%+ coverage., TestCeleryGoalTaskQueue, TestGoalTaskQueueProtocol

### Community 723 - "test_agent_advanced.py"
Cohesion: 0.22
Nodes (18): _make_app(), Phase 4 advanced agent tests: clone, readiness, and release gate., An agent with no connectors should have a failing readiness check., An agent with connectors and a goal template should have ready=True., fully-autonomous + eval_suite_id should create the agent successfully., bounded-autonomous mode should not require eval_suite_id., Clone without specifying a name should append '(copy)' to original name., _signup() (+10 more)

### Community 724 - "test_ghost_run.py"
Cohesion: 0.18
Nodes (18): _counter_svc(), _make_app(), Any, FastAPI, Tests for POST /goals/ghost-run endpoint. Covers: 1. All strategies submitted…, When no strategies are provided, defaults are used and have required fields., Each strategy gets a unique goal_id — no duplicates., agent_id from the strategy request is forwarded to submit_goal. (+10 more)

### Community 725 - "test_rollback_experiment.py"
Cohesion: 0.18
Nodes (18): _make_app(), _make_opt_v2(), Any, FastAPI, Tests for POST /experiments/{experiment_id}/rollback API endpoint., When rollback() returns False → 400 Bad Request., When self_optimizer_v2 not wired on app.state → 503., Empty body → default reason string applied. (+10 more)

### Community 726 - "test_rpa_execute.py"
Cohesion: 0.11
Nodes (12): app(), authed_client(), fixture, Tests for RPA execute and session endpoints., Closing a non-existent session returns 404., Session created by T1 is not visible to T2., All RPA tools should be executable without crashing., Create, list, and close RPA sessions. (+4 more)

### Community 727 - "_make_app"
Cohesion: 0.19
Nodes (5): _make_app(), FastAPI, Extra coverage for app/api/workflows.py — workflow CRUD and run endpoints., TestWorkflowCrud, TestWorkflowRun

### Community 728 - "e2e_full/conftest.py"
Cohesion: 0.19
Nodes (15): app(), _backends(), client(), _migrated_backends(), Any, fixture, Session harness for the ``e2e_full`` tier. This tier proves the *wired*…, Boot ``create_app(manage_pools=True)`` with its real lifespan running. (+7 more)

### Community 729 - "test_sse_resume.py"
Cohesion: 0.12
Nodes (7): Tests for SSE resume-from-sequence., SSE endpoint must parse Last-Event-ID header., SSE response must include id: lines for resume., TestAutoscaleSignal, TestGoalServiceSubscribeEventsSinceSequence, TestLoadTestFiles, TestSSEEndpointLastEventId

### Community 730 - "S3Connector"
Cohesion: 0.15
Nodes (9): BaseConnector, register, Handle S3 event notifications (SQS or EventBridge)., Fetch and yield a single S3 object., Check include/exclude glob/extension patterns., AWS S3 and S3-compatible object storage ingestion., List S3 objects sorted by LastModified, yield those newer than cursor., S3Connector (+1 more)

### Community 731 - "MarkdownParser"
Cohesion: 0.10
Nodes (17): _flatten(), JSONParser, JSON/JSONL parser — schema-aware flattening for structured data., Flatten a JSON object into key:value pairs., Parse JSON/JSONL into readable key:value text., MarkdownParser, Markdown parser — AST-aware section chunking., Parse Markdown into clean text, preserving heading structure. (+9 more)

### Community 732 - "api/analytics.py"
Cohesion: 0.29
Nodes (16): agent_analytics(), cost_analytics(), eval_analytics(), _get_aggregator(), get_spans(), goal_analytics(), list_traces(), Any (+8 more)

### Community 733 - "resize_image_b64"
Cohesion: 0.14
Nodes (13): Resize a base64 image if it exceeds max_size bytes. Returns new base64., resize_image_b64(), test_resize_image_b64_handles_invalid(), test_resize_image_b64_small_image_unchanged(), TestResizeImageB64, Small image (under max_size) is returned unchanged., Invalid base64 input returns original string without raising., Large image without Pillow installed returns original base64. (+5 more)

### Community 734 - "StructuredLogStore"
Cohesion: 0.22
Nodes (7): Redis Streams-backed structured log store. Falls back to an in-memory ring…, Wire a real (or fake) Redis client. Called from main.py lifespan., StructuredLogStore, asyncio, StructuredLogStore falls back to in-memory ring buffer when Redis is…, set_redis() wires the Redis client; subsequent emits use Redis path., TestStructuredLogStore

### Community 735 - "google_oauth.py"
Cohesion: 0.16
Nodes (17): _generate_pkce(), google_callback(), google_login(), _pkce_redis_key(), _pkce_store_pop(), _pkce_store_set(), Any, get (+9 more)

### Community 736 - "Any"
Cohesion: 0.20
Nodes (5): Any, GDPR compliance. Amendment 8.3: All controls read from real DB records — no…, SOC 2 Type II — audit completeness check., Run all compliance checks and return combined report., HIPAA compliance: all required controls must pass.

### Community 737 - "decide_rollout"
Cohesion: 0.27
Nodes (14): CanaryEvidence, CertificationPolicy, decide_rollout(), BaseModel, datetime, field_validator, Quantitative, fail-closed promotion policy for agent-pattern canaries., Return a deterministic promotion decision; absent trust evidence always holds. (+6 more)

### Community 738 - "DeduplicationCache"
Cohesion: 0.05
Nodes (45): DeduplicationCache, In-memory deduplication cache with TTL, namespaced per tenant., Remove hashes older than TTL., _make_agent_loop(), Construct an AgentGraph backed by FakeProvider (no real LLM required). WARNING:…, AgentTestHarness, Any, AgentTestHarness — test agent behavior with mocked tools. Allows testing agent… (+37 more)

### Community 739 - "test_routing.py"
Cohesion: 0.32
Nodes (14): _make_graph(), Unit tests for the RoutingMixin — _route and _route_after_execute., _state(), _tenant(), test_max_reflection_rounds_default(), test_route_after_execute_continue_on_success(), test_route_after_execute_failed_on_failed_status(), test_route_complete_when_verification_success() (+6 more)

### Community 740 - "UniversalArgumentResolver"
Cohesion: 0.05
Nodes (36): get_healer(), get_resolver(), _normalise_key(), Any, Universal Tool Intelligence Layer ================================== Makes the…, Lowercase, camelCase→snake, remove all non-alphanumeric for comparison., Resolves LLM-generated argument dicts to match ANY tool's JSON Schema.…, Return a new arguments dict normalised to match tool_schema. (+28 more)

### Community 741 - "AutonomyEnforcer"
Cohesion: 0.12
Nodes (10): AutonomyEnforcer, AutonomyLevel, AutonomyLevelConfig, SUPPLEMENT A — Autonomy Levels L0-L5 (Detailed). Enforces the autonomy level…, SUPPLEMENT A — Enforces autonomy level constraints on every action. Resolution…, Resolve effective autonomy level. Most specific wins., Check if an action is permitted at the given autonomy level. Returns (allowed,…, Return True if this action needs human approval at the given level. (+2 more)

### Community 742 - "hitl_extension.py"
Cohesion: 0.15
Nodes (11): make_context_item(), make_number_context(), Any, HITLWorkflowGateway — extends HITLGateway with workflow-engine HITL features.…, Validate and consume a magic link token (single-use). Returns the payload dict…, Return inbox stats for a tenant., Build a rich context item dict for a HITL request., Create a number display context item with optional risk thresholds. (+3 more)

### Community 743 - "test_agents_extra.py"
Cohesion: 0.19
Nodes (6): _make_app(), FastAPI, Extra coverage for app/api/agents.py — AgentStore methods, snapshot functions,…, TestAgentApiEndpoints, TestCloneEndpoint, TestSnapshotEndpoints

### Community 744 - "api/test_artifacts_comprehensive.py"
Cohesion: 0.22
Nodes (17): _make_app(), _make_artifact_row(), _make_db_factory(), Any, FastAPI, Comprehensive tests for /artifacts API endpoints — targets 16% → 60%+ coverage., Create a mock async DB session factory., test_delete_artifact_no_db_returns_404() (+9 more)

### Community 745 - "test_phase5_knowledge_graph.py"
Cohesion: 0.20
Nodes (17): _make_app(), Phase 5: Tenant Knowledge Graph tests., Tenant A cannot see Tenant B's nodes., test_add_edge_between_nodes(), test_add_node_manually(), test_entity_extraction_deterministic(), test_extract_text_creates_nodes(), test_find_path_between_nodes() (+9 more)

### Community 746 - "test_live_platform.py"
Cohesion: 0.11
Nodes (17): live_client(), fixture, Phase 17: Live Platform Testing. Run against a real running backend with:…, Skills can be executed on real backend., Comprehensive check that no secrets appear in any API response., Backend health check passes., Model registry returns catalog without secrets., RAG query returns structured result. (+9 more)

### Community 747 - "TestTenantServiceCachedLookup"
Cohesion: 0.16
Nodes (10): asyncio, Updating a tenant must invalidate the Redis cache., invalidate_tenant_cache is a no-op when redis=None., invalidate_tenant_cache swallows Redis errors gracefully., On a Redis miss, get_tenant_cached writes in-memory result to Redis., get_tenant_cached falls back to in-memory on Redis cache miss., get_tenant_cached returns None when tenant is not found anywhere., get_tenant_cached falls back gracefully when redis=None. (+2 more)

### Community 748 - "collect_sse"
Cohesion: 0.20
Nodes (13): collect_sse(), Collect SSE ``data:`` lines from GET /goals/{goal_id}/stream. Stops when…, _event_type(), _inline_provider(), patterns_client(), _PlanExecVerifyProvider, Any, FakeProvider (+5 more)

### Community 749 - "test_system_comprehensive.py"
Cohesion: 0.24
Nodes (14): _make_app(), FastAPI, Comprehensive tests for /system endpoints — targets 30% → 70%+ coverage., Redis errors should be caught — fallback to building keys., test_health_all_down(), test_health_all_healthy(), test_health_partial_failure(), test_jwks_no_redis_no_service() (+6 more)

### Community 750 - "api/coordination.py"
Cohesion: 0.10
Nodes (39): BuilderProject, BuilderProjectRequest, create_builder_project(), get_builder_project(), Any, BaseModel, get, Request (+31 more)

### Community 751 - "AttributionVerifier"
Cohesion: 0.11
Nodes (10): AttributionReport, AttributionVerifier, Attribution Verifier — verify that citations actually support the answer. For…, Extract citation numbers from text like [1], [2], [3]., Return sentences that contain `[citation_number]`., Jaccard similarity of 4+-character word sets., Verify that citations in an answer are grounded by the cited chunks. Uses…, Check whether each cited chunk supports the corresponding answer sentence.… (+2 more)

### Community 752 - "test_spawn_tool.py"
Cohesion: 0.29
Nodes (15): execute_spawn_tool(), Any, spawn() — the governed tool exposed to agents to create child agents., Execute the spawn tool. Returns structured result for the LLM., _approved_verdict(), _denied_verdict(), _make_tenant_ctx(), asyncio (+7 more)

### Community 753 - "TestDomainPolicies"
Cohesion: 0.20
Nodes (6): apply_domain_policy(), DomainPolicy, get_domain_policy(), Per-Domain Content Policies ============================== Domain-specific…, Apply domain policy to content. Returns (processed_content, violations)., TestDomainPolicies

### Community 754 - "GraphAccessControl"
Cohesion: 0.15
Nodes (10): get_graph_access_control(), GraphAccessControl, Any, Knowledge Graph Access Control — SUPPLEMENT U5. Controls who can see, query,…, Enforces role-based access to knowledge graphs. Usage: gac =…, Return the list of operations allowed for a role., Return True if the given role is allowed to perform operation., Raise PermissionError if role cannot perform operation. (+2 more)

### Community 755 - "test_gap_fill_phase2.py"
Cohesion: 0.06
Nodes (29): ComplexityScore, QueryComplexityScorer, Query Complexity Scorer — route queries to appropriate model tiers. Features…, Return a tier name for model routing., Score query complexity using lightweight lexical features. Thresholds…, Score *query* and return a :class:`ComplexityScore`., Any, SLO Burn-Rate Tracker — track error budgets and burn rates per tenant. An SLO… (+21 more)

### Community 756 - "GraphFactory"
Cohesion: 0.33
Nodes (13): GraphFactory, Any, profile(), asyncio, D-2: a LOCAL-tier profile that selects the supervisor/debate strategies must…, services(), test_all_selected_existing_reasoning_patterns_compile_before_run(), test_compiled_nodes_exactly_reflect_profile_reasoning() (+5 more)

### Community 757 - "multi_turn_eval.py"
Cohesion: 0.23
Nodes (9): MultiTurnCase, MultiTurnEvaluator, MultiTurnResult, Any, Multi-turn dialogue evaluator. Evaluates agents on multi-turn conversations…, Evaluate agent behaviour across multi-turn conversations. Parameters ----------…, Run the case against *agent_fn* and score the conversation. Parameters…, Turn (+1 more)

### Community 758 - "org/metrics.py"
Cohesion: 0.12
Nodes (3): Any, PART 22 — Org-level Prometheus Metrics. Exposes org-level Prometheus…, _Stub

### Community 759 - "test_mission_flow.py"
Cohesion: 0.06
Nodes (27): ImprovementCyclePhase, ImprovementProposal, LearningCategory, OrgSelfImprovementEngine, StrEnum, PART 24 — Self-Improvement Cycle. PART 25 — Self-Healing Recovery Hierarchy.…, An improvement proposal from the self-optimization cycle., PART 24: Drives the continuous improvement cycle for the org. Improvements that… (+19 more)

### Community 760 - "BulkheadRegistry"
Cohesion: 0.16
Nodes (12): BulkheadRegistry, Per-tenant bulkhead semaphores for concurrent tool call limits., Per-tenant asyncio.Semaphore to prevent one tenant monopolizing workers. Each…, Set per-tenant concurrency limit., How many concurrent calls this tenant can still make., BulkheadRegistry creates independent semaphores per tenant., test_bulkhead_registry_per_tenant_semaphore(), asyncio (+4 more)

### Community 761 - "test_migrations.py"
Cohesion: 0.12
Nodes (14): parametrize, Tests that verify migration files are syntactically valid and chain correctly.…, Each migration module exposes revision, down_revision, upgrade, downgrade., Each migration references the correct down_revision., All migration modules can be imported without errors., Avoid defining the same api_keys.tenant_id index implicitly and explicitly., 0010 adds workflow metadata columns with database defaults., agent_id and ix_goals_tenant_agent already exist from 0004_goals. (+6 more)

### Community 762 - "test_compliance_download.py"
Cohesion: 0.12
Nodes (12): app(), authed_client(), fixture, Tests for GDPR compliance export and download., Request an export then download it as JSON., Downloaded export payload contains the authenticated tenant's ID., Downloading a non-existent export request returns 404., Export request is immediately retrievable via the status endpoint. (+4 more)

### Community 763 - "guardrail_engine.py"
Cohesion: 0.14
Nodes (12): GuardrailAction, GuardrailSeverity, LLMJudge, StrEnum, Six-layer guardrail engine for AgentVerse. Layers (evaluated in order): 1.…, Uses a fast LLM to semantically evaluate risk that regex cannot catch.…, _sev(), Pattern libraries for the AgentVerse guardrail engine. Contains 100+ injection… (+4 more)

### Community 764 - "TestRAGPlatformQueryPlanner"
Cohesion: 0.19
Nodes (4): RAGResult, Full RAG retrieval result with all legs and synthesis., TestRAGResult, TestRAGPlatformQueryPlanner

### Community 765 - "_MockSession"
Cohesion: 0.20
Nodes (3): _MockSession, Any, Minimal async SQLAlchemy session mock.

### Community 766 - "OrgHealthScore"
Cohesion: 0.09
Nodes (21): DeptAnalytics, OrgAnalyticsService, OrgHealthScore, Any, AsyncSession, Aggregates org-level and department-level metrics. Called by…, Compute the full 8-factor OrgHealthScore for an organization., Org-level overview: missions, costs, agents, bottlenecks. (+13 more)

### Community 767 - "OrgEventPublisher"
Cohesion: 0.21
Nodes (7): _make_envelope(), OrgEventPublisher, Any, PART 21 + PART 29 — Org Audit Event Publisher. PART 21: Org-level audit event…, PART 29 — Publishes org events to Redis pub/sub and persists to audit trail.…, Publish an org event. Returns the correlation_id. Validates event_type against…, Build a spec-compliant event envelope (PART 29).

### Community 768 - "_make_db_mock"
Cohesion: 0.12
Nodes (16): _make_db_mock(), Build a mock DB session factory., Line 921: DB available → runs insert (succeeds or fails gracefully)., Line 921: DB fails gracefully → still returns consent_id., Lines 944-947: DB available → runs UPDATE., Lines 847-849: DB insert succeeds → job_id returned., Lines 1076-1077: DB insert succeeds → returns contract details., Lines 1130-1152: DB raises exception → returns []. (+8 more)

### Community 769 - "test_backend_fixes.py"
Cohesion: 0.14
Nodes (13): _clear_mfa_state(), _enable_mfa_direct(), _make_mfa_app(), FastAPI, fixture, Tests for all backend world-class fixes. Covers: - MFA session tokens + rate…, MFA verify endpoint issues a session token on TOTP success., Session token must only contain URL-safe characters. (+5 more)

### Community 770 - "test_multimodal_e2e.py"
Cohesion: 0.17
Nodes (15): _fake_whisper(), Any, fixture, e2e_full: multimodal ingestion (image + audio) processed end-to-end. Proves the…, An image submitted through the API is captioned and stored as a completed job., A WAV submitted through the API is transcribed via the real AudioParser., A real, tiny (4x4 red) PNG — generated, not an opaque binary fixture., A real, short (0.2s) silent mono WAV via the stdlib ``wave`` module. (+7 more)

### Community 771 - "test_token_metrics.py"
Cohesion: 0.20
Nodes (15): asyncio, Tests for FakeProvider streaming and supports_streaming., stream_complete yields at least one non-empty token., FakeProvider.supports_streaming returns True., Joined tokens from stream_complete are non-empty., Each call to stream_complete advances the response index., Concatenated stream tokens reconstruct the full response (modulo spacing)., stream_complete behaves as a proper async generator (supports async for). (+7 more)

### Community 772 - "TestAiOpsModels"
Cohesion: 0.11
Nodes (13): AlertSeverity, DriftAlert, DriftType, EvalDataset, EvalResult, LLMJudge, StrEnum, AI Ops - observability, evals, regression, and drift models. (+5 more)

### Community 773 - "verify_goal_token"
Cohesion: 0.24
Nodes (8): _b64url(), mint_goal_token(), Short-Lived Goal Execution Tokens =================================== When a…, Mint a short-lived signed token for a goal execution., Verify and decode a goal token. Returns payload or None if invalid., verify_goal_token(), Each token must have a unique ID for anti-replay., TestGoalTokens

### Community 774 - "models/auth.py"
Cohesion: 0.18
Nodes (14): APIKeyScope, CustomRole, IPAllowlistEntry, Base, SQLAlchemy ORM models for scopes, custom roles, role assignments, and IP…, Per-tenant IP CIDR allowlist entry (enforcement-ready). Distinct from the…, Canonical registry of all scopes supported by the platform. Seeded at startup…, Explicit scope delegation from one principal to another. Supports least-… (+6 more)

### Community 775 - "ChannelAuthGuard"
Cohesion: 0.21
Nodes (3): ChannelAuthGuard, Verifies channel-specific authentication for every inbound command. All…, TestCheckScope

### Community 776 - "EmailParser"
Cohesion: 0.15
Nodes (7): EmailParser, _html_to_text(), EmailParser — extracts plain text from raw RFC-5322 email messages. Handles…, Parse a raw email string into structured text for ingestion., Return a list of text chunks from the email (subject + body parts)., Extract key metadata fields from a raw email., TestEmailParser

### Community 777 - "SlackIngestor"
Cohesion: 0.20
Nodes (5): Any, Slack channel message ingestor via Web API., SlackIngestor, Extra coverage for all knowledge ingestors — mock all external HTTP/lib calls., TestSlackIngestor

### Community 778 - "sign_envelope"
Cohesion: 0.16
Nodes (11): _get_signing_key(), Compute and set the HMAC-SHA256 signature on the envelope in-place., Return the signing key bytes, enforcing production requirements. Raises:…, sign_envelope(), build_workload_manifests(), Build a digest-pinned Job and matching default-deny NetworkPolicy., FakeKubernetesClient, request() (+3 more)

### Community 779 - "wait_for_status"
Cohesion: 0.22
Nodes (12): Poll GET /goals/{goal_id} until its status matches, or raise on timeout.…, wait_for_status(), halluc_client(), _inline_provider(), Any, FakeProvider, fixture, e2e_full: an ungrounded answer on a high-risk goal should be flipped by the… (+4 more)

### Community 780 - "test_rag_goal_retrieval_e2e.py"
Cohesion: 0.27
Nodes (12): _CompletingProvider, _fake_embedder(), _inline_provider(), _parse(), Any, FakeProvider, fixture, rag_client() (+4 more)

### Community 781 - "test_durable_execution.py"
Cohesion: 0.05
Nodes (53): check_pause_cancel(), clear_signals(), GoalCancelledError, is_cancelled_sync(), is_paused_sync(), Any, Exception, Cross-process goal lifecycle signals via Redis pub/sub + flag keys. Allows API… (+45 more)

### Community 782 - "_strip_secret_redis_schedule_fields"
Cohesion: 0.11
Nodes (16): _strip_secret_redis_schedule_fields(), test_strip_secret_redis_schedule_fields_case_insensitive(), test_strip_secret_redis_schedule_fields_removes_secrets(), TestStripSecretFields, test_strip_case_insensitive_field_names(), test_strip_preserves_non_secret_fields(), test_strip_removes_known_secret_fields(), test_strip_returns_empty_when_all_secret() (+8 more)

### Community 783 - "test_dr_drill.py"
Cohesion: 0.13
Nodes (14): skipif, DR drill validation — tests backup/restore capability., DR script must document RPO and RTO targets., DR script must cover the 5 required drill steps., docker-compose.yml must have a pgbackup service for automated backups., pgbackup service must configure retention periods., Shell script must be syntactically valid (bash -n check)., DR drill script must exist and be executable. (+6 more)

### Community 784 - "test_self_improvement_smoke.py"
Cohesion: 0.25
Nodes (13): _make_capturing_db(), asyncio, Smoke test: all 4 self-improvement DB tables can receive rows via fake DB. This…, SelfOptimizer.persist_suggestion must INSERT into self_optimization_suggestions., Return (db_factory, captured_list) where captured_list grows on INSERT., ExecutionMemory.record_async must INSERT into execution_memory table., LongTermMemoryStore.store_async must INSERT into long_term_memory table., EvalRunner.score_and_persist must INSERT into evaluations with scores JSON. (+5 more)

### Community 785 - "test_a2a.py"
Cohesion: 0.20
Nodes (14): _make_app(), FastAPI, fixture, Tests for A2A protocol endpoints — updated for DB-backed + HMAC implementation., Set A2A_TENANT_ID for all tests in this module., When A2A_SHARED_SECRET not set, any request is accepted., When A2A_SHARED_SECRET is set, bad signature returns 401., _set_a2a_tenant() (+6 more)

### Community 786 - "TestToolReliabilityStoreWithDBError"
Cohesion: 0.16
Nodes (7): _FailingDB, When DB fails, get_reliability must fall back to in-process cache., DB failure in get_unreliable_tools returns empty list., record() with explicit db_session_factory=None skips DB path., Tests that exercise the DB fallback paths., DB failure must not prevent in-memory update., TestToolReliabilityStoreWithDBError

### Community 787 - "TestToolReliabilityDBPaths"
Cohesion: 0.14
Nodes (8): Tests for DB-backed record/get paths (lines 50, 74-81, 113-123)., Line 50: DB write inside record()., DB write for failure case., Lines 74-81: DB returns a row → parse it., DB returns no row → falls back to in-memory cache., Lines 113-123: get_unreliable_tools with DB returning results., DB returns no unreliable tools., TestToolReliabilityDBPaths

### Community 788 - "test_rag_patterns_functional.py"
Cohesion: 0.11
Nodes (17): fixture, Functional tests for all 9 RAG patterns., RetrievalPlanner.select_strategy returns correct strategy for query types., AdaptiveRAGPattern.execute calls the right strategy., RetrieverTool requires an injected gateway., KGQueryEngine entity expansion returns structured facts., RAPTORPattern summarizes chunks into a hierarchical answer., _detect_uncertainty correctly identifies hedging phrases. (+9 more)

### Community 789 - "test_cli.py"
Cohesion: 0.13
Nodes (9): Tests for the agentverse CLI (app/cli/main.py)., submit without its required positional arg should exit non-zero., logs without its required positional arg should exit non-zero., connectors --help shows help before any network call., status without its required positional arg should exit non-zero., test_connectors_help_exits_without_key(), test_logs_requires_goal_id(), test_status_requires_goal_id() (+1 more)

### Community 790 - "EvalRunner"
Cohesion: 0.04
Nodes (107): EvalRunner, Any, EvalScorecard, Eval runner — scores completed goals on 7 dimensions., Use LLM to rate how logically coherent the steps are relative to the goal.…, Scores a completed AgentState on the 7 evaluation dimensions., Use LLM to rate how accurately the agent achieved the goal. Falls back to the…, Score asynchronously, replacing heuristic coherence AND accuracy with LLM… (+99 more)

### Community 791 - "test_goal_hitl_lifecycle_e2e.py"
Cohesion: 0.23
Nodes (13): _create_supervised_agent(), _HighRiskPlanProvider, _pinned_high_risk_provider(), Any, FakeProvider, fixture, e2e_full: full goal lifecycle through a real HITL approval gate. The Raccoon-…, Deterministic provider that plans one explicit high-risk step. Branches on the… (+5 more)

### Community 792 - "_make_mock_db"
Cohesion: 0.18
Nodes (9): _make_mock_db(), Build a fake async DB session factory., Lines 1242-1254: DB exception → falls back to memory., Lines 1394-1438: DB exception on publish → stored in memory., Lines 1573-1578: DB failure during install → success=False., Lines 1673-1674: DB exception on add_review → success=False., list_templates DB exception → in-memory fallback., list_reviews DB exception → in-memory fallback. (+1 more)

### Community 793 - "ReadinessEvaluator"
Cohesion: 0.10
Nodes (37): _capability(), _catalogue_item(), get_strategy(), get_strategy_certification(), get_strategy_readiness(), list_strategies(), Any, get (+29 more)

### Community 794 - "TestRunGoalPaths"
Cohesion: 0.19
Nodes (8): Lines 337-346, 364-365, 533-539, 630-636., Context that makes the distributed lock always succeed., Lines 345-346: invalid plan string → PROFESSIONAL., Lines 358-363: emergency stop returns blocked., Lines 630-636: fake provider blocked in production., Lines 533-535: ANTHROPIC_API_KEY path., Lines 537-539: OPENAI_API_KEY path., TestRunGoalPaths

### Community 795 - "api/artifacts.py"
Cohesion: 0.27
Nodes (13): delete_artifact(), get_artifact(), list_artifacts(), Any, delete, get, Request, Artifact REST API — list, get, download, delete agent-produced files. (+5 more)

### Community 796 - "ocr.py"
Cohesion: 0.25
Nodes (13): BatchOcrRequest, BatchOcrResponse, extract_document(), extract_documents_batch(), OcrFieldResult, OcrRequest, OcrResponse, BaseModel (+5 more)

### Community 797 - "api/policy_rules.py"
Cohesion: 0.27
Nodes (13): create_policy_rule(), delete_policy_rule(), evaluate_rules_dry_run(), list_policy_rules(), PolicyRuleUpsert, Any, BaseModel, delete (+5 more)

### Community 798 - "PromptBlock"
Cohesion: 0.31
Nodes (10): PromptBlock, PromptBudget, PromptBudgetExceededError, PromptBudgetResult, BaseModel, ValueError, Typed exact-token prompt budgeting with immutable safety blocks., _count() (+2 more)

### Community 799 - "requires_consensus"
Cohesion: 0.24
Nodes (4): Any, Return True when this goal warrants 3-way consensus verification. Two calling…, requires_consensus(), TestRequiresConsensus

### Community 800 - "Any"
Cohesion: 0.18
Nodes (7): Any, Register a new prompt variant for A/B testing. If *db* is provided (an async…, Set Redis client for cache invalidation between replicas., Persist a variant to the prompt_variants table., Update win/loss counts in DB after A/B test result., Load all active variants from DB into in-process cache. Call at startup and…, Register a pre-built PromptVariant for the given tenant. If *db* is None, the…

### Community 801 - "test_reliability_fixes.py"
Cohesion: 0.10
Nodes (14): asyncio, Regression tests for 0C.4 reliability correctness., H15: AuditWriter must have chain initialization from DB., H15: Audit hash must include tool_name in the hash input., H14: rollback_all_async must await inverse, not fire-and-forget., H14: get_inverse_fn must return an awaitable coroutine, not a sync wrapper., H16: Circuit breaker must use wall clock time for cross-replica correctness., H16: RedisCircuitBreaker must store time.time() not time.monotonic(). (+6 more)

### Community 802 - "record_desired_workers"
Cohesion: 0.19
Nodes (8): Emit desired worker count per plan for autoscaling. The *plan* label is passed…, record_desired_workers(), Tests for per-plan autoscale desired-worker gauge., DESIRED_WORKERS gauge reflects the last set value., All four standard plan names can be recorded without error., DESIRED_WORKERS Gauge is registered and exportable., TestAutoscaleGauge, TestDesiredWorkersGaugeExists

### Community 803 - "role_taxonomy.py"
Cohesion: 0.18
Nodes (12): AgentStatus, FullRoleDefinition, get_role(), get_roles_for_dept(), StrEnum, PART 4 + PART 6 — Complete role taxonomy (22 departments) + OrgAgent lifecycle.…, Spec PART 6 agent status state machine: IDLE → PLANNING → PLAN_READY →…, Returns True if the transition is valid per the spec state machine. (+4 more)

### Community 804 - "RedisBulkheadRegistry"
Cohesion: 0.15
Nodes (7): Any, Semaphore, Redis-backed registry of per-tenant distributed bulkheads. Falls back to…, Set per-tenant concurrency limit., Get a bulkhead for a tenant (Redis if available, local otherwise)., Get or create semaphore for tenant., RedisBulkheadRegistry

### Community 805 - "GoalDeduplicator"
Cohesion: 0.18
Nodes (8): _dedup_key(), GoalDeduplicator, Any, Goal-level request deduplication. When two tenants submit identical goals…, Redis-backed goal-level deduplication. Usage: dedup =…, Return the in-flight goal_id for this (tenant, goal) pair, or None., Register a new goal. Returns True if this is the first registration (i.e. no…, Delete the dedup key so future identical goals can be submitted.

### Community 806 - "test_step_enforcement.py"
Cohesion: 0.34
Nodes (13): RetryConfig, _clean_registry(), _compiler(), Any, fixture, 2.W-7: the compiler node wrapper must enforce the DSL's ``retry``, per-step…, _register(), _step() (+5 more)

### Community 807 - "test_goals_metrics.py"
Cohesion: 0.15
Nodes (11): app(), authed_client(), fixture, Tests for GET /goals/metrics endpoint., Client with a valid tenant API key., Fresh tenant has all-zero metrics., After submitting a dry-run goal, total_goals increments., Two tenants each see only their own metrics. (+3 more)

### Community 808 - "TestMaybePromote"
Cohesion: 0.15
Nodes (7): Lines 296-297: key not found → None., Line 304: no challengers → None., Line 298: challenger.run_count < min_runs → None., Line 326: no best_challenger found → None., Lines 315: best_challenger > best_score + significant → promoted., Line 307-308: control.run_count < min_runs → None., TestMaybePromote

### Community 809 - "agent/supervisor.py"
Cohesion: 0.20
Nodes (7): Supervisor agent — coordinates multiple sub-agents to achieve complex goals.…, _FakeGoalService, Any, asyncio, Regression: SubAgentTask must carry the REAL goal_id from submit_goal so the…, test_run_threads_real_goal_id_onto_each_task(), test_sub_agent_task_has_goal_id_field()

### Community 810 - "ShadowRouter"
Cohesion: 0.16
Nodes (10): Any, Shadow Router — fire requests to a candidate model alongside the primary.…, Return recent shadow results as serialisable dicts., Return True with probability equal to sample_rate., Fire primary + shadow requests concurrently; return primary to caller. The…, Fire primary (and optionally shadow) call; return primary response. The shadow…, ShadowResult, ShadowRouter (+2 more)

### Community 811 - "test_phase1_gaps.py"
Cohesion: 0.30
Nodes (11): _make_app(), FastAPI, Phase 1: Tests for existing product gap fixes. Covers: - Gap 1: /me/llm-config…, test_llm_config_get_requires_auth(), test_llm_config_get_returns_empty_by_default(), test_llm_config_save_and_retrieve(), test_llm_config_save_requires_auth(), test_provider_capabilities_present() (+3 more)

### Community 813 - "test_workflow_definition_bridge.py"
Cohesion: 0.20
Nodes (13): app_factory(), _app_url(), postgres_url(), async_sessionmaker, fixture, Real-Postgres coverage for the workflows → workflow_definitions bridge. The…, A DB-mode create must make the workflow triggerable by the run engine., Tenant B must not see tenant A's bridged workflow definition (RLS). (+5 more)

### Community 814 - "test_evals_scorecard_e2e.py"
Cohesion: 0.26
Nodes (10): _CompletingProvider, evals_client(), _inline_provider(), Any, FakeProvider, fixture, e2e_full: a completed goal produces an eval scorecard. Phase-3 *evals*…, Auto-eval on completion now runs: the completion hook reads eval_runner via the… (+2 more)

### Community 815 - "ApprovalChainRegistry"
Cohesion: 0.17
Nodes (14): ApprovalChainRegistry, Cross-department approval chain engine. Defines approval chains for high-risk…, Read-only registry of built-in approval chains. Usage:: reg =…, Tests for ApprovalChain — app/org/approval_chain.py, prod_deploy requires all approvers; not any., test_approval_chain_any_vs_all(), test_approval_chain_financial_commitment_requires_cfo(), test_approval_chain_policy_lookup() (+6 more)

### Community 816 - "test_memory_recall_e2e.py"
Cohesion: 0.26
Nodes (10): _CompletingProvider, _inline_provider(), memory_client(), Any, FakeProvider, fixture, e2e_full: a goal writes execution memory; a later goal can recall it. Phase-3…, A first goal populates memory; a second, later goal in the same tenant… (+2 more)

### Community 817 - "upsert_google_user"
Cohesion: 0.22
Nodes (10): Any, User management service — upsert, lookup, membership management., Upsert a Google-authenticated user and create a personal tenant. Returns…, upsert_google_user(), Base, User and TenantMembership ORM models., Global user identity (email-unique, cross-tenant)., User ↔ Tenant membership with role. (+2 more)

### Community 818 - "test_bm25_ws_raptor.py"
Cohesion: 0.11
Nodes (17): MCPWebSocketClient context manager must work without error., MCPServerConfig must accept transport and ws_url fields., MCPServerConfig websocket alias is accepted., BM25 should report whether rank_bm25 is installed., RAPTOR must use asyncio.gather for parallel summarization., test_bm25_handles_empty_index(), test_bm25_handles_empty_query(), test_bm25_indexes_and_searches() (+9 more)

### Community 819 - "EmailChannelAdapter"
Cohesion: 0.24
Nodes (5): EmailChannelAdapter, Any, Email inbound command parser — org@commands.agentverse.io., Verify sender is in allowed list., Format OrgResponse as an email payload.

### Community 820 - "CloudDestructionGuard"
Cohesion: 0.27
Nodes (4): CloudDestructionGuard, Scans text for irreversible cloud/infrastructure destruction commands., kubectl delete namespace must be caught., TestCloudDestructionGuard

### Community 821 - "test_guardrails_v3.py"
Cohesion: 0.17
Nodes (9): IndirectInjectionResult, Any, Indirect Injection Scanner ============================ Scans tool outputs and…, Scan RAG-retrieved chunks for injection attempts before LLM injection., Wrap tool output in untrusted delimiters to signal LLM it's external data., scan_rag_chunks(), wrap_in_untrusted(), Output Anomaly Detection ========================= Detects statistical… (+1 more)

### Community 822 - "scan_output_for_anomalies"
Cohesion: 0.26
Nodes (4): Any, Scan LLM output for anomalies. Returns dict: {clean, anomalies, severity}, scan_output_for_anomalies(), TestOutputAnomaly

### Community 823 - "verify_stream_token"
Cohesion: 0.23
Nodes (15): _b64url(), mint_stream_token(), Any, Short-lived SSE stream tokens. EventSource cannot send request headers, so…, Mint a short-lived signed token authorizing SSE reads for one tenant., Verify and decode a stream token. Returns the payload or None if invalid., verify_stream_token(), Tests for short-lived SSE stream tokens (app/auth/stream_tokens.py). (+7 more)

### Community 824 - "test_model_router_e2e.py"
Cohesion: 0.30
Nodes (10): _CompletingProvider, _inline_provider(), _parse(), Any, FakeProvider, fixture, e2e_full: per-role model selection is observable on the live goal path. Phase-3…, router_client() (+2 more)

### Community 825 - "test_oauth_security.py"
Cohesion: 0.17
Nodes (11): Tests that OAuth security fixes work correctly., HTTP 400/401 from OAuth server returns None, not a mock token., Unreachable OAuth server returns None., OAuth server returns 200 but no access_token — should return None., Expired or forged state parameter returns None., Successful exchange stores the real token., test_exchange_code_returns_none_on_connection_error(), test_exchange_code_returns_none_on_empty_access_token() (+3 more)

### Community 826 - "test_graph_persistence_wiring.py"
Cohesion: 0.21
Nodes (12): _agent_source(), Read combined source of graph.py and all node mixin files., graph.py must use on_goal_completed(), not the non-existent record_result()., graph.py must reference persist_tool_outcome for cross-restart trust., graph.py must persist scorecards via OrchestrationPersistence., graph.py must call RegressionGate for low-scoring goals., SelfOptimizerV2 must NOT have record_result() (only on_goal_completed)., test_graph_calls_on_goal_completed_not_record_result() (+4 more)

### Community 827 - "test_artifact_tool.py"
Cohesion: 0.18
Nodes (6): asyncio, Tests for ArtifactTool and related infrastructure., test_artifact_tool_definition_valid(), test_artifact_tool_handles_bytes(), test_artifact_tool_importable(), test_artifact_tool_returns_artifact_id()

### Community 828 - "_make_eval_runner"
Cohesion: 0.15
Nodes (13): _make_eval_runner(), Lines 752-760: creates suite and returns suite_id., Lines 785-787: lists suites from runner., Line 802: returns 404 for unknown suite_id., Lines 802-803: returns suite metadata., Lines 813-826: adds task to suite., Lines 847-849: get suite results., test_add_golden_task_with_runner() (+5 more)

### Community 829 - "test_goals_eval.py"
Cohesion: 0.17
Nodes (10): app(), authed_client(), fixture, Tests for GET /goals/{id}/eval endpoint., A dry-run goal creates a goal_id we can request eval for., Eval response always includes the required scorecard envelope keys., A tenant cannot fetch the eval for another tenant's goal., test_eval_after_dry_run() (+2 more)

### Community 830 - "ApprovalChainEngine"
Cohesion: 0.27
Nodes (3): ApprovalChainEngine, ApprovalRequest, Runtime engine for approval chain matching and request management. G-20:…

### Community 831 - "test_workflow_trigger_e2e.py"
Cohesion: 0.27
Nodes (12): _create_workflow(), _inline_runner(), Any, fixture, e2e_full: workflow create → trigger → persisted run → queryable. Proves the…, The create/read path is genuinely wired (DB-backed, RLS-scoped)., Two API-created workflows must each run their own definition. The run engine…, Run workflow triggers inline for one test (no Celery worker in the harness).… (+4 more)

### Community 832 - "test_user_models.py"
Cohesion: 0.15
Nodes (5): Tests for Phase 1a — User + TenantMembership models., Migration must enable RLS on tenant_memberships., TestEntitlementsModule, TestGoogleOAuthRouter, TestUserModel

### Community 833 - "build_services"
Cohesion: 0.22
Nodes (7): build_services(), Any, FastAPI, Bootstrap: service construction for AgentVerse. All ``app.state.*`` assignments…, Construct all application services and store them on ``app.state``. ..…, build_services must accept app and settings parameters., TestBootstrapServices

### Community 834 - "test_prompt_variants_api.py"
Cohesion: 0.21
Nodes (12): anyio, Behavioral tests for /intelligence/prompt-variants API endpoints. All tests use…, GET /intelligence/prompt-variants must return 401 without an API key., POST creates a new challenger variant and it appears in the listing., POST /{id}/promote marks the variant as control and sets promoted_at., GET /{id}/report returns score fields for a known variant., DELETE /{id} removes the variant; subsequent list no longer contains it., test_create_variant_registers_with_optimizer() (+4 more)

### Community 835 - "TestMultimodalPipeline"
Cohesion: 0.16
Nodes (7): asyncio, fixture, get_job must enforce tenant isolation., Empty text should still create a job (returns empty spans)., Without a vision provider the job should fail gracefully., set_provider should not raise., TestMultimodalPipeline

### Community 836 - "TestInputClamping"
Cohesion: 0.15
Nodes (7): Input validation: top_k clamped to [1, 100], query length capped at 10 000., top_k > 100 must be clamped to 100., top_k < 1 must be raised to 1., Queries longer than 10 000 chars must be truncated., Queries at or below 10 000 chars must not be modified., search_knowledge endpoint code applies the clamp inline., TestInputClamping

### Community 837 - "coordination_group_chat.py"
Cohesion: 0.27
Nodes (11): _api_key(), _authenticate(), describe_group_chat_websocket(), group_chat_websocket(), GroupChatWebSocketHandshake, _origin_allowed(), Any, BaseModel (+3 more)

### Community 838 - "list_active_sessions"
Cohesion: 0.21
Nodes (11): list_active_sessions(), Any, delete, get, Request, User auth session management — list active sessions, revoke, idle timeout., List all active login sessions for the current user., Revoke a specific session (remote logout). (+3 more)

### Community 839 - "roles.py"
Cohesion: 0.24
Nodes (10): _add(), count_roles(), get_role_by_name(), get_roles_for_dept(), PART 4 — Complete Role Taxonomy (456 roles across 22 departments). Provides: -…, Full role definition per PART 5 spec., Return all role definitions for a department., Case-insensitive role lookup by name. (+2 more)

### Community 840 - "asyncio"
Cohesion: 0.25
Nodes (6): asyncio, Tests for explicit persisted versus in-memory mutation boundaries., Line 112: _db_create_collection with None db returns early., Line 172: _db_ingest_chunk with None db returns early., Line 453: _db_ingest_with_citations with None db returns early., TestKnowledgeStoreAwaitedBoundaries

### Community 841 - "test_observability_trace_e2e.py"
Cohesion: 0.31
Nodes (9): _CompletingProvider, _inline_provider(), obs_client(), _parse(), Any, FakeProvider, fixture, e2e_full: a goal emits a durable runtime decision-trace over SSE. Phase-3… (+1 more)

### Community 842 - "generate_api_key"
Cohesion: 0.21
Nodes (7): generate_api_key(), Generate a NIST SP 800-131A compliant API key. Was: uuid4() — 122 bits, not…, test_generate_api_key_custom_prefix(), test_generate_api_key_default_prefix(), test_generate_api_key_hash_deterministic(), test_generate_api_key_unique(), TestAPIKeyGeneration

### Community 843 - "TestReviewRiskLevels"
Cohesion: 0.18
Nodes (6): Lines 114-117: medium severity → risk_level='medium'., Lines 107-119: all checks pass → risk_level='safe', approved=True., _check_scopes: CRITICAL_SCOPES produce severity='high' finding →…, _check_autonomous_with_dangerous_connectors → critical finding., Line 113: 'high' severity finding → risk_level='high'. CRITICAL_SCOPES produce…, TestReviewRiskLevels

### Community 844 - "scan_tool_output"
Cohesion: 0.29
Nodes (3): Scan tool output for indirect injection attempts. Always wraps content in…, scan_tool_output(), TestIndirectInjection

### Community 845 - "advanced_services.py"
Cohesion: 0.21
Nodes (7): ChannelRouter, ConversationTurn, get_channel_router(), P1 + P4 + P10 — Policy Evidence Engine, Strategic Advisor, Collective…, Q9 — Routes commands from any channel to the org system. Maintains per-channel…, Append a turn to an ongoing conversation., Route an incoming command to the appropriate handler.

### Community 846 - "TestSelectVariant"
Cohesion: 0.18
Nodes (6): Line 262: tenant not found → falls back to 'global'., No variants registered → None., No challengers → always control., Lines 269-270: 30% of the time → challenger returned., random.random >= 0.70 → fallback to control., TestSelectVariant

### Community 847 - "test_semantic_cache.py"
Cohesion: 0.25
Nodes (10): bridge(), asyncio, fixture, SemanticCacheBridge: tenant isolation, error guard, fresh evidence rule., test_cache_never_overrides_fresh_evidence(), test_deterministic_safe_stored(), test_error_output_never_cached(), test_nondeterministic_never_cached() (+2 more)

### Community 848 - "RedisBulkhead"
Cohesion: 0.20
Nodes (6): Try to acquire a slot. Returns True if acquired, False if at limit., Release a previously acquired slot., Approximate available slots (non-blocking estimate)., Get current available slots from Redis., Redis-backed distributed bulkhead — enforces concurrency limits across ALL…, RedisBulkhead

### Community 849 - "RoutingOptimizer"
Cohesion: 0.29
Nodes (6): MeasuredOutcome, OptimizationRecommendation, Bounded rolling optimization recommendations., RoutingOptimizer, test_hedging_requires_idempotency_budget_and_deadline_pressure(), test_optimizer_requires_samples_and_never_weakens_hard_limits()

### Community 850 - "agent/consensus.py"
Cohesion: 0.24
Nodes (6): ConsensusResult, 3-Way Consensus Verification for high-stakes goals. For goals touching…, Run all configured verifiers and compute majority verdict., Run the LLM judge with a rubric-scored prompt., _run_judge(), VerifierVote

### Community 851 - "_name_tokens"
Cohesion: 0.20
Nodes (9): _name_tokens(), Tool risk classification for governed real tool calls. Covers all major…, Split a camelCase / snake_case / PascalCase name into lowercase tokens., test_name_tokens_all_caps(), test_name_tokens_camel_case(), test_name_tokens_empty(), test_name_tokens_mixed(), test_name_tokens_pascal_case() (+1 more)

### Community 852 - "TestCheckAuthRateLimit"
Cohesion: 0.23
Nodes (7): asyncio, Redis without .pipeline() uses direct zadd/zcard/expire., No Redis wired → no-op, request allowed., Under rate limit with Redis pipeline → request allowed., The rate limit HTTPException is caught by the outer except block and logged as…, Redis error → allow request (availability over blocking)., TestCheckAuthRateLimit

### Community 853 - "test_builder_preview.py"
Cohesion: 0.17
Nodes (11): Test builder preview hosting endpoints., Must show 'Building' status when no index.html artifact found., Must serve actual index.html content when artifact exists., Created project must have a /builder/preview/ URL., GET /builder/preview/{id} must return HTML, not JSON., Preview must not crash if artifact_store.list_artifacts raises AttributeError., test_builder_preview_building_message_when_no_artifacts(), test_builder_preview_handles_missing_list_artifacts() (+3 more)

### Community 854 - "test_openapi_schema.py"
Cohesion: 0.17
Nodes (7): openapi_schema(), fixture, Tests that OpenAPI schema includes all registered endpoints., Schema must have at least 65 paths (we have 70+)., Every endpoint should define at least one response., test_all_endpoints_have_response_schemas(), test_schema_has_minimum_path_count()

### Community 855 - "api/test_replay.py"
Cohesion: 0.27
Nodes (11): _get_routes(), _make_app(), Return all registered paths by walking FastAPI's OpenAPI schema. app.routes…, GET /goals/{id}/replay must be registered., GET /goals/{id}/timeline must be registered., Tracing must work even without OTLP endpoint., test_in_process_tracing_configured(), test_replay_endpoint_exists() (+3 more)

### Community 856 - "test_deletion_cascade.py"
Cohesion: 0.23
Nodes (15): Verifiable receipt for a data-subject deletion cascade (GDPR/DPDP erasure). A…, factories(), postgres_url(), _prepare_runtime_role(), async_sessionmaker, AsyncSession, fixture, Integration tests for the data-subject deletion cascade (P0-2). Proves that… (+7 more)

### Community 857 - "test_knowledge_graph_e2e.py"
Cohesion: 0.29
Nodes (11): _build_source_and_doc(), _fake_embedder(), _make_collection(), Any, fixture, e2e_full: knowledge graph auto-population from real document ingestion. Proves…, A second tenant must not see the first tenant's auto-populated graph., Swap the wired ingestion pipeline's embedder for a deterministic 768-dim fake.… (+3 more)

### Community 858 - "OpenRouterProvider"
Cohesion: 0.24
Nodes (4): OpenRouterProvider, OpenRouter provider. Set ``OPENROUTER_API_KEY`` in the environment to enable.…, Fetch the current model catalog from OpenRouter., TestOpenRouterProvider

### Community 859 - "RedisDeduplicationCache"
Cohesion: 0.20
Nodes (6): Any, Redis-backed cross-replica deduplication cache. Prevents duplicate in-flight…, Check if identical goal is already in-flight. Returns goal_id or None., Register a goal to prevent duplicates during its execution window., Remove dedup entry after goal completes., RedisDeduplicationCache

### Community 860 - "_trigram_score"
Cohesion: 0.33
Nodes (4): Simple character trigram overlap score in [0, 1]., _trigram_score(), Covers line 66: short string returns 0.0., TestTrigramScore

### Community 861 - "grant_elevation"
Cohesion: 0.24
Nodes (8): ElevationToken, grant_elevation(), Any, Temporary Elevated Scope ========================= Time-boxed impersonation and…, Verify and decode an elevation token., Grant temporary elevated access. Returns a signed token., verify_elevation(), TestTemporalElevation

### Community 862 - "test_supervisor_debate_wiring.py"
Cohesion: 0.32
Nodes (9): _FakeAgentStore, _node_names(), Any, D-2: the real supervisor/debate reasoning nodes must be reachable from an…, Regression guard: without the flags, the nodes stay absent (opt-in only)., _svc_with_agent_config(), test_default_path_enables_debate_from_agent_config(), test_default_path_enables_supervisor_from_agent_config() (+1 more)

### Community 863 - "asyncio"
Cohesion: 0.17
Nodes (9): asyncio, Integration scenarios for ApprovalChainEngine (in-memory store)., End-to-end mission lifecycle using GoalService with in-memory store., GoalService can be created without a DB session (in-memory mode)., Freshly created GoalService with no goals returns empty result., Getting a non-existent goal returns None or raises cleanly., _tenant(), TestApprovalChainEngineIntegration (+1 more)

### Community 864 - "test_auth_login_redirects"
Cohesion: 0.22
Nodes (8): Force reload from disk (used in tests / hot reload)., asyncio, validate_jwt raises ImportError when python-jose not installed., GET /auth/config returns sso_enabled: false when SSO off., GET /auth/login returns a redirect to Keycloak., test_auth_login_redirects(), test_get_sso_config_disabled(), test_validate_jwt_raises_without_jose()

### Community 865 - "ConversationManager"
Cohesion: 0.25
Nodes (6): Conversation, ConversationManager, ConversationTurn, AsyncSession, Multi-turn ConversationManager — maintains context across channels., Maintains conversation state across multiple turns, regardless of channel. Uses…

### Community 866 - "TestMemoryV2Models"
Cohesion: 0.22
Nodes (5): MemoryConflict, MemoryProvenance, Provenance tracking for a memory entry., A detected conflict between two memory entries., TestMemoryV2Models

### Community 867 - "MCPFullSpec"
Cohesion: 0.24
Nodes (7): get_mcp_full_spec(), MCPFullSpec, MCPPrompt, MCPResource, QA11 — A resource exposed via the MCP server (read-only data source)., QA11 — A reusable prompt template exposed via MCP., QA11 — Complete MCP server spec: tools + resources + prompts. Beyond just tool…

### Community 868 - "DiscordChannelAdapter"
Cohesion: 0.28
Nodes (5): DiscordChannelAdapter, Any, Format OrgResponse as Discord Interaction Response (type 4 = channel message)., Discord bot integration via Interactions Webhook., Verify Discord Ed25519 signature on interaction payload.

### Community 869 - "test_gap_completions.py"
Cohesion: 0.14
Nodes (12): CitationVerifier, Any, Verifies that cited claims are supported by source documents., Legacy LLM reranker retained for non-model-specific callers., Reranker, asyncio, Tests for GAP 2A-2D completions., test_citation_verifier_no_provider() (+4 more)

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

### Community 879 - "test_critical_fixes.py"
Cohesion: 0.18
Nodes (12): _agent_source(), asyncio, Tests for critical bug fixes in AgentVerse. Covers: CRITICAL-1:…, Read combined source of graph.py and all node mixin files., RedisCostController must have check_and_record (not just…, graph.py must not call model_router.route() — use model_for() or similar., Concurrent wave step mutations must not corrupt AgentState., HITLGateway must not use deprecated asyncio.get_event_loop(). (+4 more)

### Community 880 - "test_billing.py"
Cohesion: 0.36
Nodes (8): client(), fixture, MonkeyPatch, TestClient, Tests for billing webhook security — secret enforcement and HMAC validation., test_webhook_accepts_valid_signature(), test_webhook_rejects_invalid_signature(), test_webhook_returns_503_when_secret_unset()

### Community 881 - "TestRedisCache"
Cohesion: 0.22
Nodes (5): Line 146: _redis attribute set., Lines 150-155: publish called on redis., No redis → returns immediately without error., Lines 154-155: redis.publish raises → silenced., TestRedisCache

### Community 882 - "TestIsSignificant"
Cohesion: 0.22
Nodes (5): Lines 339-340: < 10 samples → False., Lines 347-349: scipy not available → mean comparison., Lines 348-349: challenger NOT > control * 1.05 → False., Lines 340-346: scipy available → mannwhitneyu used., TestIsSignificant

### Community 883 - "test_eval_suite_api.py"
Cohesion: 0.27
Nodes (10): app(), authed_client(), asyncio, fixture, Tests for eval suite REST API endpoints., test_add_golden_task(), test_create_eval_suite(), test_eval_suite_auth_required() (+2 more)

### Community 884 - "test_emergency_stop.py"
Cohesion: 0.27
Nodes (11): _make_app(), Tests for Phase 11 (Emergency Stop), Phase 12 (HITL rejection note), and Phase…, Emergency stop response must include all documented fields., Phase 12: rejection note is persisted to execution_context for replanning., _signup(), test_clear_emergency_stop_endpoint_exists(), test_emergency_stop_cancelled_goals_response_shape(), test_emergency_stop_endpoint_exists() (+3 more)

### Community 885 - "TestSharePointConnector"
Cohesion: 0.18
Nodes (4): asyncio, Verify the base URL is correct., Test that bad credentials cause an httpx error (not silent failure)., TestSharePointConnector

### Community 886 - ".subscribe_to_changes"
Cohesion: 0.29
Nodes (5): Long-running coroutine: subscribe to policy_changes channel and reload on…, Targets the pubsub listener loop in PolicyEngine., Lines 226–261: subscribe_to_changes reconnects on exception., Lines 242–255: processes policy change messages., TestPoliciesExtra

### Community 887 - "TestPdfIngestor"
Cohesion: 0.18
Nodes (5): Mocked pypdf returns text chunks., Pages with < 30 chars of text are skipped., Page returning None for text is handled., Exception during extraction returns empty list., TestPdfIngestor

### Community 888 - "TestPdfIngestor"
Cohesion: 0.18
Nodes (6): Happy path: pypdf is available with real-ish page content., Pages with < 30 chars of text are skipped., PdfReader exception produces empty list (not crash)., None/empty extract_text is skipped., When pypdf not installed, returns placeholder chunk., TestPdfIngestor

### Community 889 - "gcp_server.py"
Cohesion: 0.36
Nodes (7): _api_key_param(), _auth_headers(), call_tool(), _get_access_token(), Any, GCP MCP server — Google Cloud Platform operations. Environment variables:…, Obtain a Google OAuth2 access token from available credentials. Priority: 1.…

### Community 890 - "TestGetMetrics"
Cohesion: 0.22
Nodes (4): Lines 1836-1841: cost_today_usd from cost_controller., Line 1840-1841: exception → 0.0., Lines 1751-1800: DB failure → in-memory fallback., TestGetMetrics

### Community 891 - "chat/templates.py"
Cohesion: 0.24
Nodes (5): _now(), datetime, Prompt templates (personas) for the chat system. Built-in personas + user-saved…, Template, TemplateStore

### Community 892 - "app/ingestion/connectors/__init__.py"
Cohesion: 0.20
Nodes (5): GDriveConnector — lists and downloads files from Google Drive for ingestion.…, Ingestion connectors — external data source adapters., NotionConnector — fetches pages from the Notion API for ingestion. Usage -----…, SharePoint / OneDrive connector via Microsoft Graph API. Authentication: OAuth…, Tests for SharePoint connector.

### Community 893 - ".exchange_code"
Cohesion: 0.20
Nodes (5): Exchange authorization code for tokens (PKCE flow)., Persist an OAuth token to the database for cross-restart recovery., Encrypt *value* using the vault if available, else return as-is., Remove OAuth state tokens older than 10 minutes., Get and validate a pending OAuth flow by state token.

### Community 894 - "brevo_server.py"
Cohesion: 0.22
Nodes (8): call_tool(), _headers(), Any, Brevo (formerly Sendinblue) MCP server — email, contacts, campaigns.…, call_tool(), _headers(), Any, WhatsApp Business MCP server — send messages via Meta Cloud API. Environment:…

### Community 895 - "firebase_server.py"
Cohesion: 0.33
Nodes (9): call_tool(), _from_firestore_doc(), _fs_base(), _headers(), Any, Firebase / Firestore MCP server — document database and push notifications.…, Convert a Python value to Firestore REST API typed value., Flatten a Firestore REST document into plain Python dict. (+1 more)

### Community 896 - "._decompose"
Cohesion: 0.32
Nodes (4): Any, Use LLM to decompose goal into independent sub-tasks., Synthesize results from all sub-agents via LLM into a coherent answer., Decompose and execute goal across multiple sub-agents.

### Community 897 - "PolicyEvidenceEngine"
Cohesion: 0.20
Nodes (6): get_policy_evidence(), PolicyEvidenceEngine, Return True if the decision has at least one evidence record., P1 — Every policy decision must have a justification chain. When the system…, Link a decision to its supporting evidence., Return all evidence supporting a policy decision.

### Community 898 - "_resolve_request_strategy"
Cohesion: 0.29
Nodes (8): Resolve an API strategy ID and translate contract errors to stable HTTP…, _resolve_request_strategy(), MonkeyPatch, parametrize, test_rag_query_default_is_canonical_hybrid(), test_rag_query_rejects_unknown_strategy(), test_rag_query_resolves_every_known_strategy(), test_rag_query_resolves_historical_ids()

### Community 899 - "certify_sandbox"
Cohesion: 0.29
Nodes (7): certify_sandbox(), BaseModel, datetime, timedelta, Evidence-backed production sandbox readiness certification., SandboxCertificationEvidence, test_sandbox_certification_fails_closed_for_missing_or_expired_evidence()

### Community 900 - "test_capabilities.py"
Cohesion: 0.46
Nodes (6): _auth_header(), _make_app(), Tests for Phase 2: Capability Registry API., test_capabilities_endpoint_returns_list(), test_capabilities_search_endpoint_exists(), test_missing_capabilities_endpoint_exists()

### Community 901 - "KokoroTTS"
Cohesion: 0.27
Nodes (3): KokoroTTS, Any, Kokoro TTS — MIT-licensed, 82 M params, high quality, no API key. Requires: pip…

### Community 902 - "OmniVoiceTTS"
Cohesion: 0.29
Nodes (3): OmniVoiceTTS, Any, OmniVoice TTS — k2-fsa/OmniVoice, local, 600+ languages, RTF 0.025. Requires:…

### Community 903 - "test_tool_aware_planning.py"
Cohesion: 0.24
Nodes (9): _agent_source(), Tests for Phase 5: tool-aware planning in graph.py., graph.py _node_plan must inject tool schemas into system prompt., Plan validation should warn about unknown tools., Executor prompt must match the JSON-only tool-call parser contract., Read combined source of graph.py and all node mixin files., test_executor_prompt_requires_structured_tool_call_json(), test_graph_injects_tool_schemas_into_planner() (+1 more)

### Community 904 - "test_insights.py"
Cohesion: 0.28
Nodes (12): _make_app(), FastAPI, Tests for /insights endpoints., test_agent_health_returns_defaults_without_db(), test_analysis_returns_404_for_unknown_goal(), test_analysis_returns_heuristic_suggestions(), test_benchmarks_returns_platform_data(), test_estimate_requires_auth() (+4 more)

### Community 905 - "test_trigger_fire_e2e.py"
Cohesion: 0.27
Nodes (9): _create_webhook_trigger(), Any, e2e_full: trigger → dispatcher → real goal, proven against live Postgres+Redis.…, Sanity: firing a non-existent trigger is a clean 404, not a 503/500., Create a webhook trigger with a goal_template; return its schedule_id., # NOTE: goal_template currently lives on the store record, not on the, # NOTE: reading back the persisted trigger_events audit row is intentionally, test_fire_creates_real_goal_and_dedups_second_fire() (+1 more)

### Community 906 - "_FakeWorkflowMCPClient"
Cohesion: 0.31
Nodes (5): _FakeAgentStore, _FakeWorkflowMCPClient, Any, SimpleNamespace, test_workflow_step_complete_redacts_authorization_bearer_tool_output()

### Community 907 - "load/goal_submission.js"
Cohesion: 0.20
Nodes (8): errorRate, GOALS, goalsFailed, goalsSubmitted, options, pollLatency, submitLatency, VUS

### Community 908 - "test_celery_critical.py"
Cohesion: 0.20
Nodes (9): Tests for Celery infrastructure critical fixes., Keycloak realm-export.json must exist for docker-compose to start., OTel collector config must exist for the otel-collector service., fire_due_schedules must use continue not raise on per-schedule errors., goals_dlq must be consumed by the worker — check docker-compose., test_fire_due_schedules_continues_on_error(), test_goals_dlq_queue_in_worker_queues(), test_keycloak_realm_file_exists() (+1 more)

### Community 909 - "TestFireDueSchedules"
Cohesion: 0.20
Nodes (4): Lines 1261, 1291-1384: schedule types and exception path., Line 1261: advance_and_dispatch returns None when goal_kwargs is None., Line 1372-1373: exception processing a schedule is logged and continued., TestFireDueSchedules

### Community 910 - "test_sse_bridge_sentinel.py"
Cohesion: 0.24
Nodes (8): asyncio, Test SSE bridge sends sentinel on terminal events (FIX H9/H10)., subscribe_events must create a bounded queue to prevent OOM., _subscribe_celery_goal_events must send _SENTINEL after terminal events., test_bridge_sends_sentinel_for_terminal_events(), test_sentinel_sent_on_goal_complete(), test_sentinel_sent_on_goal_failed(), test_subscribe_events_uses_bounded_queue()

### Community 911 - "get_sandbox_config"
Cohesion: 0.28
Nodes (8): get_sandbox_config(), Any, get, Request, Per-tenant staging/sandbox environment. Provides a sandboxed copy of the…, Submit a goal in sandbox mode — uses SimulationRunner, no real tools called., Return sandbox configuration for the tenant., submit_sandbox_goal()

### Community 912 - "Base"
Cohesion: 0.09
Nodes (16): ChatArtifact, ChatMessage, ChatMessageUsage, ChatSession, ChatSessionFolder, Base, SQLAlchemy ORM models for the chat feature., Artifact DB model for storing RPA outputs, screenshots, reports, etc. (+8 more)

### Community 913 - "SlackChannelAdapter"
Cohesion: 0.33
Nodes (4): Any, Slack channel adapter — handles slash commands and @mention events., Format as Slack Block Kit message., SlackChannelAdapter

### Community 914 - "WebhookChannelAdapter"
Cohesion: 0.31
Nodes (5): _infer_command_from_trigger(), Any, Generic HMAC-signed webhook receiver., Infer a natural-language command from a webhook trigger event., WebhookChannelAdapter

### Community 916 - "gmail_server.py"
Cohesion: 0.38
Nodes (6): _build_mime_message(), call_tool(), _headers(), Any, Gmail MCP server — email reading, sending, drafts, and label management via…, Build a base64url-encoded RFC 2822 message.

### Community 917 - "test_orchestration_integration.py"
Cohesion: 0.32
Nodes (7): integration, GoalRuntimeProfile JSON must be serializable to execution_context format., SemanticCacheBridge must not leak cache entries between tenants., Full flow: submit goal with DYNAMIC_ORCHESTRATION=true → goal accepted, no…, test_full_goal_submission_with_dynamic_orchestration(), test_goal_runtime_profile_persisted_to_execution_context(), test_semantic_cache_bridge_tenant_isolation_with_real_cache()

### Community 918 - "Any"
Cohesion: 0.25
Nodes (4): Any, Return aggregated (noisy) statistics for a signal type., Return aggregated benchmarks across all orgs for the tenant., Return the last N turns of a conversation.

### Community 919 - "SubTenantManager"
Cohesion: 0.22
Nodes (5): get_sub_tenant_manager(), QA4 — An enterprise child tenant under a parent tenant., QA4 — Manage enterprise hierarchy of sub-tenants., SubTenant, SubTenantManager

### Community 920 - "_mock_boto3_ses"
Cohesion: 0.15
Nodes (13): _mock_boto3_ses(), _mock_boto3_sqs(), Any, Return a mock boto3 SES client whose methods return the given dicts., test_ses_get_send_statistics(), test_ses_list_identities(), test_ses_send_email(), test_ses_unknown_tool() (+5 more)

### Community 921 - "coordination_magentic.py"
Cohesion: 0.38
Nodes (11): get_ledger(), HumanReviewRequest, _ledger(), list_revisions(), Any, BaseModel, get, Request (+3 more)

### Community 922 - "_ctx"
Cohesion: 0.44
Nodes (4): has_feature(), Return True if the tenant's plan includes the given feature., _ctx(), TestHasFeature

### Community 923 - "ToolTrace"
Cohesion: 0.25
Nodes (4): Any, ToolTrace — per-goal observability trace for tool calls., ToolCallRecord, ToolTrace

### Community 924 - "MacOSSayTTS"
Cohesion: 0.25
Nodes (3): MacOSSayTTS, macOS System TTS — uses the built-in `say` command, no downloads required.…, macOS built-in TTS via the `say` command. No model files required.

### Community 925 - "test_magentic_api.py"
Cohesion: 0.26
Nodes (7): HumanReviewDecision, MagenticHumanReviewService, One-time, session-scoped Magentic human-review responses., _app(), FastAPI, test_human_review_token_is_one_time_and_session_scoped(), test_ledger_reads_are_paginated_immutable_and_tenant_scoped()

### Community 926 - "TestPersistOutcome"
Cohesion: 0.25
Nodes (4): Line 191: col = 'win_count' when won=True., col = 'loss_count' when won=False., Lines 200-202: DB exception → logged, no raise., TestPersistOutcome

### Community 927 - "PromptVariantSelector"
Cohesion: 0.33
Nodes (4): PromptVariant, PromptVariantSelector, PromptVariantSelector — deterministic A/B variant selection per goal_id., Get the currently active prompt variant ID for A/B testing via…

### Community 928 - "api/test_memory_api.py"
Cohesion: 0.31
Nodes (8): _get_knowledge_routes(), _get_memory_routes(), Tests for Phase 10 memory inspection + delete endpoints. These tests verify…, Directly inspect the knowledge router without creating a full app., Directly inspect the memory router without creating a full app., test_knowledge_url_ingest_endpoint_exists(), test_memory_list_endpoint_exists(), test_memory_recall_endpoint_exists()

### Community 929 - "test_mfa_module.py"
Cohesion: 0.22
Nodes (5): Test MFA module structure and TOTP logic., Correct TOTP code must pass verification., Wrong TOTP code must fail., test_mfa_verify_correct_totp_code(), test_mfa_verify_wrong_code()

### Community 930 - "AuditLog"
Cohesion: 0.20
Nodes (11): ApprovalRequest, AuditLog, PolicyVersion, Base, SQLAlchemy ORM models for governance: audit log, approval requests, policy…, Append-only audit trail — immutability enforced by DB trigger., Human-in-the-loop approval gate for high-risk agent actions., Immutable snapshot of a policy at a given version number (migration 0056). (+3 more)

### Community 931 - "acoustic_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Acoustic (IBM) Marketing Cloud MCP server — campaigns, contacts, and email…

### Community 932 - "apache_kafka_server.py"
Cohesion: 0.40
Nodes (4): _auth(), call_tool(), Any, Apache Kafka MCP server — topic and consumer-group management via Confluent…

### Community 935 - "test_k8s_manifests.py"
Cohesion: 0.42
Nodes (8): kustomization_resources(), read_manifest(), test_backend_and_worker_pdbs_protect_rollouts(), test_external_secret_replaces_raw_secret_placeholder(), test_kustomization_includes_phase_12_resources(), test_migration_job_runs_alembic_upgrade_head_before_rollout(), test_networkpolicy_limits_backend_worker_data_store_access(), test_worker_hpa_uses_queue_depth_external_metric()

### Community 936 - "ecwid_server.py"
Cohesion: 0.33
Nodes (3): call_tool(), Any, Ecwid MCP server — Ecwid e-commerce store products, orders, and statistics.…

### Community 938 - "TestBayesianProbBetter"
Cohesion: 0.22
Nodes (5): Candidate 2x better than control → prob > 0.95., Equal means → prob ≈ 0.5., Control 2x better than candidate → prob < 0.1., Fix 6: numpy.default_rng() per call — each call creates a new Generator.…, TestBayesianProbBetter

### Community 939 - "TestDocxIngestor"
Cohesion: 0.22
Nodes (5): When python-docx not installed, returns placeholder chunk., Happy path: python-docx is available and parses paragraphs., If Document() raises, returns []., Paragraphs shorter than 20 chars are filtered out., TestDocxIngestor

### Community 940 - "tenancy/test_context.py"
Cohesion: 0.22
Nodes (3): Tests for TenantContext, PlanTier, and PlanLimits., test_tenant_context_holds_fields(), test_tenant_context_is_frozen()

### Community 941 - "sap_server.py"
Cohesion: 0.38
Nodes (6): _base_url(), call_tool(), _get_token(), Any, AsyncClient, SAP ERP MCP server — purchase orders, materials, inventory, and procurement.…

### Community 942 - "_cosine_similarity"
Cohesion: 0.43
Nodes (3): _cosine_similarity(), Covers line 52: mag == 0 returns 0.0., TestCosineSimularity

### Community 943 - "ApprovalChain"
Cohesion: 0.17
Nodes (6): ApprovalChain, Any, Alias for required_roles — preferred public surface., Return 'all' or 'any' normalised from strategy., Return the chain for *chain_id* (or a registered alias), or None., Return every built-in approval chain.

### Community 944 - "TestSubscribeEvents"
Cohesion: 0.29
Nodes (3): Lines 2021-2027: terminal goal returns without queuing., Lines 2030-2040: live queue receives None sentinel → exits., TestSubscribeEvents

### Community 945 - "WhatsAppChannelAdapter"
Cohesion: 0.39
Nodes (3): Any, WhatsApp Business Cloud API adapter., WhatsAppChannelAdapter

### Community 946 - "quickbooks_server.py"
Cohesion: 0.47
Nodes (5): _base_url(), call_tool(), _headers(), Any, QuickBooks Online MCP server — accounting queries, invoices, customers, and…

### Community 947 - "mailchimp_server.py"
Cohesion: 0.39
Nodes (7): _auth(), _base(), call_tool(), Any, Mailchimp MCP server — lists, campaigns, and members via Mailchimp API v3.…, _server_prefix(), _subscriber_hash()

### Community 948 - "notion_server.py"
Cohesion: 0.39
Nodes (7): call_tool(), _call_tool_inner(), _format_page(), _notion_headers(), Any, Notion MCP server — Notion REST API v1 integration. Environment variables:…, Extract a clean summary from a raw Notion page object.

### Community 949 - ".__init__"
Cohesion: 0.33
Nodes (5): Admission, CheckpointCallback, Executor, ReleaseBudget, ReserveBudget

### Community 950 - "CollectiveIntelligence"
Cohesion: 0.29
Nodes (5): CollectiveIntelligence, get_collective_intel(), P10 — Cross-org learning with differential privacy. Aggregates anonymised…, Add Laplace noise for differential privacy., Record an anonymised signal from an org.

### Community 953 - "test_guardrail_rules_persistence.py"
Cohesion: 0.15
Nodes (16): PostgresGuardrailRuleRepository, async_sessionmaker, AsyncSession, Durable, tenant-scoped store for GuardrailRule objects., Load rules. With ``tenant_id`` set, scope to that tenant under its RLS context;…, _alembic(), _app_role_url(), app_url() (+8 more)

### Community 954 - "amadeus_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Amadeus MCP server — travel search, flight booking, and hotel discovery.…

### Community 955 - "test_startup_wiring.py"
Cohesion: 0.17
Nodes (11): ReflexionStore.__init__ must accept db_factory., ABTestingEngine.__init__ must accept db_factory., load_tool_trust_from_db('*') must load all tenants, not return 0 rows., query_nodes() for unknown tenant must schedule DB load., KnowledgeGraphStore must track hydrated tenants for lazy load., test_ab_testing_engine_accepts_db_factory(), test_kg_lazy_hydration_triggers_on_first_miss(), test_knowledge_graph_store_has_hydrated_tenants_set() (+3 more)

### Community 956 - "BrowserFallbackTTS"
Cohesion: 0.07
Nodes (10): AzureTTS, Azure Cognitive Services TTS — requires AZURE_TTS_KEY + AZURE_TTS_REGION., BrowserFallbackTTS, Browser fallback TTS — returns empty WAV; browser uses Web Speech API instead.…, ElevenLabsTTS, ElevenLabs TTS — paid API, voice cloning. Requires ELEVENLABS_API_KEY., OpenAITTS, OpenAI TTS — requires OPENAI_API_KEY. (+2 more)

### Community 957 - "_cleanup_expired_crdt_tokens"
Cohesion: 0.24
Nodes (6): _cleanup_expired_crdt_tokens(), Evict tokens past their TTL from the in-memory store., Yjs CRDT WebSocket — binary message fan-out with Redis pub/sub. Authentication…, yjs_crdt_sync(), Short-lived CRDT tokens are cleaned up when expired., TestCollabCRDTTokenStore

### Community 958 - "graph"
Cohesion: 0.20
Nodes (9): Guard the source: the injection block must not reference ``e.category``., test_graph_dept_memory_block_does_not_read_category(), graph(), provider(), fixture, tenant_ctx(), test_production_retrieval_paths_have_no_store_or_engine_search(), 0B.3: Verifier cache must use should_skip_cache guard. (+1 more)

### Community 959 - "test_hitl_gate.py"
Cohesion: 0.29
Nodes (5): _agent_source(), Test HITL gate is enforced by default in fully-autonomous mode (FIX C1/C7)., graph.py must gate the write_high bypass behind…, Read combined source of graph.py and all node mixin files., test_hitl_gate_source_contains_env_flag()

### Community 961 - "NotebookParser"
Cohesion: 0.20
Nodes (7): NotebookParser, ParquetParser, Jupyter Notebook parser — cell-pair extraction (code + output)., Parse .ipynb Jupyter notebooks into cell-pair text chunks. Each code cell + its…, Parse Parquet files — sample rows and schema as text., test_notebook_parser_code_cells(), test_notebook_parser_invalid()

### Community 962 - "TestDocxIngestor"
Cohesion: 0.25
Nodes (4): Happy path with mocked python-docx., Paragraphs shorter than 20 chars are filtered out., Exception returns empty list., TestDocxIngestor

### Community 963 - "_CallTrackerSession"
Cohesion: 0.22
Nodes (3): _CallTrackerSession, Session that returns pre-configured rows for successive execute() calls., Set rows to return for calls 0, 1, 2, ...

### Community 964 - "get_public_status"
Cohesion: 0.29
Nodes (6): get_public_status(), Any, get, Request, Public status page API — no authentication required., Public system health — used by the status page, no auth required.

### Community 965 - "amazon_sqs_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _client(), Any, Amazon SQS MCP server — message queue operations via SQS API. Environment:…

### Community 966 - "constant_contact_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Constant Contact MCP server — email marketing contacts, lists, and campaigns.…

### Community 967 - "0097_coordination_runtime.py"
Cohesion: 0.38
Nodes (5): _domain_column(), Column, Add the canonical durable coordination runtime. Revision ID:…, _rls_policy(), upgrade()

### Community 968 - "get_logger"
Cohesion: 0.02
Nodes (109): call_tool(), _headers(), Any, Airtable MCP server — database records management across bases and tables.…, call_tool(), _headers(), Any, Capsule CRM MCP server — contacts, opportunities, and notes management.… (+101 more)

### Community 969 - "bamboohr_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, BambooHR MCP server — HR employee data, time-off, and org structure.…

### Community 970 - "test_gateway_auth.py"
Cohesion: 0.29
Nodes (5): Per-channel authentication guard — Q9 of spec. Each channel has its own auth…, # TODO: Look up hashed key in DB, guard(), fixture, Phase 2 — Gateway auth tests. Covers app/gateway/auth.py: -…

### Community 971 - "buildium_server.py"
Cohesion: 0.33
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Buildium MCP server — property management, leases, tenants, and financials.…

### Community 972 - "jotform_server.py"
Cohesion: 0.60
Nodes (4): call_tool(), _params(), Any, JotForm MCP server — form building and submission management. Environment:…

### Community 973 - "cloudinary_server.py"
Cohesion: 0.43
Nodes (6): _base(), call_tool(), Any, Cloudinary MCP server — media upload, transformation, and management.…, Generate Cloudinary API signature., _sign()

### Community 974 - "evernote_server.py"
Cohesion: 0.38
Nodes (6): call_tool(), _enml_wrap(), _headers(), Any, Evernote MCP server — note-taking, notebooks, and search via Evernote API.…, Wrap plain text in minimal ENML.

### Community 975 - "doordash_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _make_jwt(), Any, DoorDash Drive MCP server — on-demand delivery creation and management.…, Create JWT for DoorDash authentication.

### Community 976 - "looker_server.py"
Cohesion: 0.33
Nodes (6): call_tool(), _get_token(), Any, AsyncClient, Looker MCP server — Looker Business Intelligence. Environment: LOOKER_BASE_URL:…, Obtain a Looker API bearer token (cached).

### Community 977 - "freshservice_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Freshservice MCP server — IT service management (ITSM) integration.…

### Community 978 - "microsoft_onenote_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft OneNote MCP server — notebook and page management via Microsoft Graph…

### Community 979 - "yotpo_server.py"
Cohesion: 0.33
Nodes (6): call_tool(), _get_utoken(), Any, AsyncClient, Yotpo MCP server — Yotpo reviews, loyalty points, and marketing campaigns.…, Exchange app_key+secret for a short-lived uToken.

### Community 980 - ".call_tool"
Cohesion: 0.33
Nodes (4): Any, Call a tool via WebSocket RPC and await the result. Args: tool_name: MCP tool…, Subscribe to a real-time event channel. Yields data payloads from the channel…, List available tools on the WS MCP server.

### Community 981 - "StrategicAdvisor"
Cohesion: 0.29
Nodes (5): Weekly strategic intelligence brief for an org., P4 — AI-generated weekly intelligence brief. Runs every Sunday 18:00 org…, Generate the weekly strategic brief for an org., StrategicAdvisor, StrategicBrief

### Community 982 - "VoiceWebhookAdapter"
Cohesion: 0.24
Nodes (6): Any, Trim text to TTS-friendly length, ending at sentence boundary., Voice command adapter — receives pre-transcribed voice input. Typical flow: 1.…, Verify HMAC-SHA256 signature on the payload., Return a TTS-optimised response (shorter, conversational)., VoiceWebhookAdapter

### Community 983 - "gorgias_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Gorgias MCP server — e-commerce customer support platform. Environment:…

### Community 984 - "microsoft_todo_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft To Do MCP server — task list management via Microsoft Graph API.…

### Community 985 - "moosend_server.py"
Cohesion: 0.60
Nodes (4): call_tool(), _params_with_key(), Any, Moosend MCP server — email marketing lists, subscribers, and campaigns.…

### Community 987 - "._assign"
Cohesion: 0.29
Nodes (3): Simple round-robin — for now returns None (DB-backed in production)., Picks reviewer with fewest pending requests., Stub — hooks into a skills registry in production.

### Community 988 - "omnisend_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Omnisend MCP server — omnichannel marketing contacts, segments, campaigns, and…

### Community 989 - "AgentVerse Load Tests"
Cohesion: 0.29
Nodes (6): AgentVerse Load Tests, Autoscale signal, CI smoke profile, Requirements, Running k6, Running locust

### Community 990 - "qa-agent.md"
Cohesion: 0.29
Nodes (6): Common failure patterns, Coverage thresholds, Key security checks, Known pre-existing failures, Quick commands, Test matrix — what to run for each PR

### Community 991 - "monday_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _monday_gql(), Any, Monday.com MCP server — monday.com GraphQL API v2 integration. Environment…

### Community 993 - "smoke.js"
Cohesion: 0.33
Nodes (6): errorRate, handleSummary(), healthLatency, metricsLatency, options, textSummary()

### Community 994 - "test_mission_execute_wired_services.py"
Cohesion: 0.38
Nodes (6): _make_app(), Any, asyncio, FastAPI, Regression: the mission-execute path must use the REQUEST's wired app.state…, test_mission_execute_threads_request_app_state()

### Community 995 - "pushbullet_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Pushbullet MCP server — push notifications, links, notes, and file sharing to…

### Community 996 - "toast_pos_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Toast POS MCP server — restaurant orders, menu management, and payments.…

### Community 997 - "webflow_server.py"
Cohesion: 0.47
Nodes (5): call_tool(), _headers(), Any, Webflow MCP server — CMS collections, sites, and publishing. Environment:…, _site_id()

### Community 998 - "PostgreSQLConnector"
Cohesion: 0.20
Nodes (9): _build_dsn(), PostgreSQLConnector, BaseConnector, register, Convert a DB row to human-readable text for embedding., PostgreSQL incremental ingestion (query or CDC mode)., Yield rows from configured tables newer than cursor., _row_to_text() (+1 more)

### Community 999 - "WebCrawlConnector"
Cohesion: 0.24
Nodes (6): BaseConnector, register, Web crawl connector with sitemap and robots.txt support., Crawl seed URLs and discover new/changed pages., WebCrawlConnector, test_web_crawl_connector_source_type()

### Community 1000 - "error_response"
Cohesion: 0.33
Nodes (5): error_response(), JSONResponse, Request, Standard error response utilities., Return a standardized error response with a correlation_id. The correlation_id…

### Community 1001 - "GoalTokenStore"
Cohesion: 0.33
Nodes (3): GoalTokenStore, Any, Tracks active goal tokens and supports revocation.

### Community 1002 - "0101_magentic_moa.py"
Cohesion: 0.47
Nodes (4): _base_columns(), Column, _rls(), upgrade()

### Community 1003 - "0102_camel_generative_swarm_auction.py"
Cohesion: 0.47
Nodes (4): _base(), Column, _rls(), upgrade()

### Community 1004 - "0104_memory_learning.py"
Cohesion: 0.47
Nodes (4): Column, _rls(), _timestamps(), upgrade()

### Community 1006 - "asana_server.py"
Cohesion: 0.53
Nodes (5): _asana_headers(), call_tool(), _call_tool_inner(), Any, Asana MCP server — Asana REST API v1.0 integration. Environment variables:…

### Community 1007 - "get_approval_engine"
Cohesion: 0.33
Nodes (6): get_approval_engine(), Return the process-level ApprovalChainEngine singleton., check_requires_approval returns the prod_deploy chain for matching action., Non-risky actions return None (no approval required)., test_g06_check_requires_approval_matches_prod_deploy(), test_g06_check_requires_approval_no_match_for_benign_action()

### Community 1008 - "tests/conftest.py"
Cohesion: 0.22
Nodes (10): app(), _docker_available(), _keep_scaling_tasks_bound(), fixture, pytest_collection_modifyitems(), Shared pytest fixtures for all test packages., Return True if a Docker daemon is reachable., Auto-skip tests that require Docker or OPENAI_API_KEY when unavailable. (+2 more)

### Community 1010 - "bitbucket_server.py"
Cohesion: 0.53
Nodes (5): _auth_header(), call_tool(), _call_tool_inner(), Any, Bitbucket MCP server — wraps Bitbucket Cloud REST API 2.0. Environment…

### Community 1011 - "chargebee_server.py"
Cohesion: 0.47
Nodes (5): _auth(), _base(), call_tool(), Any, Chargebee MCP server — subscription billing, customers, and invoices.…

### Community 1012 - "TestSubscribeHITLRejections"
Cohesion: 0.33
Nodes (4): Lines 317-353: inner subscriber loop., Redis connect failure → except block → sleep (cancelled immediately)., Lines 326-349: PMessa processing updates goal record., TestSubscribeHITLRejections

### Community 1014 - "customerio_server.py"
Cohesion: 0.47
Nodes (5): _app_headers(), call_tool(), Any, Customer.io MCP server — customers, events, and campaigns. Environment:…, _track_headers()

### Community 1015 - "env.py"
Cohesion: 0.50
Nodes (3): Alembic environment — async engine, DB URL sourced from application Settings., _run_async(), _run_migrations()

### Community 1016 - "freshdesk_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Freshdesk MCP server — customer support tickets and contacts. Environment:…

### Community 1017 - "athenahealth_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Athenahealth EHR MCP server — patient records, appointments, and clinical data.…

### Community 1018 - "call_tool"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Calendly MCP server — scheduling, event types, and invitations. Environment:…

### Community 1019 - "instagram_server.py"
Cohesion: 0.53
Nodes (5): _account_id(), call_tool(), _params(), Any, Instagram MCP server — Graph API for business account management. Environment:…

### Community 1020 - "klaviyo_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Klaviyo MCP server — profiles, lists, events, and campaigns. Environment:…

### Community 1021 - "mattermost_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Mattermost MCP server — interact with Mattermost REST API v4. Environment:…

### Community 1022 - "microsoft_outlook_server.py"
Cohesion: 0.53
Nodes (5): _build_recipients(), call_tool(), _headers(), Any, Microsoft Outlook MCP server — email management via Microsoft Graph API.…

### Community 1023 - "test_coordination_handoffs.py"
Cohesion: 0.38
Nodes (7): _app(), _create(), Membership, FastAPI, TestClient, test_handoff_api_accept_replay_conflict_and_missing_token(), test_handoff_api_is_idempotent_tenant_and_session_scoped()

### Community 1024 - "pipedrive_server.py"
Cohesion: 0.53
Nodes (5): _base_url(), call_tool(), _params(), Any, Pipedrive MCP server — deals, persons, organizations, activities, stages.…

### Community 1025 - "test_tools_router.py"
Cohesion: 0.29
Nodes (9): app(), asyncio, fixture, Tests for the /tools/* API endpoints., test_email_send_requires_auth(), test_execute_code_happy_path(), test_execute_code_requires_auth(), test_file_ops_require_auth() (+1 more)

### Community 1026 - "pandadoc_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, PandaDoc MCP server — document creation, templates, and eSignature.…

### Community 1027 - "snovio_server.py"
Cohesion: 0.40
Nodes (5): call_tool(), _get_access_token(), Any, Snov.io MCP server — lead generation: email finding, verification, and prospect…, Obtain OAuth 2.0 access token from Snov.io.

### Community 1028 - "test_goals_batch_submit_route_exists"
Cohesion: 0.40
Nodes (5): asyncio, Goals app schema test — checks /goals/batch or similar exists., Verify a streaming endpoint is registered in the app., test_goals_batch_submit_route_exists(), test_goals_stream_endpoint_exists()

### Community 1030 - "wave_server.py"
Cohesion: 0.47
Nodes (5): call_tool(), _gql(), Any, AsyncClient, Wave MCP server — accounting, invoices, customers, and transactions via…

### Community 1031 - "test_hitl_gap_closures.py"
Cohesion: 0.10
Nodes (17): fake_org(), fake_redis(), _FakeOrg, _FakeRedis, inspect_source(), prod_chain(), Any, fixture (+9 more)

### Community 1032 - "RoleResolver"
Cohesion: 0.22
Nodes (7): Any, Resolve the complete permission set for a role, traversing parent chain., RoleResolver, A role that references itself should not infinite loop., test_role_resolver_cycle_guard(), test_role_resolver_role_not_found(), test_role_resolver_simple_role()

### Community 1033 - ".test_db_exception_falls_back_to_memory"
Cohesion: 0.40
Nodes (3): Lines 2069-2073: fallback to in-memory., Lines 2069-2070: DB exception → in-memory., TestGetAuditEntries

### Community 1034 - "wrike_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), Any, Wrike MCP server — Wrike REST API v4 integration. Environment variables:…, _wrike_headers()

### Community 1036 - "zoho_crm_server.py"
Cohesion: 0.47
Nodes (5): _base_url(), call_tool(), _headers(), Any, Zoho CRM MCP server — records CRUD and search across CRM modules. Environment…

### Community 1038 - "db_tenant_ctx"
Cohesion: 0.50
Nodes (4): db_tenant_ctx(), fixture, A TenantContext whose tenant_id actually exists in the tenants table. Creates a…, tenant_ctx()

### Community 1039 - "azure_devops_server.py"
Cohesion: 0.42
Nodes (8): _base_url(), call_tool(), _call_tool_inner(), _headers(), _org(), _project(), Any, Azure DevOps MCP server — manage repos, PRs, work items, and pipelines.…

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

### Community 1047 - "facebook_conversions_server.py"
Cohesion: 0.31
Nodes (8): call_tool(), Any, AsyncClient, Facebook Conversions MCP server — Facebook Conversions API server-side event…, SHA256 hash a string for PII fields., Send events to the Facebook Conversions API., _send_events(), _sha256()

### Community 1048 - "test_keycloak_sso.py"
Cohesion: 0.47
Nodes (5): asyncio, Tests for SSO JIT tenant provisioning., test_create_tenant_from_sso_returns_api_key(), test_get_tenant_by_sso_sub_in_memory(), test_get_tenant_by_sso_sub_not_found()

### Community 1049 - "test_org_db_e2e.py"
Cohesion: 0.40
Nodes (5): Any, e2e_full: the AI Organization OS DB path must work end-to-end. Regression guard…, POST /v1/org/compose must build a real org with departments + missions.…, test_org_compose_from_nl_builds_a_real_org(), test_org_list_and_create_roundtrip()

### Community 1051 - "test_internal_url_is_blocked"
Cohesion: 0.47
Nodes (5): _collection(), Any, parametrize, e2e_full: the SSRF egress guard is enabled on the live URL-ingestion path. The…, test_internal_url_is_blocked()

### Community 1052 - "test_goal_idempotency_e2e.py"
Cohesion: 0.33
Nodes (8): fresh_client(), _inline_goals(), Any, fixture, e2e_full: goal submission idempotency on the live path. Proves the wired…, A dedicated tenant per test so cumulative goal caps don't leak across tests.…, test_distinct_idempotency_keys_both_accepted(), test_duplicate_idempotency_key_is_rejected()

### Community 1053 - "TestChannelAuthConstants"
Cohesion: 0.33
Nodes (3): admin scope must cover the core governance actions., Every non-empty scope must include 'read' so users can inspect state., TestChannelAuthConstants

### Community 1054 - "test_redbeat_config.py"
Cohesion: 0.33
Nodes (5): Verify RedBeat scheduler configuration is correctly wired., Beat scheduler should be RedBeat when configured., Beat schedule must be a dict with at least one task., test_celery_app_has_beat_schedule(), test_celery_app_redbeat_scheduler()

### Community 1055 - "TestTenantContextIsolation"
Cohesion: 0.33
Nodes (3): Tenant context isolation — cross-tenant data must not leak., Two TenantContext objects are independent value objects., TestTenantContextIsolation

### Community 1056 - "test_ingestion_pipeline_e2e.py"
Cohesion: 0.28
Nodes (8): _fake_embedder(), Any, fixture, e2e_full: document ingestion → pgvector → retrievable by query. The plan's…, A query against an empty sibling collection must not return the document., Pin a deterministic 768-dim embedder for both ingest and search. Ingest reads…, test_document_ingests_and_is_retrievable(), test_search_isolated_by_collection()

### Community 1059 - "v1/router.py"
Cohesion: 0.50
Nodes (4): api_info(), health_v1(), get, AgentVerse v1 API router.

### Community 1061 - "0091_rag_ingestion_structures.py"
Cohesion: 0.50
Nodes (3): _create_index_concurrently(), Retry an interrupted concurrent build without replacing a valid index., upgrade()

### Community 1062 - "0100_handoffs_group_chat.py"
Cohesion: 0.50
Nodes (3): _json(), Column, upgrade()

### Community 1063 - "affinity_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Affinity CRM MCP server — lists, list entries, persons, organizations.…

### Community 1064 - "apollo_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Apollo.io MCP server — people & company search/enrichment, email lookup.…

### Community 1065 - "attio_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Attio MCP server — records, notes on a modern CRM platform. Environment…

### Community 1066 - "box_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Box MCP server — file management via Box Content API. Environment variables:…

### Community 1068 - "copper_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Copper CRM MCP server — people, companies, and opportunities. Environment…

### Community 1069 - "discord_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Discord MCP server — interact with Discord API v10. Environment:…

### Community 1070 - "gong_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Gong MCP server — call recordings, transcripts, users, and call statistics.…

### Community 1071 - "SalesforceConnector"
Cohesion: 0.36
Nodes (4): BaseConnector, register, Salesforce CRM connector — SObjects via REST API and SOQL., SalesforceConnector

### Community 1072 - "intercom_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Intercom MCP server — customers, conversations, notes, tags. Environment:…

### Community 1073 - "TeamsConnector"
Cohesion: 0.32
Nodes (5): BaseConnector, register, Acquire OAuth2 token via client credentials flow., Microsoft Teams connector — channels, threads, and DMs via Graph API., TeamsConnector

### Community 1074 - "mailerlite_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, MailerLite MCP server — subscribers, groups, and campaigns. Environment:…

### Community 1075 - "netsuite_server.py"
Cohesion: 0.50
Nodes (4): _base(), call_tool(), Any, NetSuite MCP server — ERP records, search, and saved searches via REST API.…

### Community 1076 - "planhat_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Planhat MCP server — companies, end-users, and activity logging. Environment…

### Community 1077 - "jenkins_server.py"
Cohesion: 0.39
Nodes (7): _auth(), call_tool(), _call_tool_inner(), _job_path(), Any, Jenkins MCP server — interact with Jenkins CI/CD via REST API. Environment…, Convert 'folder/job' style names to /job/folder/job/ URL segments.

### Community 1078 - "salesforce_server.py"
Cohesion: 0.50
Nodes (4): _auth_headers(), call_tool(), Any, Salesforce MCP server — SOQL queries, records CRUD, metadata, SOSL search.…

### Community 1079 - "sendgrid_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, SendGrid MCP server — transactional & marketing email via SendGrid v3 API.…

### Community 1080 - "teamwork_server.py"
Cohesion: 0.50
Nodes (4): _base(), call_tool(), Any, Teamwork MCP server — project management, tasks, and milestones. Environment:…

### Community 1081 - "twilio_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Twilio MCP server — SMS, WhatsApp, voice calls, and number lookup. Environment:…

### Community 1082 - "zuora_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _get_token(), Any, Zuora MCP server — subscription billing, accounts, invoices, and subscriptions.…

### Community 1083 - "long_term_extractor.py"
Cohesion: 0.50
Nodes (4): LongTermCandidate, BaseModel, Typed evidence-only long-term memory extraction., validate_extraction()

### Community 1084 - "PolicyEvidence"
Cohesion: 0.40
Nodes (3): PolicyEvidence, Evidence record supporting a policy decision., Record a piece of evidence for a policy.

### Community 1085 - "dr-drill.sh"
Cohesion: 0.90
Nodes (4): fail(), log(), pass(), dr-drill.sh script

### Community 1086 - "_bypass_ssrf"
Cohesion: 0.40
Nodes (4): _bypass_ssrf(), fixture, conftest for tests/e2e — bypass SSRF guard for mock/test hostnames. Several e2e…, Disable SSRF hostname-resolution guard for e2e tests. e2e tests use mock URLs…

### Community 1087 - "_no_resource_limits_in_process"
Cohesion: 0.40
Nodes (4): _no_resource_limits_in_process(), fixture, conftest for tests/execution_environment — prevent in-process resource limits.…, Prevent _set_resource_limits from mutating the pytest process's RLIMIT. Skip…

### Community 1092 - "AgentVerse Load Tests"
Cohesion: 0.40
Nodes (4): AgentVerse Load Tests, Environment Variables, Prerequisites, Run

### Community 1093 - "soak.js"
Cohesion: 0.40
Nodes (4): ENDPOINTS, errorRate, latencyTrend, options

### Community 1094 - "FakeRedis"
Cohesion: 0.18
Nodes (5): FakeRedis, asyncio, MCPClient tool discovery resolves expected Jira tool names from mock MCP…, test_jira_mcp_tools_discovered_by_name(), test_real_atlassian_mcp_smoke_discovers_tools_when_credentials_present()

### Community 1099 - "test_tool_reliability.py"
Cohesion: 0.32
Nodes (6): asyncio, Tests for P2.3 tool reliability memory., test_tool_reliability_api_endpoint_exists(), test_tool_reliability_no_db_get_unreliable_empty(), test_tool_reliability_record_failure(), test_tool_reliability_record_success()

### Community 1101 - "braintree_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Braintree MCP server — payment transactions, customers, and subscriptions.…

### Community 1102 - "test_celery_routing.py"
Cohesion: 0.25
Nodes (7): Tests for Celery worker checkpointer wiring (Fix 1 — Redis LangGraph…, The _WORKER_CHECKPOINTER module-level variable must be present in tasks., _setup_worker_checkpointer must be a callable (connectable to worker_init)., AgentGraph construction in run_goal passes checkpointer= kwarg., test_agent_graph_receives_checkpointer_kwarg(), test_setup_worker_checkpointer_is_callable(), test_worker_checkpointer_module_exists()

### Community 1104 - "MySQLConnector"
Cohesion: 0.33
Nodes (4): MySQLConnector, BaseConnector, register, MySQL / MariaDB connector — query-based incremental ingestion.

### Community 1106 - "google_docs_server.py"
Cohesion: 0.43
Nodes (6): call_tool(), _extract_text(), _google_token(), Any, Google Docs MCP server — create, read, and update Google Documents. Environment…, Pull all text runs from a Docs JSON response into a flat string.

### Community 1107 - "kubernetes_server.py"
Cohesion: 0.52
Nodes (6): call_tool(), _call_tool_inner(), _kube_headers(), _ns(), Any, Kubernetes MCP server — interact with Kubernetes clusters via REST API.…

### Community 1108 - "vercel_server.py"
Cohesion: 0.52
Nodes (6): call_tool(), _call_tool_inner(), _headers(), Any, Vercel MCP server — manage Vercel projects, deployments, and domains.…, _team_params()

### Community 1115 - "digitalocean_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _headers(), Any, DigitalOcean MCP server — manage DigitalOcean resources via v2 API. Environment…

### Community 1116 - "docusign_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, DocuSign MCP server — eSignature envelopes and signing workflows. Environment:…

### Community 1117 - "fireflies_server.py"
Cohesion: 0.47
Nodes (5): call_tool(), _gql(), Any, AsyncClient, Fireflies.ai MCP server — meeting transcription and conversation intelligence.…

### Community 1118 - "freshsales_server.py"
Cohesion: 0.40
Nodes (4): call_tool(), _headers(), Any, Freshsales CRM MCP server — contacts, deals, and accounts management.…

### Community 1119 - "help_scout_server.py"
Cohesion: 0.33
Nodes (5): call_tool(), _get_token(), Any, AsyncClient, Help Scout MCP server — customer support conversations and mailbox management.…

### Community 1120 - "heroku_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _headers(), Any, Heroku MCP server — manage Heroku apps, dynos, and pipelines via Platform API.…

### Community 1121 - "linear_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _gql(), Any, Linear MCP server — Linear GraphQL API integration. Environment variables:…

### Community 1123 - "netlify_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _call_tool_inner(), _headers(), Any, Netlify MCP server — manage Netlify sites, deploys, and functions. Environment…

### Community 1124 - "loadtest/goal_submission.js"
Cohesion: 0.50
Nodes (3): options, submitDuration, submitErrors

### Community 1125 - "AgentVerse — Backend"
Cohesion: 0.50
Nodes (3): AgentVerse — Backend, Development, Stack

### Community 1126 - "_digest"
Cohesion: 0.67
Nodes (3): _digest(), main(), Any

### Community 1127 - "test_graph_critical.py"
Cohesion: 0.13
Nodes (15): _agent_source(), Tests for critical graph.py fixes., When one wave step raises PermissionError, others are cancelled., ToolCall must be importable from tool_calls — no NameError in graph.py, ToolCall can be instantiated without NameError at graph.py usage point., Read combined source of graph.py and all node mixin files., Analytics should look for tool_call_complete not tool_call., GraphState exposes bounded evidence instead of private model reasoning. (+7 more)

### Community 1128 - "shopify_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Shopify MCP server — products, orders, customers, inventory management.…

### Community 1129 - "auth_throughput.js"
Cohesion: 0.50
Nodes (3): authLatency, errorRate, options

### Community 1131 - "test_structlog_migration.py"
Cohesion: 0.50
Nodes (3): Verify critical modules use structlog instead of stdlib logging., Verify that migrated modules do not import stdlib logging directly., test_migrated_modules_use_get_logger()

### Community 1132 - "terraform_server.py"
Cohesion: 0.53
Nodes (5): call_tool(), _headers(), _org(), Any, Terraform Cloud MCP server — workspaces, runs, and variables via API v2.…

### Community 1133 - "scaling/conftest.py"
Cohesion: 0.50
Nodes (3): fixture, Test-isolation hygiene for the scaling (Celery task) suite. Several Celery…, _reset_db_engine_singletons()

### Community 1135 - "woocommerce_server.py"
Cohesion: 0.47
Nodes (5): _auth(), _base(), call_tool(), Any, WooCommerce MCP server — WordPress e-commerce store management. Environment:…

### Community 1136 - "test_spec_module_importable"
Cohesion: 0.50
Nodes (3): parametrize, Every spec-required module must be importable without error., test_spec_module_importable()

### Community 1137 - "TestScopeEnforcementBypass"
Cohesion: 0.50
Nodes (3): 0B.9: No-roles API keys must NOT bypass scope enforcement for writes., API keys without roles are denied on write endpoints by default., TestScopeEnforcementBypass

### Community 1255 - "zendesk_server.py"
Cohesion: 0.47
Nodes (5): _base(), call_tool(), _headers(), Any, Zendesk MCP server — tickets, users, organizations, and search. Environment:…

### Community 1283 - "amazon_ses_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _client(), Any, Amazon SES MCP server — email delivery via SES API. Environment:…

### Community 1284 - "appsheet_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, AppSheet MCP server — no-code app data management and action invocation.…

### Community 1285 - "aweber_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, AWeber MCP server — email marketing subscribers, lists, and broadcasts.…

### Community 1286 - "bigquery_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google BigQuery MCP server — data warehouse queries and management.…

### Community 1287 - "bitly_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Bitly MCP server — URL shortening and click analytics. Environment:…

### Community 1328 - "campaign_monitor_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Campaign Monitor MCP server — email campaigns, subscriber lists, and delivery…

### Community 1491 - "close_crm_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Close CRM MCP server — leads, contacts, and activities. Environment variables:…

### Community 1492 - "cloudflare_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Cloudflare MCP server — CDN, DNS, and Workers management via Cloudflare API v4.…

### Community 1494 - "drip_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Drip MCP server — ecommerce CRM, subscriber management, and email automation.…

### Community 1496 - "emma_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Emma email marketing MCP server — contacts, groups, mailings, and analytics.…

### Community 1498 - "figma_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Figma MCP server — design file access and collaboration via Figma API.…

### Community 1499 - "formstack_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Formstack MCP server — forms, submissions, and documents. Environment:…

### Community 1503 - "getresponse_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, GetResponse MCP server — email marketing contacts, campaigns, and statistics.…

### Community 1504 - "google_forms_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google Forms MCP server — form creation and response management via Google…

### Community 1505 - "google_slides_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google Slides MCP server — presentation management via Google Slides API v1.…

### Community 1506 - "google_tasks_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Google Tasks MCP server — task list and task management via Google Tasks API…

### Community 1507 - "gravity_forms_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Gravity Forms MCP server — WordPress form builder data and submissions.…

### Community 1510 - "hubspot_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, HubSpot MCP server — contacts, companies, deals, notes, CRM search. Environment…

### Community 1515 - "linkedin_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, LinkedIn MCP server — profile, people/company search, and posting. Environment…

### Community 1516 - "loom_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Loom MCP server — video messaging via Loom API v1. Environment: LOOM_API_KEY:…

### Community 1517 - "loops_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Loops MCP server — transactional and marketing email for SaaS products.…

### Community 1518 - "mailgun_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Mailgun MCP server — transactional email sending, domain management, and event…

### Community 1519 - "manychat_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, ManyChat MCP server — chat marketing subscriber management and content…

### Community 1520 - "maropost_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Maropost MCP server — email marketing, contact management, and campaign…

### Community 1521 - "microsoft_excel_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Microsoft Excel MCP server — workbook and spreadsheet management via Microsoft…

### Community 1523 - "onesignal_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, OneSignal MCP server — push notifications, device management, and segments.…

### Community 1525 - "order_desk_server.py"
Cohesion: 0.40
Nodes (3): call_tool(), Any, Order Desk MCP server — Order Desk order management, inventory, and shipments.…

### Community 1529 - "plivo_server.py"
Cohesion: 0.50
Nodes (4): _auth(), call_tool(), Any, Plivo MCP server — cloud communications: SMS, voice calls, and phone number…

### Community 1531 - "postmark_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Postmark MCP server — transactional email sending, templates, streams, bounces,…

### Community 1532 - "recruitee_server.py"
Cohesion: 0.50
Nodes (4): _base(), call_tool(), Any, Recruitee MCP server — applicant tracking, candidates, offers, and stages.…

### Community 1534 - "ringcentral_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, RingCentral MCP server — cloud communications: SMS, voice calls, and message…

### Community 1537 - "shipstation_server.py"
Cohesion: 0.40
Nodes (3): call_tool(), Any, ShipStation MCP server — ShipStation shipping, orders, shipments, and labels.…

### Community 1538 - "signnow_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, SignNow MCP server — electronic signature management. Environment:…

### Community 1540 - "surveymonkey_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, SurveyMonkey MCP server — survey creation and response analysis. Environment:…

### Community 1541 - "twitch_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Twitch MCP server — streaming platform: streams, users, followers, videos, and…

### Community 1542 - "typeform_server.py"
Cohesion: 0.50
Nodes (4): call_tool(), _headers(), Any, Typeform MCP server — form building and response collection. Environment:…

### Community 1544 - "wufoo_server.py"
Cohesion: 0.50
Nodes (4): _base_url(), call_tool(), Any, Wufoo MCP server — form building, entries, and report data. Environment:…

### Community 1547 - "_Types"
Cohesion: 0.40
Nodes (3): EmbedContentConfig, GenerateContentConfig, _Types

### Community 1551 - "aws_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, AWS MCP server — unified AWS operations across S3, Lambda, EC2, CloudWatch,…

### Community 1552 - "buffer_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Buffer MCP server — Buffer social media post scheduling, analytics, and profile…

### Community 1554 - "clockify_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Clockify MCP server — time tracking, projects, workspaces, and reports.…

### Community 1555 - "convertkit_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, ConvertKit MCP server — subscribers, forms, sequences, and tags. Environment:…

### Community 1557 - "ebay_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, eBay MCP server — eBay marketplace item search, orders, and selling statistics.…

### Community 1559 - "etsy_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Etsy MCP server — Etsy marketplace shops, listings, and orders management.…

### Community 1560 - "expensify_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Expensify MCP server — expense management. Environment:…

### Community 1561 - "facebook_lead_ads_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Facebook Lead Ads MCP server — Facebook Lead Ad forms, leads, ad accounts, and…

### Community 1562 - "facebook_pages_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Facebook Pages MCP server — Facebook Page posts, insights, comments, and…

### Community 1564 - "filestack_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Filestack MCP server — file upload, transformation, and management.…

### Community 1566 - "freshbooks_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, FreshBooks MCP server — accounting, invoices, clients, and expenses.…

### Community 1568 - "grafana_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Grafana MCP server — monitoring and observability. Environment: GRAFANA_URL:…

### Community 1569 - "greenhouse_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Greenhouse MCP server — applicant tracking, jobs, candidates, and applications.…

### Community 1570 - "gumroad_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Gumroad MCP server — Gumroad digital product sales, subscriptions, and license…

### Community 1571 - "gusto_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Gusto MCP server — payroll, HR, employees, pay periods, and benefits.…

### Community 1572 - "harvest_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Harvest MCP server — time tracking, expense management, projects, and…

### Community 1573 - "hive_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Hive MCP server — project and action management, workspaces, and collaboration.…

### Community 1574 - "hootsuite_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Hootsuite MCP server — Hootsuite social media profile management, scheduling,…

### Community 1575 - "invoice_ninja_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Invoice Ninja MCP server — invoicing, clients, and payments. Environment:…

### Community 1576 - "kajabi_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Kajabi MCP server — Kajabi online courses, members, offers, and pipeline…

### Community 1577 - "knack_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Knack MCP server — no-code database records, objects, and views. Environment:…

### Community 1578 - "lightspeed_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Lightspeed MCP server — Lightspeed Retail POS products, sales, customers, and…

### Community 1579 - "magento_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Magento MCP server — Magento e-commerce products, orders, customers, and…

### Community 1580 - "mandrill_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Mandrill (Mailchimp Transactional) MCP server. Environment: MANDRILL_API_KEY:…

### Community 1582 - "miro_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Miro MCP server — visual collaboration boards, sticky notes, frames, and items.…

### Community 1583 - "ninox_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Ninox MCP server — database management, tables, and records. Environment:…

### Community 1584 - "pinterest_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Pinterest MCP server — Pinterest boards, pins, analytics, and search.…

### Community 1585 - "pivotal_tracker_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Pivotal Tracker MCP server — agile stories, projects, and iterations.…

### Community 1586 - "procore_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Procore MCP server — construction management, projects, RFIs, submittals, and…

### Community 1587 - "profitwell_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, ProfitWell MCP server — subscription metrics, MRR, churn, and customer…

### Community 1588 - "pushover_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Pushover MCP server — push notifications to mobile devices and desktop clients.…

### Community 1589 - "redmine_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Redmine MCP server — issue tracking, projects, users, and time entries.…

### Community 1590 - "samcart_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, SamCart MCP server — checkout platform products, orders, and customers.…

### Community 1591 - "smartsheets_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Smartsheet MCP server — project management sheets, rows, and reports.…

### Community 1592 - "sonarqube_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, SonarQube MCP server — code quality and security analysis. Environment:…

### Community 1593 - "spotify_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Spotify MCP server — Spotify music track search, playlists, and artist info.…

### Community 1594 - "sprout_social_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Sprout Social MCP server — Sprout Social social media management, analytics,…

### Community 1595 - "squarespace_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Squarespace MCP server — Squarespace website pages, products, orders, and…

### Community 1596 - "storyblok_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Storyblok MCP server — Storyblok headless CMS stories, components, and…

### Community 1597 - "substack_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Substack MCP server — Substack newsletter posts, subscribers, stats, and email…

### Community 1598 - "teachable_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Teachable MCP server — Teachable online course users, enrollments, coupons, and…

### Community 1599 - "thinkific_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Thinkific MCP server — Thinkific online courses, users, enrollments, and stats.…

### Community 1600 - "toggl_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Toggl Track MCP server — time tracking, projects, clients, and reports.…

### Community 1603 - "vimeo_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Vimeo MCP server — Vimeo video hosting, uploads, folders, and analytics.…

### Community 1604 - "vonage_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Vonage (Nexmo) MCP server — SMS messaging, voice calls, and account management.…

### Community 1605 - "wistia_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Wistia MCP server — Wistia video hosting, media management, and analytics.…

### Community 1606 - "zoho_books_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Zoho Books MCP server — accounting, invoices, contacts, and expenses.…

### Community 1607 - "zoho_invoice_server.py"
Cohesion: 0.50
Nodes (3): call_tool(), Any, Zoho Invoice MCP server — invoicing, customers, and invoice lifecycle.…

### Community 1608 - ".__init__"
Cohesion: 0.50
Nodes (3): async_sessionmaker, AsyncSession, Embedder

### Community 1610 - "_check_rate_limit_with_fallback"
Cohesion: 0.12
Nodes (14): _check_rate_limit_with_fallback(), Any, Check rate limit, using in-process counter when Redis is unavailable. Returns…, ASGIApp, KeyResolver, asyncio, Requests denied count should align with the effective limit., H2: A2A in-memory fallback must not return other tenants' tasks. (+6 more)

### Community 1612 - "_extract_text"
Cohesion: 0.67
Nodes (3): _extract_text(), Extract plain-text body from a multipart email., Message

## Knowledge Gaps
- **181 isolated node(s):** `Turn`, `ResponseAction`, `ArtifactRef`, `MCPTool`, `MCPPromptArgument` (+176 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 15059 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **102 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TenantContext` connect `TenantContext` to `test_agents_comprehensive2.py`, `OrgMCPServer`, `CompletionRequest`, `GoalService`, `AuditLog`, `FakeProvider`, `test_mcp_circuit_breaker.py`, `test_full_pipeline_real_world.py`, `RAGStrategy`, `._get_stats`, `AgentState`, `HITLGateway`, `SourceConfig`, `.purge_expired`, `ScheduleStore`, `ColBERTPattern`, `GoalRuntimeProfile`, `GuardrailChecker`, `db_tenant_ctx`, `build_default_registry`, `test_dynamic_orchestration_e2e.py`, `retrieve`, `state_context.py`, `test_layer4_complete.py`, `TenantMiddleware`, `api/knowledge.py`, `RetrievalResult`, `test_civilization_extra4.py`, `agent/graph.py`, `test_schedules_api.py`, `org/router.py`, `test_tenants.py`, `TestTenantContextIsolation`, `LongTermMemoryStore`, `test_workflows_extra2.py`, `CostController`, `MoAProposal`, `test_tenants_comprehensive.py`, `test_workflows_comprehensive.py`, `test_runtime_scorecard.py`, `workflow/test_router.py`, `test_agents_extra4.py`, `test_governance_extra3.py`, `CRDTRoomManager`, `generate_gst_invoice`, `ContentType`, `test_a2a_comprehensive.py`, `GoalStatus`, `test_chunkers.py`, `TenantService`, `create_app`, `SupervisorAgent`, `OAuthState`, `RAFTDatasetRecord`, `OrchestrationPersistence`, `asyncio`, `SelfOptimizer`, `ExecutionMemory`, `test_goals_extra.py`, `_make_app`, `test_tenants_comprehensive2.py`, `test_db_paths_comprehensive.py`, `EmbeddingOrchestrator`, `test_knowledge_persistence.py`, `test_truly_live_everything.py`, `_TemplateStore`, `test_knowledge_extra4.py`, `CredentialVault`, `app/main.py`, `test_rbac_comprehensive2.py`, `test_tool_reliability_store_get_unreliable`, `AgentStore`, `SystemTemplateStore`, `test_gateway_entrypoints.py`, `test_tenants_extra4.py`, `test_knowledge_api.py`, `RetrievalEvaluator`, `test_governor.py`, `EpisodicMemoryStore`, `test_catalog_comprehensive.py`, `test_rpa_comprehensive.py`, `asyncio`, `test_router_versions.py`, `api/governance.py`, `Constitution`, `marketplace_monetization.py`, `test_modular_rag.py`, `test_helpers.py`, `test_phase2_model_registry.py`, `test_supervisor_debate_nodes.py`, `WorkflowExecutor`, `RollbackEngine`, `GoalPersistenceEngine`, `test_celery_agentgraph.py`, `test_safe_web_capability.py`, `test_civilization_api_comprehensive2.py`, `test_connectors_catalog.py`, `test_knowledge_comprehensive.py`, `tenants.py`, `test_goal_service_distributed_strategy.py`, `org/service.py`, `test_phase10_11_ai_ops_memory.py`, `test_workflows.py`, `test_program09_coordination_api.py`, `test_agent_graph_build_gaps.py`, `CollaborationStore`, `test_retrieval_gateway.py`, `BrowserSessionManager`, `api/test_collab.py`, `test_tools_api_comprehensive.py`, `goals.py`, `observability.py`, `PolicyEngine`, `BenchmarkStore`, `execution_environment/models.py`, `test_insights_extra.py`, `test_store_comprehensive3.py`, `test_connectors_extra2.py`, `test_goal_tree_comprehensive.py`, `rls.py`, `rag_platform.py`, `agent/test_router.py`, `Classification`, `GraphNode`, `chat/router.py`, `test_workflows_comprehensive2.py`, `Marketplace`, `test_jira_agent_execution.py`, `insights.py`, `CapabilitySearch`, `assert_public_url`, `test_semantic_cache_world_class.py`, `test_router_hitl.py`, `test_training_export_comprehensive2.py`, `test_collab_extra3.py`, `test_agents_api.py`, `check_grounding`, `civilization/test_events.py`, `test_optimistic_concurrency.py`, `SemanticCache`, `test_replay_comprehensive.py`, `test_learning.py`, `agents.py`, `test_middleware_comprehensive.py`, `test_workflow_builder.py`, `asyncio`, `test_entitlements.py`, `MCPServerConfig`, `test_guardrails_comprehensive.py`, `test_analytics_comprehensive.py`, `test_goals.py`, `test_agent_identity.py`, `test_raft_lifecycle.py`, `test_middleware_full.py`, `test_phase_n8_n10.py`, `coordination/store.py`, `test_agent_knowledge_binding.py`, `test_governance_comprehensive2.py`, `semantic_cache.py`, `NotificationService`, `rag/raft.py`, `test_ghost_run.py`, `registry_wiring.py`, `goal_service.py`, `test_key_rotation.py`, `test_ingestion_time_strategies.py`, `test_oauth_flow.py`, `test_rollback_experiment.py`, `triggers.py`, `_make_app`, `TemplateSecurityReviewer`, `RegressionGate`, `DeduplicationCache`, `test_routing.py`, `test_phase8_9_guardrails_trust.py`, `routers.py`, `test_agents_extra.py`, `api/test_artifacts_comprehensive.py`, `test_phase5_knowledge_graph.py`, `test_scope_enforcement_comprehensive.py`, `workflow_nodes.py`, `AgentCollabSession`, `test_security_audit_findings.py`, `test_keycloak_comprehensive.py`, `test_spawn_tool.py`, `policies.py`, `ComplianceController`, `test_org_router.py`, `get_inverse_fn`, `test_goals_comprehensive.py`, `test_backend_fixes.py`, `test_society.py`, `tasks.py`, `integrations.py`, `api/mfa.py`, `test_openapi_importer_comprehensive.py`, `test_self_improvement_smoke.py`, `test_policy_runtime.py`, `test_rag_patterns_functional.py`, `test_governance_extra2.py`, `EvalRunner`, `test_rag_patterns_real_openai.py`, `run_goal`, `ReadinessEvaluator`, `_make_service`, `test_reliability_fixes.py`, `IngestionOrchestrator`, `test_enterprise_comprehensive2.py`, `api/test_governance_comprehensive.py`, `test_phase1_gaps.py`, `test_enterprise_intelligence_gaps.py`, `ToolContext`, `test_audit_v2.py`, `PlanTier`, `test_workflow_planner_comprehensive.py`, `tenant_service.py`, `test_oauth_security.py`, `test_reasoning_retrieval_strategies.py`, `MCPClient`, `test_real_postgres.py`, `test_phase6_7_rag_runtime.py`, `test_knowledge_base_pipeline.py`, `test_router_runs.py`, `test_insights_comprehensive.py`, `test_phase14_deep_coverage.py`, `test_mfa.py`, `schedules.py`, `PromptVariant`, `asyncio`, `test_collab_api_comprehensive.py`, `test_final_coverage_push.py`, `test_rate_limiter.py`, `test_supervisor_debate_wiring.py`, `test_main_lifespan.py`, `test_a2a_dispatch.py`, `RAFTJobRecord`, `_make_app`, `test_critical_fixes.py`, `test_connectors_comprehensive2.py`, `test_api_e2e.py`, `test_emergency_stop.py`, `test_semantic_cache_comprehensive.py`, `RetrieverTool`, `test_orchestrator.py`, `.exchange_code`, `test_scopes_rbac.py`, `api/test_connectors.py`, `AgentRouter`, `test_insights.py`, `SimulationRunner`, `test_costs_comprehensive.py`, `_FakeWorkflowMCPClient`, `test_guardrails_comprehensive2.py`, `_make_app`, `test_agent_patterns_real_openai.py`, `get_settings`, `_ctx`, `test_magentic_api.py`, `test_enterprise_extra3.py`, `test_memory_comprehensive.py`, `test_enterprise_api.py`, `test_scheduler.py`, `tenancy/test_context.py`, `Any`, `.approve`, `test_connectors_comprehensive.py`, `_make_app`, `SIEMType`, `test_goal_service_run_eval.py`, `collab.py`, `graph`, `test_phase3_4_multimodal.py`, `MetaAgentPlanner`, `test_goals_final.py`, `test_knowledge_comprehensive2.py`, `test_templates_comprehensive2.py`, `test_enterprise_v2.py`, `_ctx`, `workflows.py`, `_make_agents_app`, `test_schedules_comprehensive.py`, `.__init__`, `RoutingDecision`, `Governor`, `test_schedules_extra.py`, `CredentialInjector`, `.find_completed_model`, `test_perception_comprehensive.py`, `test_phase12_13_skills_frontend.py`, `dpdp.py`, `estimate_cost`, `OAuthFlowManager`, `test_coordination_handoffs.py`?**
  _High betweenness centrality (0.184) - this node is a cross-community bridge._
- **Why does `get_logger()` connect `get_logger` to `activecampaign_server.py`, `CompletionRequest`, `FakeProvider`, `GoalService`, `AuditLog`, `TenantContext`, `logging.py`, `RAGStrategy`, `AgentState`, `HITLGateway`, `_instantiate_provider`, `ColBERTPattern`, `WorkflowCompiler`, `api/knowledge.py`, `agent/graph.py`, `org/router.py`, `LongTermMemoryStore`, `CostController`, `test_batch6_servers.py`, `test_batch7_servers.py`, `ContentType`, `test_remaining_connectors.py`, `test_a2a_comprehensive.py`, `OrchestrationPersistence`, `SelfOptimizer`, `ExecutionMemory`, `test_google_storage_payment_connectors.py`, `test_database_analytics_connectors.py`, `runtime_profile.py`, `test_batch8_servers.py`, `CredentialVault`, `app/main.py`, `WorkflowDefinition`, `AgentStore`, `ToolReliabilityStore`, `StepDefinition`, `SystemTemplateStore`, `test_extra_coverage_servers5.py`, `EpisodicMemoryStore`, `Constitution`, `SIEMConfig`, `GoalPersistenceEngine`, `api/civilization.py`, `rpa/test_artifacts_comprehensive.py`, `org/service.py`, `civilization/test_metrics.py`, `test_medium_fixes.py`, `RPAArtifactStore`, `BrowserSessionManager`, `org/test_security.py`, `goals.py`, `DataClassifier`, `ModelRouter`, `QualityGateSystem`, `test_bus.py`, `assert_public_url`, `check_grounding`, `civilization/test_events.py`, `test_extra_coverage_servers4.py`, `agents.py`, `CostOptimizer`, `RerankPolicy`, `app/tools/__init__.py`, `NotificationService`, `WebhookDeliverySystem`, `registry_wiring.py`, `goal_service.py`, `RuntimeSSEEmitter`, `test_extra_coverage_servers6.py`, `test_data_servers_dispatch.py`, `test_blackboard.py`, `workflow_nodes.py`, `NLTriggerResolver`, `workflow/router.py`, `test_keycloak_comprehensive.py`, `policies.py`, `test_tool_cache.py`, `test_tracing_comprehensive.py`, `test_new_tools.py`, `org/events.py`, `test_society.py`, `tasks.py`, `integrations.py`, `WorkflowState`, `BrowserAgent`, `test_saml_provider.py`, `ExperimentRegistry`, `run_goal`, `test_test_runner.py`, `test_final_fixes.py`, `test_auth_api_coverage.py`, `IngestionOrchestrator`, `OutboundWebhookService`, `ComplianceBundleManager`, `test_audit_v2.py`, `Tokenizer`, `ComplianceChecker`, `ChannelRateLimiter`, `memory_v2.py`, `MCPClient`, `ToolSelector`, `org/connectors/__init__.py`, `check_tool_args_for_exfil`, `test_servers_comprehensive.py`, `InMemoryCacheBackend`, `LLMResponseCache`, `PromptVariant`, `test_a2a_dispatch.py`, `test_communication_connectors.py`, `test_expression_engine.py`, `test_orchestrator.py`, `test_scopes_rbac.py`, `TenantOptimizationState`, `test_cost_dashboard_api.py`, `ImprovementActionRecord`, `admin.py`, `test_scope_seeder.py`, `_ingest_repo_background`, `_sign`, `OcrEngine`, `workflow/test_security.py`, `LargePayloadStore`, `collab.py`, `test_audit_v2_comprehensive.py`, `router_runs.py`, `ConsensusVerifier`, `OrgMCPResources`, `.__init__`, `CredentialInjector`, `UsageService`, `tenancy/billing.py`, `ArtifactTool`, `router_hitl.py`, `audit_v3.py`, `OAuthFlowManager`, `test_devops_connectors.py`, `OpenAIFineTuneProvider`, `JiraIngestor`, `OrgLearningPipeline`, `celery_tasks.py`, `TenantUserService`, `router_versions.py`, `GitHubIngestor`, `DataCategory`, `OAuthState`, `SkillSelector`, `a2a/__init__.py`, `test_audit_scopes_limits.py`, `SCIMHandler`, `check_and_process_emails`, `test_project_management_connectors.py`, `RetrievalEvaluator`, `PostgresWorkflowRunStore`, `asyncio`, `AnswerSynthesizer`, `CustomRoleStore`, `CommandScheduler`, `GoalRefinementPipeline`, `SubTenantService`, `execution_environment/models.py`, `ConfluenceIngestor`, `OrgSimulationEngine`, `rls.py`, `AgentCredentialStore`, `scan_for_encoding_attacks`, `test_ingestors_coverage.py`, `test_replay_comprehensive.py`, `warm_permission_cache`, `test_otel.py`, `test_analytics_comprehensive.py`, `semantic_cache.py`, `google_oauth.py`, `DeduplicationCache`, `UniversalArgumentResolver`, `AutonomyEnforcer`, `hitl_extension.py`, `test_spawn_tool.py`, `TestDomainPolicies`, `guardrail_engine.py`, `OrgEventPublisher`, `verify_goal_token`, `SlackIngestor`, `test_durable_execution.py`, `EvalRunner`, `ocr.py`, `Any`, `GoalDeduplicator`, `agent/supervisor.py`, `upsert_google_user`, `test_guardrails_v3.py`, `verify_stream_token`, `agent/consensus.py`, `grant_elevation`, `.subscribe_to_changes`, `gcp_server.py`, `.exchange_code`, `brevo_server.py`, `firebase_server.py`, `gmail_server.py`, `acoustic_server.py`, `apache_kafka_server.py`, `ecwid_server.py`, `sap_server.py`, `quickbooks_server.py`, `mailchimp_server.py`, `notion_server.py`, `.approve`, `amadeus_server.py`, `amazon_sqs_server.py`, `constant_contact_server.py`, `bamboohr_server.py`, `test_gateway_auth.py`, `buildium_server.py`, `jotform_server.py`, `cloudinary_server.py`, `evernote_server.py`, `doordash_server.py`, `looker_server.py`, `freshservice_server.py`, `microsoft_onenote_server.py`, `yotpo_server.py`, `gorgias_server.py`, `microsoft_todo_server.py`, `moosend_server.py`, `omnisend_server.py`, `monday_server.py`, `pushbullet_server.py`, `toast_pos_server.py`, `webflow_server.py`, `asana_server.py`, `bitbucket_server.py`, `chargebee_server.py`, `customerio_server.py`, `freshdesk_server.py`, `athenahealth_server.py`, `call_tool`, `instagram_server.py`, `klaviyo_server.py`, `mattermost_server.py`, `microsoft_outlook_server.py`, `pipedrive_server.py`, `pandadoc_server.py`, `snovio_server.py`, `wave_server.py`, `wrike_server.py`, `zoho_crm_server.py`, `azure_devops_server.py`, `facebook_conversions_server.py`, `affinity_server.py`, `apollo_server.py`, `attio_server.py`, `box_server.py`, `copper_server.py`, `discord_server.py`, `gong_server.py`, `intercom_server.py`, `mailerlite_server.py`, `netsuite_server.py`, `planhat_server.py`, `jenkins_server.py`, `salesforce_server.py`, `sendgrid_server.py`, `teamwork_server.py`, `twilio_server.py`, `zuora_server.py`, `braintree_server.py`, `google_docs_server.py`, `kubernetes_server.py`, `vercel_server.py`, `digitalocean_server.py`, `docusign_server.py`, `fireflies_server.py`, `freshsales_server.py`, `help_scout_server.py`, `heroku_server.py`, `linear_server.py`, `netlify_server.py`, `shopify_server.py`, `terraform_server.py`, `woocommerce_server.py`, `zendesk_server.py`, `amazon_ses_server.py`, `appsheet_server.py`, `aweber_server.py`, `bigquery_server.py`, `bitly_server.py`, `campaign_monitor_server.py`, `close_crm_server.py`, `cloudflare_server.py`, `drip_server.py`, `emma_server.py`, `figma_server.py`, `formstack_server.py`, `getresponse_server.py`, `google_forms_server.py`, `google_slides_server.py`, `google_tasks_server.py`, `gravity_forms_server.py`, `hubspot_server.py`, `linkedin_server.py`, `loom_server.py`, `loops_server.py`, `mailgun_server.py`, `manychat_server.py`, `maropost_server.py`, `microsoft_excel_server.py`, `onesignal_server.py`, `order_desk_server.py`, `plivo_server.py`, `postmark_server.py`, `recruitee_server.py`, `ringcentral_server.py`, `shipstation_server.py`, `signnow_server.py`, `surveymonkey_server.py`, `twitch_server.py`, `typeform_server.py`, `wufoo_server.py`, `aws_server.py`, `buffer_server.py`, `clockify_server.py`, `convertkit_server.py`, `ebay_server.py`, `etsy_server.py`, `expensify_server.py`, `facebook_lead_ads_server.py`, `facebook_pages_server.py`, `filestack_server.py`, `freshbooks_server.py`, `grafana_server.py`, `greenhouse_server.py`, `gumroad_server.py`, `gusto_server.py`, `harvest_server.py`, `hive_server.py`, `hootsuite_server.py`, `invoice_ninja_server.py`, `kajabi_server.py`, `knack_server.py`, `lightspeed_server.py`, `magento_server.py`, `mandrill_server.py`, `miro_server.py`, `ninox_server.py`, `pinterest_server.py`, `pivotal_tracker_server.py`, `procore_server.py`, `profitwell_server.py`, `pushover_server.py`, `redmine_server.py`, `samcart_server.py`, `smartsheets_server.py`, `sonarqube_server.py`, `spotify_server.py`, `sprout_social_server.py`, `squarespace_server.py`, `storyblok_server.py`, `substack_server.py`, `teachable_server.py`, `thinkific_server.py`, `toggl_server.py`, `vimeo_server.py`, `vonage_server.py`, `wistia_server.py`, `zoho_books_server.py`, `zoho_invoice_server.py`?**
  _High betweenness centrality (0.131) - this node is a cross-community bridge._
- **Why does `create_app()` connect `create_app` to `CompletionRequest`, `FakeProvider`, `GoalService`, `AuditLog`, `TenantContext`, `test_goals_batch_submit_route_exists`, `test_tools_router.py`, `AgentState`, `HITLGateway`, `SourceConfig`, `_instantiate_provider`, `ScheduleStore`, `GuardrailChecker`, `WorkflowCompiler`, `test_colbert_runtime.py`, `build_default_registry`, `TenantMiddleware`, `test_wiring_integrity.py`, `RetrievalResult`, `test_dispatcher.py`, `org/router.py`, `MultimodalPipeline`, `LongTermMemoryStore`, `MoAProposal`, `CostController`, `Settings`, `IdempotencyStore`, `TenantService`, `test_memory_learning_services.py`, `OrchestrationPersistence`, `asyncio`, `SelfOptimizer`, `ExecutionMemory`, `VoyageProvider`, `GuardrailEngine`, `test_tool_reliability.py`, `test_session_store_comprehensive.py`, `test_perception_api.py`, `CredentialVault`, `app/main.py`, `RPAExecutor`, `test_rbac_comprehensive2.py`, `WorkflowDefinition`, `AgentStore`, `ToolReliabilityStore`, `StepDefinition`, `SystemTemplateStore`, `LedgerRevision`, `PostgresWorkflowRunStore`, `EpisodicMemoryStore`, `asyncio`, `test_agent_identity_comprehensive.py`, `SIEMConfig`, `test_main_create_app.py`, `test_routers.py`, `HandoffRecord`, `rpa/test_artifacts_comprehensive.py`, `tenants.py`, `test_goal_service_distributed_strategy.py`, `test_program09_coordination_api.py`, `test_medium_fixes.py`, `CollaborationStore`, `test_retrieval_gateway.py`, `test_ingestion_pipeline.py`, `BrowserSessionManager`, `api/test_collab.py`, `PolicyEngine`, `catalogue.py`, `rls.py`, `ModelRouter`, `VoiceAlertManager`, `Classification`, `test_tenant_service_db.py`, `Marketplace`, `SelfOptimizerV2`, `CapabilitySearch`, `test_jira_agent_execution.py`, `test_agents_api.py`, `CostTracker`, `decision_store.py`, `SemanticCache`, `LLMConfigStore`, `warm_permission_cache`, `TriggerConsumerSupervisor`, `MCPServerConfig`, `AuditV3`, `test_raft_lifecycle.py`, `get_session_factory`, `coordination/store.py`, `NotificationService`, `CeleryGoalTaskQueue`, `rag/raft.py`, `test_nl_scheduler_comprehensive.py`, `registry_wiring.py`, `goal_service.py`, `test_agent_advanced.py`, `test_rpa_execute.py`, `e2e_full/conftest.py`, `WorkflowService`, `_FakeRedis`, `routers.py`, `test_security_audit_findings.py`, `test_scope_enforcement_comprehensive.py`, `NLTriggerResolver`, `test_keycloak_comprehensive.py`, `policies.py`, `test_tool_cache.py`, `test_tracing_comprehensive.py`, `GraphFactory`, `ComplianceController`, `test_compliance_download.py`, `org/events.py`, `GoalCostBreakdown`, `tasks.py`, `integrations.py`, `BrowserAgent`, `test_voice_e2e.py`, `EvalRunner`, `ReadinessEvaluator`, `run_goal`, `test_auth_api_coverage.py`, `Event`, `PageAnalyzer`, `HITLWorkflowGateway`, `RedisBulkheadRegistry`, `KnowledgeGraphStore`, `test_goals_metrics.py`, `test_audit_v2.py`, `PlanTier`, `ComplianceChecker`, `test_redis_factory.py`, `test_goals_eval.py`, `MCPClient`, `MemoryRecord`, `test_real_postgres.py`, `ToolSelector`, `test_fakeredis_gaps.py`, `InMemoryCacheBackend`, `test_openapi_schema.py`, `api/test_replay.py`, `LLMResponseCache`, `test_auth_login_redirects`, `test_main_lifespan.py`, `test_billing.py`, `test_api_e2e.py`, `test_full_stack_e2e.py`, `test_eval_suite_api.py`, `test_emergency_stop.py`, `test_chat_api.py`, `test_scopes_rbac.py`, `test_capabilities.py`, `AgentRouter`, `PermissionCache`, `SimulationRunner`, `ImprovementActionRecord`, `test_civilization_api.py`, `get_settings`, `test_magentic_api.py`, `HealthCheck`, `test_scheduler.py`, `test_phase4_connectors.py`, `test_agent_builder.py`, `test_scope_seeder.py`, `get_vault`, `SIEMType`, `test_guardrail_rules_persistence.py`, `MetaAgentPlanner`, `ProspectiveMemoryService`, `_WorkflowStore`, `test_goals_final.py`, `MarketplaceV2`, `DeletionOrchestrator`, `CredentialInjector`, `UsageService`, `get_approval_engine`, `tests/conftest.py`, `audit_v3.py`, `OAuthFlowManager`, `OpenAIFineTuneProvider`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Are the 171 inferred relationships involving `FakeProvider` (e.g. with `FakeRunner` and `run_goal()`) actually correct?**
  _`FakeProvider` has 171 INFERRED edges - model-reasoned connections that need verification._
- **Are the 89 inferred relationships involving `AgentGraph` (e.g. with `GraphState` and `RetrievalEntryPointError`) actually correct?**
  _`AgentGraph` has 89 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Turn`, `ResponseAction`, `ArtifactRef` to the rest of the system?**
  _181 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `CompletionRequest` be split into smaller, more focused modules?**
  _Cohesion score 0.009971810074136495 - nodes in this community are weakly interconnected._