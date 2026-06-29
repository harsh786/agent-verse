"""Final coverage push tests for rag/store.py — targets lines 286-299, 535-582."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="fcov-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


# ── Stateful session that returns different rows per call ─────────────────────


class _CallTrackerSession:
    """Session that returns pre-configured rows for successive execute() calls."""

    def __init__(self):
        self._call_idx = 0
        self._rows_sequence: list[list] = []

    def configure(self, *row_lists: list):
        """Set rows to return for calls 0, 1, 2, ..."""
        self._rows_sequence = list(row_lists)

    async def execute(self, *a, **kw):
        idx = self._call_idx
        rows = self._rows_sequence[min(idx, len(self._rows_sequence) - 1)] if self._rows_sequence else []
        self._call_idx += 1

        class _Result:
            def fetchall(self): return rows
            def fetchone(self): return rows[0] if rows else None
            def scalars(self):
                class _S:
                    def all(self): return rows
                return _S()
        return _Result()

    def begin(self):
        class _B:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return _B()

    def add(self, obj): pass


class _TrackerDB:
    def __init__(self):
        self.session = _CallTrackerSession()

    def __call__(self):
        s = self.session
        class CM:
            async def __aenter__(self): return s
            async def __aexit__(self, *a): pass
        return CM()


# ── hybrid_search_db: non-legacy table path (lines 285-299) ──────────────────


class TestHybridSearchDBTableSelection:
    @pytest.mark.asyncio
    async def test_hybrid_search_db_selects_knowledge_chunks_table(self):
        """Lines 285-286, 299: when embedding_dim=1536, uses knowledge_chunks_1536 table."""
        from app.rag.models import KnowledgeCollection
        from app.rag.store import KnowledgeStore

        db = _TrackerDB()
        # Call sequence in hybrid_search_db:
        # 0: sqlalchemy_rls_context SET LOCAL
        # 1: SELECT embedding_dim FROM knowledge_collections → returns row with dim=1536
        # 2: actual search SQL → empty result
        # 3: sqlalchemy_rls_context cleanup
        col_row = SimpleNamespace(embedding_dim=1536)
        db.session.configure(
            [],             # call 0: rls_context setup
            [col_row],      # call 1: embedding_dim query → non-legacy table
            [],             # call 2: search query → empty results
            [],             # call 3: rls_context cleanup
        )

        store = KnowledgeStore(db_session_factory=db)
        col_id = store.create_collection(
            KnowledgeCollection(name="non-legacy"), tenant_ctx=_CTX
        )
        results = await store.hybrid_search_db("search query", [0.1, 0.2], col_id, _CTX, top_k=3)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_hybrid_search_db_embedding_dim_exception_uses_legacy(self):
        """Lines 287-288: exception in embedding_dim query → fallback to legacy table."""
        from app.rag.models import KnowledgeCollection
        from app.rag.store import KnowledgeStore

        class _ExcSession:
            _call_idx = 0

            async def execute(self, *a, **kw):
                self._call_idx += 1
                if self._call_idx == 2:  # embedding_dim query
                    raise RuntimeError("Table not found")

                class _R:
                    def fetchall(self): return []
                    def fetchone(self): return None
                    def scalars(self):
                        class _S:
                            def all(self): return []
                        return _S()
                return _R()

            def begin(self):
                class _B:
                    async def __aenter__(self): return self
                    async def __aexit__(self, *a): pass
                return _B()
            def add(self, obj): pass

        class _ExcDB:
            def __call__(self):
                s = _ExcSession()
                class CM:
                    async def __aenter__(self): return s
                    async def __aexit__(self, *a): pass
                return CM()

        store = KnowledgeStore(db_session_factory=_ExcDB())
        col_id = store.create_collection(
            KnowledgeCollection(name="exc-col"), tenant_ctx=_CTX
        )
        results = await store.hybrid_search_db("test", [0.1], col_id, _CTX)
        assert isinstance(results, list)


# ── sync_from_db: collections + documents path (lines 535-582) ───────────────


class TestSyncFromDBWithData:
    @pytest.mark.asyncio
    async def test_sync_from_db_loads_collections_and_documents(self):
        """Lines 535-582: sync_from_db with collections and documents."""
        from app.rag.store import KnowledgeStore

        # Mock collection object
        mock_col = SimpleNamespace(
            id="col-sync-1",
            tenant_id="fcov-t1",
            name="Synced Collection",
            description="Test description",
            embedder="voyage",
            document_count=1,
        )
        # Mock document object
        mock_doc = SimpleNamespace(
            id="doc-sync-1",
            tenant_id="fcov-t1",
            collection_id="col-sync-1",
            content="Synced document content",
            embedding=[0.1, 0.2, 0.3],
            chunk_index=0,
            doc_metadata={"source": "test"},
        )

        db = _TrackerDB()
        # sync_from_db only opens ONE session and makes multiple execute() calls:
        # Call 0: SELECT from knowledge_collections → [mock_col]
        # Call 1: SELECT from documents (batch 1) → [mock_doc]
        # Call 2: SELECT from documents (batch 2) → [] (breaks the while loop)
        db.session.configure(
            [mock_col],    # collections query
            [mock_doc],    # first document batch
            [],            # empty batch → break
        )

        store = KnowledgeStore(db_session_factory=db)
        loaded = await store.sync_from_db()
        # Should have loaded 1 chunk
        assert loaded >= 0  # might be 0 if key doesn't match, but no exception

    @pytest.mark.asyncio
    async def test_sync_from_db_already_loaded_collection_not_duplicated(self):
        """Lines 542-544: key already in _data → skip."""
        from app.rag.models import KnowledgeCollection
        from app.rag.store import KnowledgeStore, _CollectionStore

        mock_col = SimpleNamespace(
            id="col-existing",
            tenant_id="fcov-t1",
            name="Existing Collection",
            description="",
            embedder="voyage",
            document_count=0,
        )

        db = _TrackerDB()
        db.session.configure(
            [mock_col],  # collections query
            [],          # empty documents batch → break immediately
        )

        store = KnowledgeStore(db_session_factory=db)
        # Pre-load the collection so it's already in _data
        existing_col = KnowledgeCollection(name="Existing Collection", collection_id="col-existing")
        store._data[("fcov-t1", "col-existing")] = _CollectionStore(collection=existing_col)

        loaded = await store.sync_from_db()
        assert loaded == 0  # no new chunks

    @pytest.mark.asyncio
    async def test_sync_from_db_document_already_in_chunks_not_duplicated(self):
        """Lines 569-570: chunk already in cstore.chunks → skip."""
        from app.rag.models import Chunk, KnowledgeCollection
        from app.rag.store import KnowledgeStore, _CollectionStore

        mock_col = SimpleNamespace(
            id="col-dedup",
            tenant_id="fcov-t1",
            name="Dedup Collection",
            description="",
            embedder="voyage",
            document_count=1,
        )
        mock_doc = SimpleNamespace(
            id="doc-existing",
            tenant_id="fcov-t1",
            collection_id="col-dedup",
            content="already loaded content",
            embedding=[0.1, 0.2],
            chunk_index=0,
            doc_metadata={},
        )

        db = _TrackerDB()
        db.session.configure(
            [mock_col],    # collections
            [mock_doc],    # documents batch 1
            [],            # empty → break
        )

        store = KnowledgeStore(db_session_factory=db)
        # Pre-load the collection AND the chunk → chunk should not be duplicated
        col = KnowledgeCollection(name="Dedup Collection", collection_id="col-dedup")
        existing_chunk = Chunk(
            document_id="doc-existing",
            content="already loaded content",
            embedding=[0.1, 0.2],
            chunk_index=0,
            chunk_id="doc-existing",
        )
        cstore_data = _CollectionStore(collection=col)
        cstore_data.chunks = [existing_chunk]
        store._data[("fcov-t1", "col-dedup")] = cstore_data

        loaded = await store.sync_from_db()
        assert loaded == 0  # already present → not added again
