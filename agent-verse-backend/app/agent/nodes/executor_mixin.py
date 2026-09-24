"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from app.agent.prompts import (
    EXECUTOR_SYSTEM,
)
from app.agent.sanitization import (
    _EXECUTOR_CONTEXT_MAX_LENGTH,
)
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus, SubGoal
from app.agent.tool_calls import ToolCall, extract_tool_call, repair_tool_call_arguments
from app.agent.tool_risk import classify_tool_risk
from app.governance.audit import AuditEvent
from app.governance.grants import enforce_tool_call
from app.governance.hitl import ApprovalStatus
from app.governance.permissions import ActionLevel
from app.governance.policies import PolicyResult
from app.intelligence.explainability import DecisionTrace
from app.observability.metrics import (
    record_approval_wait,
    record_tool_call,
    track_tool_call,
)
from app.pipeline.steps import smart_context_fetch
from app.providers.base import CompletionRequest, Message, ToolDefinition
from app.rag.contracts import RAGStrategy
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
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

import contextlib

from app.agent.graph_types import GraphState, RetrievalEntryPointError  # noqa: F401
from app.agent.nodes._helpers import (
    _extract_scope_value,
    _guardrail_should_fail_closed,
    _is_high_risk_step,
    resolve_effective_tool_risk,
)

# Per-goal tool-call budget. Once the goal has spent this many tool calls across
# all steps, the executor stops calling tools and is instructed to synthesize a
# final answer from the data already gathered. Without this, a weak planner
# re-searches on every replan and never converges (observed: 34 web searches
# across 11 replans before a goal failed). Overridable via ``_tool_call_budget``.
_DEFAULT_TOOL_CALL_BUDGET = 12

# Prefixes that mark plain LLM reasoning ("I'll call the tool…") rather than an
# actual tool result. Such text must never be cached or served as a step result.
_LLM_REASONING_PREFIXES = (
    "i'll ",
    "i will ",
    "i'll use",
    "i will use",
    "to complete",
    "let me ",
    "i need to ",
    "i can ",
    "i should ",
    "step 1",
    "first,",
    "first i",
    "i'll now",
    "i'll start",
    "i'll call",
    "i'll search",
    "now i'll",
    "next, i",
    "to search",
)

# Empty-collection markers — a "no rows" result is not a reusable answer.
_EMPTY_RESULT_MARKERS = (
    '{"issues": [], "total": 0}',
    '{"projects": []}',
    '{"items": []}',
    "[]",
    "{}",
)


def _is_uncacheable_output(output: str | None) -> bool:
    """Single source of truth for "must not enter or be served from the cache".

    Applied symmetrically on BOTH write and read so a poisoned entry — an error,
    an approval placeholder, an empty collection, or plain LLM reasoning text —
    can never be stored *or* served as if it were a successful tool result.
    Previously the read paths were weaker than the write path, so a stale
    "requires approval (non-supervised mode)" or error response could be served
    as a fake success and satisfy the verifier.
    """
    text = (output or "").strip()
    if len(text) < 10:
        return True
    lowered = text.lower()
    if (
        lowered.startswith('{"error')
        or lowered.startswith("error:")
        or "requires approval" in lowered
        or "model_not_found" in lowered
        or "invalid model" in lowered
        or "rate_limit_exceeded" in lowered
        or "mcp client unavailable" in lowered
        or "tool not available" in lowered
        or "argument validation failed" in lowered
        or "circuit open" in lowered
    ):
        return True
    if text in _EMPTY_RESULT_MARKERS or '"total": 0' in text or '"issues": []' in text or (
        '"projects": []' in text
    ):
        return True
    # Plain LLM reasoning text ("I'll call the tool…") — not an actual result.
    if lowered.startswith(_LLM_REASONING_PREFIXES):
        return True
    if "will use" in lowered and "tool" in lowered:
        return True
    if "will call" in lowered and len(text) < 500:  # noqa: SIM103
        return True
    return False


