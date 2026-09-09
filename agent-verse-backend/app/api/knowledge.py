"""Knowledge API — collections, document ingestion, hybrid search, semantic cache."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import uuid as _uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field, SecretStr, model_validator

from app.core.config import get_settings
from app.ingestion.orchestrator import EmptyIndexedContentError
from app.ingestion.repository_security import (
    RepositoryLimits,
    RepositorySecurityError,
    read_repository_files,
    repository_usage,
    resolve_repository_source,
    validate_branch,
    validate_patterns,
)
from app.net.ssrf_guard import SSRFError, assert_public_url
from app.rag.contracts import (
    RAGCitation,
    RAGExecutionResult,
    RAGStrategy,
    UnavailableRAGStrategyError,
    UnknownRAGStrategyError,
    resolve_rag_strategy,
)
from app.rag.gateway import CollectionNotFoundError
from app.rag.models import Chunk, Document, KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.rag_platform.retriever import RAGRetriever, RAGSynthesisError
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.rag.indexing import IndexingDependency

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# Embedding dimension used for random dummy embeddings when no real embedder is present.
_EMBEDDING_DIM = 768


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateCollectionRequest(BaseModel):
    name: str
    description: str = ""
    embedder_type: str = "voyage"


class IngestRequest(BaseModel):
    collection_id: str
    source_type: str = "text"  # git / markdown / text / openapi / python / code
    content: str
    metadata: dict[str, Any] = {}


class RepoIngestRequest(BaseModel):
    repo_url: str
    collection_id: str
    branch: str = "main"
    file_patterns: list[str] = ["**/*.py", "**/*.md", "**/*.ts", "**/*.js"]
    max_files: int = 200


class OpenAPIIngestRequest(BaseModel):
    content: str  # OpenAPI JSON or YAML string
    collection_id: str
    source_url: str = ""


class UrlIngestRequest(BaseModel):
    collection_id: str
    url: str
    source_type: str = "web"  # web|github|confluence|jira|slack


class RpaUrlIngestRequest(BaseModel):
    """Ingest one or more URLs scraped via the RPA executor (browser or httpx)."""

    collection_id: str
    urls: list[str]  # supports batch ingestion (max 20)
    selector: str = "body"  # CSS selector for text extraction
    screenshot: bool = False  # capture screenshot and store as metadata
    source_type: str = "rpa-web"  # stored in metadata for attribution
    max_chars: int = 50_000  # per-URL char cap
    include_links: bool = False  # whether to extract link URLs from the page


class GitHubIngestRequest(BaseModel):
    collection_id: str
    owner: str
    repo: str
    branch: str = "HEAD"
    max_files: int = 300


class ConfluenceIngestRequest(BaseModel):
    collection_id: str
    base_url: str
    space_key: str
    token: SecretStr  # SecretStr prevents token from appearing in logs or tracebacks
    user: str
    max_pages: int = 1000


class JiraIngestRequest(BaseModel):
    collection_id: str
    base_url: str
    project_key: str
    token: SecretStr  # SecretStr prevents token from appearing in logs or tracebacks
    user: str
    jql_extra: str = ""
    max_issues: int = 500


class SlackIngestRequest(BaseModel):
    collection_id: str
    channel_id: str
    token: SecretStr  # SecretStr prevents token from appearing in logs or tracebacks
    channel_name: str = ""
    max_messages: int = 500


IndexingStrategy = Literal["raptor", "agentic_chunking"]


class CollectionIngestRequest(BaseModel):
    """Request body for POST /collections/{collection_id}/documents (IngestionOrchestrator path)."""

    content: str = Field(min_length=1, max_length=1_000_000)
    content_type: str = "auto"
    source_url: str = ""
    source_identity: str = Field(
        default="",
        max_length=256,
        pattern=r"^[A-Za-z0-9._:/-]*$",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    in_memory_only: bool = False
    indexing_strategies: set[IndexingStrategy] = Field(default_factory=set)
    raptor_cluster_size: int = Field(default=4, ge=2, le=64)
    raptor_max_levels: int = Field(default=3, ge=1, le=8)
    parent_window_size: int = Field(default=1, ge=0, le=16)
    raptor_summary_batch_size: int = Field(default=16, ge=1, le=64)
    proposition_batch_size: int = Field(default=16, ge=1, le=64)
    embedding_batch_size: int = Field(default=64, ge=1, le=256)

    @model_validator(mode="after")
    def validate_indexed_ingestion(self) -> CollectionIngestRequest:
        if self.indexing_strategies and not self.source_identity:
            raise ValueError("source_identity is required for indexed ingestion")
        if self.indexing_strategies and self.in_memory_only:
            raise ValueError("indexed ingestion does not support in_memory_only")
        return self


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _knowledge_store(request: Request) -> KnowledgeStore:
    return request.app.state.knowledge_store  # type: ignore[no-any-return]


def _semantic_cache(request: Request) -> SemanticCache:
    return request.app.state.semantic_cache  # type: ignore[no-any-return]


def _retrieval_gateway(request: Request) -> Any:
    gateway = getattr(request.app.state, "retrieval_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="Retrieval service is unavailable")
    return gateway


async def _resolve_indexing_llm(
    request: Request,
    tenant_ctx: TenantContext,
    strategies: set[IndexingStrategy],
) -> dict[RAGStrategy, IndexingDependency]:
    from app.rag.indexing import IndexingDependency

    gateway = getattr(request.app.state, "retrieval_gateway", None)
    dependencies = getattr(gateway, "dependencies", None)
    resolver = getattr(dependencies, "llm_resolver", None)
    resolved_dependencies: dict[RAGStrategy, IndexingDependency] = {}
    if resolver is not None:
        for strategy_name in strategies:
            strategy = RAGStrategy(strategy_name)
            try:
                resolved = resolver(tenant_ctx, strategy)
                if inspect.isawaitable(resolved):
                    resolved = await resolved
            except Exception:
                return {}
            provider = getattr(resolved, "provider", None)
            model = str(getattr(resolved, "model", "") or "").strip()
            if provider is None or not model:
                return {}
            resolved_dependencies[strategy] = IndexingDependency(provider, model)
        return resolved_dependencies

    provider = getattr(request.app.state, "_app_provider", None)
    provider_default = getattr(provider, "_default_model", "")
    model = str(
        getattr(request.app.state, "indexing_model", "")
        or (provider_default if isinstance(provider_default, str) else "")
        or get_settings().default_model
    ).strip()
    if provider is None or not model:
        return {}
    return {RAGStrategy(strategy): IndexingDependency(provider, model) for strategy in strategies}


def _parse_retrieval_filters(filters: str | None) -> dict[str, Any]:
    if filters is None or not filters.strip():
        return {}
    try:
        parsed = json.loads(filters)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="filters must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="filters must be a JSON object")
    return parsed


def _raise_retrieval_http_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, UnknownRAGStrategyError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, CollectionNotFoundError):
        raise HTTPException(status_code=404, detail="Knowledge collection not found") from exc
    if isinstance(exc, UnavailableRAGStrategyError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, RAGSynthesisError):
        raise HTTPException(status_code=503, detail="Answer synthesis is unavailable") from exc
    raise HTTPException(status_code=503, detail="Retrieval service is unavailable") from exc


def _cache_stats(request: Request) -> dict[str, dict[str, int]]:
    """Per-tenant hit/miss counters stored lazily on app.state."""
    if not hasattr(request.app.state, "_cache_stats"):
        request.app.state._cache_stats = {}
    return request.app.state._cache_stats  # type: ignore[no-any-return]


def _fallback_embedding(dim: int = _EMBEDDING_DIM) -> list[float]:
    raise HTTPException(
        status_code=503,
        detail=(
            "Embedding provider not configured. "
            "Set one of: VOYAGE_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY "
            "(cloud embeddings) or SENTENCE_TRANSFORMERS_MODEL=all-MiniLM-L6-v2 "
            "(local CPU embeddings via sentence-transformers)."
        ),
    )


async def _persist_chunks_or_http(
    store: KnowledgeStore,
    chunks: list[Chunk],
    *,
    collection_id: str,
    tenant_ctx: TenantContext,
) -> list[str]:
    try:
        return await store.ingest_chunks_async(
            chunks,
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Knowledge collection not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid knowledge ingestion payload") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Knowledge persistence is unavailable",
        ) from exc


async def _embed_texts_or_http(texts: list[str], embedder: Any) -> list[list[float]]:
    if embedder is None:
        raise HTTPException(status_code=503, detail="Embedding provider is unavailable")
    from app.providers.base import embed_texts

    try:
        embeddings = await embed_texts(texts, provider=embedder)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Embedding provider is unavailable",
        ) from exc
    if len(embeddings) != len(texts) or any(not embedding for embedding in embeddings):
        raise HTTPException(status_code=503, detail="Embedding provider is unavailable")
    return embeddings


# ---------------------------------------------------------------------------
# Endpoints — collections
# ---------------------------------------------------------------------------


@router.get("/collections")
async def list_collections(request: Request) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _knowledge_store(request)
    collections = await store.list_collections_async(tenant_ctx=tenant_ctx)
    return [
        {
            "collection_id": c.collection_id,
            "name": c.name,
            "description": c.description,
            "document_count": c.document_count,
            "embedder": c.embedder,
        }
        for c in collections
    ]


@router.post("/collections", status_code=status.HTTP_201_CREATED)
async def create_collection(request: Request, body: CreateCollectionRequest) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _knowledge_store(request)
    collection = KnowledgeCollection(
        name=body.name,
        description=body.description,
        embedder=body.embedder_type,
    )
    try:
        cid = await store.create_collection_async(collection, tenant_ctx=tenant_ctx)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Knowledge persistence is unavailable",
        ) from exc
    return {
        "collection_id": cid,
        "name": body.name,
        "description": body.description,
        "document_count": 0,
        "embedder": body.embedder_type,
    }


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(request: Request, collection_id: str) -> None:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _knowledge_store(request)

    # Verify collection exists and belongs to this tenant
    collection = await store.get_collection_async(collection_id, tenant_ctx=tenant_ctx)
    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection {collection_id} not found",
        )

    # H-5: Block deletion if a legal hold is active on this resource
    _legal_hold_mgr = getattr(request.app.state, "legal_hold_manager", None)
    if _legal_hold_mgr is not None:
        try:
            _is_held = await _legal_hold_mgr.is_under_hold(
                resource_id=collection_id, tenant_id=tenant_ctx.tenant_id
            )
            if _is_held:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Resource is under legal hold and cannot be deleted",
                )
        except HTTPException:
            raise
        except Exception:
            pass  # Legal hold check failure is non-fatal; allow deletion

    try:
        deleted = await store.delete_collection_async(
            collection_id,
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Knowledge persistence is unavailable",
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge collection not found")


# ---------------------------------------------------------------------------
# Endpoints — ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_document(request: Request, body: IngestRequest) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _knowledge_store(request)

    # Verify collection exists.
    collection = await store.get_collection_async(body.collection_id, tenant_ctx=tenant_ctx)
    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection {body.collection_id} not found",
        )

    content_hash = hashlib.sha256(body.content.encode()).hexdigest()
    str_metadata = {k: str(v) for k, v in body.metadata.items()}
    document = Document(
        collection_id=body.collection_id,
        source=body.source_type,
        content=body.content,
        content_hash=content_hash,
        metadata=str_metadata,
    )

    # Split into token-aware chunks for accurate LLM context window usage.
    from app.knowledge.chunker_v2 import chunk_by_tokens

    _raw_chunks = chunk_by_tokens(body.content, max_tokens=512, overlap_tokens=64)
    chunks_text = [
        type("_C", (), {"content": chunk, "start_char": 0, "end_char": len(chunk)})()
        for chunk in _raw_chunks
    ]

    # Fallback: very short content that doesn't meet min_chunk threshold
    if not chunks_text and body.content.strip():
        chunks_text = [
            type(
                "_C",
                (),
                {"content": body.content.strip(), "start_char": 0, "end_char": len(body.content)},
            )()
        ]

    chunks: list[Chunk] = []
    embedder = getattr(request.app.state, "embedder", None)
    embeddings = await _embed_texts_or_http(
        [text_chunk.content for text_chunk in chunks_text],
        embedder,
    )
    for idx, text_chunk in enumerate(chunks_text):
        chunk_text = text_chunk.content
        chunks.append(
            Chunk(
                document_id=document.document_id,
                content=chunk_text,
                embedding=embeddings[idx],
                chunk_index=idx,
                metadata={**str_metadata, "source_type": body.source_type},
            )
        )
    await _persist_chunks_or_http(
        store,
        chunks,
        collection_id=body.collection_id,
        tenant_ctx=tenant_ctx,
    )

    return {
        "document_id": document.document_id,
        "collection_id": body.collection_id,
        "chunks_created": len(chunks),
        "content_hash": content_hash,
    }


# ---------------------------------------------------------------------------
# Endpoints — search
# ---------------------------------------------------------------------------


@router.get("/search")
async def search_knowledge(
    request: Request,
    q: str,
    collection_id: str,
    top_k: int | None = None,
    limit: int | None = Query(
        default=None,
        ge=1,
        le=100,
        deprecated=True,
        description="Deprecated alias translated to the canonical top_k parameter.",
    ),
    threshold: float = 0.5,
    strategy: str = RAGStrategy.HYBRID.value,
    filters: str | None = Query(default=None, description="JSON object of metadata filters."),
) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    boundary_limit = limit if isinstance(limit, int) else None
    boundary_filters = filters if isinstance(filters, str) else None
    if top_k is not None and boundary_limit is not None:
        raise HTTPException(status_code=422, detail="Use top_k or limit, not both")
    effective_top_k = max(
        1,
        min(top_k if top_k is not None else boundary_limit or 10, 100),
    )
    q = q[:10000]
    try:
        resolve_rag_strategy(strategy)
        result: RAGExecutionResult = await _retrieval_gateway(request).execute(
            tenant_ctx,
            collection_id=collection_id,
            query=q,
            strategy_id=strategy,
            top_k=effective_top_k,
            filters=_parse_retrieval_filters(boundary_filters),
        )
    except Exception as exc:
        _raise_retrieval_http_error(exc)
    return [
        {
            "chunk_id": citation.chunk_id,
            "content": citation.content,
            "score": citation.score,
            "vector_score": citation.metadata.get("vector_score", citation.score),
            "trigram_score": citation.metadata.get("trigram_score", 0.0),
            "source_file": citation.metadata.get("source_file", citation.source),
            "source_url": citation.metadata.get("source_url", ""),
            "char_offset": citation.metadata.get("char_offset"),
            "line_start": citation.metadata.get("line_start"),
            "requested_strategy_id": result.requested_strategy_id,
            "resolved_strategy_id": result.resolved_strategy_id.value,
        }
        for citation in result.citations
        if citation.score >= threshold
    ]


# ---------------------------------------------------------------------------
# Endpoints — semantic cache
# ---------------------------------------------------------------------------


@router.get("/cache/stats")
async def get_cache_stats(request: Request) -> dict[str, Any]:
    """Return rich cache stats including hit rate, L1/L2 breakdown, and bytes saved."""
    tenant_ctx: TenantContext = _require_tenant(request)
    cache = _semantic_cache(request)
    stats = cache.stats(tenant_ctx=tenant_ctx)
    # Also report current cache size
    size = 0
    if hasattr(cache, "size"):
        with suppress(Exception):
            size = await cache.size(tenant_ctx.tenant_id)
    return {
        **stats,
        "redis_entries": size,
        "threshold": getattr(cache, "_threshold", 0.92),
        "ttl_seconds": getattr(cache, "_ttl", 3600),
    }


@router.delete("/cache", status_code=status.HTTP_200_OK)
async def clear_cache(request: Request) -> dict[str, Any]:
    """Clear ALL cache entries for this tenant (both in-process LRU and Redis)."""
    tenant_ctx: TenantContext = _require_tenant(request)
    cache = _semantic_cache(request)
    deleted = 0
    if hasattr(cache, "clear_async"):
        deleted = await cache.clear_async(tenant_ctx=tenant_ctx)
    else:
        cache.clear(tenant_ctx=tenant_ctx)  # sync fallback
    return {"cleared": True, "redis_keys_deleted": deleted}


@router.post("/cache/warm")
async def warm_cache(request: Request) -> dict[str, Any]:
    """Pre-populate the cache with common step patterns for this tenant."""
    tenant_ctx: TenantContext = _require_tenant(request)
    cache = _semantic_cache(request)
    body = await request.json()
    patterns = body.get("patterns", [])  # [{"query": "...", "response": "..."}]
    embedder = getattr(request.app.state, "embedder", None)
    if not hasattr(cache, "warm"):
        return {"warmed": 0, "error": "Cache warming not supported"}
    count = await cache.warm(patterns=patterns, embedder=embedder, tenant_id=tenant_ctx.tenant_id)
    return {"warmed": count, "total_patterns": len(patterns)}


# ---------------------------------------------------------------------------
# Endpoints — file upload ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest/file", status_code=201)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    collection_id: str = Form(...),
) -> dict[str, Any]:
    """Ingest a file into a knowledge collection.

    Supports: .txt, .md, .py, .ts, .js, .json, .pdf, .docx
    Open source parsing only (pypdf, python-docx if installed).
    """
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    content_bytes = await file.read()
    filename = file.filename or "uploaded_file"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    source_type = "code" if ext in {"py", "ts", "js", "jsx", "tsx"} else "text"

    # Parse content
    if ext == "pdf":
        try:
            import io

            import pypdf  # type: ignore[import-not-found]

            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            text = content_bytes.decode("utf-8", errors="replace")
    elif ext in {"docx", "doc"}:
        try:
            import io

            import docx  # type: ignore[import-not-found]

            doc = docx.Document(io.BytesIO(content_bytes))
            text = "\n".join(para.text for para in doc.paragraphs)
        except ImportError:
            text = content_bytes.decode("utf-8", errors="replace")
    else:
        text = content_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(422, "File is empty or could not be parsed")

    # Chunk using token-aware chunker
    from app.knowledge.chunker_v2 import chunk_by_tokens as _chunk_by_tokens_file

    _raw_file_chunks = _chunk_by_tokens_file(text, max_tokens=512, overlap_tokens=64)
    chunks = [
        type("_C", (), {"content": chunk, "start_char": 0, "end_char": len(chunk)})()
        for chunk in _raw_file_chunks
    ]

    # Fallback for very short content
    if not chunks and text.strip():
        chunks = [
            type(
                "_C",
                (),
                {"content": text.strip(), "start_char": 0, "end_char": len(text)},
            )()
        ]

    document_id = _uuid.uuid4().hex
    rag_chunks: list[Chunk] = []
    non_empty_chunks = [chunk for chunk in chunks if chunk.content.strip()]
    embeddings = await _embed_texts_or_http(
        [chunk.content for chunk in non_empty_chunks],
        embedder,
    )
    for idx, (chunk, embedding) in enumerate(zip(non_empty_chunks, embeddings, strict=True)):
        rag_chunks.append(
            Chunk(
                document_id=document_id,
                content=chunk.content,
                embedding=embedding,
                chunk_index=idx,
                metadata={
                    "source_file": filename,
                    "ext": ext,
                    "char_offset": str(chunk.start_char),
                    "source_type": source_type,
                },
            )
        )
    await _persist_chunks_or_http(
        store,
        rag_chunks,
        collection_id=collection_id,
        tenant_ctx=tenant,
    )

    return {
        "filename": filename,
        "chunks_created": len(rag_chunks),
        "collection_id": collection_id,
        "file_size_bytes": len(content_bytes),
    }


# ---------------------------------------------------------------------------
# Endpoints — repository ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest/repo", status_code=202)
async def ingest_repository(request: Request, body: RepoIngestRequest) -> dict[str, Any]:
    """Clone a git repository and ingest all matching files.

    Uses git (open source) for cloning. No cloud API calls.
    Returns immediately — ingestion runs in background.
    """
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)
    settings = get_settings()

    try:
        if body.max_files < 1:
            raise RepositorySecurityError("Repository max_files must be positive")
        repository_source = resolve_repository_source(body.repo_url)
        repository_url = repository_source.url
        branch = validate_branch(body.branch)
        file_patterns = validate_patterns(body.file_patterns)
    except RepositorySecurityError as exc:
        raise HTTPException(status_code=400, detail="Invalid repository ingestion input") from exc

    collection = await store.get_collection_async(
        body.collection_id,
        tenant_ctx=tenant,
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Knowledge collection not found")
    await _embed_texts_or_http(["Repository ingestion readiness check"], embedder)
    try:
        await store.reconcile_stale_ingestion_jobs_async(
            tenant_ctx=tenant,
            stale_after_seconds=settings.repo_ingest_stale_job_seconds,
        )
        job_id = await store.create_ingestion_job_async(
            collection_id=body.collection_id,
            source_url=repository_url,
            source_type="repository",
            title=repository_url.rstrip("/").rsplit("/", 2)[-1],
            tenant_ctx=tenant,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Knowledge persistence is unavailable",
        ) from exc

    import asyncio

    # Run in background task
    task = asyncio.create_task(
        _ingest_repo_background(
            job_id=job_id,
            repo_url=repository_url,
            collection_id=body.collection_id,
            branch=branch,
            file_patterns=file_patterns,
            max_files=min(body.max_files, settings.repo_ingest_max_files),
            store=store,
            embedder=embedder,
            tenant_ctx=tenant,
            limits=RepositoryLimits(
                max_files=min(body.max_files, settings.repo_ingest_max_files),
                max_file_bytes=settings.repo_ingest_max_file_bytes,
                max_total_bytes=settings.repo_ingest_max_total_bytes,
                max_repository_bytes=settings.repo_ingest_max_repository_bytes,
                max_repository_files=settings.repo_ingest_max_repository_files,
            ),
            clone_timeout_seconds=settings.repo_ingest_clone_timeout_seconds,
            curl_resolve=repository_source.curl_resolve,
            lease_seconds=settings.repo_ingest_lease_seconds,
            heartbeat_seconds=settings.repo_ingest_heartbeat_seconds,
        )
    )
    tasks = getattr(request.app.state, "repository_ingestion_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state.repository_ingestion_tasks = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    return {
        "status": "ingestion_started",
        "job_id": job_id,
        "repo_url": repository_url,
        "collection_id": body.collection_id,
        "branch": branch,
        "message": "Repository ingestion started in background.",
    }


@router.get("/ingest/jobs/{job_id}")
async def get_ingestion_job(request: Request, job_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    try:
        await store.reconcile_stale_ingestion_jobs_async(
            tenant_ctx=tenant,
            stale_after_seconds=get_settings().repo_ingest_stale_job_seconds,
        )
        job = await store.get_ingestion_job_async(job_id, tenant_ctx=tenant)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Knowledge persistence is unavailable",
        ) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return job


async def _ingest_repo_background(
    job_id: str,
    repo_url: str,
    collection_id: str,
    branch: str,
    file_patterns: list[str],
    max_files: int,
    store: Any,
    embedder: Any,
    tenant_ctx: Any,
    limits: RepositoryLimits | None = None,
    clone_timeout_seconds: int = 120,
    curl_resolve: str | None = None,
    lease_seconds: int = 60,
    heartbeat_seconds: int = 5,
) -> None:
    """Clone and atomically ingest under a disk/file quota and durable lease.

    Git/libcurl does not expose reliable aggregate network-byte accounting here.
    The worker therefore fails closed on continuously monitored clone disk bytes,
    clone file count, wall-clock timeout, selected bytes, and selected file count.
    """
    import asyncio
    import pathlib
    import shutil
    import tempfile

    from app.knowledge.chunker_v2 import chunk_by_tokens as _chunk_by_tokens_repo
    from app.observability.logging import get_logger

    logger = get_logger(__name__)
    if limits is None:
        settings = get_settings()
        limits = RepositoryLimits(
            max_files=min(max_files, settings.repo_ingest_max_files),
            max_file_bytes=settings.repo_ingest_max_file_bytes,
            max_total_bytes=settings.repo_ingest_max_total_bytes,
            max_repository_bytes=settings.repo_ingest_max_repository_bytes,
            max_repository_files=settings.repo_ingest_max_repository_files,
        )

    tmpdir = tempfile.mkdtemp(prefix="agentverse_repo_")
    config_dir = tempfile.mkdtemp(prefix="agentverse_git_config_")
    proc: Any = None
    communicate_task: asyncio.Task[Any] | None = None
    lease_owner = _uuid.uuid4().hex
    try:
        if curl_resolve is None:
            repository_source = resolve_repository_source(repo_url)
            repo_url = repository_source.url
            curl_resolve = repository_source.curl_resolve
        branch = validate_branch(branch)
        file_patterns = validate_patterns(file_patterns)
        claimed = await store.claim_ingestion_job_async(
            job_id,
            collection_id=collection_id,
            source_url=repo_url,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            tenant_ctx=tenant_ctx,
        )
        if not claimed:
            raise RuntimeError("Repository ingestion job lease could not be claimed")
        # Clone using git — non-blocking async subprocess
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "http.followRedirects=false",
            "-c",
            f"http.curloptResolve={curl_resolve}",
            "-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--depth=1",
            "--single-branch",
            "--no-tags",
            "--branch",
            branch,
            "--",
            repo_url,
            tmpdir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                "PATH": os.defpath,
                "HOME": config_dir,
                "XDG_CONFIG_HOME": config_dir,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": "https",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_LFS_SKIP_SMUDGE": "1",
                "GCM_INTERACTIVE": "never",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "LC_ALL": "C.UTF-8",
                **(
                    {"GIT_SSL_CAINFO": get_settings().repo_ingest_ca_bundle}
                    if get_settings().repo_ingest_ca_bundle
                    else {}
                ),
            },
        )
        communicate_task = asyncio.create_task(proc.communicate())
        deadline = asyncio.get_running_loop().time() + clone_timeout_seconds
        next_heartbeat = 0.0
        while not communicate_task.done():
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                proc.kill()
                communicate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await communicate_task
                await asyncio.shield(proc.wait())
                raise RuntimeError("Repository clone timed out")
            file_count, disk_bytes = repository_usage(pathlib.Path(tmpdir))
            if file_count > limits.max_repository_files or disk_bytes > limits.max_repository_bytes:
                proc.kill()
                communicate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await communicate_task
                await asyncio.shield(proc.wait())
                raise RepositorySecurityError("Repository clone quota exceeded")
            if now >= next_heartbeat:
                heartbeat = await store.heartbeat_ingestion_job_async(
                    job_id,
                    lease_owner=lease_owner,
                    lease_seconds=lease_seconds,
                    tenant_ctx=tenant_ctx,
                )
                if not heartbeat:
                    proc.kill()
                    communicate_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await communicate_task
                    await asyncio.shield(proc.wait())
                    raise RuntimeError("Repository ingestion lease was lost")
                next_heartbeat = now + heartbeat_seconds
            await asyncio.sleep(0.05)
        await communicate_task

        if proc.returncode != 0:
            raise RuntimeError("Repository clone failed")

        prepared_chunks: list[Chunk] = []
        repository_files = read_repository_files(
            pathlib.Path(tmpdir),
            file_patterns,
            limits,
        )
        for repository_file in repository_files:
            heartbeat = await store.heartbeat_ingestion_job_async(
                job_id,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
                tenant_ctx=tenant_ctx,
            )
            if not heartbeat:
                raise RuntimeError("Repository ingestion lease was lost")
            from app.agent.exfil_guard import check_tool_args_for_exfil

            blocked, _reason = check_tool_args_for_exfil(
                "write_file",
                {"content": repository_file.content},
                tenant_id=tenant_ctx.tenant_id,
            )
            if blocked:
                raise RepositorySecurityError("Repository content failed secret scan")
            suffix = pathlib.PurePosixPath(repository_file.relative_path).suffix.lstrip(".")
            source_type = "code" if suffix in {"py", "ts", "js"} else "text"
            raw_chunks = _chunk_by_tokens_repo(
                repository_file.content,
                max_tokens=512,
                overlap_tokens=64,
            )
            document_id = hashlib.sha256(
                f"{repo_url}:{repository_file.relative_path}".encode()
            ).hexdigest()[:32]
            embeddings = await _embed_texts_or_http(raw_chunks, embedder)
            heartbeat = await store.heartbeat_ingestion_job_async(
                job_id,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
                tenant_ctx=tenant_ctx,
            )
            if not heartbeat:
                raise RuntimeError("Repository ingestion lease was lost")
            prepared_chunks.extend(
                Chunk(
                    document_id=document_id,
                    content=chunk_content,
                    embedding=embeddings[index],
                    chunk_index=index,
                    metadata={
                        "source_file": repository_file.relative_path,
                        "repo_url": repo_url,
                        "source_type": source_type,
                        "source_doc_id": document_id,
                    },
                )
                for index, chunk_content in enumerate(raw_chunks)
            )

        heartbeat = await store.heartbeat_ingestion_job_async(
            job_id,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            tenant_ctx=tenant_ctx,
        )
        if not heartbeat:
            raise RuntimeError("Repository ingestion lease was lost")
        await store.ingest_repository_chunks_async(
            prepared_chunks,
            job_id=job_id,
            collection_id=collection_id,
            source_url=repo_url,
            lease_owner=lease_owner,
            tenant_ctx=tenant_ctx,
        )
        logger.info(
            "repo_ingest_complete",
            repo=repo_url,
            files=len(repository_files),
        )
    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            proc.kill()
            if communicate_task is not None:
                communicate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await communicate_task
            with suppress(Exception):
                await asyncio.shield(proc.wait())
        await asyncio.shield(
            store.fail_ingestion_job_async(
                job_id,
                lease_owner=lease_owner,
                error_message="Repository ingestion cancelled",
                tenant_ctx=tenant_ctx,
            )
        )
        raise
    except Exception as exc:
        logger.warning("repo_ingest_failed", repo=repo_url, error=type(exc).__name__)
        try:
            await store.fail_ingestion_job_async(
                job_id,
                lease_owner=lease_owner,
                error_message="Repository ingestion failed",
                tenant_ctx=tenant_ctx,
            )
        except Exception as status_exc:
            logger.error(
                "repo_ingest_status_update_failed",
                job_id=job_id,
                error=type(status_exc).__name__,
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        shutil.rmtree(config_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Endpoints — OpenAPI spec ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest/openapi", status_code=201)
async def ingest_openapi(request: Request, body: OpenAPIIngestRequest) -> dict[str, Any]:
    """Ingest an OpenAPI spec — creates a chunk per endpoint."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    try:
        import json as _json

        try:
            spec = _json.loads(body.content)
        except _json.JSONDecodeError:
            import yaml as _yaml  # type: ignore[import-untyped]

            spec = _yaml.safe_load(body.content)
    except Exception as exc:
        raise HTTPException(422, f"Could not parse OpenAPI spec: {exc}") from exc

    paths = spec.get("paths", {})
    rag_chunks: list[Chunk] = []
    source_doc_id = _uuid.uuid4().hex
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options"}:
                continue
            if not isinstance(op, dict):
                continue
            summary = op.get("summary", "")
            description = op.get("description", "")
            params = [p.get("name", "") for p in op.get("parameters", []) if isinstance(p, dict)]

            chunk_text = (
                f"{method.upper()} {path}\n"
                f"Summary: {summary}\n"
                f"Description: {description}\n"
                f"Parameters: {', '.join(params) if params else 'none'}"
            ).strip()

            if not chunk_text:
                continue

            embedding = (await _embed_texts_or_http([chunk_text], embedder))[0]

            rag_chunks.append(
                Chunk(
                    document_id=source_doc_id,
                    content=chunk_text,
                    embedding=embedding,
                    chunk_index=len(rag_chunks),
                    metadata={
                        "source_url": body.source_url,
                        "source_type": "openapi",
                        "source_doc_id": source_doc_id,
                        "endpoint": f"{method.upper()} {path}",
                    },
                )
            )
    await _persist_chunks_or_http(
        store,
        rag_chunks,
        collection_id=body.collection_id,
        tenant_ctx=tenant,
    )

    return {
        "endpoints_ingested": len(rag_chunks),
        "collection_id": body.collection_id,
        "source_url": body.source_url,
    }


