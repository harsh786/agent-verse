"""D-6 (BK1): complete adaptive-RAG auto-selection coverage.

Before this change, `select_adaptive_strategy` had no auto-select branch for
RAPTOR, AGENTIC_CHUNKING, COLBERT, RAFT, or MODULAR, so they only ever ran
when explicitly requested. This module:

1. Adds a representative-query parametrized test proving each of the 5 is now
   reachable through the heuristic selector when certified/available.
2. Asserts every `RAGStrategy` member (other than the meta-strategy ADAPTIVE
   itself) is reachable through `select_adaptive_strategy` for *some* query
   and availability set -- i.e. no strategy is silently unreachable.
3. Proves COLBERT and RAFT degrade gracefully (fall through to the next-best
   certified strategy) rather than being selected when their dependency is
   not certified.
4. Proves the gateway's ADAPTIVE dispatch leg (`execute_core_strategy`) can
   actually run MODULAR/COLBERT/RAFT once selected -- these three were absent
   from `_REASONING_ADAPTER_FACTORIES`, so selecting them previously raised
   "adaptive selected an undispatchable strategy" instead of executing.
5. Regression-guards the insertion order of the new branches against the
   pre-existing heuristics they sit next to (MULTI_HOP's "compare", HYDE's
   "explain"/"what is" prefix, FUSION's "summarize").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.rag.agentic.patterns.adaptive import select_adaptive_strategy
from app.rag.contracts import (
    DIRECT_CORE_RAG_STRATEGIES,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
)
from app.rag.gateway import (
    ResolvedLLM,
    RetrievalExecutionContext,
    RetrievalRuntimeDependencies,
    execute_core_strategy,
)
from app.tenancy.context import PlanTier, TenantContext

ALL_STRATEGIES: tuple[RAGStrategy, ...] = tuple(
    strategy for strategy in RAGStrategy if strategy is not RAGStrategy.ADAPTIVE
)
SAFE_ONLY: tuple[RAGStrategy, ...] = (RAGStrategy.NAIVE, RAGStrategy.HYBRID)

TENANT = TenantContext(tenant_id="tenant-1", plan=PlanTier.PROFESSIONAL, api_key_id="key-1")

# ---------------------------------------------------------------------------
# 1 & 2: every non-ADAPTIVE strategy is reachable, with a representative query
# for the 5 that previously had no auto-select branch (and their existing
# neighbours, to lock in relative ordering).
# ---------------------------------------------------------------------------

# strategy -> (query, availability tuple the query is evaluated against)
QUERY_AND_AVAILABILITY_BY_STRATEGY: dict[RAGStrategy, tuple[str, tuple[RAGStrategy, ...]]] = {
    RAGStrategy.MEMORY_AUGMENTED: (
        "remember what we discussed about the onboarding flow",
        ALL_STRATEGIES,
    ),
    RAGStrategy.CODE: ("what does parse_request_id() do", ALL_STRATEGIES),
    RAGStrategy.GRAPH: ("show connected relationships in the graph", ALL_STRATEGIES),
    RAGStrategy.WEB_AUGMENTED: ("what is trending on the internet right now", ALL_STRATEGIES),
    RAGStrategy.SELF_RAG: ("fact-check this claim about the retention period", ALL_STRATEGIES),
    RAGStrategy.SPECULATIVE: ("give me a quick answer on the retention period", ALL_STRATEGIES),
    RAGStrategy.FLARE: (
        "write a comprehensive report on our data retention policy",
        ALL_STRATEGIES,
    ),
    RAGStrategy.FUSION: ("what are the pros and cons of the retention options", ALL_STRATEGIES),
    # --- previously unreachable (D-6 / BK1) ---
    RAGStrategy.RAPTOR: (
        "give a hierarchical summary overview of the entire knowledge base",
        ALL_STRATEGIES,
    ),
    RAGStrategy.AGENTIC: (
        "investigate why the deployment failed and gather the evidence",
        ALL_STRATEGIES,
    ),
    RAGStrategy.AGENTIC_CHUNKING: (
        "give me a side-by-side tabular comparison of the two plans",
        ALL_STRATEGIES,
    ),
    RAGStrategy.RAFT: (
        "explain this with grounding from our fine-tuned model",
        ALL_STRATEGIES,
    ),
    RAGStrategy.MULTI_HOP: ("compare retention across tenants", ALL_STRATEGIES),
    RAGStrategy.HYDE: ("what is dynamic orchestration", ALL_STRATEGIES),
    RAGStrategy.CORRECTIVE: ("please verify this is correct", ALL_STRATEGIES),
    RAGStrategy.COLBERT: (
        "find the exact verbatim phrase matching clause 5.2",
        ALL_STRATEGIES,
    ),
    RAGStrategy.MODULAR: (
        "run this through the modular pipeline",
        ALL_STRATEGIES,
    ),
    # --- default ladder ---
    RAGStrategy.HYBRID: ("list all active agents", ALL_STRATEGIES),
    RAGStrategy.NAIVE: ("list all active agents", (RAGStrategy.NAIVE,)),
}


def test_every_query_and_availability_fixture_covers_every_non_adaptive_strategy() -> None:
    """Fail loudly if a new/renamed strategy silently has no fixture at all."""
    expected = {strategy for strategy in RAGStrategy if strategy is not RAGStrategy.ADAPTIVE}
    assert set(QUERY_AND_AVAILABILITY_BY_STRATEGY) == expected


@pytest.mark.parametrize(
    ("strategy", "query_and_availability"),
    list(QUERY_AND_AVAILABILITY_BY_STRATEGY.items()),
    ids=[strategy.value for strategy in QUERY_AND_AVAILABILITY_BY_STRATEGY],
)
def test_every_strategy_is_auto_selectable_for_its_representative_query(
    strategy: RAGStrategy,
    query_and_availability: tuple[str, tuple[RAGStrategy, ...]],
) -> None:
    query, availability = query_and_availability
    decision = select_adaptive_strategy(query, availability)
    assert decision.strategy is strategy
    assert decision.reason


# ---------------------------------------------------------------------------
# 3: COLBERT / RAFT degrade gracefully (never raise) when uncertified.
# ---------------------------------------------------------------------------


def test_colbert_query_falls_through_when_colbert_uncertified() -> None:
    decision = select_adaptive_strategy(
        "find the exact verbatim phrase matching clause 5.2",
        SAFE_ONLY,
    )
    assert decision.strategy is not RAGStrategy.COLBERT
    assert decision.strategy is RAGStrategy.HYBRID
    assert "colbert_unavailable" in decision.reason


def test_raft_query_falls_through_when_raft_uncertified() -> None:
    decision = select_adaptive_strategy(
        "explain this with grounding from our fine-tuned model",
        SAFE_ONLY,
    )
    assert decision.strategy is not RAGStrategy.RAFT
    assert decision.strategy is RAGStrategy.HYBRID
    assert "raft_unavailable" in decision.reason


def test_agentic_chunking_and_raptor_fall_through_when_uncertified() -> None:
    raptor_decision = select_adaptive_strategy(
        "give a hierarchical summary overview of the entire knowledge base",
        SAFE_ONLY,
    )
    assert raptor_decision.strategy is RAGStrategy.HYBRID
    assert "raptor_unavailable" in raptor_decision.reason

    chunking_decision = select_adaptive_strategy(
        "give me a side-by-side tabular comparison of the two plans",
        SAFE_ONLY,
    )
    assert chunking_decision.strategy is RAGStrategy.HYBRID
    assert "agentic_chunking_unavailable" in chunking_decision.reason


def test_modular_query_falls_through_when_modular_uncertified() -> None:
    decision = select_adaptive_strategy(
        "run this through the modular pipeline",
        SAFE_ONLY,
    )
    assert decision.strategy is RAGStrategy.HYBRID
    assert "modular_unavailable" in decision.reason


# ---------------------------------------------------------------------------
# Regression guard: the new branches must not shadow the pre-existing,
# already-tested heuristics they were inserted next to.
# ---------------------------------------------------------------------------


def test_new_branches_do_not_shadow_existing_neighbours() -> None:
    # FUSION's "summarize the various different aspects" must still win over
    # the new RAPTOR "overview/summary" heuristic (RAPTOR is checked after
    # FUSION, and its trigger phrases are deliberately more specific).
    fusion = select_adaptive_strategy(
        "summarize the various different aspects of the policy", ALL_STRATEGIES
    )
    assert fusion.strategy is RAGStrategy.FUSION

    # MULTI_HOP's "compare ... across" must still win when the query is not
    # also tabular/side-by-side shaped (AGENTIC_CHUNKING is checked first but
    # its terms are disjoint from this phrasing).
    multi_hop = select_adaptive_strategy("compare retention across tenants", ALL_STRATEGIES)
    assert multi_hop.strategy is RAGStrategy.MULTI_HOP

    # HYDE's "what is" / "explain" prefix must still win for a plain abstract
    # question that carries no grounding/fine-tuned-model signal (RAFT is
    # checked first but requires that additional signal).
    hyde = select_adaptive_strategy("what is dynamic orchestration", ALL_STRATEGIES)
    assert hyde.strategy is RAGStrategy.HYDE

    # A plain "explain ..." query with no RAFT signal also still reaches HYDE.
    hyde_explain = select_adaptive_strategy("explain dynamic orchestration", ALL_STRATEGIES)
    assert hyde_explain.strategy is RAGStrategy.HYDE

    # Generic queries are still unaffected -> HYBRID default.
    default = select_adaptive_strategy("list all active agents", ALL_STRATEGIES)
    assert default.strategy is RAGStrategy.HYBRID


# ---------------------------------------------------------------------------
# 4: the gateway's ADAPTIVE leg can actually dispatch what the selector
# returns for the 5 completed strategies -- proving "selecting it runs, not
# raises" end to end, not just at the pure-selector level.
# ---------------------------------------------------------------------------


def test_raptor_and_agentic_chunking_are_direct_core_strategies() -> None:
    # These dispatch through execute_core_strategy's precomputed-index branch
    # (see app/rag/gateway.py), which only runs for DIRECT_CORE_RAG_STRATEGIES
    # members -- confirming the D-7 gateway path is still wired for both.
    assert RAGStrategy.RAPTOR in DIRECT_CORE_RAG_STRATEGIES
    assert RAGStrategy.AGENTIC_CHUNKING in DIRECT_CORE_RAG_STRATEGIES


def _adaptive_context(
    available: tuple[RAGStrategy, ...],
    strategy_llms: dict[RAGStrategy, ResolvedLLM],
) -> RetrievalExecutionContext:
    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(SimpleNamespace())

    return RetrievalExecutionContext(
        tenant_context=TENANT,
        strategy=RAGStrategy.ADAPTIVE,
        filters={},
        dependencies=RetrievalRuntimeDependencies(
            embedder=SimpleNamespace(),
            llm=ResolvedLLM(provider=SimpleNamespace(), model="fallback-model"),
            graph_capability=None,
            search_capability=None,
            policy_services=(),
            available_strategies=available,
            strategy_llms=strategy_llms,
        ),
        _db_operation_runner=runner,
    )


@pytest.mark.parametrize(
    ("query", "strategy", "adapter_path"),
    [
        (
            "run this through the modular pipeline",
            RAGStrategy.MODULAR,
            "app.rag.contracts.ModularRAGRuntimeAdapter.execute",
        ),
        (
            "find the exact verbatim phrase matching clause 5.2",
            RAGStrategy.COLBERT,
            "app.rag.contracts.ColBERTRAGRuntimeAdapter.execute",
        ),
        (
            "explain this with grounding from our fine-tuned model",
            RAGStrategy.RAFT,
            "app.rag.contracts.RAFTRAGRuntimeAdapter.execute",
        ),
    ],
    ids=["modular", "colbert", "raft"],
)
async def test_adaptive_leg_dispatches_newly_wired_strategies(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    strategy: RAGStrategy,
    adapter_path: str,
) -> None:
    observed: dict[str, Any] = {}

    async def fake_execute(
        self: Any,
        request: RAGExecutionRequest,
        context: RetrievalExecutionContext,
    ) -> RAGExecutionResult:
        observed["strategy"] = context.strategy
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=strategy,
        )

    monkeypatch.setattr(adapter_path, fake_execute)

    context = _adaptive_context(
        available=(strategy, RAGStrategy.HYBRID),
        strategy_llms={},
    )
    request = RAGExecutionRequest(
        tenant_id=TENANT.tenant_id,
        query=query,
        requested_strategy_id="adaptive",
        collection_id="collection-1",
    )

    result = await execute_core_strategy(RAGStrategy.ADAPTIVE, request, context)

    assert observed["strategy"] is strategy
    assert result.resolved_strategy_id is RAGStrategy.ADAPTIVE
    decision_trace = result.strategy_trace[0]
    assert decision_trace.detail["selected_strategy"] == strategy.value
