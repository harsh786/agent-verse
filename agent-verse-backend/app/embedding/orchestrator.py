"""EmbeddingOrchestrator — selects embedding model per content type and tenant policy.

Honesty note on "multimodal" embeddings (finding D-11)
------------------------------------------------------
The registry advertises ``voyage-multimodal-3`` for image/video content, but the
platform does **not** currently have a real image->vector path. Image and video
content is embedded as *caption-then-text-embed*: a caption is produced upstream
(perception/OCR) and that text is embedded with a text model. To keep this
honest, a selection whose modality is image/multimodal is flagged with
``requires_captioning=True`` and any physical embedding through this orchestrator
reports ``embedding_input="text_of_caption"`` — never a native multimodal vector.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.embedding.dimension_policy import DimensionPolicy
from app.embedding.model_registry import EmbeddingModelRegistry
from app.embedding.vector_index_policy import VectorIndexPolicy
from app.ingestion.content_classifier import ContentType

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext

_MODALITY_MAP: dict[ContentType, list[str]] = {
    ContentType.TEXT: ["text"],
    ContentType.MARKDOWN: ["text"],
    ContentType.CODE: ["code", "text"],
    ContentType.PDF: ["text"],
    ContentType.DOCX: ["text"],
    ContentType.HTML: ["text"],
    ContentType.IMAGE: ["multimodal", "image", "text"],
    ContentType.AUDIO: ["text"],  # transcript embedding
    ContentType.VIDEO: ["multimodal", "text"],
    ContentType.CSV: ["text"],
    ContentType.JSON: ["text"],
}

_COST_BY_PLAN = {
    "free": ["free", "low"],
    "starter": ["free", "low"],
    "professional": ["free", "low", "medium"],
    "enterprise": ["free", "low", "medium", "high"],
}

# Provider fallback order — used when the primary provider fails.
# Each entry is a provider name; the orchestrator tries them in order.
_FALLBACK_ORDER = ["anthropic", "openai", "voyage", "gemini", "fake"]

# Default batch size for embed_batch()
_DEFAULT_BATCH_SIZE = 32

# Modalities that cannot be embedded natively and are realised as
# caption-then-text-embed (see the module docstring, finding D-11).
_CAPTION_FIRST_MODALITIES = frozenset({"image", "multimodal"})


@dataclass
class EmbeddingSelectionResult:
    model_id: str
    dimension: int
    modality: str
    cost_class: str
    provider: str
    selection_reason: str = ""
    # D-11: True when the modality (image/multimodal) is realised as
    # caption-then-text-embed rather than a native multimodal vector.
    requires_captioning: bool = False


@dataclass
class BatchEmbeddingResult:
    """Result of a batch embedding operation.

    ``embeddings[i]`` is ``None`` when all providers failed for the batch
    containing item *i*.  Callers **must** check for ``None`` before
    inserting into a vector index — a ``None`` signals an embedding that
    could not be computed (as opposed to a zero vector, which would
    silently corrupt cosine-similarity search).
    """

    embeddings: list[list[float] | None]
    model_id: str
    provider: str
    errors: list[str] = field(default_factory=list)
    # D-12: indices whose embedding FAILED. Their slot in ``embeddings`` is the
    # empty-list sentinel ``[]`` — never a zero vector — so callers can skip or
    # retry them instead of indexing corruption.
    failed_indices: list[int] = field(default_factory=list)


@dataclass
class RoutedEmbeddingResult:
    """Result of physically embedding via the *selected* model (finding D-10)."""

    embeddings: list[list[float]]
    model_id: str
    provider: str
    # "native" for text/code; "text_of_caption" for image/multimodal (D-11).
    embedding_input: str = "native"


class EmbeddingOrchestrator:
    def __init__(self, registry: EmbeddingModelRegistry | None = None) -> None:
        self._registry = registry or EmbeddingModelRegistry.build_default()
        self._dim_policy = DimensionPolicy()
        self._index_policy = VectorIndexPolicy()

    def select(
        self,
        content_type: ContentType,
        tenant_ctx: TenantContext | None = None,
        collection_size: int = 0,
    ) -> EmbeddingSelectionResult:
        modalities = _MODALITY_MAP.get(content_type, ["text"])
        # Use tenant plan when available; fall back to "free" tier for broad compatibility
        if tenant_ctx is not None:
            allowed_costs = _COST_BY_PLAN.get(tenant_ctx.plan.value, ["low"])
        else:
            allowed_costs = _COST_BY_PLAN.get("free", ["free", "low"])

        # Try each modality in preference order
        for modality in modalities:
            candidates = self._registry.list_by_modality(modality)
            # Filter by allowed cost class
            affordable = [c for c in candidates if c.cost_class in allowed_costs]
            if affordable:
                # Pick highest quality (largest dimension) that's affordable
                best = max(affordable, key=lambda m: m.dimension)
                return EmbeddingSelectionResult(
                    model_id=best.model_id,
                    dimension=self._dim_policy.select(best.model_id),
                    modality=best.modality,
                    cost_class=best.cost_class,
                    provider=best.provider,
                    selection_reason=f"content_type={content_type.value} modality={modality}",
                    requires_captioning=best.modality in _CAPTION_FIRST_MODALITIES,
                )

        # Fallback to any text model
        fallback = self._registry.list_by_modality("text")
        if fallback:
            m = fallback[0]
            return EmbeddingSelectionResult(
                model_id=m.model_id,
                dimension=self._dim_policy.select(m.model_id),
                modality=m.modality,
                cost_class=m.cost_class,
                provider=m.provider,
                selection_reason="fallback to text embedding",
            )

        # Ultimate fallback
        return EmbeddingSelectionResult(
            model_id="fake-embedding",
            dimension=10,
            modality="text",
            cost_class="free",
            provider="fake",
            selection_reason="no embedding model available",
        )

    async def embed_with_fallback(
        self,
        text: str,
        providers: list[Any],
        model_id: str = "",
    ) -> list[float]:
        """Embed *text* trying each provider in *providers* until one succeeds.

        Providers are tried in the order given. On failure the next provider in
        the list is attempted. Raises RuntimeError if all providers fail.
        """
        last_exc: Exception | None = None
        for provider in providers:
            try:
                from app.providers.base import embed_texts as _embed

                result = await _embed([text], provider=provider)
                if result:
                    return result[0]
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"All embedding providers failed. Last error: {last_exc}") from last_exc

    async def embed_batch(
        self,
        texts: list[str],
        providers: list[Any],
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> BatchEmbeddingResult:
        """Embed *texts* in batches of *batch_size*, with provider fallback per batch.

        Returns embeddings in the same order as *texts*.  Failed batches are
        retried with the next provider; errors for individual batches are
        recorded in the result.

        Items whose batch could not be embedded by **any** provider are set to
        ``None`` (never a zero vector) so callers can distinguish "no
        embedding available" from a real vector.  A zero vector has
        cosine-similarity ~0 to everything and would silently corrupt the
        vector index.
        """
        if not texts:
            return BatchEmbeddingResult(embeddings=[], model_id="", provider="none")

        all_embeddings: list[list[float] | None] = [None] * len(texts)
        errors: list[str] = []
        failed_indices: list[int] = []
        num_batches = math.ceil(len(texts) / batch_size)
        used_provider = "unknown"
        used_model = ""

        for batch_idx in range(num_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, len(texts))
            batch_texts = texts[start:end]

            batch_ok = False
            for provider in providers:
                try:
                    from app.providers.base import embed_texts as _embed

                    result = await _embed(batch_texts, provider=provider)
                    for i, emb in enumerate(result):
                        all_embeddings[start + i] = emb
                    used_provider = getattr(provider, "provider_name", str(provider))
                    batch_ok = True
                    break
                except Exception as exc:
                    errors.append(f"batch_{batch_idx}: {exc!s}")
                    continue

            if not batch_ok:
                _log.warning(
                    "embed_batch: batch %d (%d items) failed across all providers; "
                    "marking as None (not zero vectors) to prevent silent index "
                    "corruption.  Errors: %s",
                    batch_idx,
                    len(batch_texts),
                    "; ".join(
                        e for e in errors if e.startswith(f"batch_{batch_idx}:")
                    ),
                )
                for i in range(len(batch_texts)):
                    failed_indices.append(start + i)

        return BatchEmbeddingResult(
            embeddings=all_embeddings,
            model_id=used_model,
            provider=used_provider,
            errors=errors,
            failed_indices=failed_indices,
        )

    async def _embed_texts_with_model(
        self,
        texts: list[str],
        provider: Any,
        model_id: str,
    ) -> list[list[float]]:
        """Physically embed *texts* on *provider*, requesting *model_id*.

        Mirrors :func:`app.providers.base.embed_texts` safety semantics — when no
        provider is available or it does not implement embedding, the empty-list
        sentinel is returned (never a zero/noise vector). The key difference is
        that the *selected* ``model_id`` is threaded into the request, so the
        provider actually uses the chosen model (finding D-10).
        """
        if provider is None:
            return [[] for _ in texts]
        try:
            from app.providers.base import EmbedRequest

            resp = await provider.embed(EmbedRequest(texts=texts, model=model_id))
            return resp.embeddings
        except NotImplementedError:
            return [[] for _ in texts]

    async def embed_for_content(
        self,
        texts: list[str],
        *,
        content_type: ContentType,
        tenant_ctx: TenantContext | None = None,
        default_provider: Any = None,
        provider_resolver: Callable[[str], Any] | None = None,
        collection_size: int = 0,
    ) -> RoutedEmbeddingResult:
        """Select a model for *content_type* and ACTUALLY embed with it (D-10).

        The chosen model id is threaded into the physical embed call, so a CODE
        content type is embedded with the code model, image content is embedded
        via caption-then-text-embed, etc. — instead of the previous behaviour of
        selecting a model and then discarding it in favour of one fixed embedder.

        Provider resolution:
          * ``provider_resolver`` (optional) maps the selected provider name to a
            concrete provider instance. When it returns a provider, that provider
            is used.
          * Otherwise ``default_provider`` is used (safe fallback), still with the
            selected model id threaded through.
        """
        selection = self.select(content_type, tenant_ctx, collection_size)

        provider = default_provider
        if provider_resolver is not None:
            try:
                resolved = provider_resolver(selection.provider)
            except Exception:
                resolved = None
            if resolved is not None:
                provider = resolved

        embeddings = await self._embed_texts_with_model(texts, provider, selection.model_id)
        embedding_input = "text_of_caption" if selection.requires_captioning else "native"
        return RoutedEmbeddingResult(
            embeddings=embeddings,
            model_id=selection.model_id,
            provider=selection.provider,
            embedding_input=embedding_input,
        )

    def guard_dimension_change(self, *, existing_dim: int, new_dim: int) -> bool:
        """Reject an embedding-dimension change that would corrupt a vector index.

        Wires :meth:`VectorIndexPolicy.is_dimension_compatible` so a re-embed or
        model swap cannot silently write vectors of a different dimension into an
        existing collection (which pgvector would reject or, worse, mis-index).

        Returns ``True`` when compatible; raises ``ValueError`` on mismatch.

        TODO(main.py wiring): call this from the ingest/index path once the
        collection's stored embedding dimension is available to
        ``IngestionOrchestrator`` (the ``KnowledgeStore`` does not yet expose it
        within this module's scope).
        """
        if not self._index_policy.is_dimension_compatible(existing_dim, new_dim):
            raise ValueError(
                f"Embedding dimension mismatch: collection has {existing_dim}, "
                f"new model produces {new_dim}. Re-embed the collection before switching."
            )
        return True