# ---------------------------------------------------------------------------
# Endpoints — URL-based connector ingestion (Phase 9)
# ---------------------------------------------------------------------------


@router.post("/ingest/url", status_code=201)
async def ingest_from_url(request: Request, body: UrlIngestRequest) -> dict[str, Any]:
    """Ingest content from a URL (web page, GitHub file, Confluence page, etc.)."""
    tenant_ctx = _require_tenant(request)
    store = _knowledge_store(request)

    content = ""
    metadata: dict[str, Any] = {"source_url": body.url, "source_type": body.source_type}

    # SSRF guard — reject internal/metadata URLs before fetching
    try:
        assert_public_url(body.url, context="/ingest/url")
    except SSRFError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"URL blocked for security reasons: {exc}",
        ) from exc

    try:
        if body.source_type == "web":
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(body.url, headers={"User-Agent": "AgentVerse/1.0"})
                resp.raise_for_status()
                raw = resp.text
                import re

                content = re.sub(r"<[^>]+>", " ", raw)
                content = re.sub(r"\s+", " ", content).strip()[:50000]
                title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE)
                metadata["title"] = title_match.group(1) if title_match else body.url

        elif body.source_type == "github":
            raw_url = body.url.replace("github.com", "raw.githubusercontent.com").replace(
                "/blob/", "/"
            )
            import httpx

            headers: dict[str, str] = {}
            import os as _os

            if token := _os.getenv("GITHUB_TOKEN"):
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(raw_url, headers=headers)
                resp.raise_for_status()
                content = resp.text[:100000]
            metadata["filename"] = body.url.split("/")[-1]

        else:
            raise HTTPException(
                400,
                f"Source type '{body.source_type}' not yet supported for URL ingestion. "
                "Supported: web, github",
            )

    except HTTPException:
        raise
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning("ingest_url_fetch_failed: %s", exc)
        raise HTTPException(500, "Failed to fetch content from the requested URL") from exc

    if not content.strip():
        raise HTTPException(422, "No content extracted from URL")

    # Chunk and ingest
    from app.knowledge.chunker_v2 import chunk_by_tokens as _chunk_by_tokens_url

    _raw_url_chunks = _chunk_by_tokens_url(content, max_tokens=512, overlap_tokens=64)
    chunks = [
        type("_C", (), {"content": chunk, "start_char": 0, "end_char": len(chunk)})()
        for chunk in _raw_url_chunks
    ]
    if not chunks and content.strip():
        chunks = [
            type(
                "_C",
                (),
                {"content": content.strip(), "start_char": 0, "end_char": len(content)},
            )()
        ]

    embedder = getattr(request.app.state, "embedder", None)
    import uuid as _uuid_mod

    from app.rag.models import Chunk as RagChunk

    doc_id = _uuid_mod.uuid4().hex
    rag_chunks: list[Chunk] = []
    for idx, chunk in enumerate(chunks):
        if not chunk.content.strip():
            continue
        embedding = (await _embed_texts_or_http([chunk.content], embedder))[0]
        rag_chunks.append(
            RagChunk(
                document_id=doc_id,
                content=chunk.content,
                embedding=embedding,
                chunk_index=idx,
                metadata={
                    **{k: str(v) for k, v in metadata.items()},
                    "source_type": body.source_type,
                },
            )
        )
    await _persist_chunks_or_http(
        store,
        rag_chunks,
        collection_id=body.collection_id,
        tenant_ctx=tenant_ctx,
    )

    return {
        "collection_id": body.collection_id,
        "source_url": body.url,
        "source_type": body.source_type,
        "chunks_ingested": len(rag_chunks),
        "total_chars": len(content),
    }


