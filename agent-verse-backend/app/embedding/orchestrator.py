"""EmbeddingOrchestrator — selects embedding model per content type and tenant policy.

Registry-only routing
---------------------
Every embedding comes from the Model Registry-configured embedder. Text content is
embedded with the deployment's default embedder (the registry-resolved
``app.state.embedder``) and its own model — never a model id from a table. Only a
CODE / multimodal content type may be routed, and only to a configured registry
embedding model of that modality (:class:`EmbeddingModelRegistry`) whose width
equals the collection's (``target_dim``); otherwise the default embedder serves it.
A collection strictly bound to an embedder is never routed (the ingestion
orchestrator embeds it with its bound model before reaching here).

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

# Provider fallback order for EMBEDDINGS — used when the primary provider fails.
# Anthropic has no embeddings API, so it is not in the chain; "fake" (random-ish
# 10-dim vectors) is appended only outside production (see fallback_order()).
_FALLBACK_ORDER = ["openai", "voyage", "gemini"]


def _is_production() -> bool:
    import os

    return os.getenv("ENVIRONMENT", "development").strip().lower() == "production"


def fallback_order() -> list[str]:
    """The embedding provider fallback chain for this environment."""
    return [*_FALLBACK_ORDER] if _is_production() else [*_FALLBACK_ORDER, "fake"]


# Default batch size for embed_batch()
_DEFAULT_BATCH_SIZE = 32

# Modalities that cannot be embedded natively and are realised as
# caption-then-text-embed (see the module docstring, finding D-11).
_CAPTION_FIRST_MODALITIES = frozenset({"image", "multimodal"})


# ``EmbeddingSelectionResult.provider`` of a selection served by the default embedder.
DEFAULT_EMBEDDER_PROVIDER = "default"

# Content types whose content reaches the embedder as text of a caption (D-11).
_CAPTIONED_CONTENT = frozenset({ContentType.IMAGE, ContentType.VIDEO})


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
    # Model Registry key (``provider/model_id``) of a routed specialist; "" when
    # the default embedder serves the content.
    key: str = ""

    @property
    def uses_default_embedder(self) -> bool:
        return not self.key


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
    # D-12: indices whose embedding FAILED. Their slot in ``embeddings`` is
    # ``None`` — never a zero vector — so callers can skip or retry them instead
    # of corrupting the index.
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
        *,
        target_dim: int | None = None,
        default_model: str = "",
    ) -> EmbeddingSelectionResult:
        """The embedder for *content_type*: a registry specialist, else the default.

        A CODE / multimodal content type is routed to a configured registry model
        of that modality the tenant's plan affords, in registry order (the
        operator's preference first), whose width equals *target_dim* (the
        collection's, else the default embedder's). Unknown widths are never
        routed. Everything else — and every text content type — is served by the
        default embedder with its own model (``default_model`` names it).
        """
        del collection_size
        modalities = _MODALITY_MAP.get(content_type, ["text"])
        if tenant_ctx is not None:
            allowed_costs = _COST_BY_PLAN.get(tenant_ctx.plan.value, ["low"])
        else:
            allowed_costs = _COST_BY_PLAN.get("free", ["free", "low"])

        for modality in modalities:
            if modality == "text":
                break  # the default embedder serves text
            candidates = [
                c
                for c in self._registry.list_by_modality(modality)
                if c.cost_class in allowed_costs
                and c.dimension > 0
                and target_dim is not None
                and c.dimension == target_dim
            ]
            if candidates:
                best = candidates[0]
                return EmbeddingSelectionResult(
                    model_id=best.model_id,
                    dimension=best.dimension,
                    modality=best.modality,
                    cost_class=best.cost_class,
                    provider=best.provider,
                    selection_reason=f"content_type={content_type.value} modality={modality}",
                    requires_captioning=best.modality in _CAPTION_FIRST_MODALITIES
                    or content_type in _CAPTIONED_CONTENT,
                    key=best.key,
                )

        return EmbeddingSelectionResult(
            model_id=default_model,
            dimension=target_dim or 0,
            modality="text",
            cost_class="",
            provider=DEFAULT_EMBEDDER_PROVIDER,
            selection_reason=f"content_type={content_type.value}: default embedder",
            requires_captioning=content_type in _CAPTIONED_CONTENT,
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
        cost_controller: Any = None,
        target_dim: int | None = None,
    ) -> RoutedEmbeddingResult:
        """Select a model for *content_type* and ACTUALLY embed with it (D-10).

        The chosen model id is threaded into the physical embed call, so a CODE
        content type is embedded with the code model, image content is embedded
        via caption-then-text-embed, etc. — instead of the previous behaviour of
        selecting a model and then discarding it in favour of one fixed embedder.

        Provider resolution:
          * a routed specialist (see :meth:`select`) is built by
            ``provider_resolver`` from its Model Registry key (``provider/model_id``);
          * otherwise — no specialist, or it cannot be built — ``default_provider``
            embeds with ITS OWN model (no model id is ever forced onto it: the
            selected id used to be sent to the default embedder, which 404'd on it
            or embedded with a model it does not serve).

        Spend (KB-40): texts go out in bounded batches, each reserved against
        the tenant's budget first (``cost_controller``, else the process one)
        and recorded as embedding usage — see :mod:`app.embedding.metering`.
        A refused reservation raises ``EmbeddingBudgetExceededError`` before
        that batch is sent.
        """
        from app.providers.embedder_factory import embedder_model_name

        default_model = embedder_model_name(default_provider)
        selection = self.select(
            content_type,
            tenant_ctx,
            collection_size,
            target_dim=target_dim,
            default_model=default_model,
        )

        provider = default_provider
        if selection.key:
            resolved = None
            if provider_resolver is not None:
                try:
                    resolved = provider_resolver(selection.key)
                except Exception:
                    resolved = None
            if resolved is not None:
                provider = resolved
            else:
                _log.warning(
                    "embedding_route_unavailable: %s; the default embedder serves it",
                    selection.key,
                )
                selection = EmbeddingSelectionResult(
                    model_id=default_model,
                    dimension=target_dim or 0,
                    modality="text",
                    cost_class="",
                    provider=DEFAULT_EMBEDDER_PROVIDER,
                    selection_reason=f"{selection.key} unavailable: default embedder",
                    requires_captioning=content_type in _CAPTIONED_CONTENT,
                )

        from app.embedding.metering import embed_metered

        async def _one_batch(batch: list[str]) -> list[list[float]]:
            # "": the provider embeds with its own (registry-built) model.
            return await self._embed_texts_with_model(batch, provider, "")

        embeddings = await embed_metered(
            texts,
            _one_batch,
            tenant_ctx=tenant_ctx,
            model=selection.model_id or default_model,
            controller=cost_controller,
            label="knowledge-ingest",
        )
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

        Note: the authoritative enforcement lives at the persistence boundary —
        ``KnowledgeStore._persist_chunks`` reads the collection's stored
        ``embedding_dim`` under ``FOR UPDATE`` and rejects a mismatch on a
        non-empty collection (covered by
        ``tests/rag/test_persisted_rag_store.py::test_ingest_rejects_embedding_dimension_mismatch``).
        This method remains a reusable policy helper for callers that hold both
        dimensions in hand.
        """
        if not self._index_policy.is_dimension_compatible(existing_dim, new_dim):
            raise ValueError(
                f"Embedding dimension mismatch: collection has {existing_dim}, "
                f"new model produces {new_dim}. Re-embed the collection before switching."
            )
        return True


