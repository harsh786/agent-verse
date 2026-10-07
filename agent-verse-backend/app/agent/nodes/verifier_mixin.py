"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.agent.prompts import (
    VERIFIER_SYSTEM,
)
from app.agent.state import AgentState, GoalStatus
from app.observability.metrics import (
    record_goal_completed,
    record_verify_duration,
)
from app.providers.base import CompletionRequest, Message
from app.providers.circuit_breaker import complete_with_failover
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

from app.agent.graph_types import (  # noqa: F401
    STEP_FAILURES_KEY,
    GraphState,
    RetrievalEntryPointError,
)
from app.agent.nodes._helpers import (
    _build_verifier_summary,
    _guardrail_should_fail_closed,
)

# A final answer this short (a number, a name, an acknowledgement, a sentence or
# two) may be grounded in the agent's own step outputs on a goal that requires no
# retrieval, is not high-risk and gathered no evidence (B7 live open item 1).
_SHORT_ANSWER_MAX_CHARS = 300
_SHORT_ANSWER_MAX_WORDS = 40
_OWN_OUTPUT_EVIDENCE_CHARS = 600


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
            summary = f"[Context for verification]\n{_verif_ctx[:800]}\n\n{summary}"
        # Resolve verifier model via model_router when available (Bug 3 fix)
        _verify_model = ""
        if self._model_router is not None:
            with contextlib.suppress(Exception):
                _verify_model = self._model_router.model_for("verification") or ""
        # Strategy C: route verification (a latency-sensitive, quality-tolerant
        # role) to a faster model when the resolved strategy nominates one.
        _strategy_c = agent_state.context.get("_execution_strategy")
        _strategy_verify_model = getattr(_strategy_c, "verifier_model", "") if _strategy_c else ""
        if _strategy_verify_model:
            _verify_model = _strategy_verify_model
            with contextlib.suppress(Exception):
                await self._emit(
                    {"type": "verifier_model_routed", "model": _verify_model, "reason": "latency"}
                )
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
                    resp = await complete_with_failover(
                        self._verifier, req, fallback_models=self._role_fallback_models()
                    )
                except (RuntimeError, TimeoutError) as cb_exc:
                    raise PermissionError(f"Verification unavailable: {cb_exc}") from cb_exc
                record_verify_duration(time.monotonic() - _verify_start)
            # 2.3: Charge the verifier call through the executor's cost path (budget,
            # ledger, grant spend, per-role breakdown) — it used to log cost=0.0.
            from app.agent.nodes.llm_cost import charge_llm_call

            await charge_llm_call(
                self,
                resp=resp,
                role="verifier",
                model=_verify_model,
                agent_state=agent_state,
                tenant_ctx=tenant_ctx,
            )
            # Store in LLM cache — only on successful, non-error responses
            if _llm_rc is not None:
                try:
                    _is_error_verify = (
                        not resp.content
                        or resp.content.strip().startswith('{"error')
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
        # Deterministic gate: a step that never executed in this pass (open circuit,
        # etc.) cannot be verified as done, whatever the LLM verifier says.
        _not_executed = agent_state.context.get(STEP_FAILURES_KEY) or []
        if _not_executed and success:
            success = False
            retry = True
            reason = "Step(s) not executed: " + "; ".join(
                f"{f.get('step', '?')[:80]} ({f.get('reason', '')[:120]})"
                for f in _not_executed[:3]
            )
        # Deterministic gate: a tool call of this pass that failed, errored or was
        # denied (and was not retried successfully) is a fact, not an opinion —
        # the verifier can only downgrade a successful tool result, never upgrade
        # a failed one.
        from app.agent.tool_outcomes import ledger_for

        _failed_tools = ledger_for(self).unresolved_failures()
        if _failed_tools and success:
            success = False
            retry = True
            reason = "Tool call(s) failed: " + "; ".join(
                f"{tool} ({error[:120]})" for tool, error in _failed_tools[:3]
            )
        agent_state.context["verification_retry"] = retry

        # C5: 3-way consensus for high-risk goals
        if self._consensus_verifier is not None and not success:
            # Only attempt consensus when primary verifier says fail (to save cost)
            try:
                from app.agent.consensus import requires_consensus

                # tool_calls may be dataclasses OR plain dicts (e.g. deserialized
                # from a checkpoint / provider payload), so read risk_level both
                # ways — a bare tc.risk_level raised AttributeError on dicts and
                # silently disabled consensus verification for the whole goal.
                def _risk_of(tc: Any) -> str | None:
                    raw = tc.get("risk_level") if isinstance(tc, dict) else getattr(
                        tc, "risk_level", None
                    )
                    return str(raw) if raw is not None else None

                tool_risks: list[str] = [
                    risk
                    for step in agent_state.steps
                    for tc in getattr(step, "tool_calls", [])
                    if (risk := _risk_of(tc)) is not None
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
                    _primary_reason = reason
                    success = consensus_result.success
                    reason = consensus_result.majority_reason or reason
                    # HITL if disagreement. Only a supervised run pauses on the
                    # pending request (routing → waiting_human); anywhere else it
                    # was orphaned while the goal completed on the disputed
                    # majority. There, a disputed verdict does not overturn the
                    # primary FAIL and no request is filed (CORE-01).
                    if consensus_result.requires_hitl and self._autonomy_mode != "supervised":
                        if success:
                            success = False
                            retry = True
                            agent_state.context["verification_retry"] = retry
                            reason = (
                                "Consensus verification disputed (needs human review; "
                                "run the goal in supervised mode): "
                                f"{consensus_result.majority_reason or ''}"
                            )[:1000]
                    elif consensus_result.requires_hitl and self._hitl_gateway is not None:
                        # CORE-27: filed durably, so the waiting_human pause always
                        # corresponds to a persisted row another replica can list
                        # and approve. If it cannot be persisted nobody can
                        # adjudicate it: the primary FAIL verdict stands.
                        from app.agent.hitl_filing import (
                            HITLDeliveryError,
                            file_persisted_approval,
                        )

                        try:
                            req_id = await file_persisted_approval(
                                self._hitl_gateway,
                                goal_id=agent_state.goal_id,
                                action=f"Consensus disagreement on goal: {agent_state.goal[:100]}",
                                risk_level="high",
                                tenant_ctx=tenant_ctx,
                            )
                            self._logger.info("consensus_hitl_requested", req_id=req_id)
                        except HITLDeliveryError as exc:
                            success = False
                            agent_state.context["verification_retry"] = retry
                            reason = (
                                "Consensus verification disputed and the human-review "
                                "request could not be persisted "
                                f"({type(exc).__name__}); primary verdict stands: "
                                f"{_primary_reason}"
                            )[:1000]
                            self._logger.warning(
                                "consensus_hitl_not_persisted", error=type(exc).__name__
                            )
            except Exception as exc:
                self._logger.warning("consensus_verify_failed", error=str(exc)[:80])

        # D-4/D-5: final-answer grounding gate. Verify the answer the verifier is
        # about to accept is grounded in the evidence gathered during execution.
        # Fail-OPEN (annotate + warn) on normal goals — mirrors the executor gate —
        # but fail-CLOSED (flip success → replan) on high-risk goals so an
        # ungrounded high-risk answer is never emitted. Fully guarded.
        if success:
            try:
                from app.agent.grounding import annotate_ungrounded, check_grounding
                from app.agent.nodes._helpers import _is_high_risk_step

                _final_answer = (
                    agent_state.cited_answer
                    or " ".join(s.output for s in agent_state.steps if s.output)
                ).strip()
                _evidence = [
                    str(tc.get("output", ""))
                    for step in agent_state.steps
                    for tc in getattr(step, "tool_calls", [])
                    if tc.get("output")
                ]
                # Retrieved knowledge is also grounding evidence — an answer can be
                # supported by RAG context without any tool call. Include it so a
                # knowledge-grounded answer is not a false positive.
                _rag_knowledge = agent_state.context.get("rag_knowledge")
                if isinstance(_rag_knowledge, str) and _rag_knowledge.strip():
                    _evidence.append(_rag_knowledge)
                # GRD-1: structured tool results (counts, flags, status codes, ids,
                # a failed call's error) normalised into checkable facts: "1 deleted"
                # grounds "The order was deleted."; "0 deleted" or an error refutes it.
                from app.agent.structured_evidence import (
                    StructuredFactNLI,
                    facts_from_tool_calls,
                )

                _facts = facts_from_tool_calls(
                    tc for step in agent_state.steps for tc in getattr(step, "tool_calls", [])
                )
                if not _facts.empty:
                    _evidence.append(_facts.render())
                _high_risk = _is_high_risk_step(agent_state.goal)
                # Whether there was any real evidence (tool outputs or retrieved
                # knowledge) to check the answer against. The keyword gate below
                # fails closed on a high-risk goal whenever the answer carries a
                # concrete claim (number, id, URL, ...) that nothing supports —
                # including when there is no evidence at all: a text-only answer
                # inventing a row count for "delete production data" used to
                # complete with only a warning (CORE-03). The sentence-level NLI
                # gate still needs evidence to judge against, since without any
                # it would flag every sentence of every text-only answer.
                _had_evidence = bool(_evidence)
                # Facts the user supplied in the goal are evidence too (P5-6): a
                # high-risk answer repeating them was replanned as ungrounded.
                if (agent_state.goal or "").strip():
                    _evidence.append(agent_state.goal)
                # B7 live open item 1: a correct computed / short direct answer is
                # not an ungrounded claim ("391" for "What is 17*23?" was flagged,
                # and replanned on a high-risk goal, because no tool output held
                # it). (1) Arithmetic in the goal and the agent's own step texts is
                # recomputed deterministically and added as evidence: a correct
                # result is grounded, a wrong one is not, and an answer stating a
                # result the recomputation contradicts is flagged. (2) On a goal
                # that requires no retrieval, is not high-risk and gathered no tool
                # / retrieved evidence (pure reasoning or knowledge), a SHORT final
                # answer may rest on the agent's own step outputs (it repeats what
                # its steps produced). Retrieval-required and high-risk goals, and
                # goals with gathered evidence, keep the strict check against it.
                # This context evidence is not "real" evidence for the fail-closed
                # decision below (_had_evidence is computed before it).
                from app.agent.arithmetic_evidence import (
                    derive_arithmetic,
                    render_evidence,
                    wrong_results,
                )

                _steps = list(agent_state.steps)
                _arith = derive_arithmetic(
                    [
                        agent_state.goal or "",
                        *(str(getattr(s, "description", "") or "") for s in _steps),
                        *(s.output for s in _steps if s.output),
                        _final_answer,
                    ]
                )
                _context_evidence: list[str] = []
                if _arith:
                    _context_evidence.append(render_evidence(_arith))
                _retrieval_required = bool(
                    getattr(self, "_agent_collection_ids", None)
                ) or bool(agent_state.context.get("retrieval_required"))
                _short_answer = (
                    len(_final_answer) <= _SHORT_ANSWER_MAX_CHARS
                    and len(_final_answer.split()) <= _SHORT_ANSWER_MAX_WORDS
                )
                _own_outputs_used = False
                if (
                    _short_answer
                    and not _retrieval_required
                    and not _high_risk
                    and not _had_evidence
                ):
                    _own = " ".join(s.output for s in _steps if s.output).strip()
                    if _own:
                        _context_evidence.append(_own[:_OWN_OUTPUT_EVIDENCE_CHARS])
                        _own_outputs_used = True
                _wrong = wrong_results(_final_answer, agent_state.goal or "")
                agent_state.context["final_answer_grounding_basis"] = {
                    "arithmetic_facts": len(_arith),
                    "own_step_outputs": _own_outputs_used,
                    "retrieval_required": _retrieval_required,
                    "wrong_arithmetic": _wrong[:3],
                }
                # Run the grounding check whenever there is a final answer — NOT only
                # when tool evidence exists. check_grounding's own "concrete claims
                # present + no supporting evidence → ungrounded" branch is exactly the
                # high-risk hallucination case (an answer asserting facts with nothing
                # to back them). Guarding on ``and _evidence`` made that case
                # unreachable on the common text-only execution path.
                if _final_answer:
                    _grounding = check_grounding(
                        output=_final_answer,
                        tool_outputs=[*_evidence, *_context_evidence],
                        strict=_high_risk,
                    )
                    if _wrong:
                        # A stated result the recomputation contradicts is an
                        # ungrounded claim whatever else grounds the number.
                        from app.agent.grounding import GroundingResult

                        _grounding = GroundingResult(
                            grounded=False,
                            ungrounded_claims=list(
                                dict.fromkeys([*_wrong, *_grounding.ungrounded_claims])
                            ),
                            checked_claims=max(_grounding.checked_claims, len(_wrong)),
                            evidence_length=_grounding.evidence_length,
                        )
                    agent_state.context["final_answer_grounded"] = _grounding.grounded
                    if not _grounding.grounded:
                        agent_state.ungrounded_claims.extend(
                            _grounding.ungrounded_claims[:5]
                        )
                        await self._emit(
                            {
                                "type": "grounding_warning",
                                "stage": "final_answer",
                                "high_risk": _high_risk,
                                "ungrounded_claims": _grounding.ungrounded_claims[:5],
                            }
                        )
                        if _high_risk:
                            # Fail-closed: drive a replan instead of emitting.
                            success = False
                            retry = True
                            agent_state.context["verification_retry"] = True
                            reason = (
                                "Final answer failed grounding on a high-risk goal: "
                                f"{len(_grounding.ungrounded_claims)} unsupported claim(s). "
                                "Re-execute and cite evidence for every claim. "
                                + (reason or "")
                            ).strip()
                            self._logger.warning(
                                "final_grounding_gate_replan",
                                goal_id=agent_state.goal_id,
                                ungrounded=_grounding.ungrounded_claims[:3],
                            )
                        elif agent_state.cited_answer:
                            # Fail-open: annotate the answer with grounding caveats.
                            agent_state.cited_answer = annotate_ungrounded(
                                agent_state.cited_answer, _grounding
                            )

                    # D-4/D-5: higher-fidelity NLI claim + citation-attribution
                    # gate. Composes ClaimDecomposer + NLIChecker +
                    # AttributionVerifier via verify_grounding. Runs
                    # deterministically (provider=None) so it is safe on the hot
                    # path; still atomises the answer into claims and checks each
                    # against the evidence, catching reworded fabrications the
                    # keyword gate above misses. Fail-OPEN on normal goals,
                    # fail-CLOSED (replan) on high-risk goals.
                    from app.intelligence.grounding_verification import verify_grounding

                    _verdict = await verify_grounding(
                        _final_answer,
                        _evidence,
                        nli=StructuredFactNLI(
                            _facts, " ".join([*_context_evidence, *_evidence])
                        ),
                        context_evidence=_context_evidence,
                    )
                    agent_state.context["claim_grounding_safe"] = _verdict.safe_to_emit
                    agent_state.context["claim_grounding_score"] = _verdict.claim_score
                    if not _verdict.safe_to_emit:
                        _bad_claims = (
                            _verdict.contradicted_claims + _verdict.unsupported_claims
                        )
                        agent_state.ungrounded_claims.extend(_bad_claims[:5])
                        await self._emit(
                            {
                                "type": "claim_grounding_warning",
                                "stage": "final_answer",
                                "high_risk": _high_risk,
                                "reasons": _verdict.reasons[:5],
                                "contradicted": _verdict.contradicted_claims[:3],
                            }
                        )
                        if _high_risk and success and _had_evidence:
                            success = False
                            retry = True
                            agent_state.context["verification_retry"] = True
                            _why = "; ".join(_verdict.reasons[:2])
                            reason = (
                                "Final answer failed NLI claim/attribution "
                                f"grounding on a high-risk goal ({_why}). "
                                "Re-execute and cite evidence for every claim. "
                                + (reason or "")
                            ).strip()
                            self._logger.warning(
                                "final_claim_grounding_gate_replan",
                                goal_id=agent_state.goal_id,
                                reasons=_verdict.reasons[:3],
                            )
            except Exception as exc:
                # Grounding-gate errors must never crash verification. They fail
                # OPEN only on normal goals: a high-risk answer is never emitted
                # unverified because the grounding engine errored (CORE-29) —
                # same fail-closed rule as the executor's high-risk gate.
                self._logger.warning("final_grounding_gate_error", error=type(exc).__name__)
                from app.agent.nodes._helpers import _is_high_risk_step as _hr

                _high = _hr(agent_state.goal)
                with contextlib.suppress(Exception):
                    await self._emit(
                        {
                            "type": "grounding_check_failed",
                            "stage": "final_answer",
                            "high_risk": _high,
                            "error_type": type(exc).__name__,
                        }
                    )
                if _high and success:
                    success = False
                    retry = True
                    agent_state.context["verification_retry"] = True
                    reason = (
                        "Final answer not verified: grounding check unavailable on a "
                        f"high-risk goal ({type(exc).__name__}). " + (reason or "")
                    ).strip()

        agent_state.verification_success = success
        agent_state.verification_feedback = reason
        await self._emit({"type": "verification_done", "success": success, "reason": reason})

        # Record verifier verdict for calibration (Phase 3 Track E)
        try:
            from app.intelligence.verifier_calibration import _default_calibration_store

            _cal_store = getattr(self, "_calibration_store", _default_calibration_store)
            if _cal_store is not None:
                # Awaited (one INSERT, bounded): a background task is cancelled
                # by the worker loop's teardown when this is the goal's final
                # verdict — the one feedback judges (a05-F092-01).
                await asyncio.wait_for(
                    _cal_store.record_verdict(
                        goal_id=agent_state.goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        verifier_verdict=success,
                        verifier_model=_verify_model,
                        iteration=agent_state.iterations,
                        goal_text=agent_state.goal,
                    ),
                    timeout=5.0,
                )
        except Exception as exc:
            self._logger.warning("calibration_record_failed", error=str(exc)[:200])

        if success:
            # Record winning plan in execution memory (sync in-memory + async DB, BUG 2b fix)
            if self._exec_memory is not None:
                # One record per goal (MEM-08): record_async updates the cache
                # itself (DB-less build included) after the memory-write gate
                # vets it (MEM-68) — the sync record() stored unvetted text.
                # Awaited (one INSERT) so a lost write is visible on this
                # goal instead of vanishing in a background task (MEM-07).
                _em_persisted = await self._exec_memory.record_async(
                    goal=agent_state.goal,
                    plan=agent_state.plan,
                    success=True,
                    tenant_id=tenant_ctx.tenant_id,
                    db=self._db_session_factory,
                    goal_id=str(agent_state.goal_id or ""),
                )
                if _em_persisted is False:
                    _degraded = agent_state.context.setdefault("memory_degraded", [])
                    if "execution_memory_write" not in _degraded:
                        _degraded.append("execution_memory_write")
                    await self._emit(
                        {"type": "memory_degraded", "source": "execution_memory_write"}
                    )

            # Auto-extract long-term learnings. One awaited write through
            # store_async, which vets the content with the shared memory-write
            # gate BEFORE it reaches the cache or the DB (MEM-68). The old sync
            # extract cached unvetted text and the background persist could be
            # cancelled at loop teardown, losing it silently.
            if self._long_term_memory is not None:
                step_outputs = " ".join(s.output[:100] for s in agent_state.steps if s.output)
                try:
                    await asyncio.wait_for(
                        self._long_term_memory.extract_from_goal_async(
                            goal=agent_state.goal,
                            result=step_outputs[:500],
                            tenant_ctx=tenant_ctx,
                            db=self._db_session_factory,
                            embedder=self._embedder,
                            goal_id=str(agent_state.goal_id or ""),
                        ),
                        timeout=10.0,
                    )
                except Exception as _ltm_exc:
                    await self._memory_degraded(agent_state, "long_term_memory_write", _ltm_exc)

            # Score the completed goal — persists eval to DB (BUG 3 fix).
            # B7-L2: mark it COMPLETE first: task_completion is 1.0 iff the goal
            # reached COMPLETE, and scoring before the transition stored 0.0 for
            # every successful goal (goal_score_below fired on all of them).
            agent_state.status = GoalStatus.COMPLETE
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
                    agent_state.context["_latency_ms"] = _lat_time.monotonic() * 1000 - _start_ms
            except Exception:
                pass

            # Dynamic orchestration: scorecard + self-improvement + reflexion
            try:
                from app.core.runtime_flags import get_runtime_flags as _nv_rtf
                from app.evals.runtime_scorecard import RuntimeScorecard

                _nv_flags = _nv_rtf()
                _profile = agent_state.context.get(
                    "_runtime_profile"
                ) or agent_state.context.get("_observed_runtime_profile")
                if (
                    _nv_flags.dynamic_orchestration or _nv_flags.enable_runtime_scorecard
                ) and _profile is not None:
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
                        retrieval_result=agent_state.context.get("runtime_retrieval_evidence"),
                        cost_usd=agent_state.context.get("total_cost_usd"),
                        latency_ms=agent_state.context.get("_latency_ms"),
                        guardrail_violations=_guardrail_violations,
                    )
                    agent_state.context["scorecard"] = _scorecard_result.to_dict()
                    # Persist scorecard to eval_scorecards table
                    try:
                        _orch_persist = (
                            getattr(self._app_state, "orchestration_persistence", None)
                            if self._app_state
                            else None
                        )
                        if _orch_persist is not None:
                            import asyncio as _sc_asyncio

                            _sc_task = _sc_asyncio.ensure_future(
                                _orch_persist.persist_scorecard(_scorecard_result, profile=_profile)
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
                                    if self._app_state
                                    else None
                                )
                                if _orch_p is not None and hasattr(
                                    _orch_p, "persist_regression_case"
                                ):
                                    import asyncio as _rc_asyncio

                                    _rc_asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                                        _orch_p.persist_regression_case(
                                            {
                                                **_regression_candidate,
                                                "tenant_id": tenant_ctx.tenant_id,
                                            }
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
                            # BUG FIX: ImprovementAction is a lowercase StrEnum
                            # (e.g. "update_prompt_variant"), but the branches
                            # below match against UPPERCASE literals — without
                            # .upper() here every "X" in _action_type check is
                            # comparing against the wrong case and silently
                            # never matches, so no improvement action (prompt
                            # variant update, model routing switch, tool
                            # blacklisting) was ever actually dispatched.
                            _action_type = (
                                _action.action_type.value
                                if hasattr(_action, "action_type")
                                else str(_action)
                            ).upper()
                            if "STORE_REFLEXION_LESSON" in _action_type:
                                pass  # handled by reflexion_wirer in failure branch
                            elif "UPDATE_PROMPT_VARIANT" in _action_type:
                                # The variant's outcome is recorded exactly once, by
                                # the A/B feedback block after scoring. Recording it
                                # here as well counted every run twice.
                                pass
                            elif (
                                "SWITCH_MODEL" in _action_type
                                or "UPDATE_MODEL_ROUTING" in _action_type
                            ):
                                # N7a: Persist model switch recommendation to agent config
                                try:
                                    if self._app_state is not None and self._agent_id is not None:
                                        _agent_store = getattr(self._app_state, "agent_store", None)
                                        if _agent_store is not None and hasattr(
                                            _agent_store, "update_config"
                                        ):
                                            import asyncio as _mc_asyncio

                                            _mc_asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                                                _agent_store.update_config(
                                                    agent_id=self._agent_id,
                                                    tenant_ctx=tenant_ctx,
                                                    config_patch={
                                                        "model_downgrade_recommended": True,
                                                        "last_switch_reason": "low_eval_score",
                                                        "last_switch_score": _scorecard_result.overall_score,  # noqa: E501
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
                                            for _tc in getattr(_s, "tool_calls", None) or []:
                                                if isinstance(_tc, dict) and not _tc.get(
                                                    "success", True
                                                ):
                                                    _tn = _tc.get("tool_name", "")
                                                    if _tn and _tn not in _failed_tools:
                                                        _failed_tools.append(_tn)
                                        # MEM-01: a blacklist is its own flag, never a
                                        # synthetic failure count.
                                        for _ft in _failed_tools[:3]:
                                            await _tr_store.blacklist(
                                                tenant_id=tenant_ctx.tenant_id,
                                                tool_name=_ft,
                                                reason="blacklisted_by_self_improvement",
                                            )
                                        agent_state.context["_blacklisted_tools"] = _failed_tools[
                                            :3
                                        ]
                                except Exception as _bl_exc:
                                    from app.observability.logging import get_logger

                                    get_logger(__name__).warning(
                                        "tool_blacklist_record_failed",
                                        goal_id=agent_state.goal_id,
                                        error=str(_bl_exc)[:200],
                                    )
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
                            await self._emit(
                                _sse_sis.self_improvement_suggested(
                                    goal_id=agent_state.goal_id,
                                    suggestions=[
                                        a.action_type.value if hasattr(a, "action_type") else str(a)
                                        for a in _actions
                                    ],
                                )
                            )
                    except Exception:
                        pass
                    # Emit eval_score_recorded SSE (N12: gated on pattern SSE flag)
                    if _nv_flags.dynamic_orchestration or _nv_flags.enable_pattern_sse_events:
                        try:
                            from app.observability.runtime_decision_trace import RuntimeSSEEmitter

                            _sse_emitter = RuntimeSSEEmitter()
                            await self._emit(
                                _sse_emitter.eval_score_recorded(
                                    goal_id=agent_state.goal_id,
                                    overall_score=_scorecard_result.overall_score,
                                    scores=_scorecard_result.scores,
                                )
                            )
                        except Exception:
                            pass
            except Exception:
                pass
            # Guardrail check: final_output (Guardrails 2.0)
            _final_output_withheld = False
            if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx:
                try:
                    _g2_final_content = (
                        agent_state.cited_answer
                        or " ".join(s.output[:200] for s in agent_state.steps if s.output)
                        or agent_state.verification_feedback
                    )
                    if _g2_final_content:
                        # Seed the tenant's baseline BLOCK rules (injection / PII /
                        # secret) so the FINAL_OUTPUT gate actually enforces. The
                        # only other caller of ensure_default_rules is behind the
                        # default-off enable_guardrail_profile / dynamic_orchestration
                        # flags, so without this an unconfigured tenant had zero
                        # rules here and evaluate() returned blocked=False for
                        # everything — guardrail output enforcement was inert by
                        # default. Idempotent and cheap.
                        guardrails_engine.ensure_default_rules(tenant_ctx.tenant_id)
                        _g2_final_result = await guardrails_engine.evaluate(
                            content=str(_g2_final_content)[:2000],
                            layer=GuardrailLayer.FINAL_OUTPUT,
                            tenant_id=tenant_ctx.tenant_id,
                            goal_id=getattr(agent_state, "goal_id", None),
                        )
                        if _g2_final_result.get("blocked"):
                            agent_state.cited_answer = "[Output redacted by guardrail policy]"
                            _final_output_withheld = True
                        elif isinstance(
                            _g2_final_result.get("redacted_content"), str
                        ) and _g2_final_result["redacted_content"] != str(_g2_final_content)[:2000]:
                            # P8-1: a tenant REDACT rule's verdict was discarded.
                            agent_state.cited_answer = _g2_final_result["redacted_content"]
                            _final_output_withheld = True
                except Exception as _gv_exc:
                    # SAFE-4 (P0-15): an errored final-output guardrail must not
                    # let a high-risk answer through unredacted — fail closed.
                    if _guardrail_should_fail_closed(
                        agent_state.goal, agent_state.context.get("_risk_level")
                    ):
                        agent_state.cited_answer = "[Output redacted by guardrail policy]"
                        _final_output_withheld = True
                        self._logger.warning(
                            "verifier_guardrail_failed_closed", error=str(_gv_exc)
                        )

            # Phase 3 Track C: synthesize cited answer on success — never over an
            # answer the final-output guardrail withheld or redacted (P8-1: the
            # synthesis used to overwrite the redaction with the raw answer).
            if self._answer_synthesizer is not None and not _final_output_withheld:
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
                    await self._emit(
                        {
                            "type": "synthesis_complete",
                            "cited_answer": cited.answer[:2000],
                            "citations": [
                                {"text": c.text, "source": c.source} for c in cited.citations
                            ],
                        }
                    )
                except Exception as exc:
                    self._logger.debug("synthesis_failed", error=str(exc)[:60])

            # H-2: SelfOptimizerV2 result recording — feeds A/B experiment outcomes
            _self_opt_v2 = (
                getattr(self._app_state, "self_optimizer_v2", None) if self._app_state else None
            )
            if _self_opt_v2 and self._agent_id and isinstance(agent_state.context, dict):
                _arm = agent_state.context.get("_experiment_arm")
                if _arm:
                    _eval_scorecard = agent_state.context.get("eval_scorecard", {})
                    _eval_score: float | None = None
                    if hasattr(_eval_scorecard, "average_score"):
                        with contextlib.suppress(Exception):
                            _eval_score = float(_eval_scorecard.average_score())
                    elif isinstance(_eval_scorecard, dict):
                        _eval_score_raw = _eval_scorecard.get("average_score")
                        if _eval_score_raw is not None:
                            with contextlib.suppress(Exception):
                                _eval_score = float(_eval_score_raw)
                    # Recorded whenever an arm was assigned: a goal without an
                    # eval score is recorded as unscored (eval_score None) rather
                    # than dropped, so the goal counter and the arm's traffic stay
                    # truthful; the experiment only samples scored goals.
                    import asyncio as _asyncio

                    # One record per goal: a later terminal failure (e.g. budget
                    # exhausted after this verdict) must not record it again
                    # (AgentGraph._record_failed_experiment_goal, a05-F087-02).
                    agent_state.context["_experiment_outcome_recorded"] = True
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
            # MEM-29 / a05-F089-01: the arm-less ABTestingEngine is gone; the
            # live A/B loops are SelfOptimizerV2 (above) and PromptOptimizer.
        else:
            scorecard = None
            # FIX: On permanent failure (retry=False), roll back all registered actions
            # using the async method to guarantee each inverse completes before moving on.
            if not retry and self._rollback_engine is not None and len(self._rollback_engine) > 0:
                # The one goal-rollback path (also used for timeouts and
                # cancel-with-rollback): what was really undone vs skipped vs
                # failed is reported, never just "rolled back".
                from app.reliability.rollback import rollback_goal_side_effects

                _rb_summary = await rollback_goal_side_effects(
                    self._rollback_engine, trigger="verifier_failure", emit=self._emit
                )
                if _rb_summary is not None:
                    agent_state.context["rollback_report"] = _rb_summary
                    self._logger.info(
                        "agent_rollback_complete", **(_rb_summary.get("counts") or {})
                    )

            # Reflexion: store failure lesson (C1 fix — was incorrectly in success branch)
            try:
                from app.agent.reflexion_wirer import get_reflexion_wirer

                _rw = get_reflexion_wirer()
                import asyncio as _rf_asyncio

                _rf_asyncio.ensure_future(_rw.maybe_store_async(agent_state))  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
            except Exception:
                pass

            # H28: Also persist via OrchestrationPersistence (belt-and-suspenders)
            try:
                _orch_p = (
                    getattr(self._app_state, "orchestration_persistence", None)
                    if self._app_state
                    else None
                )
                if _orch_p is not None and hasattr(_orch_p, "persist_reflexion_lesson"):
                    import asyncio as _rl_asyncio

                    _rl_asyncio.ensure_future(_orch_p.persist_reflexion_lesson(agent_state))  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
            except Exception:
                pass

        # Feed eval result back to PromptOptimizer for A/B learning (BUG 4 fix)
        if scorecard is not None and hasattr(agent_state, "context"):
            _planner_variant_id = agent_state.context.get("planner_variant_id")
            _avg_score = scorecard.average_score()
            if self._prompt_optimizer is not None and _planner_variant_id:
                _cost = agent_state.context.get("total_cost_usd")
                _latency = agent_state.context.get("_latency_ms")
                try:
                    if getattr(self._prompt_optimizer, "db_mode", False) is True:
                        # One atomic UPDATE of the variant's aggregates, then a
                        # promotion decision gated on quality AND cost/latency.
                        _po_task = asyncio.create_task(
                            self._prompt_optimizer.arecord_result(
                                _planner_variant_id,
                                tenant_id=tenant_ctx.tenant_id,
                                eval_score=_avg_score,
                                cost_usd=float(_cost) if _cost is not None else None,
                                latency_ms=float(_latency) if _latency is not None else None,
                            )
                        )
                        self._background_tasks.add(_po_task)
                        _po_task.add_done_callback(self._background_tasks.discard)
                    else:
                        # DB-less optimizer: this process is the store (the
                        # legacy non-RLS persist_outcome was removed, MEM-28).
                        self._prompt_optimizer.record_result(
                            variant_id=_planner_variant_id,
                            eval_score=_avg_score,
                            cost_usd=float(_cost) if _cost is not None else None,
                            latency_ms=float(_latency) if _latency is not None else None,
                        )
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

        # Record episodic experience after goal completion (success or failure).
        # MEM-41: awaited (bounded) — a background task was cancelled by the
        # worker loop's teardown and the episode lost silently. A lost write
        # (False, timeout or error) is flagged on the goal.
        if self._episodic_memory is not None and tenant_ctx is not None:
            _ep_quality = float(
                agent_state.context.get("scorecard", {}).get("overall_score", 0.5)
                if isinstance(agent_state.context.get("scorecard"), dict)
                else 0.5
            )
            _ep_exc: BaseException | None = None
            try:
                _ep_ok = await asyncio.wait_for(
                    self._episodic_memory.record(
                        state=agent_state,
                        tenant_ctx=tenant_ctx,
                        quality_score=_ep_quality,
                    ),
                    timeout=10.0,
                )
            except Exception as exc:
                _ep_ok, _ep_exc = False, exc
            if _ep_ok is False:
                await self._memory_degraded(agent_state, "episodic_record", _ep_exc)

        # Procedural skills are learned once per goal from its terminal outcome
        # (success AND failure) by AgentGraph._learn_procedural_outcome (MEM-11).

        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # Routing (synchronous — LangGraph calls this synchronously)
    # ------------------------------------------------------------------
