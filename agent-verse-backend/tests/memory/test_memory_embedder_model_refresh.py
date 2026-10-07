"""The memory embedder's model id is read per call, not captured once.

``ProviderMemoryEmbedder.model_id`` was computed in ``__init__``: after a Model
Registry reload swapped the process embedder (``RegistryReloadingEmbedder``),
new memory vectors were stored — and recall filtered — under the OLD model's id.
"""

from __future__ import annotations

from typing import Any

from app.memory.embedding import ProviderMemoryEmbedder
from app.memory.postgres_repository import PostgresMemoryRepository
from app.providers.base import EmbedRequest, EmbedResponse
from app.providers.embedder_factory import EmbedderResolution, RegistryReloadingEmbedder


class _Model:
    def __init__(self, name: str) -> None:
        self._embed_model_name = name

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.1] * 1024 for _ in request.texts])


def _reloading() -> tuple[RegistryReloadingEmbedder, dict[str, Any]]:
    state: dict[str, Any] = {"version": 1, "model": "model-a"}

    def _resolve() -> EmbedderResolution:
        return EmbedderResolution(
            embedder=_Model(state["model"]), provider="onprem", model=state["model"],
            source="registry",
        )

    proxy = RegistryReloadingEmbedder(
        _resolve(), resolve=_resolve, check_interval_s=0.0, version=lambda: state["version"]
    )
    return proxy, state


async def test_model_id_follows_a_registry_reload() -> None:
    proxy, state = _reloading()
    embedder = ProviderMemoryEmbedder(proxy)
    assert embedder.model_id == "model-a"

    state.update(version=2, model="model-b")  # an operator changed the embedding model
    vector, reason = await embedder.embed_checked("remember this")  # the embed reloads

    assert reason == "ok" and vector is not None
    assert embedder.model_id == "model-b"


async def test_repository_stores_and_matches_the_current_model() -> None:
    proxy, state = _reloading()
    repo = PostgresMemoryRepository(None, embedder=ProviderMemoryEmbedder(proxy))  # type: ignore[arg-type]
    assert repo.embedding_model == "model-a"
    state.update(version=2, model="model-b")
    proxy.refresh(force=True)
    assert repo.embedding_model == "model-b"
