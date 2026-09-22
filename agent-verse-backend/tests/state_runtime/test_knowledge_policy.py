"""Direct coverage for the graph-vs-vector/KB retrieval routing decision.

Two collaborating decision points had zero (or only incidental) direct
coverage before this file:

- ``KnowledgePolicyEngine.decide`` (app/state_runtime/knowledge_policy.py):
  given a ``KnowledgeRuntimeProfile`` and a classified query type, decides
  whether to use the vector KB, the knowledge graph, a web fallback, and
  memory.
- ``KGQueryEngine.select_strategy`` (app/state_runtime/kg_query_engine.py):
  classifies a raw query string into a KG traversal strategy
  (``entity``/``path``/``impact``/``none``), which is the thing that feeds
  ``query_type`` into the policy above in real call sites.

``KGQueryEngine.query``'s per-strategy traversal behaviour (entity
expansion, path traversal, neighbourhood) already has dedicated coverage in
``tests/state_runtime/test_kg_neighbourhood.py`` and
``test_kg_path_traversal.py``; this file focuses on the routing/classification
decision itself and the fallback when the graph store is unavailable.
"""
from __future__ import annotations

import pytest

from app.knowledge_graph.models import GraphNode, NodeType
from app.knowledge_graph.store import KnowledgeGraphStore
from app.orchestration.runtime_profile import KnowledgeRuntimeProfile
from app.state_runtime.kg_query_engine import KGQueryEngine
from app.state_runtime.knowledge_policy import KnowledgeDecision, KnowledgePolicyEngine


# ---------------------------------------------------------------------------
# KnowledgePolicyEngine.decide — routes to graph
# ---------------------------------------------------------------------------


class TestDecideRoutesToGraph:
    @pytest.mark.parametrize("query_type", ["relationship", "impact", "dependency", "causal"])
    def test_graph_eligible_query_types_use_graph_when_available(self, query_type: str) -> None:
        profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="populated", graph_strategy="entity"
        )
        decision = KnowledgePolicyEngine().decide(profile, query_type=query_type)
        assert decision.use_graph is True

    def test_graph_used_alongside_kb_not_instead_of_it(self) -> None:
        profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="populated", graph_strategy="entity"
        )
        decision = KnowledgePolicyEngine().decide(profile, query_type="relationship")
        assert decision.use_graph is True
        assert decision.use_kb is True


# ---------------------------------------------------------------------------
# KnowledgePolicyEngine.decide — routes to vector/KB only
# ---------------------------------------------------------------------------


class TestDecideRoutesToVector:
    def test_factual_query_does_not_use_graph_even_when_graph_available(self) -> None:
        profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="populated", graph_strategy="entity"
        )
        decision = KnowledgePolicyEngine().decide(profile, query_type="factual")
        assert decision.use_graph is False
        assert decision.use_kb is True

    def test_default_query_type_is_factual_and_skips_graph(self) -> None:
        profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="populated", graph_strategy="entity"
        )
        decision = KnowledgePolicyEngine().decide(profile)
        assert decision.use_graph is False

    def test_unrecognized_query_type_falls_back_to_vector_only(self) -> None:
        profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="populated", graph_strategy="entity"
        )
        decision = KnowledgePolicyEngine().decide(profile, query_type="summarization")
        assert decision.use_graph is False
        assert decision.use_kb is True


# ---------------------------------------------------------------------------
# KnowledgePolicyEngine.decide — ambiguous / mixed cases
# ---------------------------------------------------------------------------


class TestDecideAmbiguousCases:
    def test_relationship_query_but_graph_strategy_none_skips_graph(self) -> None:
        """Query type says 'use graph' but the strategy selector explicitly
        disabled graph usage for this goal — strategy wins."""
        profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="populated", graph_strategy="none"
        )
        decision = KnowledgePolicyEngine().decide(profile, query_type="relationship")
        assert decision.use_graph is False

    def test_relationship_query_but_graph_state_empty_skips_graph(self) -> None:
        """Query type says 'use graph' but there is nothing in the graph
        for this tenant — an empty graph must not be queried."""
        profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="empty", graph_strategy="entity"
        )
        decision = KnowledgePolicyEngine().decide(profile, query_type="impact")
        assert decision.use_graph is False

    def test_kb_empty_but_graph_populated_still_allows_graph(self) -> None:
        """Mixed state: vector KB has nothing, graph does. Graph-eligible
        query types should still route to graph even though use_kb is off."""
        profile = KnowledgeRuntimeProfile(
            kb_state="empty", graph_state="populated", graph_strategy="entity"
        )
        decision = KnowledgePolicyEngine().decide(profile, query_type="dependency")
        assert decision.use_kb is False
        assert decision.use_graph is True


# ---------------------------------------------------------------------------
# KnowledgePolicyEngine.decide — fallback behavior
# ---------------------------------------------------------------------------


