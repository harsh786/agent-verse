"""Guard: no new direct ``provider.complete(...)`` calls under ``app/``.

A direct call skips everything :func:`app.providers.guarded_completion.complete_decision`
(and the agent roles' ``complete_with_failover``) provide: the goal / tenant
budget, the token ledger, the bounded timeout and the per-model circuit breaker.
Many call sites did exactly that; they were migrated, and this scan keeps new
ones from creeping back.

Every remaining ``.complete(`` call outside ``app/providers/`` must be listed in
:data:`ALLOWED` under its ``"<path>::<enclosing qualname>"`` with the number of
calls and a reason. The test also fails on a stale entry (count changed or call
gone), so the list shrinks as debt is paid down. To fix a failure: route the
call through ``complete_decision(provider, request, role=..., tenant_ctx=... |
tenant_id=...)`` — do not add it here unless the receiver is not an LLM provider
or the call is itself a guarded wrapper.
"""

from __future__ import annotations

import ast
import collections
import pathlib

_APP = pathlib.Path(__file__).resolve().parents[2] / "app"

_WRAPPER = "guarded wrapper: delegates to the inner provider inside its own metering/tracing"
_NOT_LLM = "receiver is not an LLM provider"
_RAG = (
    "receives the RAG strategy LLM, a _BudgetedProvider (app/rag/gateway.py) that is "
    "metered by the RAG cost guard and routes through complete_decision(charge=False)"
)
_OWNED = "pending migration: file is owned by a concurrent change (HITL / workflows / insights)"
_DEBT = "pending migration: known direct call, not yet routed through complete_decision"

