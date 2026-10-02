"""New collections are sized to the embedder's REAL output dimension.

``_db_create_collection`` used to trust the static ``settings.embedding_dim``
(default 2048), which disagrees with e.g. all-mpnet-base-v2 (768-d): the
collection was recorded with the wrong vector width. When the active embedder's
dimension is known it now wins; an embedder whose dimension has no chunk table
fails with a clear error instead of silently creating an unusable collection.
Existing collections are untouched (only the INSERT of a new one is affected).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.rag.models import KnowledgeCollection
from app.rag.store import EmbeddingDimensionError, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.rag.test_store_db_paths import _Result, _ScriptedDB

pytestmark = pytest.mark.asyncio

_CTX = TenantContext(tenant_id="dim-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
# RATE-01: advisory lock + collection count precede the INSERT.
_LIMIT_OK = (_Result(), _Result(scalar=0))


def _inserted_dim(db: _ScriptedDB) -> int:
    for sql, params in db.session.calls:
        if "INSERT INTO knowledge_collections" in sql:
            return int(params["dim"])
    raise AssertionError("no collection INSERT issued")


async def test_known_embedder_dimension_wins_over_the_static_setting() -> None:
    db = _ScriptedDB(*_LIMIT_OK, _Result(scalar="c1"))
    store = KnowledgeStore(db_session_factory=db, embedding_dim=768)
    with patch("app.core.config.get_settings", return_value=Settings(embedding_dim=2048)):
        await store.create_collection_async(
            KnowledgeCollection(name="n", collection_id="c1"), tenant_ctx=_CTX
        )
    assert _inserted_dim(db) == 768


async def test_embedder_dimension_can_be_bound_after_construction() -> None:
    db = _ScriptedDB(*_LIMIT_OK, _Result(scalar="c2"))
    store = KnowledgeStore(db_session_factory=db)
    store.set_embedding_dim(1024)
    with patch("app.core.config.get_settings", return_value=Settings(embedding_dim=2048)):
        await store.create_collection_async(
            KnowledgeCollection(name="n", collection_id="c2"), tenant_ctx=_CTX
        )
    assert _inserted_dim(db) == 1024


async def test_unknown_embedder_dimension_falls_back_to_the_setting() -> None:
    db = _ScriptedDB(*_LIMIT_OK, _Result(scalar="c3"))
    store = KnowledgeStore(db_session_factory=db)
    with patch("app.core.config.get_settings", return_value=Settings(embedding_dim=1536)):
        await store.create_collection_async(
            KnowledgeCollection(name="n", collection_id="c3"), tenant_ctx=_CTX
        )
    assert _inserted_dim(db) == 1536


async def test_unsupported_embedder_dimension_fails_clearly() -> None:
    db = _ScriptedDB(_Result(scalar="c4"))
    store = KnowledgeStore(db_session_factory=db, embedding_dim=384)
    with pytest.raises(EmbeddingDimensionError, match="384"):
        await store.create_collection_async(
            KnowledgeCollection(name="n", collection_id="c4"), tenant_ctx=_CTX
        )
    assert not any("INSERT" in sql for sql, _ in db.session.calls)
    assert store.get_collection("c4", tenant_ctx=_CTX) is None


async def test_dimension_error_is_a_value_error_for_existing_callers() -> None:
    assert issubclass(EmbeddingDimensionError, ValueError)
