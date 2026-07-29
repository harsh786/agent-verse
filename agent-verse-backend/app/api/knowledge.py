"""Knowledge API — collections, document ingestion, hybrid search, semantic cache."""

from __future__ import annotations

import hashlib
import uuid as _uuid
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, SecretStr

from app.net.ssrf_guard import SSRFError, assert_public_url
from app.rag.models import Chunk, Document, KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import TenantContext

# Check if Playwright is available at module load time
try:
    import playwright.async_api as _playwright_api  # type: ignore[import-not-found]
    _check_playwright = _playwright_api.async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

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
    """Ingest one or more URLs using headless Playwright for JS-rendered content."""
    collection_id: str
    urls: list[str]              # supports batch ingestion (max 20)
    selector: str = "body"       # CSS selector for text extraction
    screenshot: bool = False     # capture screenshot and store as metadata
    source_type: str = "rpa-web" # stored in metadata for attribution
    max_chars: int = 50_000      # per-URL char cap
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


class CollectionIngestRequest(BaseModel):
    """Request body for POST /collections/{collection_id}/documents (IngestionOrchestrator path)."""
    content: str
    content_type: str = "auto"
    source_url: str = ""
    metadata: dict[str, Any] = {}
    dry_run: bool = False
    in_memory_only: bool = False


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
        )
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
async def create_collection(
    request: Request, body: CreateCollectionRequest
) -> dict[str, Any]:
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

    # Delete from DB: chunks first (FK constraint), then the collection row
    db = getattr(store, "_db", None)
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session, session.begin():
                await session.execute(
                    text(
                        "DELETE FROM documents "
                        "WHERE collection_id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
                await session.execute(
                    text(
                        "DELETE FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("delete_collection_db_failed: %s", exc)

    # Remove from in-memory cache
    key = (tenant_ctx.tenant_id, collection_id)
    store._data.pop(key, None)


# ---------------------------------------------------------------------------
# Endpoints — ingestion
# ---------------------------------------------------------------------------

@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_document(
    request: Request, body: IngestRequest
) -> dict[str, Any]:
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
        chunks.append(Chunk(
            document_id=document.document_id,
            content=chunk_text,
            embedding=embeddings[idx],
            chunk_index=idx,
            metadata={**str_metadata, "source_type": body.source_type},
        ))
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
    top_k: int = 10,
    threshold: float = 0.5,
) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _knowledge_store(request)

    # Input validation: clamp top_k and cap query length
    top_k = max(1, min(top_k, 100))
    q = q[:10000]

    embedder = getattr(request.app.state, "embedder", None)

    # FIX 5: Fail loudly when no embedder is configured.
    # Previously: returned empty embeddings silently, corrupting search results.
    # Now: raises 503 with an actionable message for the operator.
    if embedder is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "No embedding provider configured. "
                "Set VOYAGE_API_KEY or OPENAI_API_KEY to enable knowledge base search."
            ),
        )

    from app.providers.base import embed_texts
    query_embeddings = await embed_texts([q], provider=embedder)
    query_embedding = query_embeddings[0]
    if hasattr(store, "hybrid_search_db"):
        results = await store.hybrid_search_db(
            q, query_embedding, collection_id, tenant_ctx, top_k=top_k
        )
    else:
        results = store.hybrid_search(q, query_embedding, collection_id, tenant_ctx, top_k=top_k)
    return [
        {
            "chunk_id": r.chunk_id,
            "content": r.content,
            "score": r.score,
            "vector_score": r.vector_score,
            "trigram_score": r.trigram_score,
            # Source citation fields
            "source_file": getattr(r, "metadata", {}).get("source_file", ""),
            "source_url": getattr(r, "metadata", {}).get("source_url", ""),
            "char_offset": getattr(r, "metadata", {}).get("char_offset"),
            "line_start": getattr(r, "metadata", {}).get("line_start"),
        }
        for r in results
        if r.score >= threshold
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
    for idx, (chunk, embedding) in enumerate(
        zip(non_empty_chunks, embeddings, strict=True)
    ):
        rag_chunks.append(Chunk(
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
        ))
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
async def ingest_repository(
    request: Request, body: RepoIngestRequest
) -> dict[str, Any]:
    """Clone a git repository and ingest all matching files.

    Uses git (open source) for cloning. No cloud API calls.
    Returns immediately — ingestion runs in background.
    """
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    collection = await store.get_collection_async(
        body.collection_id,
        tenant_ctx=tenant,
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Knowledge collection not found")
    await _embed_texts_or_http(["Repository ingestion readiness check"], embedder)
    try:
        job_id = await store.create_ingestion_job_async(
            collection_id=body.collection_id,
            source_url=body.repo_url,
            source_type="repository",
            title=body.repo_url.rstrip("/").rsplit("/", 2)[-1],
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
            repo_url=body.repo_url,
            collection_id=body.collection_id,
            branch=body.branch,
            file_patterns=body.file_patterns,
            max_files=body.max_files,
            store=store,
            embedder=embedder,
            tenant_ctx=tenant,
        )
    )
    # Don't await — return immediately
    _ = task  # Task runs in background

    return {
        "status": "ingestion_started",
        "job_id": job_id,
        "repo_url": body.repo_url,
        "collection_id": body.collection_id,
        "branch": body.branch,
        "message": "Repository ingestion started in background.",
    }


@router.get("/ingest/jobs/{job_id}")
async def get_ingestion_job(request: Request, job_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    try:
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
) -> None:
    import asyncio
    import pathlib
    import shutil
    import tempfile

    from app.knowledge.chunker_v2 import chunk_by_tokens as _chunk_by_tokens_repo
    from app.observability.logging import get_logger
    logger = get_logger(__name__)

    tmpdir = tempfile.mkdtemp(prefix="agentverse_repo_")
    try:
        await store.update_ingestion_job_async(
            job_id,
            status="running",
            chunk_count=0,
            error_message=None,
            tenant_ctx=tenant_ctx,
        )
        # Clone using git — non-blocking async subprocess
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", "--branch", branch, repo_url, tmpdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except TimeoutError:
            proc.kill()
            raise RuntimeError("Repository clone timed out") from None

        if proc.returncode != 0:
            raise RuntimeError("Repository clone failed")

        files_processed = 0
        prepared_chunks: list[Chunk] = []
        seen_files: set[pathlib.Path] = set()

        for pattern in file_patterns:
            for filepath in pathlib.Path(tmpdir).glob(pattern):
                if files_processed >= max_files:
                    break
                if not filepath.is_file() or filepath in seen_files:
                    continue
                seen_files.add(filepath)
                text_content = filepath.read_text(encoding="utf-8", errors="replace")
                if not text_content.strip():
                    continue
                ext = filepath.suffix.lstrip(".")
                src_type = "code" if ext in {"py", "ts", "js", "jsx", "tsx"} else "text"
                raw_chunks = _chunk_by_tokens_repo(
                    text_content,
                    max_tokens=512,
                    overlap_tokens=64,
                )
                rel_path = str(filepath.relative_to(tmpdir))
                document_id = hashlib.sha256(
                    f"{repo_url}:{rel_path}".encode()
                ).hexdigest()[:32]
                embeddings = await _embed_texts_or_http(raw_chunks, embedder)
                prepared_chunks.extend(
                    Chunk(
                        document_id=document_id,
                        content=chunk_content,
                        embedding=embeddings[index],
                        chunk_index=index,
                        metadata={
                            "source_file": rel_path,
                            "repo_url": repo_url,
                            "source_type": src_type,
                            "source_doc_id": document_id,
                        },
                    )
                    for index, chunk_content in enumerate(raw_chunks)
                )
                files_processed += 1

        await store.ingest_chunks_async(
            prepared_chunks,
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
        )
        await store.update_ingestion_job_async(
            job_id,
            status="completed",
            chunk_count=len(prepared_chunks),
            error_message=None,
            tenant_ctx=tenant_ctx,
        )
        logger.info("repo_ingest_complete", repo=repo_url, files=files_processed)
    except Exception as exc:
        logger.warning("repo_ingest_failed", repo=repo_url, error=type(exc).__name__)
        try:
            await store.update_ingestion_job_async(
                job_id,
                status="failed",
                chunk_count=0,
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


# ---------------------------------------------------------------------------
# Endpoints — OpenAPI spec ingestion
# ---------------------------------------------------------------------------

@router.post("/ingest/openapi", status_code=201)
async def ingest_openapi(
    request: Request, body: OpenAPIIngestRequest
) -> dict[str, Any]:
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
            params = [p.get("name", "") for p in op.get("parameters", [])
                      if isinstance(p, dict)]

            chunk_text = (
                f"{method.upper()} {path}\n"
                f"Summary: {summary}\n"
                f"Description: {description}\n"
                f"Parameters: {', '.join(params) if params else 'none'}"
            ).strip()

            if not chunk_text:
                continue

            embedding = (await _embed_texts_or_http([chunk_text], embedder))[0]

            rag_chunks.append(Chunk(
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
            ))
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
                content = re.sub(r'<[^>]+>', ' ', raw)
                content = re.sub(r'\s+', ' ', content).strip()[:50000]
                title_match = re.search(r'<title[^>]*>(.*?)</title>', raw, re.IGNORECASE)
                metadata["title"] = title_match.group(1) if title_match else body.url

        elif body.source_type == "github":
            raw_url = body.url.replace(
                "github.com", "raw.githubusercontent.com"
            ).replace("/blob/", "/")
            import httpx
            headers: dict[str, str] = {}
            import os as _os
            if (token := _os.getenv("GITHUB_TOKEN")):
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
        rag_chunks.append(RagChunk(
            document_id=doc_id,
            content=chunk.content,
            embedding=embedding,
            chunk_index=idx,
            metadata={**{k: str(v) for k, v in metadata.items()}, "source_type": body.source_type},
        ))
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
        rag_chunks.append(Chunk(
            document_id=source_doc_id,
            content=content,
            embedding=embedding,
            chunk_index=chunk_index,
            metadata={
                k: str(v) for k, v in (chunk_data.get("metadata") or {}).items()
            } | {
                "source_url": chunk_data.get("source_url", ""),
                "source_type": chunk_data.get("source_type", ""),
                "source_doc_id": source_doc_id,
                "page_number": str(chunk_data.get("page_number") or ""),
            },
        ))
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
async def ingest_github(
    request: Request, body: GitHubIngestRequest
) -> dict[str, Any]:
    """Ingest a GitHub repository into a knowledge collection via GitHub REST API."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    ingestor = GitHubIngestor()
    chunks = await ingestor.ingest_repo(
        body.owner, body.repo,
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
async def ingest_confluence(
    request: Request, body: ConfluenceIngestRequest
) -> dict[str, Any]:
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
async def ingest_jira(
    request: Request, body: JiraIngestRequest
) -> dict[str, Any]:
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
async def ingest_slack(
    request: Request, body: SlackIngestRequest
) -> dict[str, Any]:
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
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No embedding provider configured",
        )

    query: str = body.get("query", "")
    collection_ids: list[str] = body.get("collection_ids", [])
    top_k: int = int(body.get("top_k", 10))
    top_k = max(1, min(100, top_k))

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

    from app.knowledge.federated_search import federated_search
    try:
        results = await federated_search(
            query=query,
            collection_ids=collection_ids,
            store=store,
            top_k=top_k,
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Federated knowledge search is unavailable",
        ) from exc
    return {
        "results": results,
        "total": len(results),
        "collections_searched": len(collection_ids),
    }


# ---------------------------------------------------------------------------
# Intelligence: RAG Chat & Collection Analytics
# ---------------------------------------------------------------------------

class RagChatRequest(BaseModel):
    question: str
    collection_ids: list[str] = []   # empty = all tenant collections
    top_k: int = 5
    max_context_chars: int = 6000    # total context window for retrieved chunks
    stream: bool = False


@router.post("/chat")
async def rag_chat(request: Request, body: RagChatRequest) -> dict[str, Any]:
    """Answer a question using retrieval-augmented generation.

    Retrieves relevant chunks from the knowledge store and asks the LLM
    to answer using only those chunks. Returns the answer plus cited chunks.
    """
    tenant_ctx = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)
    provider = getattr(request.app.state, "llm_provider", None)

    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    # Resolve collection IDs (default = all tenant collections)
    collection_ids = body.collection_ids
    if not collection_ids:
        collections = await store.list_collections_async(tenant_ctx=tenant_ctx)
        collection_ids = [c.collection_id for c in collections]

    if not collection_ids:
        return {
            "answer": "No knowledge collections found. Ingest some documents first.",
            "citations": [],
            "collections_searched": 0,
            "chunks_retrieved": 0,
        }

    # Retrieve relevant chunks
    top_k = max(1, min(20, body.top_k))
    all_results: list[dict[str, Any]] = []
    query_embedding: list[float] = []

    # Embed query once if embedder is available
    if embedder is not None:
        try:
            from app.providers.base import embed_texts
            vecs = await embed_texts([body.question], provider=embedder)
            query_embedding = vecs[0]
        except Exception:
            pass

    for cid in collection_ids[:10]:  # cap at 10 collections
        try:
            if query_embedding and hasattr(store, "hybrid_search_db"):
                hits = await store.hybrid_search_db(
                    body.question, query_embedding, cid, tenant_ctx, top_k=top_k
                )
            else:
                hits = store.hybrid_search(
                    body.question, query_embedding or [], cid, tenant_ctx, top_k=top_k
                )
            for h in hits:
                all_results.append({
                    "collection_id": cid,
                    "chunk_id": getattr(h, "chunk_id", ""),
                    "content": getattr(h, "content", ""),
                    "score": round(getattr(h, "score", 0.0), 4),
                    "source_url": getattr(h, "source_url", ""),
                    "source_doc_id": getattr(h, "source_doc_id", ""),
                    "page_number": getattr(h, "page_number", None),
                })
        except Exception:
            pass

    # Sort by score, deduplicate
    all_results.sort(key=lambda x: x["score"], reverse=True)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in all_results:
        key = r["content"][:128]
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    top_chunks = deduped[:top_k]

    # Build context string, capped at max_context_chars
    context_parts: list[str] = []
    context_len = 0
    used_chunks: list[dict[str, Any]] = []
    for i, chunk in enumerate(top_chunks):
        chunk_text = f"[{i+1}] {chunk['content']}"
        if context_len + len(chunk_text) > body.max_context_chars:
            break
        context_parts.append(chunk_text)
        context_len += len(chunk_text)
        used_chunks.append(chunk)

    context_str = "\n\n".join(context_parts)

    # Prepare citations
    citations = [
        {
            "index": i + 1,
            "chunk_id": c["chunk_id"],
            "collection_id": c["collection_id"],
            "score": c["score"],
            "source_url": c["source_url"],
            "page_number": c["page_number"],
            "excerpt": c["content"][:300] + ("…" if len(c["content"]) > 300 else ""),
        }
        for i, c in enumerate(used_chunks)
    ]

    if provider is None:
        return {
            "answer": (
                f"Retrieved {len(used_chunks)} chunks (no LLM available for answer synthesis). "
                "Configure ANTHROPIC_API_KEY or OPENAI_API_KEY to enable full RAG chat."
            ),
            "citations": citations,
            "collections_searched": len(collection_ids),
            "chunks_retrieved": len(used_chunks),
        }

    from app.providers.base import CompletionRequest, Message
    system_prompt = (
        "You are a helpful assistant. Answer the user's question using ONLY "
        "the provided context excerpts. If the context doesn't contain enough "
        "information, say so. Cite sources using bracket notation [1], [2], etc.\n\n"
        f"Context:\n{context_str}"
    )
    try:
        resp = await provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=body.question),
                ],
                model="",
                max_tokens=1200,
            )
        )
        answer = resp.content.strip()
    except Exception as e:
        answer = f"LLM answer generation failed: {e}. See citations for relevant content."

    return {
        "answer": answer,
        "citations": citations,
        "collections_searched": len(collection_ids),
        "chunks_retrieved": len(used_chunks),
        "question": body.question,
    }


@router.get("/collections/{collection_id}/stats")
async def get_collection_stats(
    request: Request, collection_id: str
) -> dict[str, Any]:
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
# RPA-backed URL ingestion (JavaScript-rendered pages via Playwright)
# ---------------------------------------------------------------------------

@router.post("/ingest/rpa-url", status_code=201)
async def ingest_from_rpa_url(
    request: Request, body: RpaUrlIngestRequest,
) -> dict[str, Any]:
    """Ingest one or more URLs using headless Playwright (Chromium).

    Unlike /ingest/url (httpx), this endpoint renders JavaScript, supports
    batch URLs, and optionally captures screenshots. Falls back to httpx
    when Playwright is not installed.
    """
    import re as _re

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

    # Use module-level flag and import playwright inside if available
    _playwright_ok = _PLAYWRIGHT_AVAILABLE
    if _playwright_ok:
        from playwright.async_api import async_playwright as _async_playwright

    total_chunks = 0
    results: list[dict[str, Any]] = []

    for url in body.urls:
        content: str = ""
        screenshot_b64: str = ""
        links: list[str] = []
        playwright_used = False

        # SSRF guard — reject internal/metadata URLs before fetching
        try:
            assert_public_url(url, context="/ingest/rpa-url")
        except SSRFError as exc:
            results.append({
                "url": url, "success": False,
                "error": f"URL blocked for security reasons: {exc}",
                "chunks_ingested": 0, "playwright_used": False,
            })
            continue

        if _playwright_ok:
            try:
                async with _async_playwright() as _pw:
                    _browser = await _pw.chromium.launch(headless=True)
                    _ctx = await _browser.new_context(
                        viewport={"width": 1280, "height": 900},
                        user_agent="AgentVerse-Knowledge/1.0",
                    )
                    _page = await _ctx.new_page()
                    _page.set_default_timeout(30_000)
                    await _page.goto(url, wait_until="networkidle", timeout=30_000)

                    content = await _page.inner_text(body.selector)
                    content = content[: body.max_chars]

                    if body.include_links:
                        _anchors = await _page.evaluate(
                            "Array.from(document.querySelectorAll('a[href]'))"
                            ".map(a => a.href).filter(h => h.startsWith('http'))"
                        )
                        links = list(dict.fromkeys(_anchors))[:50]

                    if body.screenshot:
                        import base64 as _b64
                        _ss_bytes = await _page.screenshot(full_page=False)
                        screenshot_b64 = _b64.b64encode(_ss_bytes).decode()

                    await _browser.close()
                playwright_used = True
            except Exception as exc:
                import httpx as _httpx
                try:
                    async with _httpx.AsyncClient(timeout=30.0) as _client:
                        _resp = await _client.get(url, headers={"User-Agent": "AgentVerse/1.0"})
                        _resp.raise_for_status()
                        raw = _resp.text
                        content = _re.sub(r"<[^>]+>", " ", raw)
                        content = _re.sub(r"\s+", " ", content).strip()[: body.max_chars]
                except Exception:
                    results.append({
                        "url": url, "success": False, "error": str(exc),
                        "chunks_ingested": 0, "playwright_used": False,
                    })
                    continue
        else:
            import httpx as _httpx
            try:
                async with _httpx.AsyncClient(timeout=30.0) as _client:
                    _resp = await _client.get(url, headers={"User-Agent": "AgentVerse/1.0"})
                    _resp.raise_for_status()
                    raw = _resp.text
                    content = _re.sub(r"<[^>]+>", " ", raw)
                    content = _re.sub(r"\s+", " ", content).strip()[: body.max_chars]
            except Exception as exc:
                results.append({
                    "url": url, "success": False, "error": str(exc),
                    "chunks_ingested": 0, "playwright_used": False,
                })
                continue

        if not content.strip():
            results.append({
                "url": url, "success": False, "error": "No content extracted",
                "chunks_ingested": 0, "playwright_used": playwright_used,
            })
            continue

        from app.knowledge.chunker_v2 import chunk_by_tokens
        raw_chunks = chunk_by_tokens(content, max_tokens=512, overlap_tokens=64)
        if not raw_chunks:
            raw_chunks = [content.strip()]

        chunk_dicts: list[dict[str, Any]] = []
        for i, chunk_text in enumerate(raw_chunks):
            chunk_dicts.append({
                "content": chunk_text,
                "source_url": url,
                "source_type": body.source_type,
                "source_doc_id": url,
                "page_number": None,
                "metadata": {
                    "source_url": url,
                    "source_type": body.source_type,
                    "playwright_used": str(playwright_used),
                    "selector": body.selector,
                    "chunk_index": str(i),
                },
            })

        if links:
            link_content = f"Page links from {url}:\n" + "\n".join(links)
            chunk_dicts.append({
                "content": link_content,
                "source_url": url,
                "source_type": f"{body.source_type}-links",
                "source_doc_id": f"{url}#links",
                "page_number": None,
                "metadata": {"source_url": url, "source_type": f"{body.source_type}-links"},
            })

        ingested = await _ingest_chunks_from_source(
            store, chunk_dicts, body.collection_id, tenant_ctx, embedder
        )
        total_chunks += ingested
        results.append({
            "url": url,
            "success": True,
            "chunks_ingested": ingested,
            "total_chars": len(content),
            "playwright_used": playwright_used,
            "screenshot_captured": bool(screenshot_b64),
            "links_extracted": len(links),
        })

    return {
        "collection_id": body.collection_id,
        "source_type": body.source_type,
        "urls_processed": len(body.urls),
        "urls_succeeded": sum(1 for r in results if r.get("success")),
        "total_chunks_ingested": total_chunks,
        "playwright_available": _playwright_ok,
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
    knowledge_store: KnowledgeStore | None = getattr(
        request.app.state, "knowledge_store", None
    )

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
        orchestrator = IngestionOrchestrator(
            knowledge_store=knowledge_store,
            embedder=getattr(request.app.state, "embedder", None),
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
        db = (
            getattr(knowledge_store, "_db", None)
            or getattr(knowledge_store, "_session_factory", None)
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