# "<path relative to app/>::<qualname>": (number of .complete( calls, reason)
ALLOWED: dict[str, tuple[int, str]] = {
    # ── guarded wrappers / not an LLM provider ────────────────────────────────
    "agent/nodes/llm_cost.py::ChargingProvider.complete": (1, _WRAPPER),
    "observability/traced_provider.py::TracedProvider.complete": (1, _WRAPPER),
    "ai_router/shadow_router.py::ShadowRouter.shadow_call": (3, _WRAPPER),
    "api/goals.py::submit_goal": (1, _NOT_LLM + " (idempotency record)"),
    "scaling/memory_tasks.py::process_due_memories": (1, _NOT_LLM + " (prospective memory)"),
    # ── RAG strategy runtime adapters (context.llm.provider is _BudgetedProvider) ─
    "rag/agentic/patterns/agentic.py::AgenticRAGRuntimeAdapter.execute": (1, _RAG),
    "rag/agentic/patterns/flare.py::FLARERAGRuntimeAdapter.execute": (3, _RAG),
    "rag/agentic/patterns/modular.py::ModularRAGRuntimeAdapter._expand": (1, _RAG),
    "rag/agentic/patterns/modular.py::ModularRAGRuntimeAdapter._grade": (1, _RAG),
    "rag/agentic/patterns/modular.py::ModularRAGRuntimeAdapter._synthesize": (1, _RAG),
    "rag/agentic/patterns/self_rag.py::SelfRAGRuntimeAdapter.execute": (2, _RAG),
    "rag/agentic/patterns/speculative.py::SpeculativeRAGRuntimeAdapter.execute.generate_draft": (
        1,
        _RAG,
    ),
    "rag/agentic/patterns/speculative.py::SpeculativeRAGRuntimeAdapter.execute.verify": (1, _RAG),
    # ── owned by concurrent changes ───────────────────────────────────────────
    "agent/debate.py::DebateOrchestrator.run.critique": (1, _OWNED),
    "agent/debate.py::DebateOrchestrator.run.propose": (1, _OWNED),
    "agent/debate.py::DebateOrchestrator.run.vote": (1, _OWNED),
    "agent/supervisor.py::SupervisorAgent._decompose": (1, _OWNED),
    "agent/supervisor.py::SupervisorAgent._synthesize": (1, _OWNED),
    "api/insights.py::analyze_failure": (1, _OWNED),
    "api/insights.py::natural_language_query": (1, _OWNED),
    "api/schedules.py::suggest_schedule": (1, _OWNED),
    # ── known debt: migrate these next ────────────────────────────────────────
    "agent/consensus.py::ConsensusVerifier.verify._run_verifier": (1, _DEBT),
    "agent/consensus.py::_run_judge": (1, _DEBT),
    "agent/goal_tree.py::_synthesize_goal_tree_results": (1, _DEBT),
    "agent/goal_tree.py::decompose_goal": (1, _DEBT),
    "agent/grounding.py::GroundingChecker.check": (1, _DEBT),
    "agent/nodes/reasoning_mixin.py::ReasoningMixin._node_refine": (1, _DEBT),
    "agent/patterns/few_shot_cot.py::FewShotCoTRuntime.execute": (1, _DEBT),
    "agent/patterns/peer_review.py::PeerReviewPattern.execute": (1, _DEBT),
    "agent/patterns/self_consistency.py::SelfConsistencyPattern.execute_with_evidence._one_sample": (
        1,
        _DEBT,
    ),
    "agent/patterns/self_refine.py::SelfRefinePattern.execute": (1, _DEBT),
    "agent/patterns/tree_of_thoughts.py::TreeOfThoughtsPattern._direct_answer": (1, _DEBT),
    "agent/patterns/tree_of_thoughts.py::TreeOfThoughtsPattern._evaluate_thoughts._eval_one": (
        1,
        _DEBT,
    ),
    "agent/patterns/tree_of_thoughts.py::TreeOfThoughtsPattern._expand_thought": (1, _DEBT),
    "agent/patterns/tree_of_thoughts.py::TreeOfThoughtsPattern._generate_thoughts._one_thought": (
        1,
        _DEBT,
    ),
    "agent/synthesis.py::AnswerSynthesizer._synthesize_with_llm": (1, _DEBT),
    "agent/workflow_executor.py::WorkflowExecutor._execute_step": (1, _DEBT),
    "agent/workflow_nodes.py::execute_decision_node": (1, _DEBT),
    "agent/workflow_planner.py::WorkflowPlanner.plan": (1, _DEBT),
    "api/collab.py::get_session_insights": (1, _DEBT),
    "api/model_registry.py::test_model": (1, _DEBT + " (operator model probe)"),
    "api/skills.py::run_skill_test": (1, _DEBT),
    "api/skills_runtime.py::execute_skill": (1, _DEBT),
    "chat/service.py::ChatService._llm_summarize": (1, _DEBT),
    "chat/service.py::ChatService._merge_summary": (1, _DEBT),
    "chat/service.py::ChatService.run_qa": (1, _DEBT),
    "chat/understanding.py::_llm_decompose": (1, _DEBT),
    "collab/agent_collab.py::AgentCollabSession.synthesize_consensus_llm": (1, _DEBT),
    "enterprise/simulation.py::SimulationRunner._stub_simulation": (1, _DEBT),
    "evals/ai_ops_runner.py::judge_case": (1, _DEBT),
    "evals/multi_turn_eval.py::MultiTurnEvaluator._score_turn": (1, _DEBT),
    "ingestion/parsers/vision_parser.py::VisionParser._describe_with_provider": (1, _DEBT),
    "intelligence/claim_decomposer.py::ClaimDecomposer.decompose": (1, _DEBT),
    "intelligence/eval_suite.py::LLMJudge.score": (1, _DEBT),
    "intelligence/meta_agent.py::MetaAgentPlanner.plan": (1, _DEBT),
    "intelligence/nli_checker.py::NLIChecker.check_consistency": (1, _DEBT),
    "intelligence/self_optimizer_v2.py::SelfOptimizerV2._generate_suggestion": (1, _DEBT),
    "multimodal/pipeline.py::MultimodalPipeline._describe_image": (1, _DEBT),
    "ocr/engine.py::OcrEngine._llm_vision_ocr": (1, _DEBT),
    "ocr/extractors/general.py::LlmStructuredExtractor.extract_async": (1, _DEBT),
    "orchestration/strategy_executor.py::DistributedStrategyExecutor.__call__.complete": (
        1,
        _DEBT,
    ),
    "perception/browser_agent.py::BrowserAgent.analyze_screenshot": (1, _DEBT),
    "proactive/planner.py::LLMProactivePlanner.apropose": (1, _DEBT),
    "rag/agentic/llm_query_transformer.py::LLMQueryTransformer._call": (1, _DEBT),
    "rag/agentic/patterns/agentic_chunking.py::AgenticChunkingPattern.extract_propositions": (
        1,
        _DEBT + " (legacy pattern API; provider passed in by the caller)",
    ),
    "rag/agentic/patterns/flare.py::FLAREPattern.execute": (2, _DEBT + " (legacy pattern API)"),
    "rag/agentic/patterns/raptor.py::RAPTORPattern.execute": (1, _DEBT + " (legacy pattern API)"),
    "rag/agentic/patterns/raptor.py::RAPTORPattern.execute._summarize_group": (
        1,
        _DEBT + " (legacy pattern API)",
    ),
    "rag/agentic/patterns/self_rag.py::SelfRAGPattern._complete_with_breaker": (
        1,
        _DEBT + " (legacy pattern API; breaker only, no budget)",
    ),
    "rag/agentic/patterns/speculative.py::SpeculativeRAGPattern.execute": (
        2,
        _DEBT + " (legacy pattern API)",
    ),
    "rag/agentic/query_expander.py::QueryExpander.expand_for_fusion_async": (1, _DEBT),
    "rag/agentic/query_reformulator.py::QueryReformulator.reformulate_async": (1, _DEBT),
    "rag/contextual_enricher.py::ContextualChunkEnricher.enrich_with_llm": (1, _DEBT),
    "rag/contextual_enricher.py::ContextualChunkEnricher.summarize_document": (1, _DEBT),
    "rag/engine.py::rerank_results": (1, _DEBT),
    "rag/engine.py::retrieve_hyde": (1, _DEBT),
    "rag/engine.py::retrieve_multi_hop": (1, _DEBT),
    "rag_platform/reranker.py::CitationVerifier.verify_citations": (1, _DEBT),
    "rag_platform/reranker.py::Reranker._llm_rerank": (1, _DEBT),
    "rag_platform/retriever.py::MinimalCitationVerifier._provider_entails": (1, _DEBT),
    "rag_platform/retriever.py::RAGRetriever.synthesize": (1, _DEBT),
    "skills_runtime/executor.py::SkillExecutor.execute": (1, _DEBT),
    "workflow/nl_trigger.py::NLTriggerResolver._llm_parse": (1, _DEBT),
}


