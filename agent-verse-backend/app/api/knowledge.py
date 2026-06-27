"""Knowledge API — collections, document ingestion, hybrid search, semantic cache."""

from __future__ import annotations

import hashlib
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.rag.models import Chunk, Document, KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import TenantContext

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


# ---------------------------------------------------------------------------
# Endpoints — collections
# ---------------------------------------------------------------------------

@router.get("/collections")
async def list_collections(request: Request) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _knowledge_store(request)
    collections = store.list_collections(tenant_ctx=tenant_ctx)
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
    cid = store.create_collection(collection, tenant_ctx=tenant_ctx)
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
    collection = store.get_collection(collection_id, tenant_ctx=tenant_ctx)
    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection {collection_id} not found",
        )

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
    store._data.pop(key, None)  # type: ignore[attr-defined]


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
    collection = store.get_collection(body.collection_id, tenant_ctx=tenant_ctx)
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

    # Split into semantic chunks using SemanticChunker with real or fallback embeddings.
    from app.rag.chunker import Chunk as _CChunk
    from app.rag.chunker import SemanticChunker
    chunker = SemanticChunker(max_chars=512, overlap_chars=64)
    source_type = body.source_type or "text"
    chunks_text = chunker.chunk(body.content, source_type=source_type)

    # Fallback: very short content that doesn't meet min_chunk threshold
    if not chunks_text and body.content.strip():
        chunks_text = [_CChunk(
            content=body.content.strip(),
            start_char=0,
            end_char=len(body.content),
        )]

    chunks_created = 0
    embedder = getattr(request.app.state, "embedder", None)
    from app.providers.base import embed_texts
    for idx, text_chunk in enumerate(chunks_text):
        chunk_text = text_chunk.content
        embeddings = await embed_texts([chunk_text], provider=embedder)
        chunk_embedding = embeddings[0]
        chunk = Chunk(
            document_id=document.document_id,
            content=chunk_text,
            embedding=chunk_embedding,
            chunk_index=idx,
            metadata={**str_metadata, "source_type": body.source_type},
        )
        store.ingest_chunk(chunk, collection_id=body.collection_id, tenant_ctx=tenant_ctx)
        chunks_created += 1

    return {
        "document_id": document.document_id,
        "collection_id": body.collection_id,
        "chunks_created": chunks_created,
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

    embedder = getattr(request.app.state, "embedder", None)
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
    tenant_ctx: TenantContext = _require_tenant(request)
    cache = _semantic_cache(request)
    stats = cache.stats(tenant_ctx=tenant_ctx)
    return {
        "tenant_id": tenant_ctx.tenant_id,
        "hits": stats["hits"],
        "misses": stats["misses"],
    }


@router.delete("/cache", status_code=status.HTTP_204_NO_CONTENT)
async def clear_cache(request: Request) -> None:
    tenant_ctx: TenantContext = _require_tenant(request)
    cache = _semantic_cache(request)
    cache.clear(tenant_ctx=tenant_ctx)


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

            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            text = content_bytes.decode("utf-8", errors="replace")
    elif ext in {"docx", "doc"}:
        try:
            import io

            import docx
            doc = docx.Document(io.BytesIO(content_bytes))
            text = "\n".join(para.text for para in doc.paragraphs)
        except ImportError:
            text = content_bytes.decode("utf-8", errors="replace")
    else:
        text = content_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(422, "File is empty or could not be parsed")

    # Chunk using SemanticChunker
    from app.rag.chunker import Chunk as _CChunk
    from app.rag.chunker import SemanticChunker
    chunker = SemanticChunker(max_chars=512, overlap_chars=64)
    chunks = chunker.chunk(text, source_type=source_type)

    # Fallback for very short content
    if not chunks and text.strip():
        chunks = [_CChunk(content=text.strip(), start_char=0, end_char=len(text))]

    ingested = 0
    document_id = _uuid.uuid4().hex
    from app.providers.base import EmbedRequest
    for idx, chunk in enumerate(chunks):
        if not chunk.content.strip():
            continue
        embedding: list[float] = []
        if embedder:
            try:
                resp = await embedder.embed(EmbedRequest(texts=[chunk.content]))
                embedding = resp.embeddings[0] if resp.embeddings else []
            except Exception:
                embedding = []
        rag_chunk = Chunk(
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
        store.ingest_chunk(rag_chunk, collection_id=collection_id, tenant_ctx=tenant)
        ingested += 1

    return {
        "filename": filename,
        "chunks_created": ingested,
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

    import asyncio
    # Run in background task
    task = asyncio.create_task(
        _ingest_repo_background(
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
        "repo_url": body.repo_url,
        "collection_id": body.collection_id,
        "branch": body.branch,
        "message": "Repository ingestion started in background. "
                   "Check /knowledge/collections for progress.",
    }


async def _ingest_repo_background(
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

    from app.observability.logging import get_logger
    from app.providers.base import EmbedRequest
    from app.rag.chunker import SemanticChunker
    logger = get_logger(__name__)

    tmpdir = tempfile.mkdtemp(prefix="agentverse_repo_")
    try:
        # Clone using git — non-blocking async subprocess
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", "--branch", branch, repo_url, tmpdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("repo_clone_timeout", repo=repo_url)
            return

        if proc.returncode != 0:
            logger.warning(
                "repo_clone_failed",
                repo=repo_url,
                error=(stderr or b"").decode("utf-8", errors="replace")[:200],
            )
            return

        chunker = SemanticChunker(max_chars=512, overlap_chars=64)
        files_processed = 0

        for pattern in file_patterns:
            for filepath in pathlib.Path(tmpdir).rglob(pattern.lstrip("*/")):
                if files_processed >= max_files:
                    break
                if not filepath.is_file():
                    continue
                try:
                    text = filepath.read_text(encoding="utf-8", errors="replace")
                    if not text.strip():
                        continue
                    ext = filepath.suffix.lstrip(".")
                    src_type = "code" if ext in {"py", "ts", "js", "jsx", "tsx"} else "text"
                    chunks = chunker.chunk(text, source_type=src_type)
                    rel_path = str(filepath.relative_to(tmpdir))
                    doc_id = _uuid.uuid4().hex
                    for idx, chunk in enumerate(chunks):
                        embedding: list[float] = []
                        if embedder:
                            try:
                                resp = await embedder.embed(EmbedRequest(texts=[chunk.content]))
                                embedding = resp.embeddings[0] if resp.embeddings else []
                            except Exception:
                                pass
                        rag_chunk = Chunk(
                            document_id=doc_id,
                            content=chunk.content,
                            embedding=embedding,
                            chunk_index=idx,
                            metadata={
                                "source_file": rel_path,
                                "repo_url": repo_url,
                                "char_offset": str(chunk.start_char),
                                "source_type": src_type,
                            },
                        )
                        store.ingest_chunk(rag_chunk, collection_id=collection_id,
                                           tenant_ctx=tenant_ctx)
                    files_processed += 1
                except Exception as exc:
                    logger.warning("repo_file_ingest_failed",
                                   file=str(filepath), error=str(exc))

        logger.info("repo_ingest_complete", repo=repo_url, files=files_processed)
    except Exception as exc:
        logger.warning("repo_ingest_failed", repo=repo_url, error=str(exc))
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
            import yaml as _yaml
            spec = _yaml.safe_load(body.content)
    except Exception as exc:
        raise HTTPException(422, f"Could not parse OpenAPI spec: {exc}")

    paths = spec.get("paths", {})
    chunks_created = 0
    from app.providers.base import EmbedRequest

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

            embedding: list[float] = []
            if embedder:
                try:
                    resp = await embedder.embed(EmbedRequest(texts=[chunk_text]))
                    embedding = resp.embeddings[0] if resp.embeddings else []
                except Exception:
                    pass

            rag_chunk = Chunk(
                document_id=_uuid.uuid4().hex,
                content=chunk_text,
                embedding=embedding,
                chunk_index=chunks_created,
                metadata={
                    "source_url": body.source_url,
                    "source_type": "openapi",
                    "endpoint": f"{method.upper()} {path}",
                },
            )
            store.ingest_chunk(rag_chunk, collection_id=body.collection_id,
                               tenant_ctx=tenant)
            chunks_created += 1

    return {
        "endpoints_ingested": chunks_created,
        "collection_id": body.collection_id,
        "source_url": body.source_url,
    }