# ---------------------------------------------------------------------------
# Endpoints — structured source ingestors (PDF, DOCX, GitHub, Confluence, etc.)
# ---------------------------------------------------------------------------


async def _ingest_chunks_from_source(
    store: KnowledgeStore,
    chunks: list[dict[str, Any]],
    collection_id: str,
    tenant_ctx: Any,
    embedder: Any,
) -> int:
    """Embed and ingest a list of chunk dicts returned by an ingestor."""
    source_chunks = [chunk for chunk in chunks if str(chunk.get("content", "")).strip()]
    embeddings = await _embed_texts_or_http(
        [str(chunk["content"]) for chunk in source_chunks],
        embedder,
    )
    fallback_source_doc_id = _uuid.uuid4().hex
    document_chunk_indexes: dict[str, int] = {}
    rag_chunks: list[Chunk] = []
    for chunk_data, embedding in zip(source_chunks, embeddings, strict=True):
        content = chunk_data.get("content", "")
        source_doc_id = str(chunk_data.get("source_doc_id") or fallback_source_doc_id)
        chunk_index = document_chunk_indexes.get(source_doc_id, 0)
        document_chunk_indexes[source_doc_id] = chunk_index + 1
        rag_chunks.append(
            Chunk(
                document_id=source_doc_id,
                content=content,
                embedding=embedding,
                chunk_index=chunk_index,
                metadata={k: str(v) for k, v in (chunk_data.get("metadata") or {}).items()}
                | {
                    "source_url": chunk_data.get("source_url", ""),
                    "source_type": chunk_data.get("source_type", ""),
                    "source_doc_id": source_doc_id,
                    "page_number": str(chunk_data.get("page_number") or ""),
                },
            )
        )
    await _persist_chunks_or_http(
        store,
        rag_chunks,
        collection_id=collection_id,
        tenant_ctx=tenant_ctx,
    )
    return len(rag_chunks)


