"""D-6: adaptive auto-selection reaches the reasoning strategies.

The live auto-selector (`select_adaptive_strategy`) must be able to choose the
reasoning strategies (FUSION / SELF_RAG / SPECULATIVE / FLARE) when the query
warrants, while still degrading through the safe HYBRID / NAIVE ladder when the
requested capability is not certified for the tenant. The gateway adaptive leg
must then dispatch whatever strategy the selector returns — including a
non-core reasoning strategy — rather than only the core seven.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.rag.agentic.patterns.adaptive import select_adaptive_strategy
from app.rag.contracts import (
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
# Selector heuristics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("fact-check this claim about the retention period", RAGStrategy.SELF_RAG),
        ("is this accurate: records are kept for seven years", RAGStrategy.SELF_RAG),
        ("give me a quick answer on the retention period", RAGStrategy.SPECULATIVE),
        ("what is the retention period, briefly", RAGStrategy.SPECULATIVE),
        ("write a comprehensive report on our data retention policy", RAGStrategy.FLARE),
        ("draft a detailed essay about compliance obligations", RAGStrategy.FLARE),
        ("what are the pros and cons of the retention options", RAGStrategy.FUSION),
        ("summarize the various different aspects of the policy", RAGStrategy.FUSION),
    ],
)
def test_reasoning_strategies_are_auto_selectable(
    query: str,
    expected: RAGStrategy,
) -> None:
    decision = select_adaptive_strategy(query, ALL_STRATEGIES)
    assert decision.strategy is expected
    assert decision.reason


@pytest.mark.parametrize(
    "query",
    [
        "fact-check this claim about the retention period",
        "give me a quick answer on the retention period",
        "write a comprehensive report on our data retention policy",
        "what are the pros and cons of the retention options",
    ],
)
def test_reasoning_signals_degrade_to_hybrid_when_uncertified(query: str) -> None:
    decision = select_adaptive_strategy(query, SAFE_ONLY)
    assert decision.strategy is RAGStrategy.HYBRID
    assert "unavailable" in decision.reason
    assert decision.reason.endswith("selected_default_hybrid")


def test_reasoning_signals_degrade_to_naive_when_only_naive() -> None:
    decision = select_adaptive_strategy(
        "write a comprehensive report on retention",
        (RAGStrategy.NAIVE,),
    )
    assert decision.strategy is RAGStrategy.NAIVE
    assert decision.reason.endswith("selected_naive")


def test_existing_core_selection_is_unchanged() -> None:
    # graph signal, graph uncertified -> default hybrid (regression guard)
    graph = select_adaptive_strategy("show graph relationships for retention", SAFE_ONLY)
    assert graph.strategy is RAGStrategy.HYBRID
    assert graph.reason == "graph_unavailable; selected_default_hybrid"
    # abstract "what is" query -> HyDE
    hyde = select_adaptive_strategy("what is dynamic orchestration", ALL_STRATEGIES)
    assert hyde.strategy is RAGStrategy.HYDE
    # comparison query -> multi-hop
    multi = select_adaptive_strategy("compare retention across tenants", ALL_STRATEGIES)
    assert multi.strategy is RAGStrategy.MULTI_HOP
    # unremarkable query -> hybrid default
    default = select_adaptive_strategy("list all active agents", ALL_STRATEGIES)
    assert default.strategy is RAGStrategy.HYBRID


# ---------------------------------------------------------------------------
# Gateway adaptive leg dispatches a selected reasoning strategy
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
async def test_adaptive_leg_dispatches_selected_reasoning_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}
    reasoning_provider = SimpleNamespace()

    async def fake_execute(
        self: Any,
        request: RAGExecutionRequest,
        context: RetrievalExecutionContext,
    ) -> RAGExecutionResult:
        observed["strategy"] = context.strategy
        observed["model"] = context.llm.model if context.llm else None
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=RAGStrategy.SELF_RAG,
        )

    monkeypatch.setattr(
        "app.rag.contracts.SelfRAGRuntimeAdapter.execute",
        fake_execute,
    )

    context = _adaptive_context(
        available=(RAGStrategy.SELF_RAG, RAGStrategy.HYBRID),
        strategy_llms={
            RAGStrategy.SELF_RAG: ResolvedLLM(provider=reasoning_provider, model="self-rag-model"),
        },
    )
    request = RAGExecutionRequest(
        tenant_id=TENANT.tenant_id,
        query="fact-check this claim about the retention period",
        requested_strategy_id="adaptive",
        collection_id="collection-1",
    )

    result = await execute_core_strategy(RAGStrategy.ADAPTIVE, request, context)

    assert observed["strategy"] is RAGStrategy.SELF_RAG
    assert observed["model"] == "self-rag-model"
    assert result.resolved_strategy_id is RAGStrategy.ADAPTIVE
    decision = result.strategy_trace[0]
    assert decision.action == "adaptive_selection"
    assert decision.detail["selected_strategy"] == "self_rag"
