"""EmbeddingModelRegistry — the embedding models content-type routing may choose from.

Built from the **Model Registry** (every configured, eligible embedding model),
never from a literal catalogue. It used to advertise ``text-embedding-3-small/
large``, ``voyage-3-lite``, ``voyage-code-3`` and ``voyage-multimodal-3`` whatever
the deployment had (and ``EMBEDDING_MODEL`` / ``EMBEDDING_PROVIDER`` read from env
only), so a CODE document was "routed" to ``voyage-code-3`` on a deployment that
never configured it — and the model id was sent to the default embedder, which
404'd on it or, worse, embedded with a model it does not serve.

A model's modality (``text`` / ``code`` / ``multimodal``) is the registry entry's
``extra["embedding_modality"]`` when set, else read off its model id (a model id
naming ``code`` is a code embedder, ``multimodal`` a multimodal one); everything
else is a text model. Text is served by the deployment's default embedder (see
:class:`app.embedding.orchestrator.EmbeddingOrchestrator`), so only a configured
code / multimodal specialist ever changes the embedder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_MODALITIES = frozenset({"text", "code", "multimodal", "image"})
_CODE_ID = re.compile(r"(^|[-_/.:])code([-_/.:]|$)")
_MULTIMODAL_ID = re.compile(r"(^|[-_/.:])multimodal([-_/.:]|$)")


@dataclass
class EmbeddingModelSpec:
    model_id: str
    modality: str  # text|code|multimodal|image
    dimension: int  # 0 = unknown (never routed to: its width cannot be checked)
    cost_class: str  # free|low|medium|high
    provider: str
    description: str = ""
    max_input_tokens: int = 8192

    @property
    def key(self) -> str:
        """The Model Registry key (``provider/model_id``) the embedder is built from."""
        return f"{self.provider}/{self.model_id}"


def embedding_modality(model: Any) -> str:
    """``text`` / ``code`` / ``multimodal`` for a registry embedding entry."""
    explicit = str((getattr(model, "extra", None) or {}).get("embedding_modality") or "")
    explicit = explicit.strip().lower()
    if explicit in _MODALITIES:
        return explicit
    model_id = str(getattr(model, "model_id", "") or "").lower()
    if _CODE_ID.search(model_id):
        return "code"
    if _MULTIMODAL_ID.search(model_id):
        return "multimodal"
    return "text"


def _entry_dimension(model: Any) -> int:
    """The entry's own width: measured by a probe, else requested (0 = unknown)."""
    extra = getattr(model, "extra", None) or {}
    for key in ("dimensions", "output_dimensions"):
        value = extra.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return 0


def _cost_class(cost_per_1k: float) -> str:
    if cost_per_1k <= 0:
        return "free"
    if cost_per_1k <= 0.0001:
        return "low"
    if cost_per_1k <= 0.001:
        return "medium"
    return "high"


class EmbeddingModelRegistry:
    def __init__(self, models: list[EmbeddingModelSpec]) -> None:
        self._by_id = {m.model_id: m for m in models}

    def get(self, model_id: str) -> EmbeddingModelSpec | None:
        return self._by_id.get(model_id)

    def list_by_modality(self, modality: str) -> list[EmbeddingModelSpec]:
        return [m for m in self._by_id.values() if m.modality == modality]

    def list_all(self) -> list[EmbeddingModelSpec]:
        return list(self._by_id.values())

    def filter(self, *, cost_class: str | None = None) -> list[EmbeddingModelSpec]:
        results = list(self._by_id.values())
        if cost_class:
            results = [m for m in results if m.cost_class == cost_class]
        return results

    @classmethod
    def build_default(cls) -> EmbeddingModelRegistry:
        """The configured embedding models of the Model Registry (see the module doc)."""
        return cls.from_model_registry()

    @classmethod
    def from_model_registry(
        cls, registry: Any = None, settings: Any = None
    ) -> EmbeddingModelRegistry:
        """One spec per configured, eligible Model Registry embedding model.

        Never raises: an unreadable registry is an empty one (text keeps the
        default embedder).
        """
        from app.ai_router.models import TaskType
        from app.ai_router.selection import ordered_configured_models
        from app.providers.registry_embedder import embedding_model_dimension

        try:
            models = list(ordered_configured_models(TaskType.EMBEDDING, registry=registry))
        except Exception:  # never fail ingestion on the registry
            return cls([])
        specs: list[EmbeddingModelSpec] = []
        for m in models:
            model_id = str(getattr(m, "model_id", "") or "")
            provider = str(getattr(m, "provider", "") or "")
            if not model_id or not provider:
                continue
            dims = _entry_dimension(m)
            if not dims:
                try:
                    dims = embedding_model_dimension(provider, model_id, settings) or 0
                except Exception:  # pragma: no cover - never block routing
                    dims = 0
            specs.append(
                EmbeddingModelSpec(
                    model_id=model_id,
                    modality=embedding_modality(m),
                    dimension=dims,
                    cost_class=_cost_class(float(getattr(m, "cost_per_1k_input", 0.0) or 0.0)),
                    provider=provider,
                    description=str(getattr(m, "display_name", "") or model_id),
                )
            )
        return cls(specs)
