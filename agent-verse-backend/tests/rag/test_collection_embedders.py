"""Per-collection embedders: binding resolution and chunk-table selection.

A knowledge collection is bound to ONE embedding model (a Model Registry entry
or the deployment default) and its width; ``CollectionEmbedders`` turns the
binding into the embedder every ingest / re-embed / query of the collection
uses. These are unit tests: the registry is the in-process one (no Redis), the
registry embedders are built but never called over the network.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore
from app.core.config import Settings
from app.observability.traced_provider import unwrap_provider
from app.providers.base import EmbedRequest, EmbedResponse
from app.providers.embedder_factory import embedder_model_name
from app.providers.registry_embedder import DimensionCheckedEmbedder
from app.rag.collection_embedders import (
    CollectionEmbedders,
    CollectionEmbedderUnavailableError,
    EmbeddingBinding,
    UnknownEmbedderError,
    chunk_table_for,
    is_unknown_label,
)
from app.rag.models import KnowledgeCollection
from app.rag.store import EmbeddingDimensionError, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

_ISOLATE_PROVIDER_ENV = True

_EM = ModelCapability.EMBEDDING
_QWEN = "Qwen/Qwen3-Embedding-0.6B"  # 1024-d in the catalog
_NEMOTRON = "nvidia/nemotron-3-embed-1b"
_GEMINI = "gemini-embedding-001"
_LAN = "http://192.168.63.104:30082/v1"
_CTX = TenantContext(tenant_id="t-collemb", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get(self, k: str) -> Any:
        return self.store.get(k)

    def set(self, k: str, v: Any) -> None:
        self.store[k] = v

    def incr(self, k: str) -> int:
        self.store[k] = str(int(self.store.get(k) or 0) + 1)
        return int(self.store[k])


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.ai_router.registry_store as rs
    import app.ai_router.selection as sel

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(_FakeRedis()))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


class _DefaultEmbedder:
    """The deployment default: NVIDIA nemotron, 2048-d."""

    _embed_model = _NEMOTRON
    embedding_dim = 2048

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.5] * 2048 for _ in request.texts], model=_NEMOTRON)


class _Resolution:
    provider = "nvidia"
    model = _NEMOTRON
    dimension = 2048


def _register(model_id: str, *, provider: str = "onprem", base_url: str | None = _LAN,
              **extra: Any) -> ModelEndpoint:
    m = ModelEndpoint(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        capabilities=[_EM],
        base_url=base_url,
        extra={"source": "override", **extra},
    )
    model_registry.register_configured(m)
    return m


def _resolver(default: Any = None, **kwargs: Any) -> CollectionEmbedders:
    emb = default if default is not None else _DefaultEmbedder()
    return CollectionEmbedders(
        lambda: emb,
        resolution=lambda: _Resolution(),
        settings=Settings(_env_file=None, embedding_dim=2048),  # type: ignore[call-arg]
        **kwargs,
    )


# ── chunk tables ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dim", [768, 1024, 1536, 2048, 3072])
def test_every_supported_width_has_its_own_chunk_table(dim: int) -> None:
    assert chunk_table_for(dim) == f"knowledge_chunks_{dim}"


@pytest.mark.parametrize("dim", [384, 4096, 0])
def test_a_width_without_a_chunk_table_is_a_clear_error(dim: int) -> None:
    with pytest.raises(EmbeddingDimensionError, match="Unsupported embedding dimension"):
        chunk_table_for(dim)


def test_labels_that_name_no_model_are_recognised() -> None:
    assert is_unknown_label("unknown") and is_unknown_label("Voyage") and is_unknown_label("")
    assert not is_unknown_label(_QWEN)


# ── binding of a collection ──────────────────────────────────────────────────


def test_binding_reads_the_collection_fields() -> None:
    explicit = EmbeddingBinding.of(
        KnowledgeCollection(name="q", embedding_dim=1024, embedding_provider="onprem",
                            embedding_model=_QWEN)
    )
    assert explicit == EmbeddingBinding("onprem", _QWEN, 1024)
    assert explicit.explicit and explicit.key == f"onprem/{_QWEN}"
    derived = EmbeddingBinding.of(KnowledgeCollection(name="d", embedding_model=_QWEN))
    assert not derived.explicit and derived.key == _QWEN
    assert EmbeddingBinding.of(KnowledgeCollection(name="u")).key == "default"


# ── resolution ───────────────────────────────────────────────────────────────


def test_an_unbound_collection_uses_the_default_embedder() -> None:
    default = _DefaultEmbedder()
    resolver = _resolver(default)
    assert resolver.resolve(None) is default
    assert resolver.resolve(EmbeddingBinding(dimension=2048)) is default


def test_an_unbound_collection_keeps_the_pre_binding_behaviour() -> None:
    """Unbound (no model known): the default embeds it; an empty one adopts the
    default's width on the first write and the persistence guard refuses a
    mismatch once it holds vectors — never a refusal on a guessed width."""
    default = _DefaultEmbedder()
    assert _resolver(default).resolve(EmbeddingBinding(dimension=1024)) is default


def test_a_binding_to_the_default_model_is_served_by_the_default_embedder() -> None:
    default = _DefaultEmbedder()
    binding = EmbeddingBinding("nvidia", _NEMOTRON, 2048)
    assert _resolver(default).resolve(binding) is default
    # The caller's default (e.g. a traced / budgeted wrapper) wins over the getter's.
    caller_default = _DefaultEmbedder()
    assert _resolver(default).resolve(binding, default=caller_default) is caller_default


def test_a_registry_binding_gets_that_models_embedder_held_to_the_collection_width() -> None:
    _register(_QWEN, api_key_encrypted=None)
    resolver = _resolver()
    embedder = resolver.resolve(EmbeddingBinding("onprem", _QWEN, 1024))
    inner = unwrap_provider(embedder)
    assert isinstance(inner, DimensionCheckedEmbedder)
    assert inner._expected == 1024  # the COLLECTION's width, not EMBEDDING_DIM (2048)
    assert embedder_model_name(embedder) == _QWEN
    assert str(inner._inner._base_url).rstrip("/") == _LAN
    # Cached: one client per (model, width), not one per request.
    assert resolver.resolve(EmbeddingBinding("onprem", _QWEN, 1024)) is embedder


def test_the_cache_is_rebuilt_when_the_registry_changes() -> None:
    _register(_QWEN)
    version = {"v": 1}
    resolver = _resolver(version=lambda: version["v"], check_interval_s=0.0)
    first = resolver.resolve(EmbeddingBinding("onprem", _QWEN, 1024))
    assert resolver.resolve(EmbeddingBinding("onprem", _QWEN, 1024)) is first
    version["v"] = 2
    assert resolver.resolve(EmbeddingBinding("onprem", _QWEN, 1024)) is not first


def test_a_registry_model_of_another_width_than_the_collection_is_refused() -> None:
    _register(_QWEN)  # 1024-d
    with pytest.raises(CollectionEmbedderUnavailableError, match="1024-d"):
        _resolver().resolve(EmbeddingBinding("onprem", _QWEN, 768))


def test_an_explicit_binding_to_an_unconfigured_model_is_never_served_by_another() -> None:
    with pytest.raises(CollectionEmbedderUnavailableError, match="not configured"):
        _resolver().resolve(EmbeddingBinding("gemini", _GEMINI, 2048))


def test_a_derived_binding_of_a_gone_model_falls_back_to_the_default_of_its_width() -> None:
    """Pre-binding collections were embedded with the deployment default."""
    default = _DefaultEmbedder()
    assert _resolver(default).resolve(EmbeddingBinding(None, "old-label", 2048)) is default
    with pytest.raises(CollectionEmbedderUnavailableError):
        _resolver(default).resolve(EmbeddingBinding(None, "old-label", 1024))


def test_provider_disambiguates_two_entries_of_one_model() -> None:
    _register(_QWEN, provider="onprem", base_url=_LAN)
    _register(_QWEN, provider="openai", base_url="http://10.1.2.3:8000/v1")
    embedder = _resolver().resolve(EmbeddingBinding("openai", _QWEN, 1024))
    inner = unwrap_provider(embedder)
    # The chosen entry's endpoint comes first (the other is its same-model failover).
    endpoints = getattr(inner._inner, "endpoint_labels", None)
    assert endpoints is not None and endpoints[0].startswith("openai@10.1.2.3")


# ── options and bindings for new collections ─────────────────────────────────


def test_options_list_the_default_and_every_registry_model_with_its_table() -> None:
    _register(_QWEN)
    _register("acme/embed-wide", dimensions=4096)
    _register("acme/embed-unmeasured")
    by_key = {o.key: o for o in _resolver().options()}
    default = by_key["default"]
    assert (default.model, default.dimension, default.is_default) == (_NEMOTRON, 2048, True)
    assert default.chunk_table == "knowledge_chunks_2048"
    qwen = by_key[f"onprem/{_QWEN}"]
    assert (qwen.dimension, qwen.available, qwen.chunk_table) == (1024, True,
                                                                   "knowledge_chunks_1024")
    wide = by_key["onprem/acme/embed-wide"]
    assert not wide.available and "no chunk table" in wide.reason
    unmeasured = by_key["onprem/acme/embed-unmeasured"]
    assert unmeasured.dimension is None and "unknown" in unmeasured.reason


async def test_a_new_collection_defaults_to_the_selected_default_embedder() -> None:
    binding = await _resolver().binding_for_new_collection(None)
    assert binding == EmbeddingBinding("nvidia", _NEMOTRON, 2048)
    assert await _resolver().binding_for_new_collection("default") == binding


async def test_a_new_collection_can_be_bound_to_a_registry_model_of_another_width() -> None:
    _register(_QWEN)
    for requested in (f"onprem/{_QWEN}", _QWEN, _QWEN.lower()):
        binding = await _resolver().binding_for_new_collection(requested)
        assert binding == EmbeddingBinding("onprem", _QWEN, 1024), requested


async def test_requesting_the_default_model_by_its_registry_key_binds_the_default() -> None:
    _register(_NEMOTRON, provider="nvidia", base_url=None, dimensions=2048)
    binding = await _resolver().binding_for_new_collection(f"nvidia/{_NEMOTRON}")
    assert (binding.model, binding.dimension) == (_NEMOTRON, 2048)


async def test_an_unsupported_width_is_refused_with_the_supported_ones() -> None:
    _register("acme/embed-wide", dimensions=4096)
    with pytest.raises(EmbeddingDimensionError, match=r"4096-d.*supported: 768, 1024"):
        await _resolver().binding_for_new_collection("acme/embed-wide")


async def test_an_unconfigured_model_is_refused_naming_the_configured_ones() -> None:
    _register(_QWEN)
    with pytest.raises(UnknownEmbedderError, match=r"not configured.*onprem/Qwen"):
        await _resolver().binding_for_new_collection("text-embedding-3-large")


async def test_an_unknown_width_is_measured_once_at_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register("acme/embed-unmeasured")

    class _Probe:
        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            return EmbedResponse(embeddings=[[0.1] * 1536 for _ in request.texts])

    resolver = _resolver()
    monkeypatch.setattr(resolver, "_build", lambda binding, entry: _Probe())
    binding = await resolver.binding_for_new_collection("acme/embed-unmeasured")
    assert binding == EmbeddingBinding("onprem", "acme/embed-unmeasured", 1536)


async def test_without_any_embedder_a_new_collection_is_unbound() -> None:
    resolver = CollectionEmbedders(lambda: None)
    assert await resolver.binding_for_new_collection(None) == EmbeddingBinding()


# ── the store routes every collection to its embedder ────────────────────────


async def test_the_store_resolves_each_collection_to_its_bound_embedder() -> None:
    _register(_QWEN)
    default = _DefaultEmbedder()
    store = KnowledgeStore(embedding_dim=2048)
    store.collection_embedders = _resolver(default)
    nvidia = KnowledgeCollection(name="n", embedding_dim=2048, embedding_provider="nvidia",
                                 embedding_model=_NEMOTRON)
    qwen = KnowledgeCollection(name="q", embedding_dim=1024, embedding_provider="onprem",
                               embedding_model=_QWEN)
    for c in (nvidia, qwen):
        await store.create_collection_async(c, tenant_ctx=_CTX)
    assert await store.embedder_for_collection(nvidia.collection_id, tenant_ctx=_CTX,
                                               default=default) is default
    qwen_embedder = await store.embedder_for_collection(qwen.collection_id, tenant_ctx=_CTX,
                                                        default=default)
    assert embedder_model_name(qwen_embedder) == _QWEN
    # An unknown collection resolves to the default (the caller answers 404).
    assert await store.embedder_for_collection("missing", tenant_ctx=_CTX,
                                               default=default) is default


# ── the retrieval gateway embeds each query with the collection's model ──────


class _Row:
    def __init__(self, values: tuple[Any, ...] | None) -> None:
        self._values = values

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._values


class _BindingSession:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Row:
        self.sql.append(str(stmt))
        return _Row(self.row)


class _Runner:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.session = _BindingSession(row)

    async def run(self, operation: Any, *, repeatable_read: bool = False) -> Any:
        return await operation(self.session)


def _gateway(resolver: Any, default: Any) -> Any:
    from app.rag.gateway import (
        KnowledgeStoreCollectionAuthorizer,
        RetrievalDependencies,
        RetrievalGateway,
    )

    return RetrievalGateway(
        RetrievalDependencies(
            session_factory=None,
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(KnowledgeStore()),
            strategy_capabilities={},
            embedder=default,
            collection_embedders=resolver,
        )
    )


async def test_the_gateway_resolves_the_collection_binding_for_query_embedding() -> None:
    from app.rag.contracts import RAGStrategy

    _register(_QWEN)
    default = _DefaultEmbedder()
    gateway = _gateway(_resolver(default), default)
    runner = _Runner(("onprem", _QWEN, 1024))
    embedder = await gateway._collection_embedder(runner, _CTX, "c-qwen", RAGStrategy.HYBRID)
    assert embedder_model_name(embedder) == _QWEN
    assert "embedding_provider" in runner.session.sql[0]
    assert "tenant_id = :tid" in runner.session.sql[0]  # explicit tenant predicate, not RLS only
    nvidia = await gateway._collection_embedder(
        _Runner(("nvidia", _NEMOTRON, 2048)), _CTX, "c-nv", RAGStrategy.HYBRID
    )
    assert nvidia is default


async def test_an_unservable_binding_fails_semantic_retrieval_with_the_reason() -> None:
    from app.rag.contracts import RAGStrategy
    from app.rag.engine import RetrievalStrategyExecutionError

    default = _DefaultEmbedder()
    gateway = _gateway(_resolver(default), default)
    embedder = await gateway._collection_embedder(
        _Runner(("gemini", _GEMINI, 3072)), _CTX, "c-gem", RAGStrategy.HYBRID
    )
    with pytest.raises(RetrievalStrategyExecutionError, match="not configured"):
        await embedder.embed(EmbedRequest(texts=["q"]))


async def test_without_a_resolver_the_gateway_keeps_its_single_embedder() -> None:
    from app.rag.contracts import RAGStrategy

    default = _DefaultEmbedder()
    gateway = _gateway(None, default)
    assert await gateway._collection_embedder(
        _Runner(("onprem", _QWEN, 1024)), _CTX, "c", RAGStrategy.HYBRID
    ) is default


def test_the_query_embedding_cache_key_separates_models_and_widths() -> None:
    from app.rag.gateway import _BudgetedEmbedder

    _register(_QWEN)
    qwen = _resolver().resolve(EmbeddingBinding("onprem", _QWEN, 1024))
    keys = {
        _BudgetedEmbedder(e, guard=None)._cache_key(  # type: ignore[arg-type]
            _BudgetedEmbedder(e, guard=None)._model(EmbedRequest(texts=["q"]))  # type: ignore[arg-type]
        )
        for e in (qwen, _DefaultEmbedder())
    }
    assert keys == {f"{_QWEN}@1024", f"{_NEMOTRON}@2048"}


# ── non-collection users: the semantic cache never compares across widths ────


def test_the_semantic_cache_never_matches_vectors_of_different_widths() -> None:
    from app.rag.semantic_cache import _cosine

    assert _cosine([1.0] * 1024, [1.0] * 2048) == 0.0
    assert _cosine([1.0] * 4, [1.0] * 4) == pytest.approx(1.0)
