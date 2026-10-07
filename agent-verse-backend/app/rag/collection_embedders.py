"""Per-collection embedders: every knowledge collection is bound to ONE embedding model.

A collection's vectors and its query vectors must come from the same model, and
vectors of different widths can never share a chunk table. The platform used to
enforce that with ONE global embedder (``EMBEDDING_DIM``): any embedding model of
another width (Gemini 3072, an on-prem Qwen3 1024, OpenAI 1536, Voyage 1024) was
refused outright, although the schema already holds one chunk table per width
(``knowledge_chunks_768/1024/1536/2048/3072``).

Now a collection is bound to an embedder when it is created:

* ``knowledge_collections.embedding_provider`` / ``embedding_model`` name the
  bound model (a Model Registry entry, or the deployment's default embedder) and
  ``embedding_dim`` its width, which selects the chunk table.
* ``embedding_provider`` set = an explicit binding (made at creation or by a
  re-embed): served by exactly that model, never silently by another.
* ``embedding_provider`` NULL = a binding derived from the collection's
  ``embedder`` label by the migration (collections created before bindings
  existed, which were always embedded with the deployment default). When that
  model is not configured any more, the default embedder serves it as long as
  the widths agree — exactly what happened before.
* ``embedding_model`` NULL = unbound (no label was known): the default embedder.

:class:`CollectionEmbedders` turns a binding into an embedder. The default
embedder serves every binding whose model it IS; any other model is built from
its Model Registry entry (own ``base_url`` + vault-encrypted key, same-model
failover, :class:`~app.providers.registry_embedder.DimensionCheckedEmbedder`
held to the COLLECTION's width) and cached until the registry changes.

The deployment default (``EMBEDDING_DIM`` / the selected default embedder) is
what new collections get when no embedder is named; memory, the semantic cache
and other non-collection users keep embedding with it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger
from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS, EmbeddingDimensionError

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_EMBEDDER_KEY",
    "CollectionEmbedderUnavailableError",
    "CollectionEmbedders",
    "EmbedderOption",
    "EmbeddingBinding",
    "UnknownEmbedderError",
    "chunk_table_for",
    "load_collection_binding",
]

DEFAULT_EMBEDDER_KEY = "default"
_DEFAULT_HINTS = frozenset({"", "default", "auto"})
_CACHE_CHECK_INTERVAL_S = 5.0
_PROBE_TIMEOUT_S = 30.0
# Labels the platform once wrote without knowing the model (never a model id).
_UNKNOWN_LABELS = frozenset({"", "unknown", "voyage"})


class CollectionEmbedderUnavailableError(RuntimeError):
    """The collection's bound embedding model cannot be served by this deployment."""


class UnknownEmbedderError(ValueError):
    """The requested embedder is not a configured embedding model."""


def chunk_table_for(dimension: int) -> str:
    """The chunk table holding ``dimension``-wide vectors (EmbeddingDimensionError if none)."""
    from app.rag.store import _chunk_table

    return _chunk_table(dimension)


def _norm_model(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v[len("models/") :] if v.startswith("models/") else v


def _norm_provider(value: str | None) -> str:
    p = (value or "").strip().lower()
    return {"google": "gemini", "openai_compatible": "openai"}.get(p, p)


def _same_model(a: str | None, b: str | None) -> bool:
    na, nb = _norm_model(a), _norm_model(b)
    return bool(na) and na == nb


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


@dataclass(frozen=True)
class EmbeddingBinding:
    """The embedder a collection is bound to (see the module docstring)."""

    provider: str | None = None
    model: str | None = None
    dimension: int | None = None

    @property
    def explicit(self) -> bool:
        """Bound at creation / by a re-embed (strict), not derived from a label."""
        return bool((self.provider or "").strip())

    @property
    def key(self) -> str:
        """``provider/model`` (or the bare model / ``default``) for display and logs."""
        if not self.model:
            return DEFAULT_EMBEDDER_KEY
        return f"{self.provider}/{self.model}" if self.provider else self.model

    @classmethod
    def of(cls, collection: Any) -> EmbeddingBinding:
        """The binding of a collection object (dataclass or ORM row)."""
        model = str(getattr(collection, "embedding_model", None) or "").strip() or None
        provider = str(getattr(collection, "embedding_provider", None) or "").strip() or None
        return cls(
            provider=provider,
            model=model,
            dimension=_positive(getattr(collection, "embedding_dim", None)),
        )


@dataclass
class EmbedderOption:
    """One embedder a new collection may be bound to (``GET /knowledge/embedders``)."""

    key: str
    provider: str
    model: str
    dimension: int | None
    is_default: bool = False
    available: bool = True
    reason: str = ""
    source: str = "registry"  # "default" | "registry"
    chunk_table: str | None = None
    # Registry entry the option was built from (None for the default).
    entry: Any = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "is_default": self.is_default,
            "available": self.available,
            "reason": self.reason,
            "source": self.source,
            "chunk_table": self.chunk_table,
        }


