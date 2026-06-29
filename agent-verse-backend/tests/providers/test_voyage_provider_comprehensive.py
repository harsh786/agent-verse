"""Comprehensive tests for VoyageProvider and LocalEmbedProvider — targets 85% coverage.

voyageai and sentence-transformers are NOT installed; all tests mock via sys.modules.
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import CompletionRequest, EmbedRequest, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_voyageai_module(embeddings: list[list[float]] | None = None) -> tuple[MagicMock, MagicMock]:
    """Return (mock_voyageai_module, mock_voyage_client)."""
    mock_client = MagicMock()
    result = MagicMock()
    result.embeddings = embeddings or [[0.1, 0.2, 0.3]]
    mock_client.embed.return_value = result

    mock_voyageai = MagicMock()  # no spec to allow dynamic attrs
    mock_voyageai.Client.return_value = mock_client

    return mock_voyageai, mock_client


def _make_sentence_transformer_module(
    encode_output: list[list[float]] | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_sentence_transformers_module, mock_model)."""
    mock_model = MagicMock()
    # .encode() returns a numpy array-like; .tolist() returns a list of lists
    output = encode_output or [[0.5, 0.6, 0.7]]
    mock_array = MagicMock()
    mock_array.tolist.return_value = output
    mock_model.encode.return_value = mock_array

    mock_st = MagicMock()  # no spec — allows dynamic attrs
    mock_st.SentenceTransformer.return_value = mock_model

    return mock_st, mock_model


# ===========================================================================
# VoyageProvider
# ===========================================================================

