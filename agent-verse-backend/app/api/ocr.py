"""OCR document extraction API."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.observability.logging import get_logger
from app.ocr.engine import OcrEngine
from app.tools.ocr_tool import OcrDocumentTool

_log = get_logger(__name__)
router = APIRouter(prefix="/ocr", tags=["ocr"])

_engine = OcrEngine()
_tool = OcrDocumentTool(ocr_engine=_engine)


class OcrRequest(BaseModel):
    image_base64: str = Field(default="", description="Base64-encoded image bytes")
    pdf_base64: str = Field(default="", description="Base64-encoded PDF bytes")
    filename: str = Field(default="document", description="Optional filename hint")
    persist_to_kb: bool = Field(
        default=False,
        description="WS-13: also index the extracted text into the knowledge base.",
    )
    collection_id: str = Field(
        default="",
        description="Target knowledge collection when persist_to_kb is set.",
    )


class OcrFieldResult(BaseModel):
    value: str
    confidence: float
    is_valid: bool = True
    raw_value: str | None = None


class OcrResponse(BaseModel):
    raw_text: str
    document_type: str
    fields: dict[str, OcrFieldResult]
    engine_used: str
    overall_confidence: float
    page_count: int
    # WS-13: populated only when persist_to_kb was requested.
    kb_persisted: bool = False
    kb_deduplicated: bool = False
    kb_chunks_ingested: int = 0
    kb_collection_id: str = ""


async def _persist_ocr_to_kb(
    request: Request,
    *,
    raw_text: str,
    collection_id: str,
    filename: str,
    engine_used: str,
) -> dict[str, Any]:
    """WS-13: index OCR'd text into the KnowledgeStore with ``source_type=ocr``.

    Cross-source deduped via ``exists_by_hash`` and stamped with provenance
    (``source_type=ocr``, ``ocr_used=true``, ``doc_content_hash``) so the SAME
    content from ingestion/RPA/OCR converges on the one store. Requires an
    authenticated tenant.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not collection_id:
        raise HTTPException(
            status_code=422, detail="collection_id is required when persist_to_kb is set"
        )
    if not raw_text.strip():
        return {"kb_persisted": False, "kb_deduplicated": False, "kb_chunks_ingested": 0}

    store = getattr(request.app.state, "knowledge_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge store is unavailable")
    embedder = getattr(request.app.state, "embedder", None)

    content = raw_text.strip()
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # Cross-source dedup against the one store (any source).
    if await store.exists_by_hash(
        content_hash=content_hash,
        tenant_id=tenant_ctx.tenant_id,
        collection_id=collection_id,
    ):
        return {
            "kb_persisted": True,
            "kb_deduplicated": True,
            "kb_chunks_ingested": 0,
            "content_hash": content_hash,
        }

    from app.api.knowledge import _ingest_chunks_from_source
    from app.knowledge.chunker_v2 import chunk_by_tokens

    source_url = f"ocr://{filename}"
    raw_chunks = chunk_by_tokens(content, max_tokens=512, overlap_tokens=64) or [content]
    chunk_dicts: list[dict[str, Any]] = [
        {
            "content": chunk_text,
            "source_url": source_url,
            "source_type": "ocr",
            "source_doc_id": source_url,
            "page_number": None,
            "metadata": {
                "source_url": source_url,
                "source_type": "ocr",
                "ingestion_provenance": "ocr",
                "ocr_used": "true",
                "ocr_engine": engine_used,
                "doc_content_hash": content_hash,
                "content_hash": content_hash,
                "chunk_index": str(i),
            },
        }
        for i, chunk_text in enumerate(raw_chunks)
    ]
    ingested = await _ingest_chunks_from_source(
        store, chunk_dicts, collection_id, tenant_ctx, embedder
    )
    return {
        "kb_persisted": True,
        "kb_deduplicated": False,
        "kb_chunks_ingested": ingested,
        "content_hash": content_hash,
    }