class TestDecideFallbackBehaviour:
    def test_empty_kb_forces_web_fallback(self) -> None:
        profile = KnowledgeRuntimeProfile(kb_state="empty")
        decision = KnowledgePolicyEngine().decide(profile)
        assert decision.use_kb is False
        assert decision.use_web_fallback is True

    def test_explicit_web_fallback_required_flag_is_respected(self) -> None:
        profile = KnowledgeRuntimeProfile(kb_state="populated", web_fallback_required=True)
        decision = KnowledgePolicyEngine().decide(profile)
        assert decision.use_web_fallback is True

    def test_populated_kb_without_fallback_flag_skips_web(self) -> None:
        profile = KnowledgeRuntimeProfile(kb_state="populated", web_fallback_required=False)
        decision = KnowledgePolicyEngine().decide(profile)
        assert decision.use_web_fallback is False

    def test_memory_is_always_used_regardless_of_other_state(self) -> None:
        empty_profile = KnowledgeRuntimeProfile(kb_state="empty", graph_state="empty")
        full_profile = KnowledgeRuntimeProfile(
            kb_state="populated", graph_state="populated", graph_strategy="entity"
        )
        assert KnowledgePolicyEngine().decide(empty_profile).use_memory is True
        assert KnowledgePolicyEngine().decide(full_profile).use_memory is True

    def test_returns_knowledge_decision_dataclass(self) -> None:
        profile = KnowledgeRuntimeProfile()
        decision = KnowledgePolicyEngine().decide(profile)
        assert isinstance(decision, KnowledgeDecision)


# ---------------------------------------------------------------------------
# KGQueryEngine.select_strategy — the classifier that feeds query_type
# ---------------------------------------------------------------------------


class TestSelectStrategyRoutesToGraph:
    @pytest.mark.parametrize(
        "query",
        [
            "How is AgentVerse related to LangGraph?",
            "What is X associated with?",
            "Is A connected to B?",
        ],
    )
    def test_relationship_language_routes_to_entity_strategy(self, query: str) -> None:
        assert KGQueryEngine().select_strategy(query) == "entity"

    @pytest.mark.parametrize(
        "query",
        [
            "What depends on the backend service?",
            "What requires this module to run?",
            "Is this service built on FastAPI?",
        ],
    )
    def test_dependency_language_routes_to_path_strategy(self, query: str) -> None:
        assert KGQueryEngine().select_strategy(query) == "path"

    @pytest.mark.parametrize(
        "query",
        [
            "What is the impact of removing this table?",
            "What was caused by the outage?",
            "Why did the deploy fail?",
            "What is the root cause of the latency spike?",
        ],
    )
    def test_impact_and_causal_language_routes_to_impact_strategy(self, query: str) -> None:
        assert KGQueryEngine().select_strategy(query) == "impact"


class TestSelectStrategyRoutesAwayFromGraph:
    @pytest.mark.parametrize(
        "query",
        ["list all open tasks", "get the current status", "show me recent goals", "count agents"],
    )
    def test_simple_listing_verbs_skip_graph_entirely(self, query: str) -> None:
        assert KGQueryEngine().select_strategy(query) == "none"


class TestSelectStrategyAmbiguousDefault:
    def test_unclassified_query_defaults_to_entity(self) -> None:
        """Anything that isn't a listing verb and doesn't match a relation/
        dependency/impact pattern still falls through to 'entity' rather
        than silently doing nothing."""
        assert KGQueryEngine().select_strategy("Acme Corp widget factory") == "entity"

    def test_mixed_signal_query_prefers_relationship_over_dependency(self) -> None:
        """A query containing both a relationship phrase and a dependency
        phrase should route deterministically to the first pattern checked
        (relationship), not oscillate."""
        query = "Is the frontend related to the backend it depends on?"
        assert KGQueryEngine().select_strategy(query) == "entity"


# ---------------------------------------------------------------------------
# Fallback: graph store unavailable
# ---------------------------------------------------------------------------


class TestKGQueryEngineFallbackWhenStoreUnavailable:
    @pytest.mark.asyncio
    async def test_no_kg_store_configured_returns_none_strategy_result(self) -> None:
        engine = KGQueryEngine(kg_store=None)
        result = await engine.query("How is A related to B?", tenant_id="t1")
        assert result.strategy_used == "none"
        assert result.facts == []
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_store_error_during_traversal_degrades_to_empty_result_not_exception(self) -> None:
        """If the underlying graph store blows up mid-query (e.g. connection
        drop), the engine must degrade gracefully rather than propagating —
        callers combine this with vector results and can't have one leg
        crash the whole retrieval."""

        class _ExplodingStore:
            def query_nodes(self, **kwargs: object) -> list[GraphNode]:
                raise RuntimeError("graph store unavailable")

        engine = KGQueryEngine(kg_store=_ExplodingStore())  # type: ignore[arg-type]
        result = await engine.query("How is A related to B?", tenant_id="t1", strategy="entity")
        assert result.strategy_used == "entity"
        assert result.facts == []
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_explicit_strategy_bypasses_classification(self) -> None:
        store = KnowledgeGraphStore()
        store.add_node(GraphNode(node_id="n1", label="Acme", node_type=NodeType.CONCEPT, tenant_id="t1"))
        engine = KGQueryEngine(kg_store=store)
        # Text would normally classify as "none" (listing verb), but an
        # explicit strategy overrides the classifier.
        result = await engine.query("list Acme", tenant_id="t1", strategy="entity")
        assert result.strategy_used == "entity"