def _scan() -> dict[str, int]:
    found: collections.Counter[str] = collections.Counter()
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        if rel.startswith("providers/"):
            continue  # the providers themselves and guarded_completion
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def walk(node: ast.AST, stack: list[str], rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    walk(child, [*stack, child.name])
                    continue
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "complete"
                ):
                    found[f"{rel}::{'.'.join(stack) or '<module>'}"] += 1
                walk(child, stack)

        walk(tree, [])
    return dict(found)


def test_no_unlisted_direct_llm_complete_calls() -> None:
    found = _scan()
    unlisted = sorted(
        f"{key} ({count} call(s))"
        for key, count in found.items()
        if key not in ALLOWED or ALLOWED[key][0] < count
    )
    assert not unlisted, (
        "Direct provider.complete() call(s) bypass budget, ledger, timeout and circuit "
        "breaker — route them through app.providers.guarded_completion.complete_decision:\n  "
        + "\n  ".join(unlisted)
    )


def test_allowlist_has_no_stale_entries() -> None:
    found = _scan()
    stale = sorted(
        f"{key} (listed {count}, found {found.get(key, 0)})"
        for key, (count, _reason) in ALLOWED.items()
        if found.get(key, 0) != count
    )
    assert not stale, "Update ALLOWED — these entries no longer match:\n  " + "\n  ".join(stale)


def test_every_allowlist_entry_has_a_reason() -> None:
    assert all(reason.strip() for _count, reason in ALLOWED.values())
