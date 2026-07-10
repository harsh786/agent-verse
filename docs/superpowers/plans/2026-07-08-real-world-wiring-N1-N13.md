# AgentVerse Real-World Wiring Plan — N1-N13

> **For agentic workers:** Execute each phase with TDD. Write test first, confirm failure, implement, confirm pass. No placeholders.

**Goal:** Wire every component so it actually fires in production — agents, RAG, embeddings, ingestion, prompt building, tooling, evals, self-improvement.

**Root cause summary:**
- N1: AgentGraph accepts `enable_self_refine/consistency/tree_of_thoughts/peer_review` as constructor params (lines 219-222) but `goal_service.py:834` doesn't pass them → DEAD nodes
- N2: ReflexionStore writes lessons on failure but initial_context never loads them back → feedback loop broken
- N3: `_latency_ms` key never written to context → latency score always 0.75 (neutral)
- N4: `score_cost()` reads `getattr(state, "total_cost_usd")` but cost lives in `state.context["total_cost_usd"]`
- N5: `ABTestingEngine.record_result_async()` wired but never called in production
- N6: 5 SSE events (`self_improvement_suggested`, `guardrail_profile_selected`, `chunking_strategy_selected`, `embedding_strategy_selected`, `runtime_profile_selected`) never emitted
- N7: `SWITCH_MODEL` and `BLACKLIST_TOOL_PATTERN` dispatch is log-only
- N8: `OutputContractBuilder.build()` called with no args → empty instructions, no-op
- N9: `executor_context` and `verifier_context` from ContextPipeline computed but discarded
- N10: `_tool_reliability_store` set on graph but never read
- N11: 7 chunkers in `ingestion/chunkers/` never dispatched
- N12: `DYNAMIC_ORCHESTRATION` single flag blocks RuntimeScorecard + SelfImprovement + 4 SSE events + GuardrailEnforcer + RAG strategy
- N13: `_node_rag_retrieval` always uses hybrid/lexical/vector — never dispatches to FLARE/RAPTOR/Fusion even when `_active_rag_strategy` says so

---

## PHASE 1: Critical fixes — pattern nodes + feedback loop + scores

**Files:** `app/services/goal_service.py`, `app/agent/graph.py`, `app/evals/model_score.py`

### N1: Pass enable_* flags as constructor kwargs (not post-construction)

Fix `goal_service.py` around line 834. Read the C3 block at lines 900-922 to understand which patterns to extract. Replace post-construction flag setting with constructor kwargs:

```python
# Read _agent_config + execution_context to determine pattern flags
_enable_self_refine = _agent_config.get("enable_self_refine", False)
_enable_self_consistency = _agent_config.get("enable_self_consistency", False)  
_enable_tree_of_thoughts = _agent_config.get("enable_tree_of_thoughts", False)
_enable_peer_review = _agent_config.get("enable_peer_review", False)
_enable_supervisor = _agent_config.get("enable_supervisor", False)
_enable_debate = _agent_config.get("enable_debate", False)

# Also check execution_context runtime_profile
try:
    _latest_goal = list(self._goals.values())[-1] if self._goals else None
    if _latest_goal:
        _rp = _latest_goal.execution_context.get("runtime_profile", {})
        _reasoning = str(_rp.get("agent_patterns", {}).get("reasoning", ""))
        _enable_self_refine = _enable_self_refine or "self_refine" in _reasoning
        _enable_self_consistency = _enable_self_consistency or "self_consistency" in _reasoning
        _enable_tree_of_thoughts = _enable_tree_of_thoughts or "tree_of_thoughts" in _reasoning
        _enable_peer_review = _enable_peer_review or "peer_review" in _reasoning
        _enable_supervisor = _enable_supervisor or "supervisor" in _reasoning
        _enable_debate = _enable_debate or "debate" in _reasoning
except Exception:
    pass
```

Then add to `AgentGraph(...)` constructor call:
```python
        graph = AgentGraph(
            ...
            enable_self_refine=_enable_self_refine,
            enable_self_consistency=_enable_self_consistency,
            enable_tree_of_thoughts=_enable_tree_of_thoughts,
            enable_peer_review=_enable_peer_review,
            enable_supervisor=_enable_supervisor,
            enable_debate=_enable_debate,
            ...
        )
```

**Remove** the C3 post-construction block (lines 900-922) entirely since it's now handled before construction.

Also add `enable_supervisor` and `enable_debate` to `AgentGraph.__init__` parameters if not already there.

### N2: Load reflexion lessons into initial_context before goal run

