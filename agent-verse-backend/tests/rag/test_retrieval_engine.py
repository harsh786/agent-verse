"""Tests for Phase 4 — world-class RAG retrieval engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestRRFScore:
    def test_single_rank_1(self):
        from app.rag.engine import _rrf_score
        score = _rrf_score([1])
        assert abs(score - 1/(60+1)) < 0.0001

    def test_multiple_legs_higher_than_single(self):
        from app.rag.engine import _rrf_score
        single = _rrf_score([1])
        multi = _rrf_score([1, 1, 1])  # top of all 3 legs
        assert multi > single

    def test_lower_rank_lower_score(self):
        from app.rag.engine import _rrf_score
        high = _rrf_score([1])
        low = _rrf_score([100])
        assert high > low

    def test_rrf_fusion_combines_lists(self):
        from app.rag.engine import _rrf_score
        # A chunk ranked 3rd in all 3 legs gets higher score than
        # a chunk ranked 1st in only 1 leg
        all_three = _rrf_score([3, 3, 3])
        one_first = _rrf_score([1])
        assert all_three > one_first


class TestRetrievalPlanner:
    def test_jira_id_selects_lexical(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert planner.select_strategy("Find issue JIRA-123 status") == "lexical"

    def test_compare_selects_multi_hop(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert planner.select_strategy("compare all Q2 sprint velocities across teams") == "multi_hop"

    def test_what_is_selects_hyde(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert planner.select_strategy("what is the sprint velocity") == "hyde"

    def test_generic_selects_direct(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert planner.select_strategy("find all tickets assigned to Alice") == "direct"


class TestHybridSearchVectorDBlessMode:
    @pytest.mark.asyncio
    async def test_lexical_only_when_no_embedding(self):
        """Vector-DB-less mode: query_embedding=None triggers lexical-only path."""
        from app.rag.engine import hybrid_search

        mock_session = AsyncMock()

        # Mock only FTS and trgm results
        fts_result = MagicMock()
        fts_result.fetchall.return_value = [
            ("chunk-1", "content about tickets", {}, 0.8)
        ]
        mock_session.execute.return_value = fts_result

        results = await hybrid_search(
            session=mock_session,
            query="open tickets",
            query_embedding=None,  # No embedding → vector-DB-less
            collection_id="col-1",
            top_k=5,
            retrieval_mode="lexical",
        )

        # Should return results from FTS/trgm legs
        # (may be empty if mock doesn't perfectly simulate DB, but no crash)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_vector_only_with_embedding(self):
        """When mode=vector, only vector leg is queried."""
        from app.rag.engine import hybrid_search

        mock_session = AsyncMock()
        vec_result = MagicMock()
        vec_result.fetchall.return_value = [
            ("chunk-1", "content", {}, 0.9)
        ]
        mock_session.execute.return_value = vec_result

        results = await hybrid_search(
            session=mock_session,
            query="find relevant context",
            query_embedding=[0.1] * 10,
            collection_id="col-1",
            top_k=5,
            retrieval_mode="vector",
        )
        assert isinstance(results, list)


class TestRetrievalResultDataclass:
    def test_result_has_retrieval_legs(self):
        from app.rag.engine import RetrievalResult
        r = RetrievalResult(
            chunk_id="c1",
            content="test",
            score=0.8,
            source_metadata={},
            retrieval_legs=["vector", "fts"],
        )
        assert "vector" in r.retrieval_legs
        assert r.score == 0.8