@router.post("/ingest/pdf", status_code=201)
async def ingest_pdf(
    request: Request,
    file: UploadFile = File(...),
    collection_id: str = Form(...),
    source_url: str = Form(default=""),
) -> dict[str, Any]:
    """Ingest a PDF file into a knowledge collection with page-level citation metadata."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    content_bytes = await file.read()
    filename = file.filename or "uploaded.pdf"

    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor

    ingestor = PdfIngestor()
    chunks = ingestor.extract_chunks(
        content=content_bytes,
        filename=filename,
        source_url=source_url or f"file://{filename}",
    )

    ingested = await _ingest_chunks_from_source(store, chunks, collection_id, tenant, embedder)
    return {"chunks_ingested": ingested, "source": filename, "source_type": "pdf"}


@router.post("/ingest/docx", status_code=201)
async def ingest_docx(
    request: Request,
    file: UploadFile = File(...),
    collection_id: str = Form(...),
    source_url: str = Form(default=""),
) -> dict[str, Any]:
    """Ingest a DOCX file into a knowledge collection."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    content_bytes = await file.read()
    filename = file.filename or "uploaded.docx"

    from app.knowledge.ingestors.docx_ingestor import DocxIngestor

    ingestor = DocxIngestor()
    chunks = ingestor.extract_chunks(
        content=content_bytes,
        filename=filename,
        source_url=source_url or f"file://{filename}",
    )

    ingested = await _ingest_chunks_from_source(store, chunks, collection_id, tenant, embedder)
    return {"chunks_ingested": ingested, "source": filename, "source_type": "docx"}