class ExecutorMixin:
    """Mixin: _node_execute, _execute_step_with_loop, _execute_step, _execute_step_with_cache."""

    def _record_provider_health(self, model: str, *, ok: bool, start: float) -> None:
        """D-13: report a live LLM provider-call outcome to the model router's health
        policy so orchestrator failover learns. Fully guarded — never raises."""
        router = getattr(self, "_model_router", None)
        if router is None or not hasattr(router, "record_provider_result"):
            return
        try:
            latency_ms = (time.monotonic() - start) * 1000.0
            # Pass ok/latency_ms by keyword so the call is robust to both the
            # adapter (positional model) and the raw orchestrator (keyword-only ok).
            router.record_provider_result(model or "", ok=ok, latency_ms=latency_ms)
        except Exception:
            pass

    async def _node_execute(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        plan: list[str] = state.get("plan") or agent_state.plan

        agent_state.status = GoalStatus.EXECUTING

        # Goal-tree decomposition: delegate large plans to parallel sub-agents
        if self._enable_goal_tree and len(plan) >= self._goal_tree_threshold:
            from app.agent.goal_tree import execute_goal_tree

            def _sub_graph_factory() -> Any:
                from opentelemetry import context as otel_context

                from app.agent.graph import (
                    AgentGraph as AgentGraph_,  # local import to avoid circular
                )

                graph = AgentGraph_(
                    planner=self._planner,
                    executor=self._executor,
                    verifier=self._verifier,
                    max_iterations=5,
                    # Inherit governance + reliability from parent
                    permission_matrix=self._permission_matrix,
                    audit_log=self._audit_log,
                    cost_controller=self._cost_controller,
                    hitl_gateway=self._hitl_gateway,
                    policy_engine=self._policy_engine,
                    result_processor=self._result_processor,
                    dedup_cache=DeduplicationCache(),  # fresh instance per sub-agent
                    rollback_engine=RollbackEngine(),  # fresh instance per sub-agent
                    guardrail_checker=self._guardrail_checker,
                    # Inherit memory + RAG
                    exec_memory=self._exec_memory,
                    long_term_memory=self._long_term_memory,
                    knowledge_store=self._knowledge_store,
                    retrieval_gateway=self._retrieval_gateway,
                    mcp_client=self._mcp_client,
                    eval_runner=self._eval_runner,
                    # Sub-agents don't recurse into goal trees
                    enable_goal_tree=False,
                    autonomy_mode=self._autonomy_mode,
                )
                graph._agent_collection_ids = list(self._agent_collection_ids)
                graph._event_callback = self._event_callback
                graph._parent_trace_context = otel_context.get_current()
                return graph

            try:
                sub_goals: list[SubGoal] = await execute_goal_tree(
                    agent_state.goal,
                    planner=self._planner,
                    tenant_ctx=tenant_ctx,
                    parent_goal_id=agent_state.goal_id,
                    graph_factory=_sub_graph_factory,
                    event_callback=self._event_callback,
                )
                agent_state.sub_goals = sub_goals
                if sub_goals:
                    child_failures = [
                        sub_goal for sub_goal in sub_goals if sub_goal.status is GoalStatus.FAILED
                    ]
                    for sub_goal in sub_goals:
                        agent_state.provenance.extend(sub_goal.provenance)
                    agent_state.context["child_retrieval_traces"] = [
                        trace for sub_goal in sub_goals for trace in sub_goal.retrieval_trace
                    ]
                    # Aggregate sub-goal results as steps so the verifier sees them
                    for sg in sub_goals:
                        step = StepResult(
                            description=sg.description,
                            output=sg.result or sg.error,
                            status=StepStatus.COMPLETE if not sg.error else StepStatus.FAILED,
                        )
                        agent_state.steps.append(step)
                    if child_failures:
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = "Nested retrieval failed"
                        await self._emit(
                            {
                                "type": "nested_goal_failed",
                                "failed_sub_goals": [
                                    sub_goal.sub_goal_id for sub_goal in child_failures
                                ],
                                "provenance": agent_state.provenance,
                                "retrieval_trace": agent_state.context["child_retrieval_traces"],
                            }
                        )
                    return {"agent_state": agent_state}
            except Exception as exc:
                # Fall through to normal execution if goal-tree fails
                await self._emit({"type": "goal_tree_error", "error": str(exc)})

        # Build StructuredPlan for wave-based parallel execution (Fix 1 + Fix 3)
        import asyncio as _asyncio

        from app.agent.structured_plan import StructuredPlan, StructuredStep

        _structured: StructuredPlan | None = None
        for _entry in plan:
            try:
                _parsed = json.loads(_entry)
                if isinstance(_parsed, dict) and "steps" in _parsed:
                    _structured = StructuredPlan.from_llm_response(_entry)
                    break
            except Exception:
                pass

        if _structured is None:
            # Plain string steps — treat as sequential (each depends on the previous)
            _structured = StructuredPlan(
                steps=[
                    StructuredStep(
                        id=f"s{i}", description=sd, depends_on=[f"s{i - 1}"] if i > 0 else []
                    )
                    for i, sd in enumerate(plan)
                ]
            )

        waves = _structured.execution_waves()
        step_global_index = 0
        # P1.1: Track completed StructuredStep objects for condition evaluation
        _completed_steps: dict[str, Any] = {}

        # ── Batch cache prefetch ────────────────────────────────────────────
        # Embed ALL plan step descriptions at once (single embedding API call)
        # then batch-check the cache. This way, before the first wave executes,
        # we already know which steps have cache hits — saving per-step embedding
        # latency during the hot path.
        _batch_cache_results: dict[str, str] = {}  # step_desc → cached_response
        if self._semantic_cache is not None and self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest as _EmbedReq

                _all_descs = [s.description for w in waves for s in w]
                if _all_descs:
                    _batch_resp = await self._embedder.embed(_EmbedReq(texts=_all_descs))
                    _batch_embs = _batch_resp.embeddings or []
                    if _batch_embs and hasattr(self._semantic_cache, "get_batch"):
                        _batch_hits = await self._semantic_cache.get_batch(
                            embeddings=_batch_embs,
                            tenant_id=tenant_ctx.tenant_id,
                        )
                        for desc, hit in zip(_all_descs, _batch_hits, strict=False):
                            if hit is not None:
                                # Skip cached empty/error/approval/reasoning results so
                                # they are never served on fresh runs — forces a real
                                # tool call. Same filter as the write path (defect: read
                                # was weaker, so a stale "requires approval"/error could
                                # be served as a fake success).
                                cached_resp = hit.response if hasattr(hit, "response") else str(hit)
                                if not _is_uncacheable_output(cached_resp):
                                    _batch_cache_results[desc] = cached_resp
                        if _batch_cache_results:
                            self._logger.info(
                                "batch_cache_prefetch",
                                total=len(_all_descs),
                                hits=len(_batch_cache_results),
                            )
            except Exception as _bp_exc:
                self._logger.debug("batch_cache_prefetch_skipped", error=str(_bp_exc)[:80])

        for wave_idx, wave in enumerate(waves):
            # P1.1: Filter out steps whose condition evaluates to False
            eligible_steps = [s for s in wave if s.should_execute(_completed_steps)]
            if not eligible_steps:
                self._logger.info(
                    "wave_all_steps_skipped_by_condition",
                    wave=wave_idx,
                    skipped=[s.id for s in wave],
                )
                continue

            if len(eligible_steps) == 1:
                # Single step — execute normally (with loop support if configured)
                struct_step = eligible_steps[0]
                step_desc = struct_step.description
                step = StepResult(description=step_desc, status=StepStatus.RUNNING)
                agent_state.steps.append(step)
                await self._emit({"type": "step_started", "step": step_desc})

                with self._tracer.start_as_current_span("agentverse.step.execute") as span:
                    span.set_attribute("step.description", step_desc[:200])
                    try:
                        if struct_step.loop_until is not None:
                            # P1.1: Loop execution
                            output = await self._execute_step_with_loop(
                                struct_step, agent_state, tenant_ctx
                            )
                        elif step_desc in _batch_cache_results:
                            # Batch prefetch hit — serve from pre-fetched cache result
                            output = _batch_cache_results[step_desc]
                            await self._emit(
                                {
                                    "type": "cache_hit",
                                    "step": step_desc,
                                    "source": "batch_prefetch",
                                }
                            )
                        elif self._semantic_cache is not None:
                            output = await self._execute_step_with_cache(
                                step_desc, agent_state, tenant_ctx
                            )
                        else:
                            output = await self._execute_step(step_desc, agent_state, tenant_ctx)
                    except PermissionError as exc:
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = str(exc)
                        step.status = StepStatus.FAILED
                        step.error = str(exc)
                        raise  # re-raise so LangGraph propagates it out of ainvoke

                step.output = output
                step.status = StepStatus.COMPLETE
                # P1.1: Update StructuredStep runtime state for condition evaluation
                struct_step.output = output
                struct_step.status = "complete"
                _completed_steps[struct_step.id] = struct_step
                await self._emit({"type": "step_complete", "step": step_desc, "output": output})
                # Persist tool outcome for cross-restart trust scores
                try:
                    _orch_persist = (
                        getattr(self._app_state, "orchestration_persistence", None)
                        if self._app_state
                        else None
                    )
                    if _orch_persist is not None:
                        _tool_nm = self._extract_tool_name(step_desc) or step_desc[:50]
                        _step_ok = (
                            output
                            and "error" not in output.lower()[:50]
                            and "failed" not in output.lower()[:50]
                        )
                        _step_lat = float(agent_state.context.get("last_step_latency_ms", 200.0))
                        import asyncio as _tp_asyncio

                        _tp_asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                            _orch_persist.persist_tool_outcome(
                                tool_name=_tool_nm,
                                success=bool(_step_ok),
                                latency_ms=_step_lat,
                                tenant_id=tenant_ctx.tenant_id,
                            )
                        )
                except Exception:
                    pass
                # Invoke step_callback for streaming simulation support
                if self._step_callback is not None:
                    try:
                        import asyncio as _asyncio_cb

                        _asyncio_cb.create_task(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                            self._step_callback(
                                "step_completed",
                                {
                                    "description": step_desc,
                                    "tool_called": self._extract_tool_name(step_desc),
                                    "output": output[:500] if output else "",
                                    "cost_increment": (
                                        agent_state.context.get("last_step_cost", 0.0)
                                        if isinstance(agent_state.context, dict)
                                        else 0.0
                                    ),
                                },
                            )
                        )
                    except Exception:
                        pass
                await self._write_checkpoint(
                    agent_state.goal_id, step_global_index, agent_state, tenant_ctx
                )
                step_global_index += 1

            else:
                # Multiple independent steps — execute in parallel via asyncio.gather
                await self._emit(
                    {
                        "type": "steps_parallel_start",
                        "wave": wave_idx,
                        "steps": [s.description for s in eligible_steps],
                        "count": len(eligible_steps),
                    }
                )

                # Pre-create StepResult objects before parallel execution to maintain order
                parallel_steps: list[StepResult] = []
                for s in eligible_steps:
                    sr = StepResult(description=s.description, status=StepStatus.RUNNING)
                    agent_state.steps.append(sr)
                    await self._emit({"type": "step_started", "step": s.description})
                    parallel_steps.append(sr)

                # Lock to protect shared agent_state mutations across concurrent coroutines
                _state_lock = _asyncio.Lock()

                async def _run_wave_step(desc: str, sr: StepResult) -> None:
                    try:
                        if self._semantic_cache is not None:
                            out = await self._execute_step_with_cache(desc, agent_state, tenant_ctx)
                        else:
                            out = await self._execute_step(desc, agent_state, tenant_ctx)
                        async with _state_lock:  # noqa: B023  # closure runs + is awaited within the same wave iteration that defines _state_lock (gather() below completes before the next wave), so the late-binding this rule warns about never happens here
                            sr.output = out
                            sr.status = StepStatus.COMPLETE
                        await self._emit({"type": "step_complete", "step": desc, "output": out})
                        # H4: Persist tool outcome for parallel wave steps
                        try:
                            _orch_persist_wave = (
                                getattr(self._app_state, "orchestration_persistence", None)
                                if self._app_state
                                else None
                            )
                            if _orch_persist_wave is not None:
                                _tool_nm_wave = self._extract_tool_name(desc) or desc[:50]
                                _step_ok_wave = bool(out and "error" not in out.lower()[:50])
                                import asyncio as _wp_asyncio

                                _wp_asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                                    _orch_persist_wave.persist_tool_outcome(
                                        tool_name=_tool_nm_wave,
                                        success=_step_ok_wave,
                                        latency_ms=200.0,
                                        tenant_id=tenant_ctx.tenant_id,
                                    )
                                )
                        except Exception:
                            pass
                    except PermissionError as exc:
                        async with _state_lock:  # noqa: B023  # closure runs + is awaited within the same wave iteration that defines _state_lock (gather() below completes before the next wave), so the late-binding this rule warns about never happens here
                            agent_state.status = GoalStatus.FAILED
                            agent_state.error_message = str(exc)
                            sr.status = StepStatus.FAILED
                            sr.error = str(exc)
                        raise
                    except Exception as exc:
                        async with _state_lock:  # noqa: B023  # closure runs + is awaited within the same wave iteration that defines _state_lock (gather() below completes before the next wave), so the late-binding this rule warns about never happens here
                            sr.status = StepStatus.FAILED
                            sr.error = str(exc)
                        raise

                tasks = [
                    _asyncio.create_task(
                        _run_wave_step(eligible_steps[i].description, parallel_steps[i])
                    )
                    for i in range(len(eligible_steps))
                ]
                try:
                    await _asyncio.gather(*tasks)
                except (PermissionError, Exception):
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await _asyncio.gather(*tasks, return_exceptions=True)
                    raise

                # P1.1: Update StructuredStep runtime state for parallel steps
                for i, struct_step_par in enumerate(eligible_steps):
                    struct_step_par.output = parallel_steps[i].output
                    struct_step_par.status = (
                        "complete" if parallel_steps[i].status == StepStatus.COMPLETE else "failed"
                    )
                    _completed_steps[struct_step_par.id] = struct_step_par

                for i in range(len(eligible_steps)):
                    await self._write_checkpoint(
                        agent_state.goal_id, step_global_index + i, agent_state, tenant_ctx
                    )
                step_global_index += len(eligible_steps)

                await self._emit(
                    {
                        "type": "steps_parallel_complete",
                        "wave": wave_idx,
                        "count": len(eligible_steps),
                    }
                )

        return {"agent_state": agent_state}

    async def _execute_step_with_loop(
        self,
        step: Any,
        agent_state: AgentState,
        tenant_ctx: TenantContext,
    ) -> str:
        """Execute a step with loop-until support (P1.1).

        Calls ``_execute_step`` repeatedly until ``loop_until`` evaluates to True
        or ``max_loop_iter`` is exceeded. Uses exponential backoff between iterations.
        """
        for iteration in range(step.max_loop_iter):
            step.iterations_used = iteration + 1
            output = await self._execute_step(step.description, agent_state, tenant_ctx)
            step.output = output

            try:
                from app.agent.structured_plan import _safe_eval_condition as _loop_eval

                done = _loop_eval(
                    step.loop_until,
                    {"output": output, "iteration": iteration + 1, "iterations": iteration + 1},
                )
            except Exception:
                done = True  # On eval error, exit loop

            if done:
                self._logger.info(
                    "loop_step_completed",
                    step_id=step.id,
                    iterations=step.iterations_used,
                )
                return output

            if iteration < step.max_loop_iter - 1:
                delay = min(2**iteration, 30)  # exponential backoff, max 30s
                self._logger.info(
                    "loop_step_retry",
                    step_id=step.id,
                    iteration=iteration + 1,
                    next_delay_s=delay,
                )
                await asyncio.sleep(delay)

        # Max iterations reached
        self._logger.warning(
            "loop_step_max_iterations_reached",
            step_id=step.id,
            max=step.max_loop_iter,
        )
        return step.output  # Return last output

    async def _execute_step(self, step: str, state: AgentState, tenant_ctx: TenantContext) -> str:
        """Run the canonical governed per-step execution pipeline."""
        tool_name = self._extract_tool_name(step)

        # H23-H26: Action safety profile — assess per-tool risk
        _asp_hitl_required = False
        _asp_reason = ""
        try:
            from app.security_runtime.action_safety_profile import (
                ActionSafetyLevel,
                ActionSafetyProfileSelector,
            )

            _asp_selector = ActionSafetyProfileSelector()
            _risk = state.context.get("_risk_level", "low")
            _asp = _asp_selector.select(
                tool_name=tool_name,
                tool_args={},
                risk_level=str(_risk),
            )
            if _asp.safety_level.value == ActionSafetyLevel.BLOCKED.value:
                return f"Action blocked by safety profile: {_asp.reason}"
            if _asp.safety_level.value == ActionSafetyLevel.HITL_REQUIRED.value:
                _asp_hitl_required = True
                _asp_reason = _asp.reason
        except Exception:
            pass

        # SAFE-3 (P0-14): route an action-safety HITL_REQUIRED verdict through the
        # HITL gateway. This is intentionally OUTSIDE the try/except above so an
        # approval rejection/timeout can never be swallowed and silently allowed.
        _asp_hitl_done = False
        if _asp_hitl_required and self._hitl_gateway is not None:
            req_id = str(
                self._hitl_gateway.request_approval(
                    goal_id=state.goal_id,
                    action=step,
                    risk_level="high",
                    tenant_ctx=tenant_ctx,
                )
            )
            _asp_hitl_done = True
            if self._autonomy_mode == "supervised":
                await self._emit(
                    {"type": "waiting_approval", "request_id": req_id, "action": step}
                )
                approval_started = time.monotonic()
                final_status = await self._hitl_gateway.wait_for_approval(
                    req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
                )
                record_approval_wait(time.monotonic() - approval_started)
                if final_status == ApprovalStatus.REJECTED:
                    raise PermissionError(
                        f"Step '{step}' rejected by human approver "
                        f"(action-safety: {_asp_reason})."
                    )
                if final_status == ApprovalStatus.TIMED_OUT:
                    raise PermissionError(
                        f"Step '{step}' approval timed out (action-safety: {_asp_reason})."
                    )
                await self._emit({"type": "approval_granted", "request_id": req_id})

        # 1. Cost check deferred — actual cost calculated after LLM call below.

        # 2. Exec memory recall — already done in rag_retrieval; skip here.

        # 3. Dedup
        if self._dedup_cache is not None:
            content_hash = hashlib.sha256(f"{step}:{state.goal}".encode()).hexdigest()
            if self._dedup_cache.is_duplicate(content_hash=content_hash, tenant_ctx=tenant_ctx):
                return "Duplicate step, returning cached result."
            self._dedup_cache.mark_seen(content_hash=content_hash, tenant_ctx=tenant_ctx)

        # 3b. Smart context fetch (per-step RAG)
        app_state = getattr(self._app_state, "state", self._app_state)
        step_strategy = RAGStrategy(
            str(state.context.get("retrieval_strategy", RAGStrategy.HYBRID.value))
        )
        step_context = await smart_context_fetch(
            goal=state.goal,
            step=step,
            tenant_ctx=tenant_ctx,
            retrieval_gateway=(
                self._retrieval_gateway or getattr(app_state, "retrieval_gateway", None)
            ),
            collection_ids=list(self._agent_collection_ids),
            strategy=step_strategy,
            top_k=int(state.context.get("retrieval_top_k", 3)),
            filters=state.context.get("retrieval_filters", {}),
            execution_id=state.goal_id,
        )

        # 4. Circuit breaker
        _active_breaker: CircuitBreaker | None = None
        if self._circuit_breakers:
            breaker = self._circuit_breakers.get("llm") or self._circuit_breakers.get(tool_name)
            if breaker is not None:
                if not breaker.can_call():
                    return "Circuit open, step skipped."
                _active_breaker = breaker  # track for success/failure recording

        # 5. Governance — permission check with scope extraction
        if self._permission_matrix is not None:
            scope_value = _extract_scope_value(step)
            level = self._permission_matrix.check(
                tool_name=tool_name,
                tenant_ctx=tenant_ctx,
                scope_value=scope_value,
            )
            if level == ActionLevel.DENY:
                record_tool_call(tool_name, "policy", "denied", 0.0)
                raise PermissionError(
                    f"Tool '{tool_name}' denied by governance policy "
                    f"for tenant '{tenant_ctx.tenant_id}'."
                )

        # 6. Guardrails — validate step text for injection, then tool name
        # 6a. Check the plan STEP TEXT for injection phrases (e.g. "ignore all previous instructions")  # noqa: E501
        # This is important: a compromised tool could return an injection-crafted step description.
        if self._guardrail_checker is not None:
            step_issues = self._guardrail_checker.check_goal(step)
            if step_issues:
                return f"Guardrail blocked step: {'; '.join(step_issues)}"

        # 6b. Check tool name (only check the name; do NOT pass the step description as tool_args
        # since it triggers false positives on benign words like "extract", "format").
        if self._guardrail_checker is not None:
            violations = self._guardrail_checker.check(
                tool_name=tool_name,
                tool_args={},
            )
            if violations:
                return f"Guardrail blocked step: {'; '.join(violations)}"

        # 6c. Profile-based GuardrailEnforcer (dynamic bundle selection from Part 11/13)
        try:
            from app.core.runtime_flags import get_runtime_flags as _ge_rtf
            from app.security_runtime.guardrail_enforcer import GuardrailEnforcer

            _ge_flags = _ge_rtf()
            _runtime_profile = state.context.get("_runtime_profile")
            if (
                _ge_flags.dynamic_orchestration or _ge_flags.enable_guardrail_profile
            ) and _runtime_profile is not None:
                _ge = GuardrailEnforcer()
                _ge_result = await _ge.check_tool_args(
                    tool_name=tool_name,
                    tool_args={},  # C2 fix: tool_args not defined at pre-LLM check stage
                    profile=_runtime_profile,
                )
                if _ge_result.blocked:
                    return f"GuardrailEnforcer blocked tool '{tool_name}': {_ge_result.reason}"
        except Exception:
            pass  # profile-based guardrail never crashes execution

        # N6b: guardrail_profile_selected SSE — only when dynamic orchestration profile present
        if self._event_callback is not None and _runtime_profile is not None:
            try:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter

                _sse_gps = RuntimeSSEEmitter()
                _bundle = (
                    getattr(
                        getattr(_runtime_profile, "security", None), "guardrail_bundle", "default"
                    )
                    or "default"
                )
                await self._emit(
                    _sse_gps.guardrail_profile_selected(
                        goal_id=state.goal_id,
                        bundle=_bundle,
                        scanners=["injection", "pii", "tool_args"],
                    )
                )
            except Exception:
                pass

        # 6b. Policy engine check (glob-based policies)
        # Carry forward the action-safety HITL request so we do not double-prompt.
        _hitl_already_requested = _asp_hitl_done
        if self._policy_engine is not None:
            policy_result = self._policy_engine.evaluate(tool_name=tool_name, tenant_ctx=tenant_ctx)
            if policy_result == PolicyResult.DENY:
                record_tool_call(tool_name, "policy", "denied", 0.0)
                raise PermissionError(
                    f"Tool '{tool_name}' denied by governance policy "
                    f"for tenant '{tenant_ctx.tenant_id}'."
                )
            elif (
                policy_result == PolicyResult.REQUIRE_APPROVAL
                and self._hitl_gateway is not None
                and not _hitl_already_requested
            ):
                req_id = str(
                    self._hitl_gateway.request_approval(
                        goal_id=state.goal_id,
                        action=step,
                        risk_level="high",
                        tenant_ctx=tenant_ctx,
                    )
                )
                _hitl_already_requested = True
                if self._autonomy_mode == "supervised":
                    await self._emit(
                        {"type": "waiting_approval", "request_id": req_id, "action": step}
                    )
                    approval_started = time.monotonic()
                    final_status = await self._hitl_gateway.wait_for_approval(
                        req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
                    )
                    record_approval_wait(time.monotonic() - approval_started)
                    if final_status == ApprovalStatus.REJECTED:
                        raise PermissionError(
                            f"Step '{step}' was rejected by human approver via policy."
                        )

        # 7. HITL gate
        if not _hitl_already_requested and self._hitl_gateway is not None:
            risk = "high" if _is_high_risk_step(step) else "low"
            if risk == "high":
                req_id = str(
                    self._hitl_gateway.request_approval(
                        goal_id=state.goal_id,
                        action=step,
                        risk_level=risk,
                        tenant_ctx=tenant_ctx,
                    )
                )
                if self._autonomy_mode == "supervised":
                    # Actually BLOCK until a human approves or rejects
                    await self._emit(
                        {"type": "waiting_approval", "request_id": req_id, "action": step}
                    )
                    approval_started = time.monotonic()
                    final_status = await self._hitl_gateway.wait_for_approval(
                        req_id, tenant_ctx=tenant_ctx
                    )
                    record_approval_wait(time.monotonic() - approval_started)
                    if final_status == ApprovalStatus.REJECTED:
                        raise PermissionError(f"Step '{step}' was rejected by human approver.")
                    elif final_status == ApprovalStatus.TIMED_OUT:
                        raise PermissionError(f"Step '{step}' approval timed out.")
                    await self._emit({"type": "approval_granted", "request_id": req_id})
                # In bounded/fully-autonomous: just log, don't block

        # 8. Execute via LLM executor
        recent_outputs = "\n".join(
            (s.output or "")[:_EXECUTOR_CONTEXT_MAX_LENGTH] for s in state.steps[-3:] if s.output
        )
        context_parts = []
        if recent_outputs:
            context_parts.append(f"Recent outputs:\n{recent_outputs}")
        if step_context:
            context_parts.append(f"Relevant knowledge:\n{step_context}")

        # Working memory (T1.2): bounded, salience-ranked recall across the WHOLE
        # run (not just the last 3 steps covered by ``Recent outputs``). Volatile —
        # lives in the checkpointed state, never the durable memory store.
        # Best-effort context: a failure here must never fail the step.
        try:
            from app.agent.working_memory_wiring import (
                sync_working_memory,
                working_memory_block,
            )

            sync_working_memory(state.context, state.steps)
            _wm_block = working_memory_block(state.context, focus=f"{state.goal}\n{step}")
            if _wm_block:
                context_parts.append(f"[Working memory]\n{_wm_block}")
        except Exception:
            pass

        # Entity/knowledge-graph memory (T2.1): extract entities observed in prior
        # step outputs into the tenant knowledge graph so later plans can recall
        # what we already learned. Recall itself is wired in PlannerMixin via
        # KnowledgeGraphFactsSource; here we close the population gap. Best-effort.
        try:
            from app.agent.entity_memory_wiring import record_entities_from_steps

            record_entities_from_steps(
                self._knowledge_graph_store,
                tenant_ctx.tenant_id,
                state.steps,
                source_id=state.goal_id,
            )
        except Exception:
            pass

        # ── Search directive parsing ───────────────────────────────────────
        try:
            from app.rag.agentic.search_directive_parser import SearchDirectiveParser

            _directive_parser = SearchDirectiveParser()
            _directives = _directive_parser.extract(step)
            if _directives and self._agent_collection_ids:
                from app.rag.agentic.retriever_tool import RetrieverTool

                _retriever = RetrieverTool(
                    retrieval_gateway=(
                        self._retrieval_gateway or getattr(app_state, "retrieval_gateway", None)
                    )
                )
                _directive_contexts: list[str] = []
                for _directive in _directives:
                    _strategy = {
                        "kb": RAGStrategy.HYBRID,
                        "graph": RAGStrategy.GRAPH,
                        "web": RAGStrategy.WEB_AUGMENTED,
                    }.get(_directive.source_type)
                    if _strategy is None:
                        raise ValueError("Unsupported search directive source")
                    _retrieval = await _retriever.retrieve(
                        query=_directive.query,
                        tenant_ctx=tenant_ctx,
                        strategy=_strategy,
                        collection_ids=list(self._agent_collection_ids),
                        top_k=3,
                        execution_id=state.goal_id,
                    )
                    if _retrieval.chunks:
                        _directive_contexts.append(
                            f"[{_directive.source_type.upper()} SEARCH: {_directive.query}]\n"
                            + _retrieval.context_text[:1000]
                        )
                if _directive_contexts:
                    _directive_context_str = "\n\n".join(_directive_contexts)
                    context_parts.append(_directive_context_str)
        except ValueError:
            raise
        # ── end search directives ──────────────────────────────────────────

        # Ground the executor in the overall GOAL, not just the step. Weak
        # planners sometimes emit a degenerate step that is the answer itself
        # (e.g. plan=["Step 1: Rome"] for "capital of Italy"); without the goal
        # the executor has no actionable instruction and returns INSUFFICIENT
        # DATA, stalling the loop. Including the goal lets it answer regardless.
        content = f"Goal: {state.goal}\nStep: {step}"
        if context_parts:
            content += "\n\n" + "\n\n".join(context_parts)

        # N9: Prepend executor context from ContextPipeline if available
        _exec_ctx = state.context.get("_executor_context", "") or ""
        if _exec_ctx and len(_exec_ctx) > 50:
            content = f"[Relevant context for this step]\n{_exec_ctx[:1200]}\n\n{content}"

        # Collect available tools for structured tool calling (Task 1)
        # IMPORTANT: OpenAI function names must match ^[a-zA-Z0-9_-]{1,64}$
        # DO NOT include server_name in the name — "Jira Connector.jira_search_issues"
        # is invalid and causes OpenAI to return text instead of a tool call.
        _tool_defs: list[ToolDefinition] = []
        _tc_ctx = state.context.get("tool_context")
        if _tc_ctx is not None and hasattr(_tc_ctx, "tools"):
            for _t in _tc_ctx.tools:
                import re as _re

                # Use only the bare tool name, sanitized to valid function-name chars
                _raw_name = _t.name if hasattr(_t, "name") else ""
                _safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "_", _raw_name)[:64]
                if not _safe_name:
                    continue
                _tool_defs.append(
                    ToolDefinition(
                        name=_safe_name,
                        description=getattr(_t, "description", ""),
                        input_schema=getattr(_t, "input_schema", {}),
                    )
                )

        # Civilization: advertise the spawn tool so the LLM can actually call it.
        # Without this the SPAWN_TOOL_DEFINITION is never offered and the spawn
        # dispatch branch below is unreachable.
        if self._civilization_spawn_enabled and self._civilization_id:
            from app.civilization.spawn_tool import SPAWN_TOOL_DEFINITION

            _tool_defs.append(
                ToolDefinition(
                    name=str(SPAWN_TOOL_DEFINITION["name"]),
                    description=str(SPAWN_TOOL_DEFINITION["description"]),
                    input_schema=dict(SPAWN_TOOL_DEFINITION["parameters"]),
                )
            )

        # Build allowed-tools allowlist for anti-hallucination grounding
        _allowed_tools_set: set[str] = set()
        if _tc_ctx is not None:
            try:
                _tools_list = getattr(_tc_ctx, "tools", []) or []
                _allowed_tools_set = {t.name for t in _tools_list if hasattr(t, "name")}
            except Exception:
                pass

        # Keep the civilization spawn tool in the anti-hallucination allowlist so
        # it is listed in the executor system prompt's ALLOWED TOOLS grounding.
        if self._civilization_spawn_enabled and self._civilization_id:
            from app.civilization.spawn_tool import (
                SPAWN_TOOL_DEFINITION as _SPAWN_TOOL_DEFINITION,
            )

            _allowed_tools_set.add(str(_SPAWN_TOOL_DEFINITION["name"]))

        # ToolPromptBuilder — enrich content with formatted tool descriptions (M5b)
        try:
            from app.context.tool_prompt_builder import ToolPromptBuilder

            if _tool_defs:
                _tpb = ToolPromptBuilder()
                _defs_as_dicts = [
                    {"name": td.name, "description": td.description} for td in _tool_defs
                ]
                _tool_context = _tpb.build(tools=_defs_as_dicts, step_context=step)
                if _tool_context:
                    content = f"{content}\n\nAvailable tools:\n{_tool_context}"
        except Exception:
            pass

        # N10: Tag unreliable tools (informational — don't hard-block, just log)
        try:
            _tr_store = getattr(self, "_tool_reliability_store", None)
            if _tr_store is not None and _tool_defs:
                _unreliable = await _tr_store.get_unreliable_tools(
                    tenant_id=tenant_ctx.tenant_id, threshold=0.3
                )
                _unreliable_names = {
                    t.get("tool_name", "") if isinstance(t, dict) else str(t)
                    for t in (_unreliable or [])
                }
                if _unreliable_names:
                    state.context["_unreliable_tools"] = list(_unreliable_names)
                    # Add a hint to the step context
                    _unreliable_hint = (
                        f"\n[Note: these tools have had reliability issues: "
                        f"{', '.join(list(_unreliable_names)[:3])}]"
                    )
                    content = content + _unreliable_hint if content else _unreliable_hint
        except Exception:
            pass

        # Select executor system prompt via PromptOptimizer if wired (Task 7)
        _executor_prompt = EXECUTOR_SYSTEM
        _exec_optimizer = getattr(self, "_prompt_optimizer", None)
        if _exec_optimizer is not None:
            _exec_variant = _exec_optimizer.select_variant("executor")
            if _exec_variant is not None:
                _executor_prompt = _exec_variant.prompt_text

        # Inject allowed-tools list into executor system prompt
        if _allowed_tools_set:
            _tool_lines = "\n".join(f"  - {n}" for n in sorted(_allowed_tools_set)[:30])
            _executor_prompt = (
                _executor_prompt + f"\n\nALLOWED TOOLS (ONLY use these exact names):\n{_tool_lines}"
                # Tools exist, but not every step needs one. Weak models otherwise
                # emit garbage (e.g. a bare "Hello!") when no listed tool fits the
                # step — because the prompt pressures them to call something. Make
                # "answer directly" an explicit, valid option so a self-contained
                # step (arithmetic, a definition) is answered in one turn instead of
                # looping until the verifier rejects the noise.
                + "\n\nIf NONE of these tools is relevant to the current step "
                "(e.g. it is arithmetic, a definition, or reasoning you can do "
                "yourself), do NOT force a tool call — answer the step directly in "
                "plain, concise prose."
            )
        elif not _tool_defs:
            # No tools are available for this step. Weaker models, still steered by
            # the tool-calling system prompt, otherwise emit a (usually
            # hallucinated) tool call such as {"tool": "openai_chat_completion", …}
            # whose raw JSON then leaks into the result. Make "answer directly" the
            # only valid move so the model returns clean prose instead.
            _executor_prompt = _executor_prompt + (
                "\n\nNO TOOLS ARE AVAILABLE for this step. Do NOT emit a tool call or "
                "any JSON — there is nothing to call. Answer the step directly in "
                "plain, concise prose using the goal and provided context. Only if you "
                'genuinely lack the information, reply exactly with: {"tool": null, '
                '"result": "INSUFFICIENT DATA: <what is missing>"}.'
            )

        # Tool-call budget — force convergence. Once the goal has spent its budget
        # of tool calls across all steps, stop searching and make the executor
        # synthesize the final answer from what it already gathered, rather than
        # re-searching on every replan (which never converges for weak planners).
        _tool_budget = int(getattr(self, "_tool_call_budget", _DEFAULT_TOOL_CALL_BUDGET))
        _tool_calls_used = sum(len(s.tool_calls or []) for s in state.steps)
        _budget_exhausted = _tool_budget > 0 and _tool_calls_used >= _tool_budget
        # Tools + tool-call policy for this step. Default: offer every tool and let
        # the provider force a call (tool_choice defaults to "required").
        _step_tool_defs = _tool_defs
        _step_tool_choice: str | None = None
        if _budget_exhausted:
            # Budget spent: stop the re-search loop but do NOT starve the final
            # delivery action. Keep only ACTION tools (writes/deliveries), drop
            # read/search tools, and use tool_choice="auto" so a pure synthesis
            # step can answer in text while a delivery step can still fire its tool.
            from app.agent.nodes._helpers import select_action_tools_for_convergence

            _step_tool_defs = select_action_tools_for_convergence(_tool_defs)
            _step_tool_choice = "auto"
            _executor_prompt = _executor_prompt + (
                f"\n\nTOOL-CALL BUDGET REACHED ({_tool_calls_used}/{_tool_budget}). Do NOT "
                "perform any more search/retrieval. Using the information already gathered "
                "in the context above, produce the best possible final answer now. If the "
                "goal requires a delivery/notification action (e.g. sending a message), you "
                "MAY still call that one action tool to complete the goal; otherwise answer "
                "directly. If some data is missing, answer with what you have and note gaps."
            )

        # Resolve executor model via model_router when available (Bug 3 fix)
        _exec_model = ""
        if self._model_router is not None:
            with contextlib.suppress(Exception):
                _exec_model = self._model_router.model_for("execution") or ""

        req = CompletionRequest(
            messages=[
                Message(role="system", content=_executor_prompt),
                Message(role="user", content=content),
            ],
            model=_exec_model,
            # Once the budget is spent, only ACTION tools remain (so the model cannot
            # keep searching but can still deliver the final answer); tool_choice
            # relaxes to "auto" so a synthesis step is not forced to call a tool.
            tools=_step_tool_defs,
            tool_choice=_step_tool_choice,
        )

        # 0. Cost pre-flight: the only budget check on this path previously ran
        # AFTER the LLM call completed (below, using the actual token cost) —
        # meaning an already-over-budget goal would still pay for, and make,
        # another full LLM call on every subsequent iteration before being
        # told "budget exceeded" post-hoc, compounding real spend past
        # per_goal_usd / per_tenant_daily_usd on every replan until
        # max_iterations finally stopped it.
        #
        # Note this can't be closed by re-checking CostController's recorded
        # totals: check_and_record deliberately does NOT charge a denied call
        # (see its Lua script docstring — "a denied request never permanently
        # charges the tenant"), so goal_total()/daily_total() stay unchanged
        # after a denial and a totals-based pre-check would immediately pass
        # again next iteration, reproducing the same bug one layer down.
        # Instead latch onto agent_state once any call has been denied for
        # this goal, and refuse to start another for the rest of the run.
        if state.context.get("_budget_exhausted"):
            return "Step skipped: budget exceeded."

        # 8a. Bulkhead — distributed concurrency limit per tenant (RedisBulkhead or Semaphore)
        _bulkhead = None
        if self._bulkhead_registry is not None and tenant_ctx is not None:
            try:
                _bulkhead = self._bulkhead_registry.get_bulkhead(tenant_ctx.tenant_id)
            except Exception:
                _bulkhead = None

        _bulkhead_acquired = False
        if _bulkhead is not None:
            try:
                if hasattr(_bulkhead, "acquire"):
                    # RedisBulkhead path
                    _bulkhead_acquired = await _bulkhead.acquire()
                    if not _bulkhead_acquired:
                        self._logger.warning(
                            "bulkhead_full",
                            tenant_id=getattr(tenant_ctx, "tenant_id", ""),
                            step=step[:100],
                        )
                        return (
                            "[Bulkhead: too many concurrent operations for this tenant."
                            " Please retry.]"
                        )
                else:
                    # asyncio.Semaphore fallback
                    await _bulkhead.acquire()
                    _bulkhead_acquired = True
            except Exception as bulkhead_exc:
                self._logger.warning("bulkhead_acquire_failed", error=str(bulkhead_exc))
                _bulkhead_acquired = False

        # Token streaming — buffer for accumulation and closure for on_token callback.
        # Defined before the bulkhead try so the closure captures step by value.
        _token_buffer: list[str] = []
        _step_for_token = step

        async def _on_token(chunk: str) -> None:
            _token_buffer.append(chunk)
            await self._emit(
                {
                    "type": "token_chunk",
                    "step": _step_for_token,
                    "token": chunk,
                    "cumulative": "".join(_token_buffer),
                }
            )

        _llm_call_start = time.monotonic()
        try:
            try:
                async with track_tool_call(tool_name=tool_name, tenant_id=tenant_ctx.tenant_id):
                    resp = await self._executor.stream_tokens(req, _on_token)
                if _active_breaker is not None:
                    _active_breaker.record_success()
                # D-13: feed provider health so ModelOrchestrator failover learns.
                self._record_provider_health(_exec_model, ok=True, start=_llm_call_start)
            except Exception:
                if _active_breaker is not None:
                    _active_breaker.record_failure()
                self._record_provider_health(_exec_model, ok=False, start=_llm_call_start)
                raise
        finally:
            if _bulkhead_acquired and _bulkhead is not None:
                try:
                    if hasattr(_bulkhead, "release"):
                        await _bulkhead.release()  # RedisBulkhead
                    else:
                        _bulkhead.release()  # asyncio.Semaphore
                except Exception:
                    pass

        # 1. Calculate actual LLM cost from token usage and check budget
        if self._cost_controller is not None:
            from app.governance.pricing import estimate_cost as _estimate_cost

            _actual_cost = _estimate_cost(
                resp.model if hasattr(resp, "model") and resp.model else "",
                resp.input_tokens,
                resp.output_tokens,
            )
            async with self._state_lock:
                state.context["total_cost_usd"] = (
                    state.context.get("total_cost_usd", 0.0) + _actual_cost
                )
            ok = await self._cost_controller.check_and_record(
                goal_id=state.goal_id,
                cost_usd=_actual_cost,
                tenant_ctx=tenant_ctx,
            )
            if not ok:
                # Latch so no further LLM call is attempted for the rest of
                # this goal's run (see the pre-flight check above).
                state.context["_budget_exhausted"] = True
                return "Step skipped: budget exceeded."

        # 1b. Record ACTUAL token cost via CostTracker when usage is available
        if self._cost_tracker is not None and getattr(resp, "usage", None) is not None:
            try:
                from app.intelligence.cost_tracker import calculate_cost as _calc_cost

                _model_name = resp.model if hasattr(resp, "model") and resp.model else _exec_model
                _real_cost = _calc_cost(
                    _model_name,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                )
                async with self._state_lock:
                    # This is the SAME LLM call already charged above (via the
                    # deprecated governance.pricing estimate, used only to drive
                    # the real-time budget check) — replace that estimate with
                    # CostTracker's authoritative figure instead of adding on
                    # top of it, so one call is billed once, not twice.
                    _already_charged = _actual_cost if "_actual_cost" in locals() else 0.0
                    state.context["total_cost_usd"] = (
                        state.context.get("total_cost_usd", 0.0) - _already_charged + _real_cost
                    )
                await self._cost_tracker.record_llm_usage(
                    model=_model_name,
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    tenant_ctx=tenant_ctx,
                    goal_id=state.goal_id or "",
                    agent_id=state.context.get("agent_id"),
                    role="executor",
                )
            except Exception as _ct_exc:
                self._logger.warning("cost_tracker_record_failed", error=str(_ct_exc))
        # 2.3: Per-goal executor cost tracking
        try:
            from app.observability.cost_breakdown import record_role_cost as _rrc

            _rrc(
                goal_id=state.goal_id,
                role="executor",
                model=_exec_model,
                input_tok=getattr(resp, "input_tokens", 0),
                output_tok=getattr(resp, "output_tokens", 0),
                cost=_actual_cost if "_actual_cost" in locals() else 0.0,
            )
        except Exception:
            pass
        raw_output = resp.content
        raw_output_sanitized = False

        # Prefer structured tool_calls from provider; fall back to text parsing (Task 1)
        _structured_tcs: list[dict[str, Any]] = resp.tool_calls if resp.tool_calls else []
        if _structured_tcs:
            _first_stc = _structured_tcs[0]
            _stc_name = _first_stc.get("name") or _first_stc.get("tool_name", "")
            _stc_args = _first_stc.get("input") or _first_stc.get("arguments") or {}
            if not isinstance(_stc_args, dict):
                _stc_args = {}
            tool_call = ToolCall(tool=_stc_name, arguments=_stc_args) if _stc_name else None
            # Update tool_name from structured response (Task 3)
            if _stc_name:
                tool_name = self._extract_tool_name(
                    step, tool_calls_result=[{"tool_name": _stc_name}]
                )
        else:
            tool_call = extract_tool_call(raw_output)
        if tool_call is not None:
            tool_call = await repair_tool_call_arguments(tool_call, step, goal=state.goal)
        # Validate tool name before dispatching
        if tool_call is not None and tool_call.tool:
            from app.agent.tool_calls import validate_tool_name as _validate_tn

            _tn_rejection = _validate_tn(tool_call.tool, _allowed_tools_set)
            if _tn_rejection:
                raw_output = _tn_rejection
                raw_output_sanitized = True
                await self._emit(
                    {
                        "type": "tool_call_failed",
                        "tool": tool_call.tool,
                        "error": _tn_rejection[:300],
                    }
                )
                record_tool_call(
                    tool_call.tool,
                    "unknown",
                    "rejected",
                    0.0,
                )
                tool_call = None  # prevent dispatch
        if tool_call is not None:
            # GuardrailEngine v2: evaluate tool arguments BEFORE the MCP call
            _guardrail_engine_v2 = (
                getattr(self._app_state, "guardrail_engine", None) if self._app_state else None
            )
            if _guardrail_engine_v2 is not None:
                try:
                    from app.intelligence.guardrail_engine import GuardrailContext as _GCtx

                    _ge_ctx = _GCtx(
                        tenant_id=tenant_ctx.tenant_id if tenant_ctx else "",
                        goal_id=state.goal_id or "",
                        agent_id=self._agent_id or "",
                        domain=getattr(tenant_ctx, "domain_context", "general")
                        if tenant_ctx
                        else "general",
                    )
                    _ge_args_result = await _guardrail_engine_v2.evaluate_tool_args(
                        tool_name=tool_name,
                        arguments=tool_call.arguments or {},
                        context=_ge_ctx,
                    )
                    if not _ge_args_result.allowed:
                        _ge_viol = (
                            _ge_args_result.violations[0] if _ge_args_result.violations else None
                        )
                        raise PermissionError(
                            f"Guardrail blocked tool call '{tool_name}': "
                            f"{_ge_viol.matched_pattern if _ge_viol else 'policy violation'}"
                        )
                except PermissionError:
                    raise
                except Exception as _ge_exc:
                    self._logger.warning("guardrail_engine_v2_pre_check_failed", error=str(_ge_exc))
                    # SAFE-4 (P0-15): an errored guardrail check must not read as
                    # "allowed" on high-risk work — fail closed.
                    if _guardrail_should_fail_closed(
                        step, state.context.get("_risk_level")
                    ):
                        raise PermissionError(
                            f"Guardrail check errored on high-risk tool "
                            f"'{tool_name}'; failing closed."
                        ) from _ge_exc

            # Guardrail check: tool_args (Guardrails 2.0)
            if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx:
                try:
                    _g2_args_str = (
                        json.dumps(tool_call.arguments)
                        if isinstance(tool_call.arguments, dict)
                        else str(tool_call.arguments)
                    )
                    _g2_args_result = await guardrails_engine.evaluate(
                        content=_g2_args_str,
                        layer=GuardrailLayer.TOOL_ARGS,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_id=getattr(state, "goal_id", None),
                        step_description=step,
                    )
                    if _g2_args_result.get("blocked"):
                        _g2_viol_name = (_g2_args_result.get("violations") or [{}])[0].get(
                            "rule_name", "policy"
                        )
                        raise PermissionError(f"Tool call blocked by guardrail: {_g2_viol_name}")
                except PermissionError:
                    raise
                except Exception as _g2_exc:
                    # SAFE-4 (P0-15): fail closed on high-risk work when the
                    # guardrail engine errors instead of silently allowing.
                    if _guardrail_should_fail_closed(step, state.context.get("_risk_level")):
                        raise PermissionError(
                            f"Guardrail (tool_args) errored on high-risk step "
                            f"'{tool_name}'; failing closed."
                        ) from _g2_exc

            tool_call_started = time.monotonic()
            if self._mcp_client is None:
                self._logger.warning("mcp_client_none_at_tool_dispatch tool=%s", tool_call.tool)
                error = self._sanitize_tool_raw_output("MCP client unavailable")
                await self._emit(
                    {
                        "type": "tool_call_failed",
                        "tool": tool_call.tool,
                        "error": error,
                    }
                )
                record_tool_call(
                    tool_call.tool,
                    "unknown",
                    "failed",
                    time.monotonic() - tool_call_started,
                )
                raw_output = error
                raw_output_sanitized = True
            else:
                tool_context = state.context.get("tool_context")
                tool_ref = (
                    tool_context.find_tool(tool_call.tool)
                    if tool_context is not None and hasattr(tool_context, "find_tool")
                    else None
                )
                if tool_ref is None:
                    # Tracks whether the civilization spawn branch already handled
                    # this call, so the RPA / "tool not found" fallthrough below
                    # does not clobber its result with a spurious failure.
                    _civ_spawn_handled = False
                    # Check if it's a civilization spawn tool call
                    if (
                        self._civilization_spawn_enabled
                        and self._civilization_id
                        and tool_call.tool == "civilization_spawn"
                    ):
                        _civ_spawn_handled = True
                        try:
                            from app.civilization.governor import Governor
                            from app.civilization.spawn_tool import execute_spawn_tool

                            _gov_kwargs: dict[str, Any] = {
                                "civilization_id": self._civilization_id,
                                "tenant_id": tenant_ctx.tenant_id,
                            }
                            if self._db_session_factory is not None:
                                _gov_kwargs["db_session_factory"] = self._db_session_factory
                            _civ_const_placeholder = None
                            try:
                                from app.civilization.models import Constitution

                                _civ_const_placeholder = Constitution()
                            except Exception:
                                pass
                            if _civ_const_placeholder is not None:
                                _gov_kwargs["constitution"] = _civ_const_placeholder
                            governor = Governor(**_gov_kwargs)
                            _spawn_args = tool_call.arguments or {}
                            _spawn_ctx = state.context if isinstance(state.context, dict) else {}
                            spawn_result = await execute_spawn_tool(
                                capability=str(_spawn_args.get("capability", "")),
                                goal=str(_spawn_args.get("goal", "")),
                                priority=str(_spawn_args.get("priority", "normal")),
                                governor=governor,
                                requester_agent_id=str(getattr(state, "agent_id", "") or ""),
                                depth=int(_spawn_ctx.get("civilization_depth", 0) or 0),
                                parent_budget_usd=float(
                                    _spawn_ctx.get("civilization_parent_budget_usd", 0.0) or 0.0
                                ),
                                parent_policy_ids=list(
                                    _spawn_ctx.get("civilization_parent_policy_ids", []) or []
                                ),
                                tenant_ctx=tenant_ctx,
                                goal_service=self._goal_service,
                                civilization_id=self._civilization_id,
                            )
                            raw_output = str(spawn_result)
                            await self._emit(
                                {
                                    "type": "child_agent_spawned",
                                    "parent_agent_id": getattr(state, "agent_id", ""),
                                    "child_agent_id": spawn_result.get("agent_id"),
                                    "child_goal_id": spawn_result.get("goal_id"),
                                    "depth": spawn_result.get("depth", 0),
                                    "capability": str(_spawn_args.get("capability", "")),
                                }
                            )
                            raw_output_sanitized = True
                            # Grantex delegation: mint narrowed grants for the child
                            # so its tool calls are enforced against authority that
                            # can only be <= the parent's (never widen). Best-effort:
                            # on failure the child simply holds no grant (fail-closed
                            # under enforcement), never over-permitted.
                            if self._enforce_grants and self._grant_store:
                                _child_aid = str(spawn_result.get("agent_id") or "")
                                with contextlib.suppress(Exception):
                                    from datetime import UTC, datetime

                                    from app.governance.grants import delegate_active_grants

                                    await delegate_active_grants(
                                        self._grant_store,
                                        tenant_id=tenant_ctx.tenant_id,
                                        parent_agent_id=str(getattr(state, "agent_id", "") or ""),
                                        child_agent_id=_child_aid,
                                        now=datetime.now(UTC),
                                    )
                            record_tool_call(
                                tool_call.tool,
                                "civilization",
                                "success",
                                time.monotonic() - tool_call_started,
                            )
                        except Exception as _spawn_exc:
                            raw_output = f"Civilization spawn error: {_spawn_exc}"
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": str(_spawn_exc),
                                }
                            )
                            raw_output_sanitized = True
                    # Check if it's a built-in RPA tool (rpa_open_url, rpa_click, etc.)
                    from app.rpa.tools import RPA_TOOLS as _RPA_TOOLS

                    _rpa_tool_names = {str(t["name"]) for t in _RPA_TOOLS}
                    if tool_call.tool in _rpa_tool_names or any(
                        tool_call.tool.endswith(f".{t}") for t in _rpa_tool_names
                    ):
                        # Dispatch directly to RPAExecutor
                        rpa_tool_name = (
                            tool_call.tool.split(".")[-1]
                            if "." in tool_call.tool
                            else tool_call.tool
                        )
                        rpa_executor = getattr(
                            state.context.get("_app_state"), "rpa_executor", None
                        ) or getattr(self, "_rpa_executor", None)
                        if rpa_executor is not None:
                            try:
                                goal_id_str = str(getattr(state, "goal_id", ""))
                                rpa_result = await rpa_executor.execute(
                                    tool_name=rpa_tool_name,
                                    arguments=tool_call.arguments or {},
                                    tenant_id=tenant_ctx.tenant_id,
                                    goal_id=goal_id_str,
                                )
                                raw_output = (
                                    rpa_result.output
                                    if rpa_result.success
                                    else f"RPA error: {rpa_result.error}"
                                )
                                await self._emit(
                                    {
                                        "type": "tool_call_complete",
                                        "tool": tool_call.tool,
                                        "server_id": "rpa",
                                        "success": rpa_result.success,
                                        "output": raw_output,
                                        "artifact_url": rpa_result.artifact_url,
                                        "artifact_name": rpa_result.artifact_name,
                                    }
                                )
                                record_tool_call(
                                    rpa_tool_name,
                                    "rpa",
                                    "success" if rpa_result.success else "failed",
                                    time.monotonic() - tool_call_started,
                                )
                                raw_output_sanitized = True
                                # ── RPA failure → ExecutionMemory + SelfOptimizer ──
                                if not rpa_result.success:
                                    _rpa_url_fail = (tool_call.arguments or {}).get("url", "") or (
                                        state.context.get("_current_rpa_url", "")
                                        if isinstance(state.context, dict)
                                        else ""
                                    )
                                    # Record failure in ExecutionMemory for recall
                                    if (
                                        self._exec_memory is not None
                                        and self._db_session_factory is not None
                                    ):
                                        _fail_task = asyncio.create_task(
                                            self._exec_memory.record_failure_async(
                                                goal=state.goal,
                                                error=(
                                                    f"RPA {rpa_tool_name} failed on "
                                                    f"{_rpa_url_fail}: "
                                                    f"{rpa_result.error or 'unknown'}"
                                                ),
                                                tenant_id=tenant_ctx.tenant_id,
                                                db=self._db_session_factory,
                                            )
                                        )
                                        self._background_tasks.add(_fail_task)
                                        _fail_task.add_done_callback(self._background_tasks.discard)
                                    # Generate RPA-specific suggestions
                                    if self._self_optimizer is not None:
                                        self._self_optimizer.analyze_rpa_failure(
                                            tool_name=rpa_tool_name,
                                            error=rpa_result.error or "",
                                            url=str(_rpa_url_fail),
                                            tenant_ctx=tenant_ctx,
                                        )
                                # ── RPA → LTM persistence ──────────────────
                                # Store extracted text and vision analysis so
                                # future goals can recall what was found on
                                # this page via semantic search.
                                if (
                                    rpa_result.success
                                    and self._long_term_memory is not None
                                    and rpa_tool_name in ("rpa_extract_text", "rpa_screenshot")
                                    and rpa_result.output
                                    and len(rpa_result.output) > 50
                                ):
                                    _rpa_url = (tool_call.arguments or {}).get(
                                        "url",
                                        (
                                            state.context.get("_current_rpa_url", "")
                                            if isinstance(state.context, dict)
                                            else ""
                                        ),
                                    )
                                    _rpa_src = (
                                        "rpa_vision"
                                        if rpa_tool_name == "rpa_screenshot"
                                        else "rpa_extraction"
                                    )
                                    _rpa_ltm_task = asyncio.create_task(
                                        self._long_term_memory.store_rpa_extraction(
                                            url=str(_rpa_url or "unknown"),
                                            extracted_text=rpa_result.output,
                                            goal_id=str(getattr(state, "goal_id", "")),
                                            tenant_ctx=tenant_ctx,
                                            db=self._db_session_factory,
                                            embedder=self._embedder,
                                            source_type=_rpa_src,
                                        )
                                    )
                                    self._background_tasks.add(_rpa_ltm_task)
                                    _rpa_ltm_task.add_done_callback(self._background_tasks.discard)
                                # Track current URL for extraction attribution
                                if rpa_tool_name == "rpa_open_url":
                                    _nav_url = (tool_call.arguments or {}).get("url", "")
                                    if isinstance(state.context, dict) and _nav_url:
                                        state.context["_current_rpa_url"] = _nav_url
                            except Exception as _rpa_exc:
                                raw_output = f"RPA execution error: {_rpa_exc}"
                                await self._emit(
                                    {
                                        "type": "tool_call_failed",
                                        "tool": tool_call.tool,
                                        "error": str(_rpa_exc),
                                    }
                                )
                                raw_output_sanitized = True
                        else:
                            raw_output = self._sanitize_tool_raw_output(
                                f"Tool not found: {tool_call.tool}"
                            )
                            raw_output_sanitized = True
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": self._sanitize_tool_event_value("Tool not found"),
                                }
                            )
                            record_tool_call(
                                tool_call.tool,
                                "unknown",
                                "failed",
                                time.monotonic() - tool_call_started,
                            )
                    elif not _civ_spawn_handled:
                        # Existing "tool_ref is None" error handling (skipped when
                        # the civilization spawn branch already handled the call).
                        raw_output = self._sanitize_tool_raw_output(
                            f"Tool not found: {tool_call.tool}"
                        )
                        raw_output_sanitized = True
                        await self._emit(
                            {
                                "type": "tool_call_failed",
                                "tool": tool_call.tool,
                                "error": self._sanitize_tool_event_value("Tool not found"),
                            }
                        )
                        record_tool_call(
                            tool_call.tool,
                            "unknown",
                            "failed",
                            time.monotonic() - tool_call_started,
                        )
                else:
                    # Grantex governance gate (mandatory, opt-in): an agent may
                    # only run a tool it holds a covering, active, unrevoked grant
                    # for. Pass-through until enforcement is enabled for the deploy.
                    _grant_decision = await enforce_tool_call(
                        self._grant_store,
                        tenant_id=tenant_ctx.tenant_id,
                        agent_id=self._agent_id or "",
                        tool_name=tool_ref.name,
                        enabled=self._enforce_grants,
                    )
                    if not _grant_decision.allowed:
                        await self._emit(
                            {
                                "type": "tool_call_blocked_by_grant",
                                "tool": tool_ref.name,
                                "reason": _grant_decision.reason,
                            }
                        )
                        raise PermissionError(
                            f"blocked by grant guard [{tool_ref.name}]: {_grant_decision.reason}"
                        )
                    tool_risk = classify_tool_risk(tool_ref.name, tool_ref.server_name)
                    # Gate write_high bypass behind an explicit env flag (default-secure).
                    import os as _os

                    _allow_fa_write_high = (
                        _os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower() == "true"
                    )
                    # Per-connector opt-in: the user explicitly marked this connector
                    # "Allow autonomous execution", so its high-risk tools run without
                    # a human approver in autonomous goals. Scoped to this connector.
                    _connector_auto_approve = bool(getattr(tool_ref, "auto_approve", False))
                    _effective_risk = resolve_effective_tool_risk(
                        tool_risk,
                        autonomy_mode=self._autonomy_mode,
                        connector_auto_approve=_connector_auto_approve,
                        allow_fa_write_high=_allow_fa_write_high,
                    )
                    if _effective_risk != tool_risk and _connector_auto_approve:
                        await self._emit(
                            {
                                "type": "tool_call_auto_approved",
                                "tool": tool_ref.name,
                                "server_id": tool_ref.server_id,
                                "reason": "connector opted into autonomous execution",
                            }
                        )
                    tool_risk = _effective_risk
                    # else: falls through to write_high HITL gate below (default-secure)
                    if tool_risk == "destructive":
                        error = self._sanitize_tool_raw_output(
                            f"Jira tool '{tool_ref.name}' denied as destructive."
                        )
                        await self._emit(
                            {
                                "type": "tool_call_failed",
                                "tool": tool_ref.name,
                                "server_id": tool_ref.server_id,
                                "error": error,
                            }
                        )
                        record_tool_call(
                            tool_ref.name,
                            tool_ref.server_id,
                            "denied",
                            time.monotonic() - tool_call_started,
                        )
                        raw_output = error
                        raw_output_sanitized = True
                    elif tool_risk == "write_high":
                        if self._hitl_gateway is None:
                            error = self._sanitize_tool_raw_output(
                                f"Jira tool '{tool_ref.name}' requires approval."
                            )
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "error": error,
                                }
                            )
                            record_tool_call(
                                tool_ref.name,
                                tool_ref.server_id,
                                "failed",
                                time.monotonic() - tool_call_started,
                            )
                            raw_output = error
                            raw_output_sanitized = True
                        else:
                            req_id = str(
                                self._hitl_gateway.request_approval(
                                    goal_id=state.goal_id,
                                    action=tool_ref.name,
                                    risk_level=tool_risk,
                                    tenant_ctx=tenant_ctx,
                                )
                            )
                            await self._emit(
                                {
                                    "type": "waiting_approval",
                                    "request_id": req_id,
                                    "action": tool_ref.name,
                                    "tool": tool_ref.name,
                                }
                            )
                            await self._emit(
                                {
                                    "type": "tool_call_pending_approval",
                                    "tool": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "request_id": req_id,
                                    "risk": tool_risk,
                                }
                            )
                            if self._autonomy_mode == "supervised":
                                _hitl_start = time.monotonic()
                                final_status = await self._hitl_gateway.wait_for_approval(
                                    req_id, tenant_ctx=tenant_ctx
                                )
                                record_approval_wait(time.monotonic() - _hitl_start)
                                if final_status == ApprovalStatus.REJECTED:
                                    raise PermissionError(
                                        f"Tool '{tool_ref.name}' was rejected by human approver."
                                    )
                                elif final_status == ApprovalStatus.TIMED_OUT:
                                    raise PermissionError(
                                        f"Tool '{tool_ref.name}' approval timed out."
                                    )
                                # APPROVED: now actually dispatch the tool call
                                await self._emit({"type": "approval_granted", "request_id": req_id})
                                _approved_result = await self._mcp_client.call_tool(
                                    server_id=tool_ref.server_id,
                                    tool_name=tool_ref.name,
                                    arguments=tool_call.arguments,
                                    tenant_ctx=tenant_ctx,
                                )
                                raw_output = (
                                    _approved_result.output
                                    if _approved_result.success
                                    else str(_approved_result.error)
                                )
                                raw_output_sanitized = False
                                record_tool_call(
                                    tool_ref.name,
                                    tool_ref.server_id,
                                    "success" if _approved_result.success else "failed",
                                    time.monotonic() - tool_call_started,
                                )
                            else:
                                # Non-supervised: log the request but do not block
                                raw_output = self._sanitize_tool_raw_output(
                                    f"High-risk tool '{tool_ref.name}' "
                                    "requires approval (non-supervised mode)."
                                )
                                raw_output_sanitized = True
                                record_tool_call(
                                    tool_ref.name,
                                    tool_ref.server_id,
                                    "approval",
                                    time.monotonic() - tool_call_started,
                                )
                    else:
                        # V4: Validate arguments against JSON schema before MCP dispatch
                        from app.agent.tool_calls import validate_tool_arguments as _validate_args

                        _tool_schema = getattr(tool_ref, "input_schema", None) or {}
                        _arg_errors_v4 = _validate_args(tool_call.arguments or {}, _tool_schema)
                        if _arg_errors_v4:
                            _arg_error_msg = (
                                f"[ARGUMENT VALIDATION FAILED] Tool '{tool_call.tool}' "
                                f"called with invalid arguments:\n"
                                + "\n".join(f"  - {e}" for e in _arg_errors_v4)
                                + "\nPlease retry with correct arguments from the tool schema."
                            )
                            raw_output = self._sanitize_tool_raw_output(_arg_error_msg)
                            raw_output_sanitized = True
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": _arg_error_msg[:300],
                                }
                            )
                            record_tool_call(
                                tool_call.tool,
                                getattr(tool_ref, "server_id", "unknown"),
                                "arg_validation_failed",
                                time.monotonic() - tool_call_started,
                            )
                        else:
                            # V5: Placeholder argument guard — prevent LLM-generated
                            # placeholder values (e.g. "your_organization/your_repository")
                            # from reaching real MCP servers.
                            _placeholder_patterns = (
                                "your_organization",
                                "your_repository",
                                "your_org",
                                "your_repo",
                                "your_project",
                                "your_workspace",
                                "your_team",
                                "your_board",
                                "<organization>",
                                "<repository>",
                                "<repo>",
                                "{organization}",
                                "{repository}",
                                "{repo}",
                                "example.com",
                                "placeholder",
                            )
                            _ph_hits = [
                                f"{k}={v!r}"
                                for k, v in (tool_call.arguments or {}).items()
                                if isinstance(v, str)
                                and any(p in v.lower() for p in _placeholder_patterns)
                            ]
                            if _ph_hits:
                                _ph_msg = (
                                    f"[PLACEHOLDER ARGUMENTS DETECTED] Tool '{tool_call.tool}' "
                                    f"was called with generic placeholder values: "
                                    f"{', '.join(_ph_hits)}. "
                                    "Please use real values from the goal context or "
                                    "user-provided configuration instead of template placeholders."
                                )
                                raw_output = self._sanitize_tool_raw_output(_ph_msg)
                                raw_output_sanitized = True
                                await self._emit(
                                    {
                                        "type": "tool_call_failed",
                                        "tool": tool_call.tool,
                                        "error": _ph_msg[:300],
                                    }
                                )
                                record_tool_call(
                                    tool_call.tool,
                                    getattr(tool_ref, "server_id", "unknown"),
                                    "placeholder_args",
                                    time.monotonic() - tool_call_started,
                                )
                            else:
                                # No placeholders — dispatch to MCP
                                try:
                                    with self._tracer.start_as_current_span(
                                        "agentverse.tool.call"
                                    ) as span:
                                        span.set_attribute(
                                            "tool.name",
                                            tool_call.tool if hasattr(tool_call, "tool") else "",
                                        )
                                        result = await self._mcp_client.call_tool(
                                            server_id=tool_ref.server_id,
                                            tool_name=tool_ref.name,
                                            arguments=tool_call.arguments,
                                            tenant_ctx=tenant_ctx,
                                        )
                                except Exception:
                                    record_tool_call(
                                        tool_ref.name,
                                        tool_ref.server_id,
                                        "failed",
                                        time.monotonic() - tool_call_started,
                                    )
                                    raise
                                # Apply PII check to raw tool output (H3 fix: result is ToolCallResult not dict)  # noqa: E501
                                raw_output_text = ""
                                if isinstance(result.output, dict):
                                    raw_output_text = str(
                                        result.output.get("content")
                                        or result.output.get("result")
                                        or ""
                                    )
                                elif isinstance(result.output, str):
                                    raw_output_text = result.output[:500]
                                if self._guardrail_checker and raw_output_text:
                                    pii_issues = self._guardrail_checker.check_output(
                                        output=raw_output_text
                                    )
                                    if pii_issues:
                                        await self._emit(
                                            {
                                                "type": "pii_redacted",
                                                "tool": getattr(tool_call, "tool", "")
                                                if tool_call
                                                else "",
                                                "issues": pii_issues,
                                            }
                                        )
                                        if self._audit_log is not None:
                                            with contextlib.suppress(Exception):
                                                self._audit_log.record(
                                                    AuditEvent(
                                                        goal_id=state.goal_id,
                                                        tool_name="guardrail_checker",
                                                        action_level=ActionLevel.ALLOW_LOG,
                                                        outcome="pii_redacted",
                                                        step_id=state.steps[-1].step_id
                                                        if state.steps
                                                        else "",
                                                        api_key_id=getattr(
                                                            tenant_ctx, "api_key_id", None
                                                        )
                                                        or "",
                                                        note=f"issues_count={len(pii_issues)} step={step[:100]}",  # noqa: E501
                                                    ),
                                                    tenant_ctx=tenant_ctx,
                                                )
                                raw_result_output = self._sanitize_tool_raw_output(result.output)
                                raw_result_error = self._sanitize_tool_raw_output(result.error)

                                # Guardrail check: tool_output (Guardrails 2.0)
                                if (
                                    _GUARDRAILS_AVAILABLE
                                    and guardrails_engine is not None
                                    and tenant_ctx
                                ):
                                    try:
                                        _g2_out_preview = (
                                            str(raw_result_output)[:500]
                                            if raw_result_output
                                            else ""
                                        )
                                        await guardrails_engine.evaluate(
                                            content=_g2_out_preview,
                                            layer=GuardrailLayer.TOOL_OUTPUT,
                                            tenant_id=tenant_ctx.tenant_id,
                                            goal_id=getattr(state, "goal_id", None),
                                        )
                                    except Exception:
                                        pass  # Guardrail errors must never break execution

                                # ── Indirect injection scan on tool output ──────────────
                                # External tool results (Confluence, web, email) may contain
                                # adversarial text designed to hijack the agent (tool poisoning).
                                if result.success and raw_result_output:
                                    try:
                                        from app.agent.exfil_guard import (
                                            check_tool_output_for_injection,
                                        )

                                        _injection_warning = check_tool_output_for_injection(
                                            tool_ref.name, raw_result_output
                                        )
                                        if _injection_warning:
                                            self._logger.warning(
                                                "indirect_injection_detected",
                                                tool=tool_ref.name,
                                                warning=_injection_warning[:120],
                                            )
                                            raw_result_output = (
                                                _injection_warning + "\n\n" + raw_result_output
                                            )
                                    except Exception:
                                        pass  # injection scan must never block execution

                                # ── C4 Fix: Populate StepResult.tool_calls ─────────────
                                # This allows the verifier's [TOOL FAILED] markers to fire.
                                if state.steps:
                                    state.steps[-1].tool_calls.append(
                                        {
                                            "tool_name": tool_ref.name,
                                            "server_id": tool_ref.server_id,
                                            "success": result.success,
                                            "error": result.error or "",
                                            "output": (
                                                str(result.output)[:300]
                                                if result.output
                                                else ""
                                            ),
                                        }
                                    )

                                # ── H3 Fix: PII check on ToolCallResult (not dict) ──────
                                raw_output_text = ""
                                if isinstance(result.output, dict):
                                    raw_output_text = str(
                                        result.output.get("content")
                                        or result.output.get("result")
                                        or ""
                                    )
                                elif isinstance(result.output, str):
                                    raw_output_text = result.output[:500]
                                await self._emit(
                                    {
                                        "type": "tool_call_complete",
                                        "tool": tool_ref.name,
                                        "server_id": tool_ref.server_id,
                                        "success": result.success,
                                        "output": self._sanitize_tool_event_value(result.output),
                                        "error": self._sanitize_tool_event_value(result.error),
                                        # tool_output preserves the raw structured dict for result_artifacts.py  # noqa: E501
                                        # without truncation so downstream consumers can access full data.  # noqa: E501
                                        "tool_output": result.output
                                        if isinstance(result.output, dict)
                                        else None,
                                    }
                                )
                                # Check for artifact capture (RPA screenshot etc.)
                                # result is always ToolCallResult — use getattr not dict access
                                _artifact_uri: str = getattr(result, "artifact_url", "") or ""
                                _artifact_name: str = getattr(result, "artifact_name", "") or ""
                                if _artifact_uri and not _artifact_uri.startswith("data:"):
                                    await self._emit(
                                        {
                                            "type": "artifact_captured",
                                            "artifact_type": "screenshot",
                                            "artifact_url": _artifact_uri,
                                            "artifact_name": _artifact_name,
                                            "tool": tool_ref.name,
                                        }
                                    )
                                record_tool_call(
                                    tool_ref.name,
                                    tool_ref.server_id,
                                    "success" if result.success else "failed",
                                    time.monotonic() - tool_call_started,
                                )
                                raw_output = (
                                    raw_result_output if result.success else raw_result_error
                                )
                                # Surface the SENT content (e.g. the brief in a telegram
                                # send) so the verifier can confirm the deliverable — the
                                # tool result is only a receipt ({'ok': True, ...}).
                                if tool_call is not None:
                                    from app.agent.nodes._helpers import surface_delivered_content

                                    raw_output = surface_delivered_content(
                                        raw_output,
                                        tool_call.tool,
                                        tool_call.arguments,
                                        result.success,
                                    )
                                raw_output_sanitized = True

        # ── Strategy B: parallel tool calls ─────────────────────────────────────
        # When the resolved strategy is PARALLEL and this turn produced more than
        # one structured tool call, dispatch the ADDITIONAL calls concurrently (the
        # first was handled by the full path above). Safety-gated helper; only
        # active for models whose profile opts into parallel tool calls, so the
        # default single-call path is completely unaffected.
        _strategy_b = state.context.get("_execution_strategy")
        _tool_mode_b = getattr(getattr(_strategy_b, "tool_mode", None), "value", "single")
        if (
            _tool_mode_b == "parallel"
            and _structured_tcs
            and len(_structured_tcs) > 1
            and tool_call is not None
        ):
            try:
                _extra_outputs = await self._dispatch_parallel_extra_tool_calls(
                    _structured_tcs[1:],
                    step,
                    state,
                    tenant_ctx,
                    locals().get("_allowed_tools_set") or set(),
                )
                for _en, _eo in _extra_outputs:
                    raw_output = f"{raw_output or ''}\n\n[parallel tool: {_en}]\n{_eo}"
                raw_output_sanitized = True
                # P5 adaptivity: record whether parallel tool calls actually worked
                # for this executor model, so the engine can learn (up/down) whether
                # to keep using PARALLEL for it. Only sampled when the model really
                # emitted multiple calls (the relevant signal).
                _cap_tracker_b = getattr(self, "_capability_tracker", None)
                if _cap_tracker_b is not None and _extra_outputs:
                    _parallel_ok = all(
                        "[error" not in _o.lower() and "requires approval" not in _o.lower()
                        for _, _o in _extra_outputs
                    )
                    _exec_model_b = str(getattr(self._executor, "_default_model", "") or "")
                    with contextlib.suppress(Exception):
                        await _cap_tracker_b.record(
                            _exec_model_b,
                            ok=_parallel_ok,
                            tenant_id=getattr(tenant_ctx, "tenant_id", None),
                            kind="parallel",
                        )
            except Exception as _pb_exc:  # pragma: no cover - defensive
                self._logger.warning("parallel_tool_dispatch_failed", error=str(_pb_exc)[:120])

        # 9. Result processor / graph sanitizer — redact secrets, truncate
        if not raw_output_sanitized:
            raw_output = self._sanitize_tool_raw_output(raw_output)

        # Check output for data leakage — redact only the genuine PII spans so a
        # single hit (e.g. a stray number) never destroys an otherwise-valid answer.
        if self._guardrail_checker is not None:
            _redactor = getattr(self._guardrail_checker, "redact_output", None)
            output_issues: list[str] = []
            _res = _redactor(output=raw_output) if callable(_redactor) else None
            if isinstance(_res, tuple) and len(_res) == 2 and isinstance(_res[1], list):
                # span redaction available (real GuardrailChecker)
                raw_output, output_issues = _res
            else:  # older checker without span redaction
                _co = self._guardrail_checker.check_output(output=raw_output)
                output_issues = _co if isinstance(_co, list) else []
                if output_issues:
                    raw_output = f"[Output redacted by guardrails: {'; '.join(output_issues)}]"
            if output_issues:
                await self._emit(
                    {"type": "pii_redacted", "issues": output_issues, "scope": "final_output"}
                )

        # GuardrailEngine v2: scan output for PII/secrets/cloud-destruction patterns
        _guardrail_engine_v2_out = (
            getattr(self._app_state, "guardrail_engine", None) if self._app_state else None
        )
        if _guardrail_engine_v2_out is not None and raw_output:
            try:
                from app.intelligence.guardrail_engine import GuardrailContext as _GCtxOut

                _ge_out_ctx = _GCtxOut(
                    tenant_id=tenant_ctx.tenant_id if tenant_ctx else "",
                    goal_id=state.goal_id or "",
                    agent_id=self._agent_id or "",
                    domain=getattr(tenant_ctx, "domain_context", "general")
                    if tenant_ctx
                    else "general",
                )
                _ge_out_result = await _guardrail_engine_v2_out.evaluate_tool_output(
                    tool_name=tool_name,
                    output=str(raw_output),
                    context=_ge_out_ctx,
                )
                if _ge_out_result.redacted_content:
                    raw_output = _ge_out_result.redacted_content
            except Exception as _ge_out_exc:
                self._logger.warning(
                    "guardrail_engine_v2_output_check_failed", error=str(_ge_out_exc)
                )

        # 10. Record rollback point
        if self._rollback_engine is not None:
            from app.reliability.tool_inverses import get_inverse_fn as _get_inverse_fn

            _rb_tool = tool_name
            _rb_args: dict[str, Any] = {}
            if tool_call is not None and tool_call.arguments:
                _rb_tool = tool_call.tool or tool_name
                _rb_args = dict(tool_call.arguments)
            self._rollback_engine.register(
                action=step,
                inverse=_get_inverse_fn(_rb_tool, _rb_args),
            )

        # 11. Decision trace for explainability — real LLM output (Task 6)
        _reasoning_text = raw_output[:500] if raw_output else "No output"
        if tool_call is not None and getattr(tool_call, "tool", None):
            _reasoning_text = f"Used tool '{tool_call.tool}': {raw_output[:300]}"
        trace = DecisionTrace(
            action=step,
            reasoning=_reasoning_text,
            evidence=[raw_output[:300]],
            alternatives=[],
            confidence=0.8,
        )
        state.context.setdefault("decision_traces", []).append(trace.to_dict())
        # Persist decision trace to DB
        if self._db_session_factory and hasattr(trace, "trace_id"):
            import asyncio as _asyncio

            _task = _asyncio.create_task(self._persist_decision_trace(trace, state, tenant_ctx))
            _task.add_done_callback(
                lambda t: (
                    (not t.cancelled() and t.exception())
                    and self._logger.warning(
                        "decision_trace_persist_failed", error=str(t.exception())
                    )
                )
            )
            # Hold a strong reference so the GC doesn't collect the task before it finishes
            self._background_tasks.add(_task)
            _task.add_done_callback(self._background_tasks.discard)

        # 12. Audit log
        if self._audit_log is not None:
            self._audit_log.record(
                AuditEvent(
                    goal_id=state.goal_id,
                    tool_name=tool_name,
                    action_level=ActionLevel.ALLOW_LOG,
                    outcome="step_complete",
                    step_id=state.steps[-1].step_id if state.steps else "",
                    api_key_id=getattr(tenant_ctx, "api_key_id", None) or "",
                    request_id=(
                        state.context.get("request_id")
                        or state.context.get("execution_context", {}).get("request_id")
                    ),
                ),
                tenant_ctx=tenant_ctx,
            )

        # 13. Claim grounding check — verify LLM claims against tool outputs.
        # SKIP when raw_output is already a structured tool result (JSON/dict),
        # as it IS the evidence and cannot be "ungrounded" against itself.
        try:
            from app.agent.grounding import annotate_ungrounded, check_grounding

            _raw_stripped = (raw_output or "").strip()
            _is_structured_tool_output = _raw_stripped.startswith(("{", "[", "{'"))
            # Ground against ALL evidence gathered for the goal — every step's tool
            # outputs plus the retrieved KB/RAG context — not just this step's tool
            # calls. A synthesis/delivery step draws on earlier retrievals, so the
            # narrow single-step view flagged KB-sourced facts as ungrounded and
            # failed correct answers until max_iterations.
            from app.agent.nodes._helpers import collect_grounding_sources

            _tool_outputs_for_grounding = collect_grounding_sources(
                state.steps, step_context or ""
            )
            if raw_output and _tool_outputs_for_grounding and not _is_structured_tool_output:
                # P0-4: high/critical-risk goals get zero ungrounded tolerance.
                _rp_ground = state.context.get("_runtime_profile")
                _risk_ground = str(
                    getattr(
                        getattr(getattr(_rp_ground, "properties", None), "risk", None),
                        "value",
                        "",
                    )
                    or ""
                ).lower()
                _ground_ratio = 0.0 if _risk_ground in ("high", "critical") else None
                _use_policy = False
                with contextlib.suppress(Exception):
                    from app.core.config import get_settings as _gs

                    _use_policy = bool(getattr(_gs(), "grounding_policy_enabled", False))
                if _use_policy:
                    # Richer per-claim policy: exact tiers + optional embedding
                    # paraphrase tier + calibrated abstention. Produces a
                    # GroundingResult-compatible verdict so downstream is unchanged.
                    from app.agent.grounding import GroundingResult
                    from app.agent.grounding_policy import GroundingPolicy

                    _embed_fn = None
                    if self._embedder is not None:
                        async def _embed_fn(texts: list[str]) -> list[list[float]]:
                            from app.providers.base import EmbedRequest

                            _resp = await self._embedder.embed(EmbedRequest(texts=texts))
                            return list(getattr(_resp, "embeddings", []) or [])

                    _min_ratio = 1.0 if _risk_ground in ("high", "critical") else 0.75
                    _pr = await GroundingPolicy(
                        min_grounded_ratio=_min_ratio, embed_fn=_embed_fn
                    ).evaluate(raw_output, _tool_outputs_for_grounding)
                    _ground_result = GroundingResult(
                        grounded=_pr.grounded,
                        ungrounded_claims=_pr.abstain,
                        checked_claims=len(_pr.verdicts),
                        evidence_length=0,
                    )
                else:
                    _ground_result = check_grounding(
                        output=raw_output,
                        tool_outputs=_tool_outputs_for_grounding,
                        max_ungrounded_ratio=_ground_ratio,
                    )
                if not _ground_result.grounded:
                    state.consecutive_ungrounded += 1
                    self._logger.info(
                        "grounding_failed",
                        ungrounded=_ground_result.ungrounded_claims[:3],
                        consecutive=state.consecutive_ungrounded,
                        step=step[:100],
                    )
                    raw_output = annotate_ungrounded(raw_output, _ground_result)
                    state.ungrounded_claims.extend(_ground_result.ungrounded_claims[:5])
                    from app.agent.state import StepStatus

                    if state.consecutive_ungrounded >= 2:
                        # P0-4: two consecutive ungrounded steps → fail the step and
                        # drive a replan using only evidence present in tool outputs.
                        if state.steps and hasattr(state.steps[-1], "status"):
                            state.steps[-1].status = StepStatus.FAILED
                        state.verification_feedback = (
                            "Two consecutive steps produced ungrounded claims: "
                            f"{'; '.join(_ground_result.ungrounded_claims[:5])}. "
                            "Replan using only evidence present in tool outputs."
                        )
                        await self._emit(
                            {
                                "type": "grounding_blocked",
                                "ungrounded_claims": _ground_result.ungrounded_claims[:5],
                                "consecutive": state.consecutive_ungrounded,
                                "step": step,
                            }
                        )
                    else:
                        # C4: Mark the current step as UNGROUNDED (first occurrence)
                        if state.steps and hasattr(state.steps[-1], "status"):
                            state.steps[-1].status = StepStatus.UNGROUNDED
                        await self._emit(
                            {
                                "type": "grounding_warning",
                                "ungrounded_claims": _ground_result.ungrounded_claims[:5],
                                "step": step,
                            }
                        )
                else:
                    state.consecutive_ungrounded = 0
                state.context["grounding_checked"] = True
        except Exception as exc:
            # Log but don't block execution — fail-open only on grounding check errors
            self._logger.warning("grounding_check_error", error=str(exc)[:80])

        # M12: Update session memory with step output
        try:
            _session_mem = getattr(self, "_session_memory", None)
            if _session_mem is not None and hasattr(_session_mem, "add"):
                _session_mem.add(
                    key=f"step_{len(state.steps)}",
                    value={"description": step, "output": (raw_output or "")[:500]},
                )
        except Exception:
            pass

        return raw_output

    async def _dispatch_parallel_extra_tool_calls(
        self,
        extra_tcs: list[dict[str, Any]],
        step: str,
        state: AgentState,
        tenant_ctx: TenantContext,
        allowed_tools_set: set[str],
    ) -> list[tuple[str, str]]:
        """Strategy B — run the *additional* tool calls of one executor turn
        concurrently, each through the core safety gates (name validation, tool
        lookup, risk gate honoring the per-connector auto_approve opt-in, argument
        validation, dispatch, output sanitization). The first tool call is handled
        by the full primary path; this is only reached for models whose profile
        opts into parallel tool calls.

        Returns ``(tool_name, sanitized_output)`` pairs. A failure in one call
        never sinks the batch — its error is captured and the others proceed.
        """
        import asyncio as _asyncio
        import os as _os

        from app.agent.tool_calls import (
            validate_tool_arguments as _validate_args,
        )
        from app.agent.tool_calls import (
            validate_tool_name as _validate_tn,
        )
        from app.agent.tool_risk import classify_tool_risk

        _allow_fa_write_high = (
            _os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower() == "true"
        )
        _tc_ctx = state.context.get("tool_context")

        async def _one(stc: dict[str, Any]) -> tuple[str, str] | None:
            name = stc.get("name") or stc.get("tool_name", "")
            args = stc.get("input") or stc.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            if not name:
                return None
            if _validate_tn(name, allowed_tools_set):
                return (name, f"[rejected: unknown tool '{name}']")
            tool_ref = _tc_ctx.find_tool(name) if _tc_ctx is not None else None
            if tool_ref is None:
                return (name, f"[tool not found: '{name}']")
            # Risk gate — same rules as the primary path (per-connector opt-in).
            risk = classify_tool_risk(tool_ref.name, tool_ref.server_name)
            eff = resolve_effective_tool_risk(
                risk,
                autonomy_mode=self._autonomy_mode,
                connector_auto_approve=bool(getattr(tool_ref, "auto_approve", False)),
                allow_fa_write_high=_allow_fa_write_high,
            )
            if eff == "destructive":
                return (tool_ref.name, f"[denied: '{tool_ref.name}' is destructive]")
            if eff == "write_high":
                # Not opted in — mirror the primary path's non-supervised behavior.
                return (
                    tool_ref.name,
                    f"High-risk tool '{tool_ref.name}' requires approval (non-supervised mode).",
                )
            _arg_errors = _validate_args(args, getattr(tool_ref, "input_schema", None) or {})
            if _arg_errors:
                return (tool_ref.name, f"[argument validation failed: {'; '.join(_arg_errors)}]")
            _t0 = time.monotonic()
            try:
                result = await self._mcp_client.call_tool(
                    server_id=tool_ref.server_id,
                    tool_name=tool_ref.name,
                    arguments=args,
                    tenant_ctx=tenant_ctx,
                )
            except Exception as exc:
                record_tool_call(
                    tool_ref.name, tool_ref.server_id, "failed", time.monotonic() - _t0
                )
                return (tool_ref.name, f"[error: {exc}]")
            record_tool_call(
                tool_ref.name,
                tool_ref.server_id,
                "success" if result.success else "failed",
                time.monotonic() - _t0,
            )
            if state.steps:
                state.steps[-1].tool_calls.append(
                    {
                        "tool_name": tool_ref.name,
                        "server_id": tool_ref.server_id,
                        "success": result.success,
                        "error": result.error or "",
                        "output": str(result.output)[:300] if result.output else "",
                    }
                )
            await self._emit(
                {
                    "type": "tool_call_complete",
                    "tool": tool_ref.name,
                    "server_id": tool_ref.server_id,
                    "success": result.success,
                    "parallel": True,
                }
            )
            out = self._sanitize_tool_raw_output(
                result.output if result.success else result.error
            )
            return (tool_ref.name, out)

        results = await _asyncio.gather(
            *[_one(stc) for stc in extra_tcs], return_exceptions=True
        )
        return [r for r in results if isinstance(r, tuple)]

    async def _execute_step_with_cache(
        self, step: str, state: AgentState, tenant_ctx: TenantContext
    ) -> str:
        """
        Execute a step using the world-class semantic cache.

        True cosine-similarity matching (threshold 0.92) means paraphrases like
        "Search GitHub for open issues" and "Find open GitHub issues" both hit
        the same cache entry — no more exact-match-only limitation.

        Flow:
          1. Embed the step description (single API call, ~50ms)
          2. L1 lookup: in-process LRU (sub-millisecond, no network)
          3. L2 lookup: Redis vector scan (cosine similarity, ~5ms)
          4. Cache MISS → execute step fully → store result in L1+L2
        """
        _cache_embedding: list[float] | None = None
        if self._semantic_cache is not None and self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest

                _cache_embed_resp = await self._embedder.embed(EmbedRequest(texts=[step]))
                _cache_embedding = (
                    _cache_embed_resp.embeddings[0] if _cache_embed_resp.embeddings else None
                )
                if _cache_embedding:
                    # Use the new true-similarity API
                    hit = await self._semantic_cache.get_similar(
                        embedding=_cache_embedding,
                        tenant_id=tenant_ctx.tenant_id,
                    )
                    # Only serve non-empty, non-error responses from cache.
                    # Same rejection rules as the write path (approval placeholders,
                    # errors, empty collections, reasoning text) so a poisoned entry
                    # can never be served as a fake success.
                    if hit is not None and not _is_uncacheable_output(hit.response):
                        await self._emit(
                            {
                                "type": "cache_hit",
                                "step": step,
                                "similarity": round(hit.similarity, 4),
                                "source": hit.source,
                                "latency_ms": round(hit.latency_ms, 1),
                            }
                        )
                        return hit.response
            except Exception as _ce:
                _cache_embedding = None
                self._logger.debug("cache_embed_failed", error=str(_ce)[:80])

        raw_output = await self._execute_step(step, state, tenant_ctx)

        # Store result — but never cache error responses, approval placeholders,
        # empty collections, or plain LLM reasoning text ("I'll call…"), so bad
        # outputs never poison the cache and get served as a fake success on a
        # future run. Single source of truth, applied symmetrically on read too.
        if (
            self._semantic_cache is not None
            and _cache_embedding is not None
            and not _is_uncacheable_output(raw_output)
        ):
            with contextlib.suppress(Exception):  # write failures must never block execution
                await self._semantic_cache.store_async(
                    embedding=_cache_embedding,
                    query=step,
                    response=raw_output,
                    tenant_id=tenant_ctx.tenant_id,
                )

        return raw_output