def _dimension_problem(dimension: int | None) -> str:
    """Why vectors of ``dimension`` cannot be stored (empty = they can)."""
    if dimension is None:
        return (
            "its output width is unknown; run 'Test connection' on it in the Model "
            "Registry (or set output_dimensions) so the right chunk table can be chosen"
        )
    if dimension not in SUPPORTED_EMBEDDING_DIMENSIONS:
        return (
            f"it produces {dimension}-d vectors and there is no chunk table of that width "
            f"(supported: {', '.join(str(d) for d in SUPPORTED_EMBEDDING_DIMENSIONS)}); "
            "set output_dimensions to a supported width if the model allows it"
        )
    return ""


class CollectionEmbedders:
    """Resolves the embedder of a collection binding (cached per model and width).

    ``default`` returns the deployment's default embedder (e.g. ``lambda:
    app.state.embedder``, so a late-bound / reloaded default is always the
    current one); ``resolution`` its :class:`EmbedderResolution` (for the
    provider name and declared width), when there is one.
    """

    def __init__(
        self,
        default: Callable[[], Any] | None = None,
        *,
        resolution: Callable[[], Any] | None = None,
        settings: Any = None,
        registry: Any = None,
        version: Callable[[], int | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        check_interval_s: float = _CACHE_CHECK_INTERVAL_S,
    ) -> None:
        self._default = default or (lambda: None)
        self._resolution = resolution or (lambda: None)
        self._settings = settings
        self._registry = registry
        self._version_fn = version
        self._clock = clock
        self._interval = check_interval_s
        self._cache: dict[tuple[str, str, int | None], Any] = {}
        self._cache_version: int | None = None
        self._checked_at = float("-inf")

    # ── configuration ────────────────────────────────────────────────────────

    def settings(self) -> Any:
        if self._settings is not None:
            return self._settings
        from app.core.config import get_settings

        return get_settings()

    def default_embedder(self) -> Any:
        try:
            return self._default()
        except Exception:  # pragma: no cover - a broken getter is "no default"
            return None

    def default_identity(
        self, default: Any = None, *, guess_width: bool = True
    ) -> tuple[str, int | None, str]:
        """``(model name, width, provider)`` of the default embedder ("" / None if unknown).

        The width is the embedder's real one when known, else the resolution's,
        else (``guess_width``) ``EMBEDDING_DIM`` — the width new default-bound
        collections get. Resolution never refuses on that guess.
        """
        from app.providers.embedder_factory import (
            embedder_dimension,
            embedder_model_name,
            target_embedding_dim,
        )

        embedder = default if default is not None else self.default_embedder()
        resolution = self._safe_resolution()
        name = embedder_model_name(embedder) if embedder is not None else ""
        name = name or str(getattr(resolution, "model", "") or "")
        dim = embedder_dimension(embedder) if embedder is not None else None
        dim = dim or _positive(getattr(resolution, "dimension", None))
        if dim is None and guess_width:
            try:
                dim = target_embedding_dim(self.settings())
            except Exception:  # pragma: no cover - settings are always readable
                dim = None
        provider = str(getattr(resolution, "provider", "") or "")
        return name, dim, provider

    def _default_width_known(self, default: Any) -> bool:
        from app.providers.embedder_factory import embedder_dimension

        return bool(
            embedder_dimension(default)
            or _positive(getattr(self._safe_resolution(), "dimension", None))
        )

    def _safe_resolution(self) -> Any:
        try:
            return self._resolution()
        except Exception:  # pragma: no cover
            return None

    def _models(self, *, include_ineligible: bool = False) -> list[Any]:
        from app.ai_router.models import TaskType
        from app.ai_router.selection import ordered_configured_models

        try:
            return list(
                ordered_configured_models(
                    TaskType.EMBEDDING,
                    registry=self._registry,
                    include_ineligible=include_ineligible,
                )
            )
        except Exception as exc:  # never fail a request on the registry
            logger.warning("collection_embedders_registry_unreadable", error=str(exc)[:200])
            return []

    # ── options for new collections ──────────────────────────────────────────

    def options(self) -> list[EmbedderOption]:
        """Every embedder a new collection may be bound to, default first."""
        from app.ai_router.selection import is_eligible, model_key
        from app.providers.registry_embedder import embedding_model_dimension

        default = self.default_embedder()
        d_name, d_dim, d_provider = self.default_identity(default)
        options: list[EmbedderOption] = []
        if default is not None:
            problem = _dimension_problem(d_dim)
            options.append(
                EmbedderOption(
                    key=DEFAULT_EMBEDDER_KEY,
                    provider=d_provider or "default",
                    model=d_name or "unknown",
                    dimension=d_dim,
                    is_default=True,
                    available=not problem,
                    reason=f"The default embedder cannot embed collections: {problem}"
                    if problem
                    else "",
                    source="default",
                    chunk_table=None if problem or d_dim is None else f"knowledge_chunks_{d_dim}",
                )
            )
        settings = self.settings()
        seen: set[str] = set()
        for entry in self._models(include_ineligible=True):
            key = model_key(entry)
            if key in seen:
                continue
            seen.add(key)
            model_id = str(getattr(entry, "model_id", "") or "")
            provider = str(getattr(entry, "provider", "") or "")
            try:
                dims = embedding_model_dimension(provider, model_id, settings)
            except Exception:  # pragma: no cover
                dims = None
            reason = ""
            if not is_eligible(entry):
                reason = f"provider {provider!r} has no credentials on this deployment"
            elif problem := _dimension_problem(dims):
                reason = problem
            is_default = (
                default is not None
                and _same_model(model_id, d_name)
                and (dims is None or d_dim is None or dims == d_dim)
            )
            options.append(
                EmbedderOption(
                    key=key,
                    provider=provider,
                    model=model_id,
                    dimension=dims if dims is not None else (d_dim if is_default else None),
                    is_default=is_default,
                    available=not reason,
                    reason=reason,
                    source="registry",
                    chunk_table=None if reason or dims is None else f"knowledge_chunks_{dims}",
                    entry=entry,
                )
            )
        return options

    def _match_option(self, requested: str, options: list[EmbedderOption]) -> EmbedderOption:
        wanted = requested.strip()
        lowered = wanted.lower()
        by_key = [o for o in options if o.key.lower() == lowered]
        if by_key:
            return by_key[0]
        by_model = [o for o in options if _same_model(o.model, wanted)]
        if by_model:
            # Prefer a servable entry, then the default.
            by_model.sort(key=lambda o: (not o.available, not o.is_default))
            return by_model[0]
        # Legacy ``embedder_type`` hints: a provider / vendor name ("nvidia", "voyage").
        norm = _norm_provider(lowered.replace("_", "-"))
        by_provider = [o for o in options if _norm_provider(o.provider) == norm]
        if by_provider:
            by_provider.sort(key=lambda o: (not o.available, not o.is_default))
            return by_provider[0]
        configured = (
            ", ".join(
                f"{o.key} ({o.model}"
                + (f", {o.dimension}-d" if o.dimension else "")
                + ("" if o.available else ", unavailable")
                + ")"
                for o in options
            )
            or "none"
        )
        raise UnknownEmbedderError(
            f"embedding model {requested!r} is not configured on this deployment "
            f"(configured: {configured}); add it in the Model Registry first"
        )

    async def binding_for_new_collection(self, requested: str | None) -> EmbeddingBinding:
        """Validate ``requested`` (an option key, a model id, ``default``) → binding.

        Raises :class:`UnknownEmbedderError` (not configured),
        :class:`EmbeddingDimensionError` (no chunk table for its width) or
        :class:`CollectionEmbedderUnavailableError` (configured but not
        servable here). An unknown width is measured with one probe embed.
        """
        wanted = (requested or "").strip()
        default = self.default_embedder()
        if wanted.lower() in _DEFAULT_HINTS:
            if default is None:
                # No embedder at all (yet): the collection is sized to EMBEDDING_DIM
                # and adopts whichever default embeds it first (legacy behaviour).
                return EmbeddingBinding()
            d_name, d_dim, d_provider = self.default_identity(default)
            # (The store refuses a default width with no chunk table when it
            # persists the collection — 422 — exactly as before bindings.)
            # A width that is only EMBEDDING_DIM (the default embedder's real one
            # is not known yet) is not a binding: the first write fixes it.
            known = self._default_width_known(default)
            return EmbeddingBinding(
                provider=(d_provider or DEFAULT_EMBEDDER_KEY) if known else None,
                model=d_name or None,
                dimension=d_dim,
            )
        option = self._match_option(wanted, self.options())
        if option.source == "default" or option.is_default:
            # The default embedder IS this model: it serves the collection.
            return await self.binding_for_new_collection(DEFAULT_EMBEDDER_KEY)
        width_problem = _dimension_problem(option.dimension)
        if option.dimension is not None and width_problem:
            raise EmbeddingDimensionError(f"embedding model {option.key}: {width_problem}")
        if not option.available and option.reason != width_problem:
            raise CollectionEmbedderUnavailableError(
                f"embedding model {option.key} cannot be used: {option.reason}"
            )
        dimension = option.dimension
        if dimension is None:
            dimension = await self._probe_dimension(option)
        chunk_table_for(dimension)
        binding = EmbeddingBinding(
            provider=option.provider, model=option.model, dimension=dimension
        )
        self.resolve(binding)  # servable here? (credentials / endpoint policy)
        return binding

    async def _probe_dimension(self, option: EmbedderOption) -> int:
        """Measure an unknown width with one embed (recorded for later selections)."""
        from app.providers.base import EmbedRequest

        embedder = self._build(
            EmbeddingBinding(provider=option.provider, model=option.model, dimension=None),
            option.entry,
        )
        try:
            response = await asyncio.wait_for(
                embedder.embed(EmbedRequest(texts=["dimension probe"], model=option.model)),
                _PROBE_TIMEOUT_S,
            )
        except Exception as exc:
            raise CollectionEmbedderUnavailableError(
                f"embedding model {option.key} could not be reached to measure its output "
                f"width ({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc
        vectors = list(getattr(response, "embeddings", []) or [])
        if not vectors or not vectors[0]:
            raise CollectionEmbedderUnavailableError(
                f"embedding model {option.key} returned no vector for a probe"
            )
        dimension = len(vectors[0])
        problem = _dimension_problem(dimension)
        if problem:
            raise EmbeddingDimensionError(f"embedding model {option.key}: {problem}")
        return dimension

    # ── resolution ───────────────────────────────────────────────────────────

    def resolve(self, binding: EmbeddingBinding | None, *, default: Any = None) -> Any:
        """The embedder serving ``binding`` (``default`` = the caller's default embedder).

        Raises :class:`CollectionEmbedderUnavailableError` when the bound model
        cannot be served here.
        """
        fallback = default if default is not None else self.default_embedder()
        if binding is None or not binding.model:
            # Unbound: the default, as before bindings (an empty collection adopts
            # its width on the first write; the persistence guard refuses a
            # mismatch once it holds vectors).
            return fallback
        d_name, d_dim, _ = self.default_identity(fallback, guess_width=False)
        widths_agree = binding.dimension is None or d_dim is None or binding.dimension == d_dim
        if fallback is not None and _same_model(binding.model, d_name) and widths_agree:
            return fallback
        entry = self._registry_entry(binding)
        if entry is not None:
            return self._cached(binding, entry)
        if not binding.explicit and fallback is not None and widths_agree:
            # A label-derived binding of a model this deployment no longer names:
            # the collection was embedded with the deployment default.
            logger.warning(
                "collection_embedder_label_unmatched",
                label=binding.model,
                dimension=binding.dimension,
                default_model=d_name,
            )
            return fallback
        raise CollectionEmbedderUnavailableError(
            f"the collection is bound to embedding model {binding.key}"
            + (f" ({binding.dimension}-d)" if binding.dimension else "")
            + ", which is not configured on this deployment; add it in the Model Registry "
            "or re-embed the collection with a configured model "
            "(POST /knowledge/collections/{id}/re-embed with embedding_model)"
        )

    def _registry_entry(self, binding: EmbeddingBinding) -> Any:
        matches = [
            m for m in self._models() if _same_model(getattr(m, "model_id", ""), binding.model)
        ]
        if not matches:
            return None
        if binding.provider:
            wanted = _norm_provider(binding.provider)
            matches.sort(key=lambda m: _norm_provider(str(getattr(m, "provider", ""))) != wanted)
        return matches[0]

    def _current_version(self) -> int | None:
        fn = self._version_fn
        if fn is None:
            from app.providers.embedder_factory import _registry_version

            fn = _registry_version
        try:
            return fn()
        except Exception:  # never fail an embed on the version check
            return None

    def _cached(self, binding: EmbeddingBinding, entry: Any) -> Any:
        now = self._clock()
        if now - self._checked_at >= self._interval:
            self._checked_at = now
            version = self._current_version()
            if version != self._cache_version:
                self._cache_version = version
                self._cache.clear()
        cache_key = (
            _norm_provider(str(getattr(entry, "provider", ""))),
            _norm_model(str(getattr(entry, "model_id", ""))),
            binding.dimension,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        embedder = self._build(binding, entry)
        self._cache[cache_key] = embedder
        return embedder

    def _build(self, binding: EmbeddingBinding, entry: Any) -> Any:
        """The registry entry's embedder, held to the collection's width."""
        from app.observability.traced_provider import traced_embedder
        from app.providers.registry_embedder import _build_choice

        if entry is None:
            entry = self._registry_entry(binding)
        if entry is None:
            raise CollectionEmbedderUnavailableError(
                f"embedding model {binding.key} is not configured on this deployment"
            )
        choice = _build_choice(entry, self._models(), self.settings(), target_dim=binding.dimension)
        if choice.embedder is None:
            raise CollectionEmbedderUnavailableError(
                f"embedding model {binding.key} cannot be used for this collection: "
                f"{choice.refusal}"
            )
        logger.info(
            "collection_embedder_built",
            model=choice.model_id,
            endpoints=choice.endpoints,
            dimension=binding.dimension,
        )
        return traced_embedder(choice.embedder)

    async def for_collection(
        self,
        store: Any,
        collection_id: str,
        *,
        tenant_ctx: Any,
        default: Any = None,
        collection: Any = None,
    ) -> Any:
        """The embedder of ``collection_id`` (read through ``store`` unless given).

        An unknown collection resolves to the default (the caller answers 404).
        """
        from app.rag.store import KnowledgeStore

        # Only a real KnowledgeStore knows bindings (test doubles keep the default).
        if collection is None and isinstance(store, KnowledgeStore) and collection_id:
            collection = await store.get_collection_async(collection_id, tenant_ctx=tenant_ctx)
        if collection is None:
            return default if default is not None else self.default_embedder()
        return self.resolve(EmbeddingBinding.of(collection), default=default)


async def load_collection_binding(
    session: Any, *, tenant_id: str, collection_id: str
) -> EmbeddingBinding | None:
    """The binding of one active collection, read in an RLS-scoped session."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT embedding_provider, embedding_model, embedding_dim "
                "FROM knowledge_collections "
                "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
            ),
            {"cid": collection_id, "tid": tenant_id},
        )
    ).fetchone()
    if row is None:
        return None
    return EmbeddingBinding(
        provider=str(row[0]).strip() or None if row[0] is not None else None,
        model=str(row[1]).strip() or None if row[1] is not None else None,
        dimension=_positive(int(row[2])) if row[2] is not None else None,
    )


def is_unknown_label(label: str | None) -> bool:
    """A collection ``embedder`` label that names no model (written before USR-3)."""
    return (label or "").strip().lower() in _UNKNOWN_LABELS