@router.post("/ingest/github", status_code=202)
async def ingest_github(request: Request, body: GitHubIngestRequest) -> dict[str, Any]:
    """Ingest a GitHub repository into a knowledge collection via GitHub REST API."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    from app.knowledge.ingestors.github_ingestor import GitHubIngestor

    ingestor = GitHubIngestor()
    chunks = await ingestor.ingest_repo(
        body.owner,
        body.repo,
        branch=body.branch,
        max_files=body.max_files,
    )

    ingested = await _ingest_chunks_from_source(store, chunks, body.collection_id, tenant, embedder)
    return {
        "chunks_ingested": ingested,
        "source": f"github:{body.owner}/{body.repo}",
        "source_type": "github",
    }


@router.post("/ingest/confluence", status_code=202)
async def ingest_confluence(request: Request, body: ConfluenceIngestRequest) -> dict[str, Any]:
    """Ingest a Confluence space into a knowledge collection."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor

    ingestor = ConfluenceIngestor(
        base_url=body.base_url,
        token=body.token.get_secret_value(),  # SecretStr: extract only at point of use
        user=body.user,
    )
    chunks = await ingestor.ingest_space(body.space_key, max_pages=body.max_pages)

    ingested = await _ingest_chunks_from_source(store, chunks, body.collection_id, tenant, embedder)
    return {
        "chunks_ingested": ingested,
        "source": f"confluence:{body.space_key}",
        "source_type": "confluence",
    }


@router.post("/ingest/jira", status_code=202)
async def ingest_jira(request: Request, body: JiraIngestRequest) -> dict[str, Any]:
    """Ingest Jira project issues into a knowledge collection."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    from app.knowledge.ingestors.jira_ingestor import JiraIngestor

    ingestor = JiraIngestor(
        base_url=body.base_url,
        token=body.token.get_secret_value(),  # SecretStr: extract only at point of use
        user=body.user,
    )
    chunks = await ingestor.ingest_project(
        body.project_key,
        jql_extra=body.jql_extra,
        max_issues=body.max_issues,
    )

    ingested = await _ingest_chunks_from_source(store, chunks, body.collection_id, tenant, embedder)
    return {
        "chunks_ingested": ingested,
        "source": f"jira:{body.project_key}",
        "source_type": "jira",
    }


@router.post("/ingest/slack", status_code=202)
async def ingest_slack(request: Request, body: SlackIngestRequest) -> dict[str, Any]:
    """Ingest a Slack channel's message history into a knowledge collection."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    from app.knowledge.ingestors.slack_ingestor import SlackIngestor

    ingestor = SlackIngestor(token=body.token.get_secret_value())
    chunks = await ingestor.ingest_channel(
        body.channel_id,
        channel_name=body.channel_name,
        max_messages=body.max_messages,
    )

    ingested = await _ingest_chunks_from_source(store, chunks, body.collection_id, tenant, embedder)
    return {
        "chunks_ingested": ingested,
        "source": f"slack:{body.channel_id}",
        "source_type": "slack",
    }


# ---------------------------------------------------------------------------
# H-7: Federated search across multiple collections
# ---------------------------------------------------------------------------