class TestVoyageProvider:
    # -----------------------------------------------------------------------
    # Instantiation
    # -----------------------------------------------------------------------

    def test_constructor_creates_client_with_api_key(self) -> None:
        mock_voyage, mock_client_factory = _make_voyageai_module()
        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            p = VoyageProvider(api_key="vk-test", model="voyage-2")
        mock_voyage.Client.assert_called_once_with(api_key="vk-test")
        assert p._model == "voyage-2"

    def test_constructor_stores_default_model(self) -> None:
        mock_voyage, _ = _make_voyageai_module()
        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            p = VoyageProvider(api_key="key")
        assert p._model == "voyage-2"

    def test_constructor_raises_import_error_when_voyageai_missing(self) -> None:
        with patch.dict(sys.modules, {"voyageai": None}):  # type: ignore[dict-item]
            import importlib
            import app.providers.voyage_provider as _mod
            importlib.reload(_mod)
            with pytest.raises(ImportError, match="voyageai"):
                _mod.VoyageProvider(api_key="key")
        import importlib
        import app.providers.voyage_provider as _mod2
        importlib.reload(_mod2)

    # -----------------------------------------------------------------------
    # complete() raises
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_raises_not_implemented(self) -> None:
        mock_voyage, _ = _make_voyageai_module()
        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            provider = VoyageProvider(api_key="key")
            with pytest.raises(NotImplementedError, match="embedding"):
                await provider.complete(
                    CompletionRequest(messages=[Message(role="user", content="hi")], model="voyage-2")
                )

    # -----------------------------------------------------------------------
    # embed()
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_embed_returns_vectors(self) -> None:
        vecs = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_voyage, mock_client = _make_voyageai_module(vecs)

        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            provider = VoyageProvider(api_key="key", model="voyage-large-2")
            result = await provider.embed(EmbedRequest(texts=["a", "b"]))

        assert result.embeddings == vecs

    @pytest.mark.asyncio
    async def test_embed_calls_client_with_correct_args(self) -> None:
        mock_voyage, mock_client = _make_voyageai_module()

        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            provider = VoyageProvider(api_key="key", model="voyage-2")
            await provider.embed(EmbedRequest(texts=["hello world"]))

        mock_client.embed.assert_called_once_with(["hello world"], model="voyage-2")

    # -----------------------------------------------------------------------
    # embed_batch()
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_embed_batch_empty_returns_empty_list(self) -> None:
        mock_voyage, mock_client = _make_voyageai_module()

        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            provider = VoyageProvider(api_key="key")
            result = await provider.embed_batch([])

        assert result == []
        mock_client.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_batch_single_batch_under_96(self) -> None:
        texts = [f"text_{i}" for i in range(50)]
        vecs = [[float(i)] for i in range(50)]
        mock_voyage, mock_client = _make_voyageai_module(vecs)

        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            provider = VoyageProvider(api_key="key")
            result = await provider.embed_batch(texts)

        assert len(result) == 50
        mock_client.embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_batch_splits_at_96(self) -> None:
        """embed_batch() makes two calls when texts > 96."""
        call_count = 0

        def _fake_embed(batch: list[str], model: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.embeddings = [[float(i)] for i in range(len(batch))]
            return result

        mock_voyage = MagicMock()  # no spec — allows dynamic attrs
        mock_client = MagicMock()
        mock_client.embed = _fake_embed
        mock_voyage.Client.return_value = mock_client

        texts = [f"text_{i}" for i in range(100)]

        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            provider = VoyageProvider(api_key="key")
            result = await provider.embed_batch(texts)

        assert len(result) == 100
        assert call_count == 2  # 96 + 4

    @pytest.mark.asyncio
    async def test_embed_batch_exactly_96_is_one_call(self) -> None:
        texts = [f"text_{i}" for i in range(96)]
        call_args: list = []

        def _fake_embed(batch: list[str], model: str) -> MagicMock:
            call_args.append(batch)
            result = MagicMock()
            result.embeddings = [[0.0] for _ in batch]
            return result

        mock_voyage = MagicMock()  # no spec
        mock_client = MagicMock()
        mock_client.embed = _fake_embed
        mock_voyage.Client.return_value = mock_client

        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            provider = VoyageProvider(api_key="key")
            result = await provider.embed_batch(texts)

        assert len(result) == 96
        assert len(call_args) == 1

    # -----------------------------------------------------------------------
    # Capability flags
    # -----------------------------------------------------------------------

    def test_supports_vision_false(self) -> None:
        mock_voyage, _ = _make_voyageai_module()
        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            p = VoyageProvider(api_key="key")
            assert p.supports_vision() is False

    def test_supports_tool_use_false(self) -> None:
        mock_voyage, _ = _make_voyageai_module()
        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            from app.providers.voyage_provider import VoyageProvider
            p = VoyageProvider(api_key="key")
            assert p.supports_tool_use() is False


# ===========================================================================
# LocalEmbedProvider
# ===========================================================================

class TestLocalEmbedProvider:

    def test_constructor_raises_import_error_when_sentence_transformers_missing(self) -> None:
        with patch.dict(sys.modules, {"sentence_transformers": None}):  # type: ignore[dict-item]
            import importlib
            import app.providers.voyage_provider as _mod
            importlib.reload(_mod)
            with pytest.raises(ImportError, match="sentence-transformers"):
                _mod.LocalEmbedProvider()
        import importlib
        import app.providers.voyage_provider as _mod2
        importlib.reload(_mod2)

    def test_constructor_stores_model_name(self) -> None:
        mock_st, _ = _make_sentence_transformer_module()
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider("all-MiniLM-L6-v2")
        mock_st.SentenceTransformer.assert_called_once_with("all-MiniLM-L6-v2")

    @pytest.mark.asyncio
    async def test_complete_raises_not_implemented(self) -> None:
        mock_st, _ = _make_sentence_transformer_module()
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider()
            with pytest.raises(NotImplementedError, match="embedding"):
                await p.complete(
                    CompletionRequest(messages=[Message(role="user", content="hi")], model="local")
                )

    @pytest.mark.asyncio
    async def test_embed_returns_vectors(self) -> None:
        vecs = [[0.1, 0.2], [0.3, 0.4]]
        mock_st, mock_model = _make_sentence_transformer_module(vecs)
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider()
            result = await p.embed(EmbedRequest(texts=["a", "b"]))
        assert result.embeddings == vecs

    @pytest.mark.asyncio
    async def test_embed_calls_encode_with_texts(self) -> None:
        mock_st, mock_model = _make_sentence_transformer_module([[0.5]])
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider()
            await p.embed(EmbedRequest(texts=["hello"]))
        mock_model.encode.assert_called_once_with(["hello"])

    @pytest.mark.asyncio
    async def test_embed_batch_empty_returns_empty_list(self) -> None:
        mock_st, _ = _make_sentence_transformer_module()
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider()
            result = await p.embed_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_batch_returns_vectors_for_texts(self) -> None:
        vecs = [[0.1], [0.2], [0.3]]
        mock_st, mock_model = _make_sentence_transformer_module(vecs)
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider()
            result = await p.embed_batch(["a", "b", "c"])
        assert result == vecs

    def test_supports_vision_false(self) -> None:
        mock_st, _ = _make_sentence_transformer_module()
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider()
            assert p.supports_vision() is False

    def test_supports_tool_use_false(self) -> None:
        mock_st, _ = _make_sentence_transformer_module()
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            from app.providers.voyage_provider import LocalEmbedProvider
            p = LocalEmbedProvider()
            assert p.supports_tool_use() is False
