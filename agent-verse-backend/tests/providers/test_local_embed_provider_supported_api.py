"""USR-6: the local embedder must use sentence-transformers' supported API.

``SentenceTransformer.get_sentence_embedding_dimension`` is deprecated (renamed
``get_embedding_dimension``) and emits a ``FutureWarning``. pytest runs with
``filterwarnings = error``, so building :class:`LocalEmbedProvider` around a
*real* ``SentenceTransformer`` fails here while the deprecated call remains.

The model is assembled from a local bag-of-words module, so nothing is
downloaded and the test runs offline.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any
from unittest.mock import patch

import pytest

st = pytest.importorskip("sentence_transformers")
_RealSentenceTransformer = st.SentenceTransformer


def _real_offline_model(_name: str, *args: Any, **kwargs: Any) -> Any:
    from sentence_transformers.sentence_transformer.modules import BoW

    return _RealSentenceTransformer(
        modules=[BoW(vocab=["alpha", "beta", "gamma"])], device="cpu"
    )


def test_local_embed_provider_uses_the_supported_dimension_api() -> None:
    from app.providers.voyage_provider import LocalEmbedProvider

    with (
        patch("sentence_transformers.SentenceTransformer", _real_offline_model),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("error")
        provider = LocalEmbedProvider(model_name="offline-bow")
        vectors = asyncio.run(provider.embed_batch(["alpha beta", "gamma"]))

    assert provider.embedding_dim == 3
    assert [len(v) for v in vectors] == [3, 3]


def test_the_deprecated_method_really_warns() -> None:
    """Guards the reproducer: the old call is still deprecated in this library."""
    model = _real_offline_model("offline-bow")
    with pytest.warns(FutureWarning, match="get_embedding_dimension"):
        model.get_sentence_embedding_dimension()