@router.post("/search/federated")
async def federated_search_endpoint(
    request: Request,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Search across multiple knowledge collections with score normalization."""
    tenant_ctx: TenantContext = _require_tenant(request)
    query: str = body.get("query", "")
    collection_ids: list[str] = body.get("collection_ids", [])
    top_k: int = int(body.get("top_k", 10))
    top_k = max(1, min(100, top_k))
    strategy = str(body.get("strategy", RAGStrategy.HYBRID.value))
    filters = body.get("filters", {})

    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="query is required",
        )
    if not collection_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="collection_ids is required",
        )
    if not isinstance(filters, dict):
        raise HTTPException(status_code=422, detail="filters must be an object")

    from app.knowledge.federated_search import federated_search

    try:
        resolve_rag_strategy(strategy)
        results = await federated_search(
            query=query,
            collection_ids=collection_ids,
            gateway=_retrieval_gateway(request),
            top_k=top_k,
            tenant_ctx=tenant_ctx,
            strategy=strategy,
            filters=filters,
        )
    except Exception as exc:
        _raise_retrieval_http_error(exc)
    return {
        "results": results,
        "total": len(results),
        "collections_searched": len(collection_ids),
        "requested_strategy_id": strategy,
        "resolved_strategy_ids": sorted(
            {str(result["resolved_strategy_id"]) for result in results}
        ),
    }


# ---------------------------------------------------------------------------
# Intelligence: RAG Chat & Collection Analytics
# ---------------------------------------------------------------------------


class RagChatRequest(BaseModel):
    question: str
    collection_ids: list[str] = Field(default_factory=list)  # empty = all tenant collections
    strategy: str = RAGStrategy.HYBRID.value
    top_k: int = Field(default=5, ge=1, le=20)
    filters: dict[str, Any] = Field(default_factory=dict)
    max_context_chars: int = Field(default=6000, ge=1, le=100_000)
    stream: bool = False


@router.post("/chat")
async def rag_chat(request: Request, body: RagChatRequest) -> dict[str, Any]:
    """Answer a question using retrieval-augmented generation.

    Retrieves relevant chunks from the knowledge store and asks the LLM
    to answer using only those chunks. Returns the answer plus cited chunks.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _knowledge_store(request)

    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    # Resolve collection IDs (default = all tenant collections)
    collection_ids = body.collection_ids
    if not collection_ids:
        collections = await store.list_collections_async(tenant_ctx=tenant_ctx)
        collection_ids = [c.collection_id for c in collections]

    if not collection_ids:
        raise HTTPException(status_code=404, detail="No knowledge collections found")

    from app.knowledge.federated_search import federated_search

    try:
        resolved_strategy = resolve_rag_strategy(body.strategy)
        results = await federated_search(
            query=body.question,
            collection_ids=collection_ids[:10],
            gateway=_retrieval_gateway(request),
            top_k=body.top_k,
            tenant_ctx=tenant_ctx,
            strategy=body.strategy,
            filters=body.filters,
            per_collection_k=body.top_k,
        )
        canonical_citations = [
            RAGCitation(
                citation_id=str(result["citation_id"]),
                chunk_id=str(result["chunk_id"]),
                content=str(result["content"]),
                score=float(result["score"]),
                source=str(result["source"]),
                metadata={
                    **dict(result.get("metadata", {})),
                    "collection_id": str(result["collection_id"]),
                    "collection_ids": list(result.get("collection_ids", [])),
                    "sources": list(result.get("sources", [])),
                    "citation_refs": list(result.get("citation_refs", [])),
                    "retrieval_legs": list(result.get("retrieval_legs", [])),
                    "strategy_trace": list(result.get("strategy_trace", [])),
                },
            )
            for result in results
        ]
        if not canonical_citations:
            raise HTTPException(status_code=404, detail="No relevant knowledge found")
        answer = await RAGRetriever(gateway=_retrieval_gateway(request)).synthesize(
            query=body.question,
            tenant_ctx=tenant_ctx,
            strategy=resolved_strategy,
            citations=canonical_citations,
            max_context_chars=body.max_context_chars,
        )
        retriever = RAGRetriever(gateway=_retrieval_gateway(request))
        verified = await retriever.verify_result(
            RAGExecutionResult(
                requested_strategy_id=body.strategy,
                resolved_strategy_id=resolved_strategy,
                citations=canonical_citations,
                retrieval_legs=[
                    leg for result in results for leg in list(result.get("retrieval_legs", []))
                ],
                strategy_trace=[
                    trace for result in results for trace in list(result.get("strategy_trace", []))
                ],
                answer=answer,
            ),
            tenant_ctx=tenant_ctx,
        )
        if not verified.grounded:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "answer_ungrounded",
                    "reason": verified.strategy_trace[-1].detail.get(
                        "reason",
                        "unsupported",
                    ),
                    "requested_strategy_id": body.strategy,
                    "strategy_trace": [
                        trace.model_dump(mode="json") for trace in verified.strategy_trace
                    ],
                },
            )
        answer = verified.answer
    except Exception as exc:
        _raise_retrieval_http_error(exc)

    citations = [
        {
            "index": i + 1,
            "citation_id": citation.citation_id,
            "chunk_id": citation.chunk_id,
            "collection_id": citation.metadata.get("collection_id", ""),
            "collection_ids": citation.metadata.get("collection_ids", []),
            "score": citation.score,
            "source": citation.source,
            "sources": citation.metadata.get("sources", []),
            "citation_refs": citation.metadata.get("citation_refs", []),
            "retrieval_legs": citation.metadata.get("retrieval_legs", []),
            "strategy_trace": citation.metadata.get("strategy_trace", []),
            "source_url": citation.metadata.get("source_url", ""),
            "page_number": citation.metadata.get("page_number"),
            "excerpt": citation.content[:300],
        }
        for i, citation in enumerate(canonical_citations)
    ]

    return {
        "answer": answer,
        "citations": citations,
        "collections_searched": len(collection_ids),
        "chunks_retrieved": len(canonical_citations),
        "question": body.question,
        "grounded": True,
        "requested_strategy_id": body.strategy,
        "resolved_strategy_ids": sorted(
            {str(result["resolved_strategy_id"]) for result in results}
        ),
        "retrieval_legs": [
            leg for result in results for leg in list(result.get("retrieval_legs", []))
        ],
        "strategy_trace": [trace.model_dump(mode="json") for trace in verified.strategy_trace],
    }


@router.get("/collections/{collection_id}/stats")
async def get_collection_stats(request: Request, collection_id: str) -> dict[str, Any]:
    """Return quality and freshness statistics for a knowledge collection."""
    tenant_ctx = _require_tenant(request)
    store = _knowledge_store(request)

    # Verify collection exists
    collections = await store.list_collections_async(tenant_ctx=tenant_ctx)
    col = next((c for c in collections if c.collection_id == collection_id), None)
    if col is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Pull chunks from in-memory store for analytics
    # _data maps (tenant_id, collection_id) → _CollectionStore; extract .chunks
    raw_data = getattr(store, "_data", {})
    cid_key = (tenant_ctx.tenant_id, collection_id)
    col_store = raw_data.get(cid_key)
    chunk_objs: list[Any] = col_store.chunks if col_store is not None else []
    doc_count = getattr(col, "document_count", 0)
    chunk_count = len(chunk_objs)

    # Compute avg embedding magnitude as a proxy for embedding health
    embeddings_present = sum(1 for c in chunk_objs if getattr(c, "embedding", None))
    embedding_coverage = round(embeddings_present / max(chunk_count, 1), 4)

    # Source type distribution
    source_types: dict[str, int] = {}
    for c in chunk_objs:
        stype = (getattr(c, "metadata", None) or {}).get("source_type", "unknown")
        source_types[stype] = source_types.get(stype, 0) + 1

    # Average chunk length
    avg_chunk_len = int(
        sum(len(getattr(c, "content", "")) for c in chunk_objs) / max(chunk_count, 1)
    )

    return {
        "collection_id": collection_id,
        "name": col.name,
        "doc_count": doc_count,
        "chunk_count": chunk_count,
        "embedding_coverage_pct": round(embedding_coverage * 100, 1),
        "avg_chunk_length": avg_chunk_len,
        "source_type_distribution": source_types,
        "embedder": getattr(col, "embedder", "unknown"),
        "health_score": round(
            min(
                1.0,
                (0.4 * embedding_coverage)
                + (0.4 * min(1.0, chunk_count / 100))
                + (0.2 * min(1.0, doc_count / 10)),
            ),
            3,
        ),
    }


# ---------------------------------------------------------------------------
# RPA-backed URL ingestion (via the one reachable RPAExecutor scraper)
# ---------------------------------------------------------------------------


