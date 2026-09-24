"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.agent.prompts import (
    PLANNER_SYSTEM,
    STRUCTURED_PLANNER_SYSTEM,
)
from app.agent.state import AgentState, GoalStatus
from app.observability.metrics import (
    record_plan_duration,
)
from app.providers.base import CompletionRequest, Message
from app.providers.circuit_breaker import call_with_circuit_breaker
from app.tenancy.context import TenantContext

# Guardrails 2.0 integration
try:
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    _GUARDRAILS_AVAILABLE = True
except ImportError:
    _GUARDRAILS_AVAILABLE = False
    guardrails_engine = None  # type: ignore[assignment]
    GuardrailLayer = None  # type: ignore[assignment]

import contextlib

from app.agent.graph_types import GraphState, RetrievalEntryPointError  # noqa: F401
from app.agent.nodes._helpers import (
    _guardrail_should_fail_closed,
    _parse_json,
)


class PlannerMixin:
    """Mixin: _node_plan."""

    async def _node_plan(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        rag_context: str = state.get("rag_context", "")
        iteration: int = state.get("iteration", 0) + 1

        agent_state.status = GoalStatus.PLANNING
        agent_state.iterations = iteration

        # ── Adaptive execution strategy (A/B/C) ────────────────────────────────
        # Resolve the per-model strategy once per goal (first plan) and stash it
        # so the executor reads the same decision. Refined by observed reliability
        # when a capability tracker is wired (P5).
        _strategy = agent_state.context.get("_execution_strategy")
        if _strategy is None:
            try:
                _strategy = await self._current_strategy(tenant_ctx)
            except Exception:  # pragma: no cover - defensive
                _strategy = getattr(self, "_execution_strategy", None)
            if _strategy is not None:
                agent_state.context["_execution_strategy"] = _strategy
                _pm = getattr(_strategy.plan_mode, "value", str(_strategy.plan_mode))
                _tm = getattr(_strategy.tool_mode, "value", str(_strategy.tool_mode))
                await self._emit(
                    {
                        "type": "execution_strategy_resolved",
                        "plan_mode": _pm,
                        "tool_mode": _tm,
                        "reason": getattr(_strategy, "reason", ""),
                    }
                )

        # ── ContextPipeline processing ─────────────────────────────────────────
        try:
            from app.context.context_pipeline import ContextPipeline
            from app.context.rerank_policy import RerankStrategy

            runtime_profile = agent_state.context.get("_runtime_profile")
            rerank_strategy = RerankStrategy.SCORE
            if runtime_profile is not None:
                reranker_name = getattr(runtime_profile.rag_strategy, "reranker", "score")
                with contextlib.suppress(ValueError):
                    rerank_strategy = RerankStrategy(reranker_name)
            retrieved_chunks = agent_state.context.get("_retrieved_chunks", [])
            if not retrieved_chunks and rag_context:
                retrieved_chunks = [{"content": rag_context, "score": 0.7, "chunk_id": "rag_0"}]
            if retrieved_chunks:
                # BK3 (D-20 follow-up): wrap the already-injected knowledge-graph
                # store / semantic cache (two-phase app.state wiring, same as every
                # other optional service on self) as ContextPipeline's duck-typed
                # producers. Absent deps -> None -> pipeline behaves exactly as
                # before (empty graph_facts / semantic_cache_hits branches).
                _graph_source = None
                _semantic_cache_source = None
                if self._knowledge_graph_store is not None:
                    from app.context.context_sources import KnowledgeGraphFactsSource

                    _graph_source = KnowledgeGraphFactsSource(self._knowledge_graph_store)
                if self._semantic_cache is not None:
                    from app.context.context_sources import SemanticCacheHitsSource

                    _semantic_cache_source = SemanticCacheHitsSource(self._semantic_cache)
                pipeline = ContextPipeline(
                    max_tokens=6000,
                    rerank_strategy=rerank_strategy,
                    graph_source=_graph_source,
                    semantic_cache=_semantic_cache_source,
                )
                reflexion_lessons = agent_state.context.get("_reflexion_lessons", [])
                # D-20: forward structured prompt-builder sources fetched by rag_mixin
                # (execution/long-term memory) plus graph_facts + semantic_cache_hits
                # when present in state. execution_memory/long_term_memory default to
                # [] (unconditionally additive); graph_facts/semantic_cache_hits are
                # left None when absent from state so ContextPipeline's own
                # best-effort producers (above) get a chance to fill them in.
                _exec_mem = agent_state.context.get("_execution_memory_records", [])
                _ltm = agent_state.context.get("_long_term_memory_records", [])
                _graph_facts = agent_state.context.get("_graph_facts")
                _sem_cache_hits = agent_state.context.get("_semantic_cache_hits")
                pipeline_result = pipeline.run(
                    chunks=retrieved_chunks,
                    query=agent_state.goal,
                    goal_context=agent_state.goal,
                    reflexion_lessons=reflexion_lessons,
                    execution_memory=_exec_mem if isinstance(_exec_mem, list) else [],
                    long_term_memory=_ltm if isinstance(_ltm, list) else [],
                    semantic_cache_hits=(
                        _sem_cache_hits if isinstance(_sem_cache_hits, list) else None
                    ),
                    graph_facts=_graph_facts if isinstance(_graph_facts, list) else None,
                    tenant_id=tenant_ctx.tenant_id,
                )
                if pipeline_result.planner_context:
                    rag_context = pipeline_result.planner_context
                    agent_state.context["_pipeline_citations"] = [
                        {"index": c.index, "url": c.source_url} for c in pipeline_result.citations
                    ]
                # N9: Store executor and verifier contexts for downstream nodes
                if pipeline_result.executor_context:
                    agent_state.context["_executor_context"] = pipeline_result.executor_context
                if pipeline_result.verifier_context:
                    agent_state.context["_verifier_context"] = pipeline_result.verifier_context
        except Exception as _ctx_exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("context_pipeline_failed_in_plan", error=str(_ctx_exc))
        # ── end ContextPipeline ────────────────────────────────────────────────

        # Build planner prompt with RAG context injected
        extra_parts: list[str] = []
        if rag_context:
            extra_parts.append(f"[Relevant context]\n{rag_context}")
        # Inject knowledge base context from agent's bound collections
        rag_knowledge: str = agent_state.context.get("rag_knowledge", "")
        if rag_knowledge:
            extra_parts.append(f"[Knowledge base context]\n{rag_knowledge}")
        tool_prompt = agent_state.context.get("tool_prompt")
        if isinstance(tool_prompt, str) and tool_prompt:
            extra_parts.append(f"[Available connector tools]\n{tool_prompt}")

        # ── Schema-Aware Prompt Injection ──────────────────────────────────────
        # Inject full JSON schemas for available tools so the LLM uses exact
        # parameter names and never halluccinates argument keys.
        try:
            _tool_ctx = agent_state.context.get("tool_context")
            if _tool_ctx is not None:
                _tools = getattr(_tool_ctx, "tools", []) or []
                if _tools:
                    from app.mcp.tool_intelligence import SchemaAwarePromptInjector

                    _schema_block = SchemaAwarePromptInjector.build_tool_schema_block(_tools)
                    if _schema_block:
                        extra_parts.append(_schema_block)
                        extra_parts.append(
                            SchemaAwarePromptInjector.build_tool_call_format_reminder()
                        )
        except Exception:
            pass  # Never block execution on schema injection failure

        # Inject civilization blackboard context (shared agent findings)
        blackboard_ctx: str = agent_state.context.get("blackboard_context", "")
        if blackboard_ctx:
            extra_parts.append(
                f"[Civilization blackboard — shared knowledge from other agents]\n{blackboard_ctx}"
            )

        # Append any visual/image context from perception pipeline
        image_context = agent_state.context.get("image_context", "")
        if image_context:
            extra_parts.append(f"[Visual context]\n{image_context}")

        # D-23: surface extracted spans from goal attachments processed
        # through MultimodalPipeline (see app.api.goals._extract_multimodal_context)
        # so the planner actually sees what was in an image/PDF/audio/table/
        # code attachment, instead of the pipeline output being dead-ended
        # at the ingestion API.
        multimodal_context: str = agent_state.context.get("multimodal_context", "")
        if multimodal_context:
            extra_parts.append(f"[Multimodal asset context]\n{multimodal_context}")

        if agent_state.verification_feedback:
            extra_parts.append(f"[Previous attempt feedback]\n{agent_state.verification_feedback}")

        # rag_remediate (see RAGMixin._node_rag_remediate) stashes freshly
        # re-retrieved context in agent_state.context["remediation_context"] after
        # a verification failure flagged a context gap, and routes back here via
        # the "rag_remediate" -> "plan" edge. Without reading it back out, that
        # entire remediation retrieval was a no-op: the planner replanned with
        # the exact same (insufficient) context that caused the gap in the first
        # place. Popped (not just read) so it is injected into this one replan
        # only, not into every later, unrelated planning round for this goal.
        remediation_context = agent_state.context.pop("remediation_context", "")
        if remediation_context:
            extra_parts.append(remediation_context)

        if state.get("reasoning_evidence"):
            extra_parts.append(
                "[Reasoning mode]\nUse deliberate decomposition; private reasoning is not retained."
            )

        # ── Skill selection ────────────────────────────────────────────────────
        try:
            from app.agent.skill_selector import SkillSelector

            _skill_sel = SkillSelector()
            _selected_skills = _skill_sel.select(agent_state.goal, max_skills=2, max_tokens=400)
            if _selected_skills:
                _skills_block = _skill_sel.build_skills_context(_selected_skills)
                extra_parts.append(_skills_block)
                # Narrow tool selection to skill's allowed_tools if specified
                _skill_tools: set[str] = set()
                for _sk in _selected_skills:
                    _skill_tools.update(_sk.allowed_tools)
                if _skill_tools:
                    agent_state.context["skill_allowed_tools"] = list(_skill_tools)
        except Exception:
            pass  # skills must never block execution

        # OutputContractBuilder — add output format constraint to planner prompt (M5c)
        try:
            from app.context.output_contract_builder import OutputContractBuilder

            _ocb = OutputContractBuilder()
            _contract = _ocb.build(goal=agent_state.goal)
            if _contract.instructions:
                extra_parts.append(f"[Output contract]\n{_contract.instructions}")
        except Exception:
            pass

        # Episodic memory recall — similar past experiences
        try:
            if self._episodic_memory is not None:
                _ep_episodes = await self._episodic_memory.recall(
                    goal=agent_state.goal,
                    tenant_id=tenant_ctx.tenant_id,
                    limit=3,
                )
                if _ep_episodes:
                    _ep_ctx = self._episodic_memory.format_for_context(_ep_episodes)
                    if _ep_ctx:
                        extra_parts.append(_ep_ctx)
        except Exception:
            pass

        # Procedural memory recall — relevant skills
        try:
            if self._procedural_memory is not None:
                _proc_skills = await self._procedural_memory.recall(
                    goal=agent_state.goal,
                    tenant_id=tenant_ctx.tenant_id,
                    limit=2,
                )
                if _proc_skills:
                    _proc_ctx = self._procedural_memory.format_for_context(_proc_skills)
                    if _proc_ctx:
                        extra_parts.append(_proc_ctx)
        except Exception:
            pass

        # Prospective memory recall — deferred intentions/reminders now due (T3.2).
        # Read-only (does NOT lease items for execution) so surfacing them in the
        # plan prompt can't collide with the scheduler's lease_due path.
        try:
            _prospective = getattr(self, "_prospective_service", None)
            if _prospective is not None:
                from datetime import UTC, datetime

                from app.agent.prospective_wiring import pending_intentions_block

                _now = datetime.now(UTC)
                _pending = await _prospective.list_active(tenant_ctx.tenant_id, now=_now)
                _pi_block = pending_intentions_block(_pending, _now)
                if _pi_block:
                    extra_parts.append(f"[Pending intentions]\n{_pi_block}")
        except Exception:
            pass

        # D-17: Structured reflexion recall — evidence-backed lessons from ReflexionService
        try:
            if self._reflexion_service is not None:
                _reflexion_records = await self._reflexion_service.recall(
                    tenant_id=tenant_ctx.tenant_id,
                    query=agent_state.goal,
                    allowed_data_classes=frozenset(
                        {"public", "internal"}
                    ),
                    top_k=5,
                    token_budget=800,
                )
                if _reflexion_records:
                    _reflexion_lines: list[str] = []
                    for _rec in _reflexion_records:
                        _conf = _rec.confidence
                        _summary = _rec.safe_summary[:200]
                        _refs = ", ".join(_rec.evidence_refs[:3]) if _rec.evidence_refs else "none"
                        _reflexion_lines.append(
                            f"- [{_conf}/10000] {_summary} (evidence: {_refs})"
                        )
                    extra_parts.append(
                        "[Structured reflexion — evidence-backed lessons]\n"
                        + "\n".join(_reflexion_lines)
                    )
        except Exception:
            pass

        user_content = f"Goal: {agent_state.goal}"
        if extra_parts:
            user_content += "\n\n" + "\n\n".join(extra_parts)
        # Use PromptOptimizer variant when wired (Task 7)
        _plan_optimizer = getattr(self, "_prompt_optimizer", None)
        if _plan_optimizer is not None:
            _plan_variant = _plan_optimizer.select_variant(
                "planner", tenant_id=tenant_ctx.tenant_id
            )
            _planner_prompt = (
                _plan_variant.prompt_text if _plan_variant is not None else PLANNER_SYSTEM
            )
            # Store variant ID for A/B feedback in verify node (BUG 4 fix)
            if _plan_variant is not None:
                agent_state.context["planner_variant_id"] = _plan_variant.variant_id
        else:
            # Strategy A: use the structured (dependency-graph) planner when the
            # resolved strategy says so — decoupled from goal-tree decomposition,
            # which stays gated on its own flag. Falls back to the plain planner
            # for weak/unknown models (safe default) or when no strategy resolved.
            from app.agent.execution_strategy import PlanMode as _PlanMode

            _plan_mode = getattr(_strategy, "plan_mode", None) if _strategy is not None else None
            _want_structured = _plan_mode == _PlanMode.STRUCTURED or self._enable_goal_tree
            _planner_prompt = STRUCTURED_PLANNER_SYSTEM if _want_structured else PLANNER_SYSTEM
        agent_system_prompt = agent_state.context.get("system_prompt", "")
        system_content = (
            f"{agent_system_prompt}\n\n{_planner_prompt}"
            if agent_system_prompt
            else _planner_prompt
        )

        # P1.3: Inject HITL rejection note so planner avoids repeating the rejected action
        rejection_note: str = agent_state.context.get("hitl_rejection_note", "")
        if rejection_note:
            system_content += (
                f"\n\n[IMPORTANT — Previous Action REJECTED by Human Operator]\n"
                f"Rejection reason: {rejection_note}\n"
                f"Do NOT repeat the rejected action. Replan with a different approach."
            )

        # Tool context is pre-built by ToolSelector in _build_tool_context and
        # injected via agent_state.context["tool_prompt"] (see lines above at
        # extra_parts.append). The duplicate discover_all_tools block has been
        # removed — single source of truth is now the tiered ToolSelector output.

        # Determine planning model — derive from the wired provider's default so
        # the model name always matches the active provider (OpenAI → gpt-4-turbo,
        # Anthropic → claude-opus-4-8, Fake → "fake", etc.).
        # Never hard-code a vendor-specific model name here.
        planning_model = getattr(self._planner, "_default_model", None) or "gpt-5.2"
        # Update ModelOrchestratorAdapter with current runtime profile for budget-aware selection
        try:
            _runtime_profile_for_router = agent_state.context.get("_runtime_profile")
            if _runtime_profile_for_router is not None and hasattr(
                self._model_router, "update_from_profile"
            ):
                _budget_ratio = agent_state.context.get("total_cost_usd", 0.0) / max(
                    getattr(_runtime_profile_for_router.model_plan, "max_cost_usd", 0.10) or 0.10,
                    0.001,
                )
                self._model_router.update_from_profile(
                    _runtime_profile_for_router,
                    budget_spent_ratio=min(1.0, _budget_ratio),
                )
        except Exception:
            pass
        if self._model_router is not None:
            routed = self._model_router.model_for_goal("planning", goal=agent_state.goal)
            if routed:
                planning_model = routed
            # Cost Auto-Downgrade: use cheaper model when budget > 60% consumed
            if self._cost_controller is not None:
                try:
                    _cost_tier = await self._cost_controller.get_cost_tier(
                        goal_id=agent_state.goal_id,
                        tenant_ctx=tenant_ctx,
                    )
                    if _cost_tier == "economy":
                        _economy_model = self._model_router.model_for("verification")
                        if _economy_model:
                            self._logger.info(
                                "cost_downgrade_economy",
                                original=planning_model,
                                downgraded=_economy_model,
                            )
                            planning_model = _economy_model
                    elif _cost_tier == "standard":
                        _standard_model = self._model_router.model_for("execution")
                        if _standard_model:
                            self._logger.info(
                                "cost_downgrade_standard",
                                original=planning_model,
                                downgraded=_standard_model,
                            )
                            planning_model = _standard_model
                except Exception:
                    pass

        # H21: Emit model_route_selected SSE after model selection
        try:
            if self._event_callback is not None:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter

                _sse_mr = RuntimeSSEEmitter()
                _runtime_prof_mr = agent_state.context.get("_runtime_profile")
                await self._emit(
                    _sse_mr.model_route_selected(
                        goal_id=agent_state.goal_id,
                        planner=planning_model,
                        executor=getattr(self._executor, "_default_model", "") or "",
                        verifier=getattr(self._verifier, "_default_model", "") or "",
                        cost_class=(
                            _runtime_prof_mr.model_plan.cost_class
                            if _runtime_prof_mr is not None
                            else "unknown"
                        ),
                    )
                )
        except Exception:
            pass

        # ── Prompt Compression: reduce token count before LLM call ────────────
        try:
            from app.agent.prompt_compressor import _default_compressor as _compressor

            system_content = _compressor.compress(system_content)
            user_content = _compressor.compress(user_content)
        except Exception:
            pass

        # ── LLM Response Cache: skip planner call on identical goals ──────────
        _llm_rc = getattr(self, "_llm_response_cache", None)
        if _llm_rc is not None and not _llm_rc.should_skip_cache(user_content):
            try:
                _cached_plan = await _llm_rc.get(
                    system=system_content,
                    user=user_content,
                    model=planning_model,
                    tenant_id=tenant_ctx.tenant_id,
                    task_type="planning",
                )
                if _cached_plan is not None:
                    self._logger.info("llm_cache_plan_hit", tenant=tenant_ctx.tenant_id)
                    # Use cached response directly — skip the LLM call
                    # Jump straight to parsing (replicate post-resp.content code)
                    resp_content = _cached_plan

                    # Store locally so the existing parse block below can use it
                    class _FakeResp:
                        content = resp_content

                    resp = _FakeResp()
                    record_plan_duration(agent_state.iterations, 0.0)
                    # bypass the real LLM call below
                    _llm_plan_cached = True
                else:
                    _llm_plan_cached = False
            except Exception:
                _llm_plan_cached = False
        else:
            _llm_plan_cached = False

        if not _llm_plan_cached:
            # Use structured output when available (Phase 3 Track A)
            _response_schema = None
            try:
                from app.agent.schemas import planner_schema

                if (
                    hasattr(self._planner, "supports_structured_output")
                    and self._planner.supports_structured_output()
                ):
                    _response_schema = planner_schema()
            except Exception:
                pass

            req = CompletionRequest(
                messages=[
                    Message(role="system", content=system_content),
                    Message(role="user", content=user_content),
                ],
                model=planning_model,
                response_schema=_response_schema,
                cache_prefix=system_content,  # stable prefix for Anthropic ephemeral caching
            )
            with self._tracer.start_as_current_span("agentverse.plan") as span:
                span.set_attribute("plan.iteration", agent_state.iterations)
                span.set_attribute("tenant.id", tenant_ctx.tenant_id)
                _plan_start = time.monotonic()
                try:
                    resp = await call_with_circuit_breaker(
                        self._planner,
                        "complete",
                        req,
                        provider_name=type(self._planner).__name__,
                    )
                except (RuntimeError, TimeoutError) as cb_exc:
                    raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
                record_plan_duration(agent_state.iterations, time.monotonic() - _plan_start)
            # 2.3: Per-goal planner cost tracking
            try:
                from app.observability.cost_breakdown import record_role_cost as _rrc

                _rrc(
                    goal_id=agent_state.goal_id,
                    role="planner",
                    model=planning_model,
                    input_tok=getattr(resp, "input_tokens", 0),
                    output_tok=getattr(resp, "output_tokens", 0),
                    cost=0.0,
                )
            except Exception:
                pass
            # Store in LLM cache — only on successful, non-error responses
            if _llm_rc is not None:
                try:
                    _is_error_resp = (
                        not resp.content
                        or "error" in resp.content.lower()[:40]
                        or resp.content.strip().startswith('{"error')
                    )
                    if not _is_error_resp:
                        await _llm_rc.set(
                            system=system_content,
                            user=user_content,
                            model=planning_model,
                            response=resp.content,
                            tenant_id=tenant_ctx.tenant_id,
                            task_type="planning",
                        )
                except Exception:
                    pass
        parsed = _parse_json(resp.content, key="steps")
        raw_steps = parsed.get("steps", [resp.content])

        # Handle structured format (list of dicts from STRUCTURED_PLANNER_SYSTEM)
        # vs plain string steps (list of str from PLANNER_SYSTEM)
        if raw_steps and isinstance(raw_steps[0], dict):
            # Structured: pass full JSON as single entry so _node_execute can parse
            # it via StructuredPlan.from_llm_response (dependency-aware parallel waves)
            plan = [resp.content]
            _plan_display = [
                str(s.get("description", s.get("id", f"step{i}"))) for i, s in enumerate(raw_steps)
            ]
        else:
            plan = [str(s) for s in raw_steps] if raw_steps else [resp.content]
            _plan_display = plan

        if not plan:
            plan = [resp.content]
            _plan_display = plan

        # Phase 5: validate that plan steps reference known tools
        _tool_warnings = await self._validate_plan_tools(_plan_display, tenant_ctx)
        for _warn in _tool_warnings:
            self._logger.warning("plan_tool_validation", warning=_warn)

        agent_state.plan = _plan_display

        # Guardrails 2.0: PLAN layer — declared in GuardrailLayer but never
        # actually checked anywhere before this fix, so a compliance rule an
        # operator targets at "plan" (there is no built-in default; an
        # operator-authored rule via POST /guardrails/rules or a future
        # bundle) had zero real effect. Gate the assembled plan text here,
        # after the planner produces it but before "execute" ever runs,
        # mirroring the block behaviour already used for FINAL_OUTPUT /
        # TOOL_ARGS. A block sets the same ``terminal_reason`` the GOAL-layer
        # check in initialize_mixin.py uses, which ``RoutingMixin._route``
        # already honours to stop the goal after this pass.
        if _GUARDRAILS_AVAILABLE and guardrails_engine is not None:
            _g2_plan_content = "\n".join(
                str(s.get("description", s.get("id", ""))) if isinstance(s, dict) else str(s)
                for s in _plan_display
            ).strip()
            if _g2_plan_content:
                try:
                    guardrails_engine.ensure_default_rules(tenant_ctx.tenant_id)
                    _g2_plan_result = await guardrails_engine.evaluate(
                        content=_g2_plan_content[:2000],
                        layer=GuardrailLayer.PLAN,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_id=agent_state.goal_id,
                    )
                    if _g2_plan_result.get("blocked"):
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = "Plan rejected by guardrail policy"
                        await self._emit(
                            {"type": "plan_rejected", "reason": agent_state.error_message}
                        )
                        return {
                            "agent_state": agent_state,
                            "plan": [],
                            "iteration": iteration,
                            "terminal_reason": "guardrail_rejected",
                        }
                except Exception as _g2_plan_exc:
                    # SAFE-4 (P0-15): an errored safety check must not read as
                    # "allowed" on high-risk work — fail closed.
                    if _guardrail_should_fail_closed(
                        agent_state.goal, agent_state.context.get("_risk_level")
                    ):
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = (
                            "Plan guardrail check errored on high-risk goal; failing closed."
                        )
                        self._logger.warning(
                            "planner_guardrail_failed_closed", error=str(_g2_plan_exc)
                        )
                        await self._emit(
                            {"type": "plan_rejected", "reason": agent_state.error_message}
                        )
                        return {
                            "agent_state": agent_state,
                            "plan": [],
                            "iteration": iteration,
                            "terminal_reason": "guardrail_rejected",
                        }

        # ── P5 adaptivity: record structured-plan reliability ──────────────────
        # When we asked for a structured (dependency-graph) plan, record whether
        # the model actually produced one. The tracker's rolling success rate
        # feeds _current_strategy so a model that keeps emitting malformed JSON is
        # auto-downgraded to SEQUENTIAL (and restored when it recovers).
        _cap_tracker = getattr(self, "_capability_tracker", None)
        if _cap_tracker is not None:
            from app.agent.execution_strategy import PlanMode as _PlanModeP5

            _pmode = getattr(_strategy, "plan_mode", None) if _strategy is not None else None
            if _pmode == _PlanModeP5.STRUCTURED:
                _structured_ok = bool(raw_steps and isinstance(raw_steps[0], dict))
                with contextlib.suppress(Exception):
                    await _cap_tracker.record(
                        planning_model,
                        ok=_structured_ok,
                        tenant_id=tenant_ctx.tenant_id,
                        kind="structured",
                    )

        await self._emit({"type": "plan_ready", "steps": _plan_display, "iteration": iteration})
        # ── Predictive Prefetch: embed all step descriptions now so semantic
        # cache lookups during execution are instant (sub-millisecond) ──────────
        if self._embedder is not None and self._semantic_cache is not None:

            async def _prefetch_steps() -> None:
                try:
                    step_texts = []
                    for _s in _plan_display:
                        if isinstance(_s, str):
                            step_texts.append(_s)
                        elif isinstance(_s, dict):
                            step_texts.append(_s.get("description") or _s.get("step") or str(_s))
                    if not step_texts:
                        return
                    # Batch embed all steps
                    _embeds = await self._embedder.embed_batch(step_texts)
                    # Warm the semantic cache with step embeddings
                    if hasattr(self._semantic_cache, "warm"):
                        await self._semantic_cache.warm(
                            queries=step_texts,
                            embeddings=_embeds,
                            tenant_id=tenant_ctx.tenant_id,
                        )
                    self._logger.debug(
                        "predictive_prefetch_complete",
                        steps=len(step_texts),
                        tenant=tenant_ctx.tenant_id,
                    )
                except Exception as _pf_exc:
                    self._logger.debug("predictive_prefetch_failed", error=str(_pf_exc)[:60])

            _pf_task = asyncio.create_task(_prefetch_steps())
            self._background_tasks.add(_pf_task)
            _pf_task.add_done_callback(self._background_tasks.discard)
        return {"agent_state": agent_state, "plan": plan, "iteration": iteration}
