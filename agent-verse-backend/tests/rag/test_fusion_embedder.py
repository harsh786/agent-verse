"""retrieve() must pass embedder to retrieve_fusion() for better variant embeddings."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession


async def test_retrieve_fusion_dispatch_passes_embedder():
    """retrieve() with strategy=fusion must pass provider as embedder."""
    from app.rag.engine import retrieve

    session = AsyncMock(spec=AsyncSession)
    captured = {}

    async def fake_retrieve_fusion(session, *, embedder=None, **kwargs):
        captured["embedder"] = embedder
        return []

    mock_provider = AsyncMock()

    with patch("app.rag.engine.retrieve_fusion", side_effect=fake_retrieve_fusion):
        await retrieve(
            session,
            query="what is fusion",
            query_embedding=[0.1] * 10,
            collection_id="col1",
            strategy="fusion",
            provider=mock_provider,
        )

    assert captured.get("embedder") is mock_provider, \
        "embedder must be passed as provider to retrieve_fusion()"