@router.post("/ingest/rpa-url", status_code=201)
async def ingest_from_rpa_url(
    request: Request,
    body: RpaUrlIngestRequest,
) -> dict[str, Any]:
    """Scrape one or more URLs via the RPA subsystem and ingest them into the KB.

    WS-13: this routes through the ONE reachable scraper — ``RPAExecutor`` (real
    Chromium when Playwright is installed, a real httpx fetch otherwise) — instead
    of a duplicated ad-hoc browser block. Scraped content is chunked with
    ``source_type``/``source_url`` provenance and a ``doc_content_hash``, and
    cross-source deduped against the one store via ``exists_by_hash`` before
    indexing.
    """
    tenant_ctx = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    if not body.urls:
        raise HTTPException(status_code=400, detail="urls list must not be empty")
    if len(body.urls) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 URLs per batch")

    for url in body.urls:
        if not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail=f"URL must start with http:// or https://: {url}",
            )

    from app.rpa.kb_emit import scrape_url_to_chunks

    executor = getattr(request.app.state, "rpa_executor", None)
    if executor is None:
        from app.rpa.executor import RPAExecutor

        executor = RPAExecutor()
        request.app.state.rpa_executor = executor

    # A non-default selector is passed through to the scraper (honoured by the
    # real browser path); "body" means the whole page.
    selectors = [body.selector] if body.selector and body.selector != "body" else None

    total_chunks = 0
    results: list[dict[str, Any]] = []

    for url in body.urls:
        # SSRF guard — reject internal/metadata URLs before fetching.
        try:
            assert_public_url(url, context="/ingest/rpa-url")
        except SSRFError as exc:
            results.append(
                {
                    "url": url,
                    "success": False,
                    "error": f"URL blocked for security reasons: {exc}",
                    "chunks_ingested": 0,
                }
            )
            continue

        try:
            scraped = await scrape_url_to_chunks(
                executor,
                url=url,
                selectors=selectors,
                source_type=body.source_type,
                max_chars=body.max_chars,
            )
        except Exception as exc:
            results.append(
                {"url": url, "success": False, "error": str(exc), "chunks_ingested": 0}
            )
            continue

        if not scraped.content.strip():
            results.append(
                {
                    "url": url,
                    "success": False,
                    "error": "No content extracted",
                    "chunks_ingested": 0,
                }
            )
            continue

        # Cross-source dedup: skip content already in the store (any source).
        if await store.exists_by_hash(
            content_hash=scraped.content_hash,
            tenant_id=tenant_ctx.tenant_id,
            collection_id=body.collection_id,
        ):
            results.append(
                {
                    "url": url,
                    "success": True,
                    "chunks_ingested": 0,
                    "deduplicated": True,
                    "total_chars": len(scraped.content),
                    "content_hash": scraped.content_hash,
                }
            )
            continue

        ingested = await _ingest_chunks_from_source(
            store, scraped.chunks, body.collection_id, tenant_ctx, embedder
        )
        total_chunks += ingested
        results.append(
            {
                "url": url,
                "success": True,
                "chunks_ingested": ingested,
                "deduplicated": False,
                "total_chars": len(scraped.content),
                "content_hash": scraped.content_hash,
            }
        )

    return {
        "collection_id": body.collection_id,
        "source_type": body.source_type,
        "scraper": "rpa-executor",
        "urls_processed": len(body.urls),
        "urls_succeeded": sum(1 for r in results if r.get("success")),
        "total_chunks_ingested": total_chunks,
        "results": results,
    }


# ── Knowledge bulk analytics ──────────────────────────────────────────────────


def _require_tenant_ctx(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


@router.get("/analytics")
async def get_knowledge_analytics(request: Request) -> dict[str, Any]:
    """Return aggregate analytics for all knowledge collections."""
    tenant = _require_tenant_ctx(request)
    knowledge_store: KnowledgeStore | None = getattr(request.app.state, "knowledge_store", None)

    if knowledge_store is None:
        return {"collections": [], "total_documents": 0, "total_collections": 0}

    try:
        collections = await knowledge_store.list_collections_async(tenant_ctx=tenant)
        analytics: list[dict[str, Any]] = []
        for col in collections:
            analytics.append(
                {
                    "collection_id": col.collection_id,
                    "name": col.name,
                    "document_count": col.document_count,
                    "total_chunks": 0,
                    "last_indexed": None,
                    "avg_relevance_score": 0.0,
                    "cache_hit_rate": 0.0,
                    "health_score": 75,
                }
            )

        return {
            "collections": analytics,
            "total_documents": sum(a["document_count"] for a in analytics),
            "total_collections": len(analytics),
        }
    except Exception as exc:
        return {
            "collections": [],
            "total_documents": 0,
            "total_collections": 0,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Document browser: list + delete individual documents
# ---------------------------------------------------------------------------


@router.post("/collections/{collection_id}/documents", status_code=status.HTTP_201_CREATED)
async def ingest_document_into_collection(
    collection_id: str,
    body: CollectionIngestRequest,
    request: Request,
) -> dict[str, Any]:
    """Ingest a document into a collection using the IngestionOrchestrator.

    Routes content through content-type detection, chunking strategy selection,
    quality filtering, and the knowledge store. Supports auto content-type detection.
    """
    tenant_ctx = _require_tenant(request)
    knowledge_store = _knowledge_store(request)

    # Verify collection exists
    col = await knowledge_store.get_collection_async(collection_id, tenant_ctx=tenant_ctx)
    if col is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection {collection_id} not found",
        )

    try:
        from app.ingestion.orchestrator import IngestionOrchestrator
        from app.rag.indexing import RAGIndexingConfig

        embedder = getattr(request.app.state, "embedder", None)
        indexing_dependencies: dict[RAGStrategy, IndexingDependency] = {}
        if body.indexing_strategies:
            indexing_dependencies = await _resolve_indexing_llm(
                request,
                tenant_ctx,
                body.indexing_strategies,
            )
            if embedder is None:
                raise HTTPException(
                    status_code=503,
                    detail="RAG indexing embedder is unavailable",
                )
            if len(indexing_dependencies) != len(body.indexing_strategies):
                raise HTTPException(
                    status_code=503,
                    detail="RAG indexing provider or model is unavailable",
                )

        indexing_config = RAGIndexingConfig(
            strategies=frozenset(RAGStrategy(strategy) for strategy in body.indexing_strategies),
            raptor_cluster_size=body.raptor_cluster_size,
            raptor_max_levels=body.raptor_max_levels,
            parent_window_size=body.parent_window_size,
            raptor_summary_batch_size=body.raptor_summary_batch_size,
            proposition_batch_size=body.proposition_batch_size,
            embedding_batch_size=body.embedding_batch_size,
        )
        orchestrator = IngestionOrchestrator(
            knowledge_store=knowledge_store,
            embedder=embedder,
            indexing_dependencies=indexing_dependencies,
            rag_indexing_config=indexing_config,
            embed_provider_resolver=getattr(
                request.app.state, "embed_provider_resolver", None
            ),
        )
        result = await orchestrator.ingest(
            content=body.content,
            content_type=body.content_type or "auto",
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
            source_url=body.source_url or "",
            metadata=body.metadata or {},
            dry_run=body.dry_run,
            in_memory_only=body.in_memory_only,
            source_identity=body.source_identity,
        )
        return {
            "ingested": result.chunks_created,
            "chunk_ids": result.chunk_ids,
            "chunks_prepared": result.chunks_prepared,
            "persisted": result.persisted,
            "collection_id": collection_id,
            "content_type": result.content_type.value,
            "chunking_strategy": result.chunking_strategy,
        }
    except HTTPException:
        raise
    except EmptyIndexedContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Indexed content produced no indexable chunks",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Knowledge persistence is unavailable",
        ) from exc


@router.get("/collections/{collection_id}/documents")
async def list_documents(
    collection_id: str,
    request: Request,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    search: str | None = Query(default=None),
) -> dict[str, Any]:
    """List documents in a knowledge collection with pagination."""
    tenant = _require_tenant(request)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)

    if knowledge_store is None:
        return {"documents": [], "total": 0}

    try:
        # Prefer a native list_documents method if available
        if hasattr(knowledge_store, "list_documents"):
            result = await knowledge_store.list_documents(
                collection_id=collection_id,
                tenant_ctx=tenant,
                limit=limit,
                offset=offset,
                search=search,
            )
            if isinstance(result, dict):
                return result
            return {"documents": result or [], "total": len(result or [])}

        # Fallback: query the DB directly
        db = getattr(knowledge_store, "_db", None) or getattr(
            knowledge_store, "_session_factory", None
        )
        if db:
            from sqlalchemy import text as _t

            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant.tenant_id),
            ):
                q = """
                    SELECT id, title, source, source_type, chunk_count, created_at,
                           LEFT(content, 200) as preview
                    FROM knowledge_documents
                    WHERE collection_id = :cid AND tenant_id = :tid
                      AND COALESCE(domain_metadata->>'record_type', '') <> 'ingestion_job'
                """
                params: dict[str, Any] = {
                    "cid": collection_id,
                    "tid": tenant.tenant_id,
                }
                if search:
                    q += " AND (title ILIKE :search OR content ILIKE :search)"
                    params["search"] = f"%{search}%"
                q += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                params["limit"] = limit
                params["offset"] = offset

                rows = (await session.execute(_t(q), params)).fetchall()
                count_q = (
                    "SELECT COUNT(*) FROM knowledge_documents "
                    "WHERE collection_id = :cid AND tenant_id = :tid "
                    "AND COALESCE(domain_metadata->>'record_type', '') <> 'ingestion_job'"
                )
                total = (
                    await session.execute(
                        _t(count_q),
                        {"cid": collection_id, "tid": tenant.tenant_id},
                    )
                ).scalar() or 0

                documents = [
                    {
                        "id": str(r[0]),
                        "title": r[1],
                        "source": r[2],
                        "source_type": r[3],
                        "chunk_count": r[4] or 0,
                        "created_at": r[5].isoformat() if r[5] else None,
                        "preview": r[6],
                    }
                    for r in rows
                ]
                return {"documents": documents, "total": int(total)}
    except Exception as exc:
        return {"documents": [], "total": 0, "error": str(exc)}

    return {"documents": [], "total": 0}