def build_provider_resolver(
    providers_by_key: dict[str, Any],
) -> Callable[[str], Any] | None:
    """A resolver over fixed embedders keyed by Model Registry key (tests / wiring).

    Returns ``None`` for an unknown key, which makes ``embed_for_content`` use the
    default embedder; ``None`` overall when no embedders are supplied.
    """
    if not providers_by_key:
        return None
    mapping = dict(providers_by_key)

    def _resolve(key: str) -> Any:
        return mapping.get(key)

    return _resolve


def build_registry_embedder_resolver(
    settings: Any = None, *, registry: Any = None
) -> Callable[[str], Any]:
    """Map a Model Registry embedding key (``provider/model_id``) to its embedder.

    The embedder is built from the model's registry entry exactly as a collection
    bound to it would be (own ``base_url`` + vault key, same-model failover; see
    :class:`app.rag.collection_embedders.CollectionEmbedders`) and cached until
    the registry changes. A key the registry does not configure (or that cannot be
    built here) resolves to ``None``: the default embedder then serves the content.
    This replaced a by-provider-NAME map of env-keyed providers built with literal
    model ids (``text-embedding-3-small`` on OpenAI, Voyage's own default, ...).
    """
    from app.rag.collection_embedders import CollectionEmbedders, EmbeddingBinding

    embedders = CollectionEmbedders(settings=settings, registry=registry)

    def _resolve(key: str) -> Any:
        provider, _, model_id = (key or "").partition("/")
        if not provider or not model_id:
            return None
        try:
            return embedders.resolve(
                EmbeddingBinding(provider=provider, model=model_id), default=None
            )
        except Exception as exc:  # unavailable here → the default embedder
            _log.warning("embedding_route_unbuildable: %s (%s)", key, str(exc)[:200])
            return None

    return _resolve