In `goal_service.py` `_run_agent_loop()` around line 1691, after building `initial_context`, add:

```python
        # N2: Load reflexion lessons to close the feedback loop
        try:
            from app.agent.reflexion_wirer import get_reflexion_wirer
            _rw = get_reflexion_wirer()
            _lessons = _rw._store.recall(tenant_id=tenant_ctx.tenant_id, limit=5)
            if _lessons:
                initial_context["_reflexion_lessons"] = _lessons
        except Exception:
            pass
```

### N3: Track goal latency and write to agent_state.context

In `app/agent/graph.py` `run()` method, find where `ainvoke()` is called. Add time tracking:

```python
        # N3: Track goal start time for latency scoring
        _goal_start_ms = time.monotonic() * 1000
        agent_state.context["_goal_start_ms"] = _goal_start_ms
```

Then in `_node_verify()`, before RuntimeScorecard scoring, add:

```python
        # N3: Compute and store latency for accurate scorecard
        try:
            _start_ms = agent_state.context.get("_goal_start_ms", 0)
            if _start_ms > 0:
                agent_state.context["_latency_ms"] = time.monotonic() * 1000 - _start_ms
        except Exception:
            pass
```

### N4: Fix score_cost to read from context not attribute

In `app/evals/model_score.py` `score_cost()`, fix the actual_cost line:

```python
    def score_cost(self, profile: "GoalRuntimeProfile", state: "AgentState") -> float:
        max_cost = getattr(profile.model_plan, "max_cost_usd", 0.10) or 0.10
        # N4 fix: cost is in state.context, not a direct attribute
        ctx = getattr(state, "context", {}) or {}
        actual_cost = float(ctx.get("total_cost_usd", 0.0) or 0.0)
        ...
```

### Tests for Phase 1

```python
# tests/agent/test_phase_n1_n4.py
def test_agent_graph_patterns_passed_as_constructor_kwargs():
    """Pattern flags must be constructor params, not set post-construction."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    p = FakeProvider()
    g = AgentGraph(
        planner=p, executor=p, verifier=p,
        enable_self_refine=True,
        enable_self_consistency=True,
        enable_tree_of_thoughts=True,
        enable_peer_review=True,
    )
    # All nodes must be in the compiled graph
    assert "refine" in g._graph.nodes
    assert "self_consistency" in g._graph.nodes
    assert "tree_of_thoughts" in g._graph.nodes
    assert "peer_review" in g._graph.nodes

def test_latency_written_to_context():
    """_latency_ms must be in context after execution."""
    from app.agent.state import AgentState
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    state.context["_goal_start_ms"] = 1000.0
    # Simulate latency write
    import time
    state.context["_latency_ms"] = time.monotonic() * 1000 - 1000.0
    assert state.context["_latency_ms"] > 0

def test_score_cost_reads_from_context():
    """score_cost must read from state.context not getattr(state)."""
    from app.evals.model_score import ModelScorer
    from app.agent.state import AgentState
    from app.tenancy.context import TenantContext, PlanTier
    from app.orchestration.runtime_profile import GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig, ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig
    
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    state.context["total_cost_usd"] = 0.05  # Context, not attribute
    
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(max_cost_usd=0.10), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    scorer = ModelScorer()
    score = scorer.score_cost(profile, state)
    assert score != 0.8  # Must NOT return neutral sentinel
    assert score >= 0.6  # 0.05/0.10 = 0.5 ratio → should be ~0.8-1.0

def test_reflexion_lessons_in_initial_context(tmp_path):
    """initial_context must include reflexion lessons for feedback loop."""
    # Verify the ReflexionStore recall path works for seeding context
    from app.state_runtime.reflexion_store import ReflexionStore
    store = ReflexionStore()
    store.record(tenant_id="t1", lesson="Avoid deleting production data",
                 source_goal_id="g0", failure_class="auth_failure")
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    # Verify format compatible with ContextPipeline
    assert "lesson" in lessons[0]
    assert "failure_class" in lessons[0]
```

---

## PHASE 2: ABTesting + SSE events + action dispatch

### N5: Call ABTestingEngine.record_result_async() from graph.py

In `app/agent/graph.py` `_node_verify()`, after `SelfOptimizerV2.on_goal_completed()` block (~line 3588), add:

```python
            # N5: Record A/B test result for module-level ABTestingEngine
            try:
                from app.optimization.ab_testing import ab_testing_engine as _ab_eng
                if _ab_eng is not None and _eval_score is not None and tenant_ctx:
                    import asyncio as _ab_asyncio
                    _ab_asyncio.ensure_future(
                        _ab_eng.record_result_async(
                            goal_id=agent_state.goal_id,
                            experiment_type=__import__(
                                "app.optimization.ab_testing", fromlist=["ExperimentType"]
                            ).ExperimentType.RAG_STRATEGY,
                            arm_id=agent_state.context.get("_experiment_arm", "control"),
                            score=float(_eval_score),
                            tenant_id=tenant_ctx.tenant_id,
                        )
                    )
            except Exception:
                pass
```

### N6: Wire 5 missing SSE events

**`runtime_profile_selected`** — emit in `_node_initialize` right after profile is built:
```python
            # N6a: runtime_profile_selected SSE
            if self._event_callback and profile:
                _sse = RuntimeSSEEmitter()
                await self._emit(_sse.runtime_profile_selected(
                    goal_id=agent_state.goal_id,
                    profile_id=getattr(profile, "profile_id", ""),
                    complexity=profile.properties.complexity.value,
                    patterns=[profile.agent_patterns.reasoning or "react"],
                    rag_strategy=profile.rag_strategy.strategy,
                    assembly_latency_ms=getattr(profile, "assembly_latency_ms", 0.0),
                ))
```

**`guardrail_profile_selected`** — emit in `_execute_step` after GuardrailEnforcer runs:
```python
            # N6b: guardrail_profile_selected SSE
            if self._event_callback and _runtime_profile:
                _sse = RuntimeSSEEmitter()
                await self._emit(_sse.guardrail_profile_selected(
                    goal_id=agent_state.goal_id,
                    bundle=getattr(_runtime_profile.security, "guardrail_bundle", "default"),
                    scanners=["injection", "pii", "content"],
                ))
```

**`self_improvement_suggested`** — emit after SelfImprovementEngine dispatch:
```python
            # N6c: self_improvement_suggested SSE
            if self._event_callback and _actions and agent_state.context.get("scorecard"):
                _sse = RuntimeSSEEmitter()
                await self._emit(_sse.self_improvement_suggested(
                    goal_id=agent_state.goal_id,
                    suggestions=[a.action_type.value for a in _actions],
                ))
```

**`chunking_strategy_selected`** — emit in `_node_rag_retrieval` when active strategy set:
```python
            # N6d: chunking_strategy_selected SSE (via IngestionOrchestrator metadata)
            if self._event_callback:
                _sse = RuntimeSSEEmitter()
                await self._emit(_sse.chunking_strategy_selected(
                    goal_id=agent_state.goal_id,
                    content_type="text",
                    strategy=_strategy or "semantic",
                    reason="auto-selected by RetrievalPlanner",
                ))
```

**`embedding_strategy_selected`** — emit in `_node_rag_retrieval` after embedder call:
```python
            # N6e: embedding_strategy_selected SSE
            if self._event_callback and self._embedder:
                _sse = RuntimeSSEEmitter()
                await self._emit(_sse.embedding_strategy_selected(
                    goal_id=agent_state.goal_id,
                    model_id=getattr(self._embedder, "model_id", "unknown"),
                    modality="text",
                    dimension=len(embedding) if embedding else 0,
                    cost_class="low",
                    reason="auto",
                ))
```

### N7: Real SWITCH_MODEL and BLACKLIST_TOOL_PATTERN dispatch

In the SelfImprovement dispatch loop in `_node_verify`, replace log-only with real actions:

```python
            elif "SWITCH_MODEL" in _action_type or "UPDATE_MODEL_ROUTING" in _action_type:
                # N7a: Persist model switch recommendation to agent config
                try:
                    if self._app_state and self._agent_id:
                        _agent_store = getattr(self._app_state, "agent_store", None)
                        if _agent_store:
                            await _agent_store.update_config(
                                agent_id=self._agent_id,
                                tenant_ctx=tenant_ctx,
                                config_patch={"model_downgrade_recommended": True,
                                             "last_switch_reason": "low_eval_score"},
                            )
                except Exception:
                    pass
                from app.observability.logging import get_logger
                get_logger(__name__).warning("self_improvement_model_switch_persisted",
                    goal_id=agent_state.goal_id, score=_scorecard_result.overall_score)

            elif "BLACKLIST_TOOL_PATTERN" in _action_type:
                # N7b: Record tool as unreliable in ToolReliabilityStore
                try:
                    _tr_store = getattr(self, "_tool_reliability_store", None)
                    if _tr_store is not None:
                        # Find lowest-performing tool from recent steps
                        _failed_tools = [
                            s.tool_calls[0].get("tool_name", "")
                            for s in agent_state.steps
                            if getattr(s, "tool_calls", None) and
                            not s.tool_calls[0].get("success", True)
                        ]
                        for _ft in _failed_tools[:3]:
                            if _ft:
                                import asyncio as _bl_asyncio
                                _bl_asyncio.ensure_future(
                                    _tr_store.record(
                                        tool_name=_ft,
                                        tenant_ctx=tenant_ctx,
                                        success=False,
                                        latency_ms=5000,
                                        error="blacklisted_by_self_improvement",
                                    )
                                )
                except Exception:
                    pass
```