@router.delete("/collections/{collection_id}/documents/{document_id}")
async def delete_document(
    collection_id: str,
    document_id: str,
    request: Request,
) -> dict[str, Any]:
    """Delete a document from a knowledge collection."""
    tenant = _require_tenant(request)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)

    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    try:
        if hasattr(knowledge_store, "delete_document_async"):
            count = await knowledge_store.delete_document_async(
                document_id=document_id,
                collection_id=collection_id,
                tenant_ctx=tenant,
            )
            return {"status": "deleted", "document_id": document_id, "chunks_deleted": count}
        raise HTTPException(status_code=501, detail="Document deletion not implemented")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/collections/{collection_id}/documents/{document_id}/reingest")
async def reingest_document(
    collection_id: str,
    document_id: str,
    request: Request,
) -> dict[str, Any]:
    """Re-ingest a document (re-fetch source URL, re-chunk, re-embed)."""
    tenant = _require_tenant(request)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)

    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    try:
        if hasattr(knowledge_store, "reingest_document"):
            await knowledge_store.reingest_document(
                document_id=document_id,
                collection_id=collection_id,
                tenant_ctx=tenant,
            )
            return {"status": "reingested", "document_id": document_id}

        # Fallback: mark document for re-indexing in DB
        db = getattr(knowledge_store, "_db", None) or getattr(
            knowledge_store, "_session_factory", None
        )
        if db:
            from sqlalchemy import text as _t

            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant.tenant_id),
            ):
                await session.execute(
                    _t(
                        "UPDATE knowledge_documents SET status = 'pending_reingest',"
                        " updated_at = NOW() WHERE id = :id AND collection_id = :cid"
                        " AND tenant_id = :tid"
                    ),
                    {"id": document_id, "cid": collection_id, "tid": tenant.tenant_id},
                )
                await session.commit()
            return {"status": "queued", "document_id": document_id, "message": "Re-ingest queued"}

        return {
            "status": "unsupported",
            "message": "Re-ingest not implemented for this storage backend",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/collections/{collection_id}/sync")
async def sync_collection(
    collection_id: str,
    request: Request,
) -> dict[str, Any]:
    """Sync all documents in a collection from their source URLs."""
    tenant = _require_tenant(request)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)

    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    try:
        if hasattr(knowledge_store, "sync_collection"):
            result = await knowledge_store.sync_collection(
                collection_id=collection_id,
                tenant_ctx=tenant,
            )
            return {"status": "syncing", "collection_id": collection_id, **(result or {})}
        return {"status": "unsupported", "message": "Sync not implemented for this storage backend"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# New ingestion sources: Email, Notion, Google Drive
# ---------------------------------------------------------------------------


class EmailIngestRequest(BaseModel):
    raw_email: str = Field(..., description="Raw RFC-5322 email string")
    collection_id: str
    source_identity: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotionIngestRequest(BaseModel):
    api_key: SecretStr = Field(..., description="Notion integration token")
    page_id: str | None = None
    database_id: str | None = None
    collection_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GDriveIngestRequest(BaseModel):
    folder_id: str = Field(..., description="Google Drive folder ID")
    collection_id: str
    service_account_key_json: str = Field(..., description="Service account key JSON as a string")
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/ingest/email")
async def ingest_email(
    body: EmailIngestRequest,
    request: Request,
) -> dict[str, Any]:
    """Ingest a raw email message into a knowledge collection."""
    tenant = _require_tenant(request)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)
    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    try:
        from app.ingestion.orchestrator import IngestionOrchestrator
        from app.ingestion.parsers.email_parser import EmailParser

        parser = EmailParser()
        parts = parser.parse(body.raw_email)
        content = "\n\n".join(parts)
        meta = {**parser.parse_metadata(body.raw_email), **body.metadata}

        orch = IngestionOrchestrator(knowledge_store=knowledge_store)
        result = await orch.ingest(
            content,
            content_type="text",
            collection_id=body.collection_id,
            tenant_ctx=tenant,
            source_url=f"email:{meta.get('message_id', '')}",
            metadata=meta,
            source_identity=body.source_identity or meta.get("message_id", ""),
            in_memory_only=False,
        )
        return {
            "status": "ingested",
            "chunks_created": result.chunks_created,
            "source": "email",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/ingest/notion")
async def ingest_notion(
    body: NotionIngestRequest,
    request: Request,
) -> dict[str, Any]:
    """Ingest Notion pages into a knowledge collection."""
    tenant = _require_tenant(request)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)
    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    if not body.page_id and not body.database_id:
        raise HTTPException(status_code=400, detail="Either page_id or database_id is required")

    try:
        from app.ingestion.connectors.notion_connector import NotionConnector
        from app.ingestion.orchestrator import IngestionOrchestrator

        connector = NotionConnector(api_key=body.api_key.get_secret_value())
        orch = IngestionOrchestrator(knowledge_store=knowledge_store)

        total_chunks = 0
        if body.page_id:
            content = await connector.fetch_page_content(body.page_id)
            if content.strip():
                res = await orch.ingest(
                    content,
                    content_type="text",
                    collection_id=body.collection_id,
                    tenant_ctx=tenant,
                    source_url=f"notion:page:{body.page_id}",
                    metadata={"notion_page_id": body.page_id, **body.metadata},
                    in_memory_only=False,
                )
                total_chunks += res.chunks_created
        elif body.database_id:
            pages = await connector.list_pages(body.database_id)
            for page in pages:
                pid = page["id"]
                content = await connector.fetch_page_content(pid)
                if not content.strip():
                    continue
                res = await orch.ingest(
                    content,
                    content_type="text",
                    collection_id=body.collection_id,
                    tenant_ctx=tenant,
                    source_url=f"notion:page:{pid}",
                    metadata={"notion_page_id": pid, **body.metadata},
                    in_memory_only=False,
                )
                total_chunks += res.chunks_created

        return {"status": "ingested", "chunks_created": total_chunks, "source": "notion"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/ingest/gdrive-folder")
async def ingest_gdrive_folder(
    body: GDriveIngestRequest,
    request: Request,
) -> dict[str, Any]:
    """Ingest all supported files from a Google Drive folder."""
    tenant = _require_tenant(request)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)
    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    try:
        import os
        import tempfile

        from app.ingestion.connectors.gdrive_connector import GDriveConnector
        from app.ingestion.orchestrator import IngestionOrchestrator

        # Write SA key to temp file so googleapiclient can read it
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(body.service_account_key_json)
            key_path = f.name

        try:
            connector = GDriveConnector(key_path=key_path)
            files = connector.list_files(body.folder_id)
            orch = IngestionOrchestrator(knowledge_store=knowledge_store)
            total_chunks = 0
            errors: list[str] = []
            for file_meta in files:
                fid = file_meta["id"]
                fname = file_meta.get("name", fid)
                mime = file_meta.get("mimeType", "")
                try:
                    content = connector.download_file(fid, mime)
                    if not content.strip():
                        continue
                    res = await orch.ingest(
                        content,
                        content_type="auto",
                        collection_id=body.collection_id,
                        tenant_ctx=tenant,
                        source_url=f"gdrive:{fid}",
                        metadata={"gdrive_file_id": fid, "filename": fname, **body.metadata},
                        in_memory_only=False,
                    )
                    total_chunks += res.chunks_created
                except Exception as file_exc:
                    errors.append(f"{fname}: {file_exc!s}")
        finally:
            os.unlink(key_path)

        return {
            "status": "ingested",
            "chunks_created": total_chunks,
            "source": "gdrive",
            "files_processed": len(files),
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
