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

class VerifierMixin:
    """Mixin: _node_verify."""

    async def _node_verify(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]

        agent_state.status = GoalStatus.VERIFYING

        # Build a rich step summary that explicitly flags failed tool calls so
        # the verifier LLM doesn't hallucinate success when tools errored out.
        # Uses module-level _build_verifier_summary to include ALL failed steps.
        summary = _build_verifier_summary(agent_state.steps)
        try:
            from app.agent.prompt_compressor import _default_compressor as _pc
            summary = _pc.compress(summary)
        except Exception:
            pass
        # N9: Prepend verifier context from ContextPipeline
        _verif_ctx = agent_state.context.get("_verifier_context", "") or ""
        if _verif_ctx and len(_verif_ctx) > 50:
            summary = (
                f"[Context for verification]\n{_verif_ctx[:800]}\n\n{summary}"
            )
        # Resolve verifier model via model_router when available (Bug 3 fix)
        _verify_model = ""
        if self._model_router is not None:
            try:
                _verify_model = self._model_router.model_for("verification") or ""
            except Exception:
                pass
        # ── LLM Response Cache for verifier ───────────────────────────────────
        _llm_rc = getattr(self, "_llm_response_cache", None)
        _verify_cached = False
        _verify_user = f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}"
        if _llm_rc is not None and not _llm_rc.should_skip_cache(_verify_user):
            try:
                _cached_verify = await _llm_rc.get(
                    system=VERIFIER_SYSTEM,
                    user=_verify_user,
                    model=_verify_model,
                    tenant_id=tenant_ctx.tenant_id,
                    task_type="verification",
                )
                if _cached_verify is not None:
                    self._logger.info("llm_cache_verify_hit", tenant=tenant_ctx.tenant_id)
                    class _FakeResp:
                        content = _cached_verify
                    resp = _FakeResp()
                    _verify_cached = True
            except Exception:
                pass
        if not _verify_cached:
            # Use structured output when available (Phase 3 Track A)
            _verify_response_schema = None
            try:
                from app.agent.schemas import verifier_schema
                if (
                    hasattr(self._verifier, "supports_structured_output")
                    and self._verifier.supports_structured_output()
                ):
                    _verify_response_schema = verifier_schema()
            except Exception:
                pass

            req = CompletionRequest(
                messages=[
                    Message(role="system", content=VERIFIER_SYSTEM),
                    Message(
                        role="user",
                        content=f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}",
                    ),
                ],
                model=_verify_model,
                response_schema=_verify_response_schema,
            )
            with self._tracer.start_as_current_span("agentverse.verify") as span:
                span.set_attribute("verify.iteration", agent_state.iterations)
                _verify_start = time.monotonic()
                try:
                    resp = await call_with_circuit_breaker(
                        self._verifier, "complete", req,
                        provider_name=type(self._verifier).__name__,
                    )
                except RuntimeError as cb_exc:
                    raise PermissionError(f"Verification unavailable: {cb_exc}") from cb_exc
                record_verify_duration(time.monotonic() - _verify_start)
            # 2.3: Per-goal verifier cost tracking
            try:
                from app.observability.cost_breakdown import record_role_cost as _rrc
                _rrc(
                    goal_id=agent_state.goal_id,
                    role="verifier",
                    model=_verify_model,
                    input_tok=getattr(resp, "input_tokens", 0),
                    output_tok=getattr(resp, "output_tokens", 0),
                    cost=0.0,
                )
            except Exception:
                pass
            # Store in LLM cache — only on successful, non-error responses
            if _llm_rc is not None:
                try:
                    _is_error_verify = (
                        not resp.content
                        or resp.content.strip().startswith("{\"error")
                        or "model_not_found" in resp.content.lower()
                    )
                    if not _is_error_verify:
                        await _llm_rc.set(
                            system=VERIFIER_SYSTEM,
                            user=f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}",
                            model=_verify_model,
                            response=resp.content,
                            tenant_id=tenant_ctx.tenant_id,
                            task_type="verification",
                        )
                except Exception:
                    pass
        # Phase 3 Track A: use parse_verifier_verdict (handles JSON and text fallback)
        from app.agent.schemas import parse_verifier_verdict
        parsed = parse_verifier_verdict(resp.content)
        success: bool = bool(parsed.get("success", False))
        reason: str = self._sanitize_tool_raw_output(parsed.get("reason", ""))
        # Store retry flag for routing: True = can replan, False = permanently blocked
        retry: bool = bool(parsed.get("retry", True)) if not success else True
        agent_state.context["verification_retry"] = retry

        # C5: 3-way consensus for high-risk goals
        if self._consensus_verifier is not None and not success:
            # Only attempt consensus when primary verifier says fail (to save cost)
            try:
                from app.agent.consensus import requires_consensus
                tool_risks = [
                    tc.risk_level
                    for step in agent_state.steps
                    for tc in getattr(step, "tool_calls", [])
                ]
                if requires_consensus(
                    agent_state.goal,
                    agent_state.context.get("domain"),
                    tool_risks,
                ):
                    consensus_result = await self._consensus_verifier.verify(
                        goal=agent_state.goal,
                        summary=summary,
                        model=_verify_model,
                    )
                    success = consensus_result.success
                    reason = consensus_result.majority_reason or reason
                    # HITL if disagreement
                    if consensus_result.requires_hitl and self._hitl_gateway is not None:
                        req_id = str(self._hitl_gateway.request_approval(
                            goal_id=agent_state.goal_id,
                            action=f"Consensus disagreement on goal: {agent_state.goal[:100]}",
                            risk_level="high",
                            tenant_ctx=tenant_ctx,
                        ))
                        self._logger.info("consensus_hitl_requested", req_id=req_id)
            except Exception as exc:
                self._logger.warning("consensus_verify_failed", error=str(exc)[:80])

        agent_state.verification_success = success
        agent_state.verification_feedback = reason
        await self._emit({"type": "verification_done", "success": success, "reason": reason})

        # Record verifier verdict for calibration (Phase 3 Track E)
        try:
            from app.intelligence.verifier_calibration import _default_calibration_store
            _cal_store = getattr(self, "_calibration_store", _default_calibration_store)
            if _cal_store is not None:
                _cal_task = asyncio.create_task(
                    _cal_store.record_verdict(
                        goal_id=agent_state.goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        verifier_verdict=success,
                        verifier_model=_verify_model,
                        iteration=agent_state.iterations,
                        goal_text=agent_state.goal,
                    )
                )
                self._background_tasks.add(_cal_task)
                _cal_task.add_done_callback(self._background_tasks.discard)
        except Exception as exc:
            self._logger.debug("calibration_record_failed", error=str(exc)[:60])

        if success:
            # Record winning plan in execution memory (sync in-memory + async DB, BUG 2b fix)
            if self._exec_memory is not None:
                self._exec_memory.record(  # sync: immediate in-memory update
                    goal=agent_state.goal,
                    plan=agent_state.plan,
                    tenant_ctx=tenant_ctx,
                )
                # Async DB persistence — only when a DB session factory is available
                if self._db_session_factory is not None:
                    _em_task = asyncio.create_task(
                        self._exec_memory.record_async(
                            goal=agent_state.goal,
                            plan=agent_state.plan,
                            success=True,
                            tenant_id=tenant_ctx.tenant_id,
                            db=self._db_session_factory,
                        )
                    )
                    self._background_tasks.add(_em_task)
                    _em_task.add_done_callback(self._background_tasks.discard)

            # Auto-extract long-term learnings (sync in-memory + async DB, BUG 1 fix)
            if self._long_term_memory is not None:
                step_outputs = " ".join(
                    s.output[:100] for s in agent_state.steps if s.output
                )
                # Sync extract: immediate in-memory update (same-session recall)
                self._long_term_memory.extract_from_goal(
                    goal=agent_state.goal,
                    result=step_outputs,
                    goal_id=agent_state.goal_id,
                    tenant_ctx=tenant_ctx,
                )
                # Async DB persistence via extract_from_goal_async (BUG 1 fix)
                if self._db_session_factory is not None:
                    _ltm_task = asyncio.create_task(
                        self._long_term_memory.extract_from_goal_async(
                            goal=agent_state.goal,
                            result=step_outputs[:500],
                            tenant_ctx=tenant_ctx,
                            db=self._db_session_factory,
                            embedder=self._embedder,
                        )
                    )
                    self._background_tasks.add(_ltm_task)
                    _ltm_task.add_done_callback(
                        lambda t: t.exception() and self._logger.warning(
                            "ltm_persist_failed", error=str(t.exception())
                        )
                    )
                    _ltm_task.add_done_callback(self._background_tasks.discard)

            # Score the completed goal — persists eval to DB (BUG 3 fix)
            scorecard = None
            if self._eval_runner is not None:
                scorecard = await self._eval_runner.score_and_persist(
                    agent_state,
                    tenant_ctx,
                    provider=self._verifier,
                    db=self._db_session_factory,
                )
                agent_state.context["eval_scorecard"] = scorecard
            agent_state.status = GoalStatus.COMPLETE
            record_goal_completed(tenant_id=tenant_ctx.tenant_id)
            await self._emit({"type": "goal_complete"})

            # N3: Compute actual latency before scoring so RuntimeScorecard gets a real value
            try:
                import time as _lat_time
                _start_ms = agent_state.context.get("_goal_start_ms", 0.0)
                if _start_ms > 0:
                    agent_state.context["_latency_ms"] = (
                        _lat_time.monotonic() * 1000 - _start_ms
                    )
            except Exception:
                pass

            # Dynamic orchestration: scorecard + self-improvement + reflexion
            try:
                from app.core.runtime_flags import get_runtime_flags as _nv_rtf
                from app.evals.runtime_scorecard import RuntimeScorecard
                _nv_flags = _nv_rtf()
                _profile = agent_state.context.get("_runtime_profile")
                if (
                    (_nv_flags.dynamic_orchestration or _nv_flags.enable_runtime_scorecard)
                    and _profile is not None
                ):
                    _scorecard = RuntimeScorecard()
                    _guardrail_violations = sum(
                        1
                        for event in agent_state.events
                        if isinstance(event, dict)
                        and (
                            event.get("action_level") == "DENY"
                            or event.get("outcome") == "denied"
                            or event.get("type") in {"tool_call_denied", "guardrail_rejected"}
                        )
                    )
                    _scorecard_result = _scorecard.score(
                        state=agent_state,
                        profile=_profile,
                        retrieval_result=agent_state.context.get(
                            "runtime_retrieval_evidence"
                        ),
                        cost_usd=agent_state.context.get("total_cost_usd"),
                        latency_ms=agent_state.context.get("_latency_ms"),
                        guardrail_violations=_guardrail_violations,
                    )
                    agent_state.context["scorecard"] = _scorecard_result.to_dict()
                    # Persist scorecard to eval_scorecards table
                    try:
                        _orch_persist = (
                            getattr(self._app_state, "orchestration_persistence", None)
                            if self._app_state else None
                        )
                        if _orch_persist is not None:
                            import asyncio as _sc_asyncio
                            _sc_task = _sc_asyncio.ensure_future(
                                _orch_persist.persist_scorecard(
                                    _scorecard_result, profile=_profile
                                )
                            )
                            if hasattr(self, "_background_tasks"):
                                self._background_tasks.add(_sc_task)
                                _sc_task.add_done_callback(self._background_tasks.discard)
                    except Exception:
                        pass
                    # RegressionGate: catalogue low-scoring goals as regression cases
                    try:
                        from app.evals.regression_gate import RegressionGate
                        _rg = RegressionGate()
                        _regression_candidate = _rg.maybe_create_regression(
                            state=agent_state,
                            scorecard=_scorecard_result,
                            profile=_profile,
                        )
                        if _regression_candidate:
                            agent_state.context["regression_candidate"] = _regression_candidate
                            # Persist regression case
                            try:
                                _orch_p = (
                                    getattr(self._app_state, "orchestration_persistence", None)
                                    if self._app_state else None
                                )
                                if _orch_p is not None and hasattr(_orch_p, "persist_regression_case"):
                                    import asyncio as _rc_asyncio
                                    _rc_asyncio.ensure_future(
                                        _orch_p.persist_regression_case(
                                            {**_regression_candidate, "tenant_id": tenant_ctx.tenant_id}
                                        )
                                    )
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # N12: Gate self-improvement on granular flag
                    _si_enabled = (
                        _nv_flags.dynamic_orchestration or _nv_flags.enable_self_improvement
                    )
                    _actions: list[Any] = []
                    if _si_enabled:
                        from app.evals.self_improvement_engine import SelfImprovementEngine
                        _engine = SelfImprovementEngine()
                        _actions = _engine.decide_actions(
                            _scorecard_result, _profile, state=agent_state
                        )
                    agent_state.context["improvement_actions"] = [
                        a.action_type.value for a in _actions
                    ]
                    # Dispatch improvement actions
                    try:
                        for _action in _actions:
                            _action_type = (
                                _action.action_type.value
                                if hasattr(_action, "action_type") else str(_action)
                            )
                            if "STORE_REFLEXION_LESSON" in _action_type:
                                pass  # handled by reflexion_wirer in failure branch
                            elif "UPDATE_PROMPT_VARIANT" in _action_type:
                                _po = (
                                    getattr(self._app_state, "prompt_optimizer", None)
                                    if self._app_state else None
                                )
                                if _po is not None and hasattr(_po, "record_result"):
                                    _variant_id = agent_state.context.get("planner_variant_id")
                                    if _variant_id:
                                        _po.record_result(
                                            variant_id=_variant_id,
                                            eval_score=_scorecard_result.overall_score,
                                        )
                            elif "SWITCH_MODEL" in _action_type or "UPDATE_MODEL_ROUTING" in _action_type:
                                # N7a: Persist model switch recommendation to agent config
                                try:
                                    if self._app_state is not None and self._agent_id is not None:
                                        _agent_store = getattr(self._app_state, "agent_store", None)
                                        if _agent_store is not None and hasattr(_agent_store, "update_config"):
                                            import asyncio as _mc_asyncio
                                            _mc_asyncio.ensure_future(
                                                _agent_store.update_config(
                                                    agent_id=self._agent_id,
                                                    tenant_ctx=tenant_ctx,
                                                    config_patch={
                                                        "model_downgrade_recommended": True,
                                                        "last_switch_reason": "low_eval_score",
                                                        "last_switch_score": _scorecard_result.overall_score,
                                                    },
                                                )
                                            )
                                except Exception:
                                    pass
                                from app.observability.logging import get_logger
                                get_logger(__name__).warning(
                                    "self_improvement_model_switch_applied",
                                    goal_id=agent_state.goal_id,
                                    score=_scorecard_result.overall_score,
                                )
                            elif "BLACKLIST_TOOL_PATTERN" in _action_type:
                                # N7b: Record tool as unreliable in ToolReliabilityStore
                                try:
                                    _tr_store = getattr(self, "_tool_reliability_store", None)
                                    if _tr_store is not None:
                                        # Find failed tools from recent steps
                                        _failed_tools: list[str] = []
                                        for _s in agent_state.steps:
                                            for _tc in (getattr(_s, "tool_calls", None) or []):
                                                if isinstance(_tc, dict) and not _tc.get("success", True):
                                                    _tn = _tc.get("tool_name", "")
                                                    if _tn and _tn not in _failed_tools:
                                                        _failed_tools.append(_tn)
                                        for _ft in _failed_tools[:3]:
                                            import asyncio as _bl_asyncio
                                            _bl_asyncio.ensure_future(
                                                _tr_store.record(
                                                    tool_name=_ft,
                                                    tenant_id=tenant_ctx.tenant_id,
                                                    success=False,
                                                    latency_ms=5000.0,
                                                    error="blacklisted_by_self_improvement",
                                                )
                                            )
                                        agent_state.context["_blacklisted_tools"] = _failed_tools[:3]
                                except Exception:
                                    pass
                                from app.observability.logging import get_logger
                                get_logger(__name__).warning(
                                    "self_improvement_tool_blacklisted",
                                    goal_id=agent_state.goal_id,
                                    score=_scorecard_result.overall_score,
                                )
                    except Exception:
                        pass
                    # N6c: self_improvement_suggested SSE
                    try:
                        if self._event_callback is not None and _actions:
                            from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                            _sse_sis = RuntimeSSEEmitter()
                            await self._emit(_sse_sis.self_improvement_suggested(
                                goal_id=agent_state.goal_id,
                                suggestions=[
                                    a.action_type.value if hasattr(a, "action_type") else str(a)
                                    for a in _actions
                                ],
                            ))
                    except Exception:
                        pass
                    # Emit eval_score_recorded SSE (N12: gated on pattern SSE flag)
                    if _nv_flags.dynamic_orchestration or _nv_flags.enable_pattern_sse_events:
                        try:
                            from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                            _sse_emitter = RuntimeSSEEmitter()
                            await self._emit(_sse_emitter.eval_score_recorded(
                                goal_id=agent_state.goal_id,
                                overall_score=_scorecard_result.overall_score,
                                scores=_scorecard_result.scores,
                            ))
                        except Exception:
                            pass
            except Exception:
                pass
            # Guardrail check: final_output (Guardrails 2.0)
            if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx:
                try:
                    _g2_final_content = (
                        agent_state.cited_answer
                        or " ".join(s.output[:200] for s in agent_state.steps if s.output)
                        or agent_state.verification_feedback
                    )
                    if _g2_final_content:
                        _g2_final_result = await guardrails_engine.evaluate(
                            content=str(_g2_final_content)[:2000],
                            layer=GuardrailLayer.FINAL_OUTPUT,
                            tenant_id=tenant_ctx.tenant_id,
                            goal_id=getattr(agent_state, "goal_id", None),
                        )
                        if _g2_final_result.get("blocked"):
                            agent_state.cited_answer = "[Output redacted by guardrail policy]"
                except Exception:
                    pass  # Guardrail errors must never block completion

            # Phase 3 Track C: synthesize cited answer on success
            if self._answer_synthesizer is not None:
                try:
                    cited = await self._answer_synthesizer.synthesize(
                        goal=agent_state.goal,
                        steps=agent_state.steps,
                        tenant_id=tenant_ctx.tenant_id,
                    )
                    agent_state.cited_answer = cited.answer
                    agent_state.provenance = [
                        {"text": c.text, "source": c.source, "step": c.step_index}
                        for c in cited.citations
                    ]
                    await self._emit({
                        "type": "synthesis_complete",
                        "cited_answer": cited.answer[:2000],
                        "citations": [{"text": c.text, "source": c.source} for c in cited.citations],
                    })
                except Exception as exc:
                    self._logger.debug("synthesis_failed", error=str(exc)[:60])

            # H-2: SelfOptimizerV2 result recording — feeds A/B experiment outcomes
            _self_opt_v2 = getattr(self._app_state, "self_optimizer_v2", None) if self._app_state else None
            if _self_opt_v2 and self._agent_id and isinstance(agent_state.context, dict):
                _arm = agent_state.context.get("_experiment_arm")
                if _arm:
                    _eval_scorecard = agent_state.context.get("eval_scorecard", {})
                    _eval_score: float | None = None
                    if hasattr(_eval_scorecard, "average_score"):
                        try:
                            _eval_score = float(_eval_scorecard.average_score())
                        except Exception:
                            pass
                    elif isinstance(_eval_scorecard, dict):
                        _eval_score_raw = _eval_scorecard.get("average_score")
                        if _eval_score_raw is not None:
                            try:
                                _eval_score = float(_eval_score_raw)
                            except Exception:
                                pass
                    if _eval_score is not None:
                        import asyncio as _asyncio
                        _v2_task = _asyncio.create_task(
                            _self_opt_v2.on_goal_completed(
                                tenant_id=tenant_ctx.tenant_id,
                                agent_id=self._agent_id,
                                goal_id=agent_state.goal_id,
                                eval_score=_eval_score,
                                cost_usd=float(agent_state.context.get("total_cost_usd", 0.0)),
                                latency_ms=0,
                            )
                        )
                        self._background_tasks.add(_v2_task)
                        _v2_task.add_done_callback(self._background_tasks.discard)
            # N5: Record result in module-level ABTestingEngine for cross-goal A/B analysis
            try:
                from app.optimization.ab_testing import ExperimentType
                from app.optimization.ab_testing import ab_testing_engine as _abt_eng
                if _abt_eng is not None and _eval_score is not None and tenant_ctx is not None:
                    import asyncio as _n5_asyncio
                    _abt_asyncio_task = _n5_asyncio.ensure_future(
                        _abt_eng.record_result_async(
                            goal_id=agent_state.goal_id,
                            experiment_type=ExperimentType.RAG_STRATEGY,
                            arm_id=agent_state.context.get("_experiment_arm", "control"),
                            score=float(_eval_score),
                            tenant_id=tenant_ctx.tenant_id,
                        )
                    )
                    if hasattr(self, "_background_tasks"):
                        self._background_tasks.add(_abt_asyncio_task)
                        _abt_asyncio_task.add_done_callback(self._background_tasks.discard)
            except Exception:
                pass
        else:
            scorecard = None
            # FIX: On permanent failure (retry=False), roll back all registered actions
            # using the async method to guarantee each inverse completes before moving on.
            if not retry and self._rollback_engine is not None and len(self._rollback_engine) > 0:
                rolled = await self._rollback_engine.rollback_all_async()
                self._logger.info("agent_rollback_complete", rolled_back=rolled)

            # Reflexion: store failure lesson (C1 fix — was incorrectly in success branch)
            try:
                from app.agent.reflexion_wirer import get_reflexion_wirer
                _rw = get_reflexion_wirer()
                import asyncio as _rf_asyncio
                _rf_asyncio.ensure_future(_rw.maybe_store_async(agent_state))
            except Exception:
                pass

            # H28: Also persist via OrchestrationPersistence (belt-and-suspenders)
            try:
                _orch_p = (
                    getattr(self._app_state, "orchestration_persistence", None)
                    if self._app_state else None
                )
                if _orch_p is not None and hasattr(_orch_p, "persist_reflexion_lesson"):
                    import asyncio as _rl_asyncio
                    _rl_asyncio.ensure_future(
                        _orch_p.persist_reflexion_lesson(agent_state)
                    )
            except Exception:
                pass

        # Feed eval result back to PromptOptimizer for A/B learning (BUG 4 fix)
        if scorecard is not None and hasattr(agent_state, "context"):
            _planner_variant_id = agent_state.context.get("planner_variant_id")
            _avg_score = scorecard.average_score()
            _won = _avg_score >= 0.7
            if self._prompt_optimizer is not None and _planner_variant_id:
                try:
                    self._prompt_optimizer.record_result(
                        variant_id=_planner_variant_id,
                        eval_score=_avg_score,
                    )
                    _po_task = asyncio.create_task(
                        self._prompt_optimizer.persist_outcome(
                            _planner_variant_id, won=_won, db=self._db_session_factory
                        )
                    )
                    self._background_tasks.add(_po_task)
                    _po_task.add_done_callback(self._background_tasks.discard)
                except Exception:
                    pass

        # Trigger self-optimization when a goal scores below the excellence threshold.
        # Using < 0.5 ensures we only collect improvement insights for genuinely failing
        # goals (below 50% score), avoiding unnecessary optimization churn on good runs.
        if (
            self._self_optimizer is not None
            and scorecard is not None
            and scorecard.average_score() < 0.5
        ):
            _so_task = asyncio.create_task(
                self._trigger_self_optimization(agent_state, scorecard, tenant_ctx)
            )
            self._background_tasks.add(_so_task)
            _so_task.add_done_callback(self._background_tasks.discard)

        # Record episodic experience after goal completion (success or failure)
        try:
            if self._episodic_memory is not None and tenant_ctx is not None:
                _ep_quality = float(
                    agent_state.context.get("scorecard", {}).get("overall_score", 0.5)
                    if isinstance(agent_state.context.get("scorecard"), dict)
                    else 0.5
                )
                _ep_task = asyncio.ensure_future(
                    self._episodic_memory.record(
                        state=agent_state,
                        tenant_ctx=tenant_ctx,
                        quality_score=_ep_quality,
                    )
                )
                if hasattr(self, "_background_tasks"):
                    self._background_tasks.add(_ep_task)
                    _ep_task.add_done_callback(self._background_tasks.discard)
        except Exception:
            pass

        # Learn procedural skill after successful goal
        try:
            if self._procedural_memory is not None and success and tenant_ctx is not None:
                _proc_task = asyncio.ensure_future(
                    self._procedural_memory.learn(
                        state=agent_state,
                        tenant_ctx=tenant_ctx,
                        success=success,
                    )
                )
                if hasattr(self, "_background_tasks"):
                    self._background_tasks.add(_proc_task)
                    _proc_task.add_done_callback(self._background_tasks.discard)
        except Exception:
            pass

        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # Routing (synchronous — LangGraph calls this synchronously)
    # ------------------------------------------------------------------

