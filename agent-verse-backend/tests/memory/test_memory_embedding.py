"""The app embedder is adapted (dimension-aware) for the canonical memory store."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from app.memory.embedding import (
    MEMORY_EMBEDDING_DIM,
    fit_memory_vector,
    memory_embedder_from_provider,
)


class _Provider:
    embed_model = "embed-small"

    def __init__(self, dim: int | None = None, error: Exception | None = None) -> None:
        self._dim = dim
        self._error = error
        self.calls: list[Any] = []

    async def embed(self, request: Any) -> Any:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(embeddings=[[0.5] * (self._dim or 0)] if self._dim else [])


def test_exact_width_is_kept_narrower_is_zero_padded_wider_is_dropped() -> None:
    assert fit_memory_vector([1.0] * MEMORY_EMBEDDING_DIM) == tuple([1.0] * 1536)
    padded = fit_memory_vector([1.0] * 1024)
    assert padded is not None and len(padded) == 1536
    assert padded[:1024] == tuple([1.0] * 1024) and set(padded[1024:]) == {0.0}
    assert fit_memory_vector([1.0] * 3072) is None


@pytest.mark.parametrize(
    ("dim", "expected_len"), [(1536, 1536), (1024, 1536), (3072, None)]
)
async def test_adapter_fits_provider_vectors(dim: int, expected_len: int | None) -> None:
    embedder = memory_embedder_from_provider(_Provider(dim))
    assert embedder is not None
    vec = await embedder("hello")
    assert (len(vec) if vec is not None else None) == expected_len


async def test_adapter_degrades_to_no_vector_on_provider_failure() -> None:
    embedder = memory_embedder_from_provider(_Provider(error=RuntimeError("401")))
    assert embedder is not None
    assert await embedder("hello") is None


def test_model_id_is_the_configured_model_not_the_wrapper_class() -> None:
    """MEM-10: the same model behind two wrapper classes must match at recall."""

    class _OtherWrapper(_Provider):
        pass

    a = memory_embedder_from_provider(_Provider(1536))
    b = memory_embedder_from_provider(_OtherWrapper(1536))
    assert a is not None and b is not None
    assert a.model_id == "embed-small" == b.model_id


def test_model_id_falls_back_to_the_class_without_a_model_name() -> None:
    class _Bare:
        async def embed(self, request: Any) -> Any:
            return SimpleNamespace(embeddings=[])

    embedder = memory_embedder_from_provider(_Bare())
    assert embedder is not None and embedder.model_id == "_Bare"


def test_no_provider_means_no_embedder() -> None:
    assert memory_embedder_from_provider(None) is None
    assert memory_embedder_from_provider(object()) is None


def test_api_and_worker_repositories_are_built_with_the_app_embedder() -> None:
    import app.main as main_mod
    from app.scaling import tasks

    main_src = inspect.getsource(main_mod)
    assert "PostgresMemoryRepository(\n                db_factory,\n                embedder=" in (
        main_src
    )
    worker_src = inspect.getsource(tasks.run_goal)
    assert "_worker_reflexion_service(\n" in worker_src
    assert "db_factory, _embedder_for_graph" in worker_src
    helper_src = inspect.getsource(tasks._worker_reflexion_service)
    assert "embedder=memory_embedder_from_provider(embedder_provider)" in helper_src