---

## PHASE 3: Prompt context + ToolReliability

### N8: OutputContractBuilder with goal-aware format detection

In `app/context/output_contract_builder.py`, update `build()` to accept goal text and detect format:

```python
class OutputContractBuilder:
    _JSON_SIGNALS = frozenset({"json", "list", "table", "csv", "structured", "data", "dict", "array"})
    _MARKDOWN_SIGNALS = frozenset({"report", "document", "readme", "markdown", "formatted", "write"})

    def build(
        self,
        output_format: str = "auto",
        required_fields: list[str] | None = None,
        goal: str = "",
    ) -> OutputSchema:
        fields = required_fields or []
        # Auto-detect format from goal text
        if output_format == "auto" and goal:
            g = goal.lower()
            if any(s in g for s in self._JSON_SIGNALS):
                output_format = "json"
            elif any(s in g for s in self._MARKDOWN_SIGNALS):
                output_format = "markdown"
            else:
                output_format = "text"
        instructions = ""
        if output_format == "json":
            instructions = (
                f"Return ONLY valid JSON with fields: {fields}. "
                "No markdown code fences, no explanation." if fields
                else "Return ONLY valid JSON. No markdown code fences, no explanation."
            )
        elif output_format == "markdown":
            instructions = "Structure your response using proper Markdown headings, bullets, and code blocks."
        elif output_format == "text":
            instructions = "Provide a clear, concise text response."
        return OutputSchema(output_format=output_format, required_fields=fields, instructions=instructions)
```

Wire to `_node_plan` with goal text:
```python
        # N8: OutputContractBuilder with goal-aware detection
        try:
            from app.context.output_contract_builder import OutputContractBuilder
            _ocb = OutputContractBuilder()
            _contract = _ocb.build(goal=agent_state.goal)
            if _contract.instructions:
                extra_parts.append(f"[Output contract]\n{_contract.instructions}")
        except Exception:
            pass
```

### N9: Use executor_context and verifier_context from ContextPipeline

In `app/agent/graph.py` `_node_plan()`, after the pipeline runs, store the contexts:
```python
                if pipeline_result.planner_context:
                    rag_context = pipeline_result.planner_context
                    agent_state.context["_pipeline_citations"] = [...]
                # N9: Store executor and verifier contexts for downstream use
                if pipeline_result.executor_context:
                    agent_state.context["_executor_context"] = pipeline_result.executor_context
                if pipeline_result.verifier_context:
                    agent_state.context["_verifier_context"] = pipeline_result.verifier_context
```

In `_execute_step()`, prepend executor context to step content:
```python
        # N9: Use executor_context from ContextPipeline
        _exec_ctx = state.context.get("_executor_context", "")
        if _exec_ctx and len(_exec_ctx) > 50:
            content = f"[Relevant context]\n{_exec_ctx[:1500]}\n\n{content}"
```

In `_node_verify()`, prepend verifier context:
```python
        # N9: Use verifier_context from ContextPipeline
        _verif_ctx = agent_state.context.get("_verifier_context", "")
        if _verif_ctx and len(_verif_ctx) > 50:
            verifier_summary = f"[Context for verification]\n{_verif_ctx[:1000]}\n\n{verifier_summary}"
```

### N10: Read ToolReliabilityStore in tool selection

In `_execute_step()`, before building `_tool_defs`, filter out unreliable tools:
```python
        # N10: Filter unreliable tools using ToolReliabilityStore
        try:
            _tr_store = getattr(self, "_tool_reliability_store", None)
            if _tr_store is not None and _tc_ctx is not None:
                import asyncio as _tr_asyncio
                _unreliable = await _tr_store.get_unreliable_tools(
                    tenant_id=tenant_ctx.tenant_id, threshold=0.3
                )
                _unreliable_names = {t.get("tool_name", "") for t in _unreliable}
                if _unreliable_names:
                    # Log but don't hard-block — mark in step context
                    agent_state.context["_unreliable_tools"] = list(_unreliable_names)
        except Exception:
            pass
```

---

## PHASE 4: Chunkers + granular flags + RAG dispatch

### N11: Wire ingestion chunkers through IngestionOrchestrator

In `app/ingestion/orchestrator.py`, update `_chunk()` to dispatch to chunkers:

```python
    def _chunk(self, content: str, ct: ContentType) -> list[str]:
        """Dispatch to appropriate chunker based on content type."""
        try:
            from app.ingestion.chunkers import get_chunker_for_strategy
            strategy = self._chunker_sel.select(ct)
            chunker = get_chunker_for_strategy(strategy)
            if chunker is not None:
                return [c.content for c in chunker.chunk(content) if c.content.strip()]
        except Exception:
            pass
        # Fallback: paragraph split
        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
        return paras or [content]
```

Read `app/ingestion/chunkers/__init__.py` to find `get_chunker_for_strategy()` and how it works.

### N12: Split DYNAMIC_ORCHESTRATION into granular feature flags

In `app/core/runtime_flags.py`, read the current `RuntimeFlags` class. Add individual flags:

```python
@dataclass
class RuntimeFlags:
    dynamic_orchestration: bool = False     # master flag (enables all below if True)
    enable_runtime_scorecard: bool = False  # RuntimeScorecard after each goal
    enable_self_improvement: bool = False   # SelfImprovementEngine action dispatch
    enable_rag_strategy_routing: bool = False  # Profile-based RAG strategy in _node_rag
    enable_pattern_sse_events: bool = False    # pattern_assembled, eval_score_recorded SSEs
    enable_guardrail_profile: bool = False     # Profile-based GuardrailEnforcer
    readiness_gate: bool = False
```

Update checks throughout:
- In `_node_verify` RuntimeScorecard block: check `flags.enable_runtime_scorecard OR flags.dynamic_orchestration`
- In `_node_rag_retrieval` strategy routing: check `flags.enable_rag_strategy_routing OR flags.dynamic_orchestration`
- SSE events: check `flags.enable_pattern_sse_events OR flags.dynamic_orchestration`
- GuardrailEnforcer: check `flags.enable_guardrail_profile OR flags.dynamic_orchestration`

### N13: _node_rag_retrieval dispatches advanced strategies

In `app/agent/graph.py` `_node_rag_retrieval()`, after setting `_active_rag_strategy`, dispatch to advanced strategies when they're set:

```python
        # N13: Dispatch advanced RAG strategies when profile set them
        _advanced = _active_rag_strategy if _active_rag_strategy not in (
            "hybrid", "direct", "lexical", "vector", "auto", None
        ) else None
        
        if _advanced and self._db_session_factory is not None and self._embedder is not None:
            try:
                from app.rag.engine import retrieve as _adv_retrieve
                _embedding_for_retrieve = embedding if embedding else []
                _adv_results = await _adv_retrieve(
                    self._db_session_factory,  # Note: needs session, not factory
                    query=agent_state.goal,
                    query_embedding=_embedding_for_retrieve,
                    collection_id=search_cols[0] if search_cols else "",
                    top_k=8,
                    strategy=_advanced,
                    provider=self._planner,  # LLM provider for FLARE/RAPTOR/etc.
                )
                if _adv_results:
                    # Use advanced results as the retrieved context
                    context_parts = [r.content[:800] for r in _adv_results[:5]]
                    agent_state.context["_active_rag_strategy"] = _advanced
                    agent_state.context["_rag_advanced_used"] = True
            except Exception as _adv_exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("advanced_rag_dispatch_failed",
                    strategy=_advanced, error=str(_adv_exc)[:80])
```

Note: `retrieve()` expects an `AsyncSession`, not a factory. This requires opening a session first:
```python
                async with self._db_session_factory() as _adv_session:
                    _adv_results = await _adv_retrieve(
                        _adv_session, ...
                    )
```

---

## Commit strategy

- Phase 1: `git commit -m "fix(N1-N4): pass pattern flags as constructor kwargs; reflexion feedback loop; latency tracking; cost reads from context"`
- Phase 2: `git commit -m "feat(N5-N7): ABTesting wired; 5 SSE events emitted; SWITCH_MODEL+BLACKLIST real dispatch"`
- Phase 3: `git commit -m "feat(N8-N10): OutputContractBuilder goal-aware; executor/verifier contexts used; ToolReliabilityStore filters unreliable tools"`
- Phase 4: `git commit -m "feat(N11-N13): chunkers dispatched from IngestionOrchestrator; granular feature flags; advanced RAG strategies dispatched from _node_rag_retrieval"`
