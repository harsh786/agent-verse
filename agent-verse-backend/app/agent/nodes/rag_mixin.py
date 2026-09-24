"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

from typing import Any

from app.agent.state import AgentState, GoalStatus
from app.rag.contracts import RAGExecutionResult, RAGStrategy, resolve_rag_strategy
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

from app.agent.graph_types import GraphState, RetrievalEntryPointError


class RAGMixin:
    """Mixin: _node_rag_retrieval, _node_rag_prime, _node_rag_remediate."""

    async def _node_rag_retrieval(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        context_parts: list[str] = []

        # H3: Check if a specific RAG strategy was assembled by the orchestration layer
        _rag_strategy = agent_state.context.get("_rag_strategy_override")
        if _rag_strategy is None:
            _runtime_profile = agent_state.context.get("_runtime_profile")
            if _runtime_profile is not None:
                _rag_strategy = getattr(
                    getattr(_runtime_profile, "rag_strategy", None), "strategy", None
                )
        requested_strategy_id = str(
            _rag_strategy
            if _rag_strategy is not None
            else agent_state.context.get(
                "retrieval_strategy",
                RAGStrategy.HYBRID.value,
            )
        )
        try:
            resolved_strategy = resolve_rag_strategy(requested_strategy_id)
        except Exception as exc:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = "Invalid retrieval strategy"
            agent_state.context["rag_retrieval_status"] = "failed"
            failure_event = {
                "type": "knowledge_retrieval_failed",
                "collections_searched": list(self._agent_collection_ids[:3]),
                "requested_strategy_id": requested_strategy_id,
                "status": "failed",
                "citations": [],
                "resolved_strategy_ids": [],
                "retrieval_legs": [],
                "strategy_trace": [],
            }
            agent_state.events.append(failure_event)
            await self._emit(failure_event)
            raise RetrievalEntryPointError("Invalid retrieval strategy") from exc
        agent_state.context["_requested_rag_strategy_id"] = requested_strategy_id
        agent_state.context["_active_rag_strategy"] = resolved_strategy.value
        agent_state.context["retrieval_strategy"] = resolved_strategy.value

        # N6e: chunking_strategy_selected SSE
        try:
            if self._event_callback is not None and _rag_strategy:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter

                _sse_cs = RuntimeSSEEmitter()
                await self._emit(
                    _sse_cs.chunking_strategy_selected(
                        goal_id=agent_state.goal_id,
                        content_type="text",
                        strategy=_rag_strategy or "semantic",
                        reason="configured canonical strategy",
                    )
                )
        except Exception:
            pass

        # 1. Execution memory: recall past winning plans (DB-backed async recall, BUG 2 fix)
        if self._exec_memory is not None:
            exec_plans: list[dict] = []
            try:
                exec_plans = await self._exec_memory.recall_async(
                    agent_state.goal,
                    tenant_id=tenant_ctx.tenant_id,
                    db=self._db_session_factory,
                    limit=3,
                )
            except Exception as _em_exc:
                self._logger.warning("exec_memory_recall_failed", error=str(_em_exc))
                exec_plans = self._exec_memory.recall(
                    goal_hint=agent_state.goal, tenant_ctx=tenant_ctx, top_k=3
                )
            if exec_plans:
                mem_text = "\n".join(f"- Past plan: {m.get('plan', [])}" for m in exec_plans)
                context_parts.append(f"[Past winning plans]\n{mem_text}")
                # D-20: stash structured records so the planner can forward them to
                # ContextPipeline.run (PromptBuilder consumes execution_memory as dicts).
                agent_state.context["_execution_memory_records"] = list(exec_plans)

        # 1b. Execution memory: recall past failure patterns to avoid repeating them.
        # DB-backed so a fresh worker/pod recalls failures earlier runs persisted
        # to execution_memory (success=FALSE); in-memory recall is the no-DB fallback.
        if self._exec_memory is not None:
            try:
                failures: list[dict] = []
                _recall_failures_async = getattr(
                    self._exec_memory, "recall_failures_async", None
                )
                if _recall_failures_async is not None:
                    try:
                        failures = await _recall_failures_async(
                            agent_state.goal,
                            tenant_id=tenant_ctx.tenant_id,
                            db=self._db_session_factory,
                            limit=3,
                        )
                    except Exception as _rf_exc:
                        self._logger.warning(
                            "exec_memory_recall_failures_failed", error=str(_rf_exc)
                        )
                        failures = self._exec_memory.recall_failures(
                            goal_hint=agent_state.goal, tenant_ctx=tenant_ctx, top_k=3
                        )
                else:
                    failures = self._exec_memory.recall_failures(
                        goal_hint=agent_state.goal, tenant_ctx=tenant_ctx, top_k=3
                    )
                if failures:
                    failure_lines = [
                        f"- {str(f.get('goal', f.get('goal_text', '')))[:100]}"
                        for f in failures[-3:]
                    ]
                    context_parts.append(
                        "[Previously Failed Approaches — Avoid These]\n" + "\n".join(failure_lines)
                    )
            except Exception:
                pass

        # 2. Long-term memory — async pgvector recall when embedder available (Task 4)
        if self._long_term_memory is not None:
            ltm = await self._long_term_memory.recall_async(
                query=agent_state.goal,
                tenant_ctx=tenant_ctx,
                top_k=3,
                db=self._db_session_factory,
                embedder=self._embedder,
            )
            if ltm:
                ltm_text = "\n".join(f"- {m.content}" for m in ltm)
                context_parts.append(f"[Domain knowledge]\n{ltm_text}")
                # D-20: stash structured records for ContextPipeline.run forwarding.
                agent_state.context["_long_term_memory_records"] = [
                    {"content": m.content} for m in ltm
                ]

        # 3. Collection retrieval through the tenant-aware gateway.
        # Collections the agent EXPLICITLY configured make grounding *required* (a
        # retrieval failure fails the goal). When none are configured we fall back
        # to auto-discovering the tenant's collections — but that is *ambient*: the
        # goal never asked to be grounded on them, so a failure there degrades to
        # best-effort instead of killing a goal that never requested RAG (otherwise
        # an unrelated collection created elsewhere in the tenant breaks the goal).
        search_collections = list(self._agent_collection_ids[:3])
        collections_explicit = bool(search_collections)
        if not search_collections and self._knowledge_store is not None:
            all_collections = await self._knowledge_store.list_collections_async(
                tenant_ctx=tenant_ctx
            )
            search_collections = [collection.collection_id for collection in all_collections[:3]]

        if search_collections:
            app_state = getattr(self._app_state, "state", self._app_state)
            gateway = self._retrieval_gateway or getattr(app_state, "retrieval_gateway", None)
            requested_strategy = str(
                agent_state.context.get("_requested_rag_strategy_id")
                or agent_state.context.get("retrieval_strategy")
                or RAGStrategy.HYBRID.value
            )
            raw_top_k = agent_state.context.get("retrieval_top_k", 3)
            retrieval_filters = agent_state.context.get("retrieval_filters", {})

            try:
                if gateway is None:
                    raise RuntimeError("Retrieval gateway is not configured")
                if not isinstance(raw_top_k, int) or isinstance(raw_top_k, bool) or raw_top_k < 1:
                    raise ValueError("Invalid retrieval top_k")
                if not isinstance(retrieval_filters, dict):
                    raise TypeError("Invalid retrieval filters")
                canonical_strategy = resolve_rag_strategy(requested_strategy)
                agent_state.context["retrieval_strategy"] = canonical_strategy.value
                agent_state.context["_active_rag_strategy"] = canonical_strategy.value
                gateway_results: list[RAGExecutionResult] = []
                for collection_id in search_collections:
                    gateway_result = await gateway.execute(
                        tenant_ctx,
                        collection_id=collection_id,
                        query=agent_state.goal,
                        strategy_id=requested_strategy,
                        top_k=raw_top_k,
                        filters=retrieval_filters,
                        execution_id=agent_state.goal_id,
                    )
                    if not isinstance(gateway_result, RAGExecutionResult):
                        raise TypeError("Retrieval gateway returned an invalid result")
                    gateway_results.append(gateway_result)
            except Exception as exc:
                # Degrade only an *ambient fallback* retrieval that failed while a
                # gateway was present (a strategy/data issue). A structurally missing
                # gateway still fails closed even for the fallback — a collection-backed
                # run must never silently answer ungrounded when retrieval is unwired.
                if not collections_explicit and gateway is not None:
                    # Ambient fallback retrieval failed — degrade to best-effort
                    # (empty grounding) so the goal proceeds; it never asked for RAG.
                    self._logger.warning(
                        "fallback_rag_retrieval_degraded",
                        error_type=type(exc).__name__,
                        strategy=requested_strategy,
                    )
                    agent_state.context["rag_retrieval_status"] = "degraded"
                    # Keep results aligned with collections for the strict zip below.
                    gateway_results = []
                    search_collections = []
                else:
                    agent_state.status = GoalStatus.FAILED
                    agent_state.error_message = "Required retrieval failed"
                    agent_state.context["rag_retrieval_status"] = "failed"
                    failure_event = {
                        "type": "knowledge_retrieval_failed",
                        "collections_searched": search_collections,
                        "requested_strategy_id": requested_strategy,
                        "status": "failed",
                        "citations": [],
                        "resolved_strategy_ids": [],
                        "retrieval_legs": [],
                        "strategy_trace": [],
                    }
                    agent_state.events.append(failure_event)
                    await self._emit(failure_event)
                    self._logger.warning(
                        "required_rag_retrieval_failed",
                        error_type=type(exc).__name__,
                        strategy=requested_strategy,
                    )
                    raise RetrievalEntryPointError("Required retrieval failed") from exc

            knowledge_citations = [
                {
                    **citation.model_dump(mode="json"),
                    "collection_id": collection_id,
                }
                for collection_id, result in zip(search_collections, gateway_results, strict=True)
                for citation in result.citations
            ]
            retrieval_legs = [
                leg.model_dump(mode="json")
                for result in gateway_results
                for leg in result.retrieval_legs
            ]
            strategy_trace = [
                trace.model_dump(mode="json")
                for result in gateway_results
                for trace in result.strategy_trace
            ]

            # Guardrails 2.0: retrieval evidence is surfaced verbatim to the
            # tenant via the knowledge_retrieved SSE event and
            # ``agent_state.provenance`` — including any raw LLM-generated
            # text a RAG strategy stashes for observability (fusion_rag's
            # ``QueryExpander`` and corrective's ``reformulate_query`` both
            # persist their query-expansion/reformulation completions
            # verbatim, in FOUR places: each citation's
            # ``metadata["fusion_queries"]``, each retrieval leg's ``query``
            # and ``metadata["query"]``, and each strategy-trace entry's
            # ``detail["query"]``). None of that path called into
            # guardrails_v2 at all (the import at the top of this module was
            # unused), so a secret or PII fragment the underlying LLM echoed
            # while expanding/reformulating a query reached the tenant
            # completely unredacted even with a compliance bundle enabled
            # (e.g. SOC2's "Block secrets in outputs" rule, which already
            # targets exactly this via
            # ``layers=["final_output", "tool_output"]``). Gate retrieval
            # evidence at TOOL_OUTPUT — the layer that rule already covers —
            # mirroring the (block/redact) handling the verifier's
            # FINAL_OUTPUT gate applies, rather than the executor's TOOL_OUTPUT
            # check further down, which evaluates but never acts on the
            # result. A single cache keyed by the raw string avoids
            # re-evaluating the same query variant once per place it appears.
            if _GUARDRAILS_AVAILABLE and guardrails_engine is not None:
                # Seed the tenant's baseline rules first (idempotent — see
                # verifier_mixin.py's identical call before its FINAL_OUTPUT
                # check). Without this, an unconfigured-or-not-yet-verified
                # tenant has zero rules at this point in the graph:
                # rag_retrieval runs immediately after initialize, before
                # verify (where ensure_default_rules was previously the ONLY
                # call site) ever seeds the baseline "secret-regex" rule that
                # actually covers TOOL_OUTPUT with a real credential regex
                # (AWS/OpenAI/Anthropic/GitHub/Google) — a bundle-only rule is
                # not enough on its own (e.g. SOC2's secrets rule only catches
                # the categories `_check_pii` implements, which does not
                # include AWS-style keys).
                guardrails_engine.ensure_default_rules(tenant_ctx.tenant_id)
                _sanitize_cache: dict[str, str] = {}

                async def _sanitize(raw_text: Any) -> Any:
                    if not isinstance(raw_text, str) or not raw_text:
                        return raw_text
                    if raw_text in _sanitize_cache:
                        return _sanitize_cache[raw_text]
                    try:
                        result = await guardrails_engine.evaluate(
                            content=raw_text[:2000],
                            layer=GuardrailLayer.TOOL_OUTPUT,
                            tenant_id=tenant_ctx.tenant_id,
                            goal_id=agent_state.goal_id,
                        )
                        if result.get("blocked"):
                            sanitized = "[redacted by guardrail policy]"
                        else:
                            sanitized = result.get("redacted_content", raw_text)
                    except Exception as _rag_guardrail_exc:
                        self._logger.warning(
                            "rag_evidence_guardrail_failed",
                            error=str(_rag_guardrail_exc)[:120],
                        )
                        sanitized = raw_text
                    _sanitize_cache[raw_text] = sanitized
                    return sanitized

                for citation in knowledge_citations:
                    citation["content"] = await _sanitize(citation.get("content"))
                    citation_metadata = citation.get("metadata")
                    if isinstance(citation_metadata, dict):
                        fusion_queries = citation_metadata.get("fusion_queries")
                        if isinstance(fusion_queries, list):
                            citation_metadata["fusion_queries"] = [
                                await _sanitize(fq) for fq in fusion_queries
                            ]
                for leg in retrieval_legs:
                    if isinstance(leg, dict):
                        if "query" in leg:
                            leg["query"] = await _sanitize(leg.get("query"))
                        leg_metadata = leg.get("metadata")
                        if isinstance(leg_metadata, dict) and "query" in leg_metadata:
                            leg_metadata["query"] = await _sanitize(leg_metadata.get("query"))
                for trace in strategy_trace:
                    detail = trace.get("detail") if isinstance(trace, dict) else None
                    if isinstance(detail, dict) and "query" in detail:
                        detail["query"] = await _sanitize(detail.get("query"))
            resolved_strategy_ids = sorted(
                {result.resolved_strategy_id.value for result in gateway_results}
            )
            knowledge_contexts = [
                (
                    f"[Collection: {citation['collection_id']}, "
                    f"source: {citation['source']}, score: {citation['score']:.2f}]\n"
                    f"{citation['content'][:600]}"
                )
                for citation in knowledge_citations
            ]
            context_parts.extend(knowledge_contexts)
            agent_state.context["rag_knowledge"] = "\n\n".join(knowledge_contexts)
            agent_state.context["rag_citations"] = knowledge_citations
            agent_state.context["rag_requested_strategy_id"] = requested_strategy
            agent_state.context["rag_resolved_strategy_ids"] = resolved_strategy_ids
            agent_state.context["rag_retrieval_legs"] = retrieval_legs
            agent_state.context["rag_strategy_trace"] = strategy_trace
            agent_state.context["rag_retrieval_status"] = "complete"
            agent_state.context["retrieval_attempted"] = True
            average_confidence = (
                sum(float(item["score"]) for item in knowledge_citations) / len(knowledge_citations)
                if knowledge_citations
                else 0.0
            )
            agent_state.context["runtime_retrieval_evidence"] = {
                "source": "knowledge_base",
                "confidence": average_confidence,
            }
            agent_state.context["retrieval_evidence_ref"] = f"goal:{agent_state.goal_id}:rag"
            agent_state.provenance.extend(knowledge_citations)
            success_event = {
                "type": "knowledge_retrieved",
                "collections_searched": search_collections,
                "chunks_found": len(knowledge_citations),
                "citations": knowledge_citations,
                "requested_strategy_id": requested_strategy,
                "resolved_strategy_ids": resolved_strategy_ids,
                "retrieval_legs": retrieval_legs,
                "strategy_trace": strategy_trace,
            }
            agent_state.events.append(success_event)
            await self._emit(success_event)

        rag_context = "\n\n".join(context_parts)

        # ── RAGTrace: record retrieval for observability ───────────────────────
        try:
            from app.rag.agentic.rag_trace import RAGTrace

            _rag_trace = RAGTrace(goal_id=agent_state.goal_id, tenant_id=tenant_ctx.tenant_id)
            _strategy = agent_state.context.get("_active_rag_strategy", "hybrid")
            _rag_trace.record_retrieval(
                strategy=_strategy,
                query=agent_state.goal[:200],
                result_count=len(context_parts),
                confidence=0.7,
                latency_ms=0,
            )
            if self._event_callback is not None:
                await self._emit(_rag_trace.to_sse_event())
        except Exception:
            pass

        # H21: Emit rag_strategy_selected SSE
        try:
            if self._event_callback is not None:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter

                _sse = RuntimeSSEEmitter()
                _strategy = agent_state.context.get(
                    "_active_rag_strategy"
                ) or agent_state.context.get("retrieval_strategy", "hybrid")
                await self._emit(
                    _sse.rag_strategy_selected(
                        goal_id=agent_state.goal_id,
                        strategy=str(_strategy),
                        sources=[],
                        reranker="score",
                    )
                )
        except Exception:
            pass

        return {"rag_context": rag_context}

    async def _node_rag_prime(self, state: GraphState) -> dict:
        """Alias for _node_rag_retrieval — spec-required name (doc-2 §9.1).

        Fires comprehensive first retrieval across all available sources before planning.
        Builds source_inventory for planner awareness. Activates web_search when KB is empty.
        """
        return await self._node_rag_retrieval(state)

    async def _node_rag_remediate(self, state: GraphState) -> dict:
        """Targeted re-retrieval when verification fails due to context gap (doc-2 §9.2).

        Triggered by _route() when verification_feedback contains gap signals:
          "insufficient", "unclear", "no information", "cannot determine",
          "lack of context", "not mentioned", "unknown"

        Strategy:
        1. Extract missing topic from verification_feedback
        2. Search with BROADER strategy (web if KB was empty before)
        3. Inject as [Remediation context: iteration N] into next plan
        4. Increment remediation_count (max 2 to prevent loops)
        """
        agent_state = state.get("agent_state")
        if agent_state is None:
            return {}

        if not agent_state.context.get("allow_rag_remediation", False):
            return {}

        try:
            from app.rag.agentic.context_gap_detector import ContextGapDetector
            from app.rag.agentic.retriever_tool import RetrieverTool

            feedback = agent_state.verification_feedback or ""
            detector = ContextGapDetector()
            missing_topic = detector.extract_missing_topic(feedback) or agent_state.goal[:100]
            app_state = getattr(self._app_state, "state", self._app_state)
            gateway = self._retrieval_gateway or getattr(app_state, "retrieval_gateway", None)
            strategy = RAGStrategy(
                str(agent_state.context.get("retrieval_strategy", RAGStrategy.HYBRID.value))
            )
            tool = RetrieverTool(retrieval_gateway=gateway)
            result = await tool.retrieve(
                query=missing_topic,
                tenant_ctx=agent_state.tenant_ctx,
                strategy=strategy,
                collection_ids=list(self._agent_collection_ids),
                top_k=7,
                min_confidence=0.2,
                execution_id=agent_state.goal_id,
            )

            count = agent_state.context.get("remediation_count", 0) + 1
            agent_state.context["remediation_count"] = count
            agent_state.context["remediation_context"] = (
                f"[Remediation context — iteration {count}]\n"
                f"Query: {missing_topic}\n"
                f"Source: {result.source} (confidence={result.confidence:.2f})\n\n"
                f"{result.context_text[:2000]}"
            )

        except Exception as exc:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = "Required retrieval remediation failed"
            agent_state.context["rag_remediation_status"] = "failed"
            event = {
                "type": "knowledge_retrieval_failed",
                "status": "failed",
                "phase": "remediation",
                "requested_strategy_id": str(
                    agent_state.context.get(
                        "retrieval_strategy",
                        RAGStrategy.HYBRID.value,
                    )
                ),
                "citations": [],
                "resolved_strategy_ids": [],
                "retrieval_legs": [],
                "strategy_trace": [],
            }
            agent_state.events.append(event)
            await self._emit(event)
            self._logger.warning(
                "rag_remediate_failed",
                error_type=type(exc).__name__,
            )
            raise RetrievalEntryPointError("Required retrieval remediation failed") from exc

        return {"agent_state": agent_state}