@router.post(
    "/extract",
    response_model=OcrResponse,
    summary="Extract text and structured fields from a document",
)
async def extract_document(
    request: Request,
    file: UploadFile | None = File(None),
    persist_to_kb: bool = Form(default=False),
    collection_id: str = Form(default=""),
    filename: str = Form(default="document"),
) -> OcrResponse:
    """Extract text and structured fields from an image or PDF document.

    Accepts either:
    - JSON body with `image_base64` or `pdf_base64`
    - Multipart file upload

    WS-13: pass ``persist_to_kb`` + ``collection_id`` to also index the extracted
    text into the knowledge base with ``source_type=ocr`` provenance (deduped
    against the one store).
    """
    # Get provider from app state if available
    provider: Any = None
    with contextlib.suppress(Exception):
        provider = getattr(request.app.state, "provider", None)

    # WS-13: persist options may arrive via multipart Form (above) or the JSON body.
    persist_requested = persist_to_kb
    persist_collection = collection_id
    persist_filename = filename

    try:
        if file is not None:
            content = await file.read()
            mime = file.content_type or ""
            if not (mime.startswith("image/") or mime == "application/pdf"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported file type: {mime}. Only images and PDFs are accepted.",
                )
            persist_filename = file.filename or persist_filename
            if mime == "application/pdf":
                result = await _tool.execute(
                    pdf_base64=base64.b64encode(content).decode(),
                    provider=provider,
                )
            else:
                result = await _tool.execute(
                    image_base64=base64.b64encode(content).decode(),
                    provider=provider,
                )
        else:
            # Try to read JSON body directly (avoids multipart vs JSON conflict)
            body: OcrRequest | None = None
            try:
                raw = await request.json()
                body = OcrRequest(**raw)
            except Exception:
                body = None

            if body and (body.image_base64 or body.pdf_base64):
                persist_requested = persist_requested or body.persist_to_kb
                persist_collection = persist_collection or body.collection_id
                if body.filename:
                    persist_filename = body.filename
                result = await _tool.execute(
                    image_base64=body.image_base64,
                    pdf_base64=body.pdf_base64,
                    provider=provider,
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Provide either a file upload or"
                        " image_base64/pdf_base64 in the request body."
                    ),
                )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        _log.exception("OCR extraction failed")
        raise HTTPException(
            status_code=500,
            detail="OCR extraction failed.",
        ) from exc

    kb_info: dict[str, Any] = {}
    if persist_requested:
        kb_info = await _persist_ocr_to_kb(
            request,
            raw_text=result["raw_text"],
            collection_id=persist_collection,
            filename=persist_filename,
            engine_used=result["engine_used"],
        )

    _log.info(
        "ocr.extract",
        extra={
            "document_type": result["document_type"],
            "fields_extracted": list(result["fields"].keys()),
            "engine_used": result["engine_used"],
            "page_count": result["page_count"],
            "has_invalid_fields": any(
                not f.get("is_valid", True)
                for f in result["fields"].values()
                if isinstance(f, dict)
            ),
        },
    )

    return OcrResponse(
        raw_text=result["raw_text"],
        document_type=result["document_type"],
        fields={k: OcrFieldResult(**v) for k, v in result["fields"].items()},
        engine_used=result["engine_used"],
        overall_confidence=result["overall_confidence"],
        page_count=result["page_count"],
        kb_persisted=bool(kb_info.get("kb_persisted", False)),
        kb_deduplicated=bool(kb_info.get("kb_deduplicated", False)),
        kb_chunks_ingested=int(kb_info.get("kb_chunks_ingested", 0)),
        kb_collection_id=persist_collection if persist_requested else "",
    )


# ── Batch endpoint ────────────────────────────────────────────────────────────


class BatchOcrRequest(BaseModel):
    documents: list[OcrRequest] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="List of documents to process (max 10)",
    )


class BatchOcrResponse(BaseModel):
    results: list[OcrResponse | None]
    total: int
    succeeded: int
    failed: int


@router.post(
    "/batch",
    response_model=BatchOcrResponse,
    summary="Extract text from multiple documents concurrently",
)
async def extract_documents_batch(
    request: Request,
    body: BatchOcrRequest,
) -> BatchOcrResponse:
    """Process up to 10 documents concurrently."""
    provider: Any = None
    with contextlib.suppress(Exception):
        provider = getattr(request.app.state, "provider", None)

    async def _extract_one(doc: OcrRequest) -> OcrResponse | None:
        try:
            res = await _tool.execute(
                image_base64=doc.image_base64,
                pdf_base64=doc.pdf_base64,
                provider=provider,
            )
            return OcrResponse(
                raw_text=res["raw_text"],
                document_type=res["document_type"],
                fields={
                    k: OcrFieldResult(
                        value=v["value"],
                        confidence=v["confidence"],
                        is_valid=v.get("is_valid", True),
                        raw_value=v.get("raw_value"),
                    )
                    for k, v in res["fields"].items()
                },
                engine_used=res["engine_used"],
                overall_confidence=res["overall_confidence"],
                page_count=res["page_count"],
            )
        except Exception as exc:
            _log.warning("Batch OCR item failed: %s", exc)
            return None

    tasks = [_extract_one(doc) for doc in body.documents]
    results: list[OcrResponse | None] = list(await asyncio.gather(*tasks))

    succeeded = sum(1 for r in results if r is not None)
    return BatchOcrResponse(
        results=results,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )
