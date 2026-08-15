"""Mixin extracted from app.agent.graph — zero semantic changes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.prompts import (
    CHAIN_OF_THOUGHT_SYSTEM,
    EXECUTOR_SYSTEM,
    PLANNER_SYSTEM,
    REFLECTION_SYSTEM,
    STRUCTURED_PLANNER_SYSTEM,
    VERIFIER_SYSTEM,
)
from app.agent.sanitization import (
    _EXECUTOR_CONTEXT_MAX_LENGTH,
    sanitize_event,
    sanitize_event_value,
    sanitize_tool_event_value,
    sanitize_tool_raw_output,
)
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus, SubGoal
from app.agent.tool_calls import ToolCall, extract_tool_call, repair_tool_call_arguments
from app.agent.tool_risk import classify_tool_risk
from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import CostController
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix
from app.governance.policies import PolicyEngine, PolicyResult
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.explainability import DecisionTrace
from app.intelligence.guardrails import GuardrailChecker
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemoryStore
from app.observability.metrics import (
    record_approval_wait,
    record_goal_completed,
    record_goal_failed,
    record_plan_duration,
    record_tool_call,
    record_verify_duration,
    track_tool_call,
)
from app.pipeline.steps import smart_context_fetch
from app.providers.base import CompletionRequest, LLMProvider, Message, ToolDefinition
from app.providers.circuit_breaker import call_with_circuit_breaker
from app.rag.contracts import RAGExecutionResult, RAGStrategy, resolve_rag_strategy
from app.rag.store import KnowledgeStore
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
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

from app.agent.graph_types import GraphState, RetrievalEntryPointError  # noqa: F401


from app.agent.nodes._helpers import (
    _is_high_risk_step,
    _is_ungrounded_status,
    _build_verifier_summary,
    _parse_json,
    _parse_verifier_response,
    _extract_tool_name as _extract_tool_name_fn,
    _extract_scope_value,
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

        # ── ContextPipeline processing ─────────────────────────────────────────
        try:
            from app.context.context_pipeline import ContextPipeline
            from app.context.rerank_policy import RerankStrategy
            runtime_profile = agent_state.context.get("_runtime_profile")
            rerank_strategy = RerankStrategy.SCORE
            if runtime_profile is not None:
                reranker_name = getattr(runtime_profile.rag_strategy, "reranker", "score")
                try:
                    rerank_strategy = RerankStrategy(reranker_name)
                except ValueError:
                    pass
            retrieved_chunks = agent_state.context.get("_retrieved_chunks", [])
            if not retrieved_chunks and rag_context:
                retrieved_chunks = [{"content": rag_context, "score": 0.7, "chunk_id": "rag_0"}]
            if retrieved_chunks:
                pipeline = ContextPipeline(max_tokens=6000, rerank_strategy=rerank_strategy)
                reflexion_lessons = agent_state.context.get("_reflexion_lessons", [])
                pipeline_result = pipeline.run(
                    chunks=retrieved_chunks,
                    query=agent_state.goal,
                    goal_context=agent_state.goal,
                    reflexion_lessons=reflexion_lessons,
                )
                if pipeline_result.planner_context:
                    rag_context = pipeline_result.planner_context
                    agent_state.context["_pipeline_citations"] = [
                        {"index": c.index, "url": c.source_url}
                        for c in pipeline_result.citations
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

        if agent_state.verification_feedback:
            extra_parts.append(
                f"[Previous attempt feedback]\n{agent_state.verification_feedback}"
            )

        if state.get("reasoning_evidence"):
            extra_parts.append(
                "[Reasoning mode]\nUse deliberate decomposition; private reasoning is not retained."
            )

        # ── Skill selection ────────────────────────────────────────────────────
        try:
            from app.agent.skill_selector import SkillSelector
            _skill_sel = SkillSelector()
            _selected_skills = _skill_sel.select(
                agent_state.goal, max_skills=2, max_tokens=400
            )
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

        user_content = f"Goal: {agent_state.goal}"
        if extra_parts:
            user_content += "\n\n" + "\n\n".join(extra_parts)
        # Use PromptOptimizer variant when wired (Task 7)
        _plan_optimizer = getattr(self, "_prompt_optimizer", None)
        if _plan_optimizer is not None:
            _plan_variant = _plan_optimizer.select_variant("planner", tenant_id=tenant_ctx.tenant_id)
            _planner_prompt = _plan_variant.prompt_text if _plan_variant is not None else PLANNER_SYSTEM
            # Store variant ID for A/B feedback in verify node (BUG 4 fix)
            if _plan_variant is not None:
                agent_state.context["planner_variant_id"] = _plan_variant.variant_id
        else:
            # Use structured planner when goal-tree is enabled for dependency-aware parallel execution
            _planner_prompt = STRUCTURED_PLANNER_SYSTEM if self._enable_goal_tree else PLANNER_SYSTEM
        agent_system_prompt = agent_state.context.get("system_prompt", "")
        system_content = (
            f"{agent_system_prompt}\n\n{_planner_prompt}" if agent_system_prompt else _planner_prompt
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
            if _runtime_profile_for_router is not None and hasattr(self._model_router, "update_from_profile"):
                _budget_ratio = (
                    agent_state.context.get("total_cost_usd", 0.0) /
                    max(getattr(_runtime_profile_for_router.model_plan, "max_cost_usd", 0.10) or 0.10, 0.001)
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
                await self._emit(_sse_mr.model_route_selected(
                    goal_id=agent_state.goal_id,
                    planner=planning_model,
                    executor=getattr(self._executor, "_default_model", "") or "",
                    verifier=getattr(self._verifier, "_default_model", "") or "",
                    cost_class=(
                        _runtime_prof_mr.model_plan.cost_class
                        if _runtime_prof_mr is not None else "unknown"
                    ),
                ))
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
                        self._planner, "complete", req,
                        provider_name=type(self._planner).__name__,
                    )
                except RuntimeError as cb_exc:
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
                        or resp.content.strip().startswith("{\"error")
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
                str(s.get("description", s.get("id", f"step{i}")))
                for i, s in enumerate(raw_steps)
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

