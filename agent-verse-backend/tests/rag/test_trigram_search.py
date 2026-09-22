"""Typo-tolerance / lexical search coverage for the trigram (pg_trgm) and
PostgreSQL full-text search legs.

Two layers are exercised:
  1. The pure-Python in-memory fallback (`app.rag.store._trigram_score` and
     `KnowledgeStore.hybrid_search`), which is what actually runs in tests
     and as the no-DB fallback.
  2. The real SQL-leg orchestration in `app.rag.engine.hybrid_search`
     (FTS `to_tsvector`/`plainto_tsquery` and pg_trgm `similarity`/`%`),
     exercised against a scripted fake AsyncSession so the graceful-failure
     and query-shaping behaviour is covered without a real Postgres.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore, _trigram_score
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="trgm-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ═══════════════════════════════════════════════════════════════════════════
# In-memory trigram scoring — typo tolerance, short strings, non-alphanumeric
# ═══════════════════════════════════════════════════════════════════════════


class TestTrigramScoreTypoTolerance:
    def test_exact_match_scores_one(self) -> None:
        assert _trigram_score("retention policy", "retention policy") == pytest.approx(1.0)

    def test_single_character_substitution_still_scores_highly(self) -> None:
        # "retention" -> "retnetion" (one transposed pair)
        score = _trigram_score("retnetion policy", "the retention policy document")
        assert score > 0.3

    def test_single_character_deletion_tolerated(self) -> None:
        # "database" -> "databse" (missing 'a')
        score = _trigram_score("databse migration", "the database migration guide")
        assert score > 0.3

    def test_single_character_insertion_tolerated(self) -> None:
        # "connector" -> "connnector" (extra 'n')
        score = _trigram_score("connnector setup", "connector setup instructions")
        assert score > 0.3

    def test_two_edits_scores_lower_than_one_edit(self) -> None:
        text = "the quick brown fox jumps"
        one_edit = _trigram_score("quik brown", text)
        two_edits = _trigram_score("quik bruwn", text)
        assert one_edit >= two_edits

    def test_edit_distance_increases_degrade_score_monotonically(self) -> None:
        text = "authentication service configuration"
        exact = _trigram_score("authentication", text)
        one_typo = _trigram_score("authentification", text)  # +1 char
        two_typo = _trigram_score("authentificaton", text)  # +1 char, -1 char
        garbled = _trigram_score("xnthzntcatoin", text)
        assert exact >= one_typo >= two_typo >= garbled

    def test_completely_different_text_scores_zero(self) -> None:
        assert _trigram_score("database migration", "zebra unicorn galaxy") == 0.0

    def test_case_insensitive_matching(self) -> None:
        lower = _trigram_score("retention policy", "the retention policy document")
        mixed = _trigram_score("ReTeNtIoN PoLiCy", "the retention policy document")
        assert lower == pytest.approx(mixed)


class TestTrigramScoreShortQueries:
    """pg_trgm (and this Python fallback) needs 3+ characters to form even
    one trigram; shorter queries must degrade gracefully to zero, not error."""

    def test_two_character_query_scores_zero(self) -> None:
        assert _trigram_score("db", "database migration guide") == 0.0

    def test_one_character_query_scores_zero(self) -> None:
        assert _trigram_score("a", "database migration guide") == 0.0

    def test_empty_query_scores_zero(self) -> None:
        assert _trigram_score("", "database migration guide") == 0.0

    def test_exactly_three_characters_produces_one_trigram(self) -> None:
        # Exactly at the threshold: one trigram, present in the text.
        assert _trigram_score("cat", "the cat sat on the mat") > 0.0

    def test_three_character_query_no_match_scores_zero(self) -> None:
        assert _trigram_score("xyz", "the cat sat on the mat") == 0.0

    def test_short_target_text_also_handled(self) -> None:
        # Text shorter than 3 chars has no trigrams of its own either.
        assert _trigram_score("cats", "ca") == 0.0


class TestTrigramScoreNonAlphanumeric:
    def test_punctuation_in_query_does_not_crash(self) -> None:
        score = _trigram_score("c++ & c#!", "learning c++ and c# together")
        assert 0.0 <= score <= 1.0

    def test_query_of_only_punctuation_does_not_crash(self) -> None:
        score = _trigram_score("!!!???...", "some ordinary text content")
        assert 0.0 <= score <= 1.0

    def test_unicode_characters_do_not_crash(self) -> None:
        score = _trigram_score("café résumé", "visiting a café for résumé review")
        assert score > 0.0

    def test_emoji_in_query_does_not_crash(self) -> None:
        score = _trigram_score("great work 🎉", "great work on this project")
        assert 0.0 <= score <= 1.0

    def test_whitespace_only_query_scores_zero_or_low(self) -> None:
        score = _trigram_score("   ", "some ordinary text content")
        assert 0.0 <= score <= 1.0

    def test_numeric_query_matches_numeric_text(self) -> None:
        score = _trigram_score("v2.0.1", "release v2.0.1 changelog")
        assert score > 0.0


# ═══════════════════════════════════════════════════════════════════════════
# KnowledgeStore.hybrid_search — end-to-end typo-tolerant retrieval
# ═══════════════════════════════════════════════════════════════════════════


class TestHybridSearchTypoTolerance:
    def _populated_store(self) -> tuple[KnowledgeStore, str]:
        store = KnowledgeStore()
        col_id = store.create_collection(
            KnowledgeCollection(name="typo-test"), tenant_ctx=_CTX
        )
        store.ingest_chunk(
            Chunk(
                document_id="d1",
                content="the retention policy applies to all customer data",
                embedding=[1.0, 0.0, 0.0],
                chunk_index=0,
            ),
            collection_id=col_id,
            tenant_ctx=_CTX,
        )
        store.ingest_chunk(
            Chunk(
                document_id="d2",
                content="unrelated content about weather forecasting",
                embedding=[0.0, 1.0, 0.0],
                chunk_index=0,
            ),
            collection_id=col_id,
            tenant_ctx=_CTX,
        )
        return store, col_id

    def test_typo_riddled_query_still_ranks_relevant_chunk_first(self) -> None:
        store, col_id = self._populated_store()
        # "retention" -> "retenton" (dropped char), "policy" -> "polcy"
        neutral_vec = [0.0, 0.0, 0.0]
        results = store.hybrid_search("retenton polcy", neutral_vec, col_id, _CTX, top_k=2)
        assert results
        assert results[0].chunk_id and "retention" in results[0].content

    def test_short_query_falls_back_to_vector_signal_only(self) -> None:
        store, col_id = self._populated_store()
        # A 2-char query contributes zero trigram signal; vector similarity
        # alone must still drive ranking (no crash from the trigram leg).
        query_vec = [1.0, 0.0, 0.0]
        results = store.hybrid_search("re", query_vec, col_id, _CTX, top_k=2)
        assert len(results) == 2
        assert all(r.trigram_score == 0.0 for r in results)

    def test_non_alphanumeric_query_does_not_crash_hybrid_search(self) -> None:
        store, col_id = self._populated_store()
        results = store.hybrid_search("!!!@@@###", [0.0, 0.0, 0.0], col_id, _CTX, top_k=2)
        assert len(results) == 2  # still returns all chunks, just low/zero scored


# ═══════════════════════════════════════════════════════════════════════════
# Engine-level SQL legs — graceful failure and query-shaping
# ═══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _LexicalFakeSession:
    """Routes `session.execute` to canned rows for the FTS/trgm legs only
    (retrieval_mode="lexical" skips vector/BM25, so no other SQL shapes are
    exercised), and can be told to raise for one leg to simulate a Postgres
    error (e.g. tsquery syntax error, pg_trgm extension unavailable)."""

    def __init__(self, *, fts_rows=None, trgm_rows=None, raise_on: str = ""):
        self.fts_rows = fts_rows or []
        self.trgm_rows = trgm_rows or []
        self.raise_on = raise_on
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params or {}))
        if self.raise_on and self.raise_on in sql:
            raise RuntimeError(f"syntax error at or near \"{(params or {}).get('q', '')}\"")
        if "ts_rank_cd" in sql:
            return _Result(self.fts_rows)
        if "similarity(content" in sql:
            return _Result(self.trgm_rows)
        return _Result([])


class TestFTSTsqueryErrorHandling:
    @pytest.mark.asyncio
    async def test_tsquery_syntax_error_non_strict_swallowed(self) -> None:
        from app.rag.engine import hybrid_search

        # plainto_tsquery is normally injection-safe, but a corrupted index
        # or an unsupported operator can still raise server-side — the FTS
        # leg must degrade gracefully rather than blow up the whole search.
        session = _LexicalFakeSession(
            raise_on="ts_rank_cd",
            trgm_rows=[("c1", "fallback via trigram", {}, 0.4)],
        )
        results = await hybrid_search(
            session,
            query="some && ! query",
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        assert [r.chunk_id for r in results] == ["c1"]
        assert results[0].retrieval_legs == ["trigram"]

    @pytest.mark.asyncio
    async def test_tsquery_syntax_error_strict_raises_leg_error(self) -> None:
        from app.rag.engine import RetrievalLegExecutionError, hybrid_search

        session = _LexicalFakeSession(raise_on="ts_rank_cd")
        with pytest.raises(RetrievalLegExecutionError) as exc_info:
            await hybrid_search(
                session,
                query="broken (( query",
                query_embedding=None,
                collection_id="col-1",
                embedding_dim=1536,
                retrieval_mode="lexical",
                top_k=5,
                strict=True,
            )
        assert exc_info.value.leg == "fts"

    @pytest.mark.asyncio
    async def test_evidence_still_recorded_when_fts_leg_fails_non_strict(self) -> None:
        from app.rag.engine import hybrid_search

        session = _LexicalFakeSession(raise_on="ts_rank_cd")
        evidence: list[dict] = []
        await hybrid_search(
            session,
            query="anything",
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
            evidence=evidence,
        )
        fts_evidence = next(e for e in evidence if e["component"] == "fts")
        assert fts_evidence["result_count"] == 0


class TestTrigramLegErrorHandling:
    @pytest.mark.asyncio
    async def test_trgm_leg_error_non_strict_swallowed_fts_still_returns(self) -> None:
        from app.rag.engine import hybrid_search

        # e.g. pg_trgm extension not installed on this database.
        session = _LexicalFakeSession(
            raise_on="similarity(content",
            fts_rows=[("c1", "fts hit content", {}, 0.7)],
        )
        results = await hybrid_search(
            session,
            query="policy",
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        assert [r.chunk_id for r in results] == ["c1"]
        assert results[0].retrieval_legs == ["fts"]

    @pytest.mark.asyncio
    async def test_trgm_leg_error_strict_raises_leg_error(self) -> None:
        from app.rag.engine import RetrievalLegExecutionError, hybrid_search

        session = _LexicalFakeSession(raise_on="similarity(content")
        with pytest.raises(RetrievalLegExecutionError) as exc_info:
            await hybrid_search(
                session,
                query="policy",
                query_embedding=None,
                collection_id="col-1",
                embedding_dim=1536,
                retrieval_mode="lexical",
                top_k=5,
                strict=True,
            )
        assert exc_info.value.leg == "trgm"

    @pytest.mark.asyncio
    async def test_both_legs_fail_non_strict_returns_empty_no_crash(self) -> None:
        from app.rag.engine import hybrid_search

        session = AsyncMock()
        session.execute.side_effect = RuntimeError("db unavailable")
        results = await hybrid_search(
            session,
            query="policy",
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        assert results == []


class TestQueryShapingEdgeCases:
    @pytest.mark.asyncio
    async def test_special_characters_passed_as_bound_param_not_interpolated(self) -> None:
        # Query text must travel as a bound `:q` parameter (never string-
        # interpolated into the SQL), so quotes/semicolons/operators are
        # inherently injection-safe and must not crash query construction.
        from app.rag.engine import hybrid_search

        session = _LexicalFakeSession()
        dangerous_query = "'; DROP TABLE knowledge_chunks_1536; --"
        await hybrid_search(
            session,
            query=dangerous_query,
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        fts_call = next(c for c in session.calls if "ts_rank_cd" in c[0])
        assert fts_call[1]["q"] == dangerous_query
        assert dangerous_query not in fts_call[0]  # never inlined into the SQL text

    @pytest.mark.asyncio
    async def test_very_short_query_dispatched_without_special_casing(self) -> None:
        # No Python-side length guard exists for the SQL legs — a 2-char
        # query is sent through unchanged; Postgres/pg_trgm decides what to
        # do with it. Must not raise on the engine side either way.
        from app.rag.engine import hybrid_search

        session = _LexicalFakeSession(fts_rows=[], trgm_rows=[])
        results = await hybrid_search(
            session,
            query="ab",
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        assert results == []
        trgm_call = next(c for c in session.calls if "similarity(content" in c[0])
        assert trgm_call[1]["q"] == "ab"

    @pytest.mark.asyncio
    async def test_non_ascii_query_characters_pass_through(self) -> None:
        from app.rag.engine import hybrid_search

        session = _LexicalFakeSession(
            fts_rows=[("c1", "café résumé content", {}, 0.5)],
        )
        results = await hybrid_search(
            session,
            query="café résumé 日本語",
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        assert [r.chunk_id for r in results] == ["c1"]

    @pytest.mark.asyncio
    async def test_fts_language_config_is_fixed_to_english(self) -> None:
        # Documents current (fixed) behaviour: `to_tsvector`/`plainto_tsquery`
        # always use the 'english' text search configuration regardless of
        # the source document's actual language — there is no per-collection
        # language override wired through to this SQL.
        from app.rag.engine import hybrid_search

        session = _LexicalFakeSession(fts_rows=[])
        await hybrid_search(
            session,
            query="bonjour le monde",
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        fts_call = next(c for c in session.calls if "ts_rank_cd" in c[0])
        assert "to_tsvector('english'" in fts_call[0]
        assert "plainto_tsquery('english'" in fts_call[0]

    @pytest.mark.asyncio
    async def test_query_longer_than_500_chars_is_truncated(self) -> None:
        from app.rag.engine import hybrid_search

        session = _LexicalFakeSession(fts_rows=[])
        long_query = "word " * 200  # 1000 chars
        await hybrid_search(
            session,
            query=long_query,
            query_embedding=None,
            collection_id="col-1",
            embedding_dim=1536,
            retrieval_mode="lexical",
            top_k=5,
        )
        fts_call = next(c for c in session.calls if "ts_rank_cd" in c[0])
        assert len(fts_call[1]["q"]) == 500
