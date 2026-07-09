# AgentVerse Gap-Closure Plan — All 74 Gaps

> **Phased plan to close every gap identified in the exhaustive re-audit.**

---

## PHASE 1 — Critical Bugs (7 items)
*Zero functionality lost without these; system is broken with them.*

### C1: graph.py reflexion call in wrong branch (success → failure)
**File:** `app/agent/graph.py` ~line 3128-3134  
**Fix:** Move `ensure_future(_rw.maybe_store_async(agent_state))` from `if success:` block into `else:` (failure) block

### C2: graph.py NameError `tool_args` in GuardrailEnforcer
**File:** `app/agent/graph.py` ~line 1677  
**Fix:** Replace `tool_args=tool_args if isinstance(tool_args, dict) else {}` with `tool_args={}`  
(tool_args doesn't exist at this point in _execute_step; actual args check is at line 2024)

### C3: goal_service.py PatternConfig ignored at graph construction
**File:** `app/services/goal_service.py` ~line 856-858  
**Fix:** After PatternConfig is built (by RuntimeProfileBuilder), read `reasoning_patterns` and translate to graph kwargs: `enable_self_refine`, `enable_self_consistency`, `enable_tree_of_thoughts`, `enable_peer_review`

### C4: goal_lifecycle.py signal_resume never called
**File:** `app/services/goal_service.py` `resume_goal()` method  
**Fix:** After the asyncio event fire, call `await signal_resume(goal_id, redis)` to clear the Redis pause flag

### C5: guardrail_enforcer.py hardcoded PlanTier.PROFESSIONAL
**File:** `app/security_runtime/guardrail_enforcer.py` ~line 62-65  
**Fix:** Read actual plan from `profile.properties` or pass tenant_ctx through; use `PlanTier(profile.tenant_plan)` or default to PROFESSIONAL only as fallback

### C6: engine.py hardcoded knowledge_chunks_1536 table
**File:** `app/rag/engine.py` ~line 80  
**Fix:** Query `knowledge_collections` for the embedding_dim (as `store.py` already does at line 277), or accept `embedding_dim` with a safe default query

### C7: embedding/router.py embed_batch() API mismatch
**File:** `app/embedding/router.py` ~line 75  
**Fix:** Replace `await self._provider.embed_batch(texts)` with `resp = await self._provider.embed(EmbedRequest(texts=texts)); return resp.embeddings`

---

## PHASE 2 — Agent Execution Wiring (H1-H8)
*Pattern system implemented but not connected to live graph.*

### H1: _node_refine never added to graph topology
**Fix:** Add `g.add_node("refine", self._node_refine)` in `_build()` + routing edge when `enable_self_refine=True`

### H2: _load_checkpoint() never called (write-only)
**Fix:** Add checkpoint resume at start of `run()` before `ainvoke()`

### H3: PatternConfig.rag_patterns ignored by _node_rag_retrieval
**Fix:** In `_node_rag_retrieval`, read `agent_state.context.get("_runtime_profile").rag_strategy.strategy` and dispatch accordingly

### H4: persist_tool_outcome missing from parallel wave path
**Fix:** Add `ensure_future(persist_tool_outcome(...))` in `_run_wave_step` after each step output

### H5: persist_scorecard not added to _background_tasks
**Fix:** Add `self._background_tasks.add(_sc_task)` + `_sc_task.add_done_callback(...)`

### H6: DynamicGraphAssembler assemble() ignores reasoning_patterns
**Fix:** Add translation map in `assemble()`: `self_consistency → enable_self_consistency=True` etc.

### H7: self_consistency/tree_of_thoughts/peer_review dead in live graph
**Fix:** Add `g.add_node("self_consistency", self._node_self_consistency)` + implement node methods

### H8: supervisor/debate/consensus nodes not in graph._build()
**Fix:** Add conditional node wiring for enable_supervisor, enable_debate flags

---

## PHASE 3 — RAG/Retrieval Gaps (H9-H16)
*Retrieval quality and routing completeness.*

### H9: "graph"/"memory"/"parametric" fall through to hybrid
**Fix:** Add `if strategy == "parametric": return []` (skip retrieval); add KG path for "graph"; add LTM path for "memory"

### H10: corrective strategy has no web_fn injection in engine.py
**Fix:** Accept `web_search_fn` param in `retrieve()` and thread to corrective path

### H11: agentic_chunking absent from engine.py dispatch
**Fix:** Add `if strategy == "agentic_chunking": ...` using AgenticChunkingPattern

### H12: query_expander.py 4-keyword synonym table
**Fix:** Add LLM-driven expansion path: when provider available, call LLM to generate 3 query variants

### H13: query_reformulator.py 2 regex substitutions only
**Fix:** Add LLM-driven reformulation: when provider available, prompt LLM for 2 alternative phrasings

### H14: fallback_chain.py phantom "graph" step
**Fix:** Implement `_graph_retrieve()` in RetrieverTool using `self._kg.query_nodes()` or remove "graph" from FALLBACK_ORDER

### H15: CROSS_ENCODER asyncio.get_event_loop() deprecated
**Fix:** Replace with `asyncio.get_running_loop()` + proper async path via `asyncio.ensure_future` or restructure as async method

### H16: _llm_rerank_sync() is keyword overlap, not LLM
**Fix:** Wire to actual `rerank_results()` from `app/rag_platform/reranker.py` via async dispatch

---

## PHASE 4 — Observability/Evals/Security Gaps (H17-H28)
*Monitoring accuracy and security enforcement.*

### H17: runtime_scorecard.py latency=cost_efficiency always identical
**Fix:** Split `ModelScorer.score()` into `score_cost()` and `score_latency()`; assign separately

### H18: SelfImprovementEngine actions never dispatched
**Fix:** Add action dispatcher in graph.py after computing actions; route each ImprovementAction to its handler

### H19: Regression candidates never persisted
**Fix:** Add `persist_regression_case()` to OrchestrationPersistence; call from graph.py

### H20: 4 Prometheus orchestration counters never emitted
**Fix:** Add `.inc()` calls in `runtime_profile_builder.py`, `pattern_selector.py`, `readiness_gate.py`

### H21: 7 of 9 SSE events never emitted
**Fix:** Add emit calls in `_node_rag_retrieval`, `_node_plan`, `_execute_step`, after self-improvement

### H22: ModelOrchestrator dead code
**Fix:** Wire `ModelOrchestrator.select_models()` into graph.py model selection OR delete and document

### H23-H26: action_safety_profile, identity_profile, governance_profile, policy_bundle_selector dead
**Fix:** Wire all 4 into `_node_initialize` → compute per-goal profiles; store in agent_state.context

### H27: tool_reliability.py never instantiated
**Fix:** Add `ToolReliabilityStore(db_factory=db_factory)` in main.py lifespan; pass to AgentGraph

### H28: persist_reflexion_lesson() has zero callers
**Fix:** Call from graph.py failure path alongside maybe_store_async

---

## PHASE 5 — API/Infra/Frontend Gaps (H29-H41)
*User-facing completeness and infrastructure correctness.*

### H29+H30: ingestion/orchestrator.py chunk_id mismatch + not wired
**Fix:** Fix UUID generation (use store-returned IDs); wire to knowledge.py POST endpoint

### H31: IMAGE → TextParser (VisionParser bypassed)
**Fix:** Add IMAGE → VisionParser mapping in parser_registry.py

### H32: QualityChecker never called during ingestion
**Fix:** Call `QualityChecker().check(chunk_content)` in IngestionOrchestrator before storing

### H33: voyage-3-lite dimension mismatch 1024 vs 512
**Fix:** Reconcile to single value (actual: 512); update both files

### H34: invite_member TODO body
**Fix:** Implement user creation + TenantMembership row + email trigger

### H35: DELETE document HTTP 501
**Fix:** Add `delete_document()` to KnowledgeStore; wire to DELETE endpoint

### H36: 7 agentic strategies not in rag_platform API
**Fix:** Add them to `RAGStrategy` enum and `GET /rag/strategies` response

### H37: IdempotencyStore never wired
**Fix:** Wire to `POST /goals` endpoint: check Idempotency-Key header, return 200 if duplicate

### H38: 19+ MCP servers silent `except: pass`
**Fix:** Replace with `except Exception as e: return {"error": str(e), "tool": tool_name}`

### H39: RuntimeDecisionPanel.tsx never mounted
**Fix:** Import and render in ObservabilityPage.tsx goal-detail view

### H40: Python SDK test_create_agent bug
**Fix:** Update test to use `AgentCreateRequest(name="ReportBot")`

### H41: TriggerType FILE_DROP/ALERTMANAGER never handled
**Fix:** Add handler stubs in fire_due_schedules for new trigger types

---

## PHASE 6 — Medium Dead Code + Wiring (M1-M26)
*Wire real implementations, remove phantom code, fix inconsistencies.*

### M1-M4: citation_threader, rag_trace, retrieval_policy, source_inventory dead
**Fix:** Wire citation_threader into ContextPipeline; wire RAGTrace into _node_rag_retrieval; wire RetrievalPolicy into RetrieverTool; use SourceInventory in _node_initialize

### M5-M6: prompt_variant_selector, tool_prompt_builder, output_contract_builder dead
**Fix:** Wire prompt_variant_selector to PromptOptimizer A/B path; wire tool_prompt_builder to executor prompt; wire output_contract_builder to verifier prompt

### M7: embedding orchestrator/policy dead
**Fix:** Wire EmbeddingOrchestrator into ingestion path; wire VectorIndexPolicy to KB creation

### M8-M11: ingestion chunkers + parsers disconnected
**Fix:** Wire chunkers through IngestionOrchestrator._chunk() dispatch; wire rich parsers through ParserRegistry

### M12-M15: memory wiring fixes
**Fix:** Fix session_memory, reflexion_store race condition, StateContextBuilder in graph, RLS gaps

### M16-M18: orchestration duplicates + stale rag field
**Fix:** Call RuntimeProfileBuilder once per goal; fix rag field in AgentPatternConfig

### M19-M21: AI router dead modules
**Fix:** Wire AIRouter into graph.py OR delete + document; delete dead policy files

### M22-M26: reliability/celery/frontend/optimization
**Fix:** Redis-backed dedup; fix celery queue mismatch; add beat schedule dedup; render patterns_used in frontend; wire LatencyOptimizer to goal timings
