"""OCR document extraction API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.observability.logging import get_logger
from app.ocr.engine import OcrEngine
from app.ocr.limits import OcrDocumentTooLargeError, ocr_limit_message, ocr_max_upload_bytes
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


def _ocr_max_bytes() -> int:
    return ocr_max_upload_bytes()


def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=ocr_limit_message(limit))


async def _read_upload_capped(file: UploadFile) -> bytes:
    """a10-F243-05: read an upload in chunks, refusing (413) past the OCR cap —
    ``await file.read()`` pulled any size into memory before validation."""
    limit = _ocr_max_bytes()
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


def _check_b64_size(*payloads: str) -> None:
    """413 when a base64 document decodes past the OCR cap (checked before the
    payload is decoded)."""
    limit = _ocr_max_bytes()
    for data in payloads:
        if data and (len(data) * 3) // 4 - data.count("=", -2) > limit:
            raise _too_large(limit)


class OcrFieldResult(BaseModel):
    value: str
    confidence: float
    is_valid: bool = True
    raw_value: str | None = None


def _provenance(res: dict[str, Any]) -> dict[str, Any]:
    """The per-page provenance / degradation fields of a tool result (OCR-FB-3)."""
    return {
        "page_engines": list(res.get("page_engines") or []),
        "vision_pages": int(res.get("vision_pages") or 0),
        "confidence_measured": bool(res.get("confidence_measured", True)),
        "degraded": bool(res.get("degraded", False)),
        "degradation_reason": res.get("degradation_reason"),
        "source_format": res.get("source_format"),
        "empty_pages": [int(n) for n in res.get("empty_pages") or []],
        "failed_pages": [int(n) for n in res.get("failed_pages") or []],
    }


def _raise_if_unread(res: dict[str, Any]) -> None:
    """Never a plain 200 with empty text for a document nothing was read from.

    * the input itself cannot be OCR'd (corrupt / truncated / encrypted PDF, a
      PDF with no pages, an undecodable image): **422** with the reason —
      resubmitting the same document can never succeed;
    * the engine failed on every page (no LLM provider, vision call failed,
      renderer failed or missing): **502** with the reason (a10-F243-03);
    * no page at all for any other reason: **502** (defensive).

    Partly-read documents answer 200 with ``degraded``, ``failed_pages`` and
    the reason.
    """
    reason = res.get("degradation_reason") or ""
    kind = res.get("failure_kind")
    if kind == "invalid_input":
        raise HTTPException(
            status_code=422, detail=f"The document cannot be OCR'd: {reason or 'unreadable input'}"
        )
    pages = int(res.get("page_count") or 0)
    failed = res.get("failed_pages") or []
    if kind == "engine_failed" or pages <= 0 or len(failed) >= pages:
        raise HTTPException(
            status_code=502,
            detail=(
                "OCR could not read the document: "
                f"{reason or 'no page of the document could be rendered'}"
            ),
        )


class OcrResponse(BaseModel):
    raw_text: str
    document_type: str
    fields: dict[str, OcrFieldResult]
    engine_used: str
    overall_confidence: float
    page_count: int
    # OCR-FB-3: per-page provenance. ``page_engines`` names the engine that read
    # each page ("tesseract" / "llm_vision"); ``vision_pages`` counts pages read by
    # LLM vision; ``confidence_measured`` is False when no page had a measured
    # confidence (every page came from vision; overall_confidence is then assumed).
    page_engines: list[str] = Field(default_factory=list)
    vision_pages: int = 0
    confidence_measured: bool = True
    # WS-6: set when the input could not be OCR'd, with the reason.
    degraded: bool = False
    degradation_reason: str | None = None
    source_format: str | None = None
    # a10-F243-03: 1-based pages with no text, and those the engine failed on.
    empty_pages: list[int] = Field(default_factory=list)
    failed_pages: list[int] = Field(default_factory=list)
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
    from app.api.knowledge import _collection_embedder_or_http

    # The collection's own embedder (app.state.embedder serves default-bound ones).
    embedder = await _collection_embedder_or_http(request, collection_id, tenant_ctx)

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
    # request= carries the tenant's guardrail engine into the Stage 6/6b
    # screening; without it only the default PII screener ran and the tenant's
    # own RAG_INGEST guardrails were skipped for OCR'd documents.
    ingested = await _ingest_chunks_from_source(
        store, chunk_dicts, collection_id, tenant_ctx, embedder, request=request
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

    Size: one document of up to ``OCR_MAX_UPLOAD_BYTES`` (default 25 MiB), raw
    upload or decoded base64; larger is a 413 naming the limit. Errors: 422 for
    a document that can never be OCR'd (corrupt, truncated, encrypted, no
    pages), 502 when the engine read no page, 200 + ``degraded`` /
    ``failed_pages`` when only some pages were read.
    """
    # The tenant's provider (BYOK first), resolved once for every page. This read
    # app.state.provider, which is never set, so the engine re-resolved a fresh
    # platform provider per page and the tenant's key was ignored.
    from app.api.llm_access import tenant_llm_provider
    from app.providers.guarded_completion import DecisionBudgetExceededError

    provider: Any = None
    _tenant = getattr(request.state, "tenant", None)
    if _tenant is not None:
        provider = await tenant_llm_provider(request, _tenant)

    # WS-13: persist options may arrive via multipart Form (above) or the JSON body.
    persist_requested = persist_to_kb
    persist_collection = collection_id
    persist_filename = filename

    try:
        if file is not None:
            mime = file.content_type or ""
            if not (mime.startswith("image/") or mime == "application/pdf"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported file type: {mime}. Only images and PDFs are accepted.",
                )
            content = await _read_upload_capped(file)
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
                _check_b64_size(body.image_base64, body.pdf_base64)
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
    except (HTTPException, DecisionBudgetExceededError):
        raise  # a budget refusal answers 429 via the app's handler
    except OcrDocumentTooLargeError as exc:
        raise _too_large(exc.limit) from exc
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

    _raise_if_unread(result)

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
        **_provenance(result),
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
    # a10-F243-01: why item i failed (None when it succeeded), aligned with
    # ``results``. A failed item used to be a bare None with no reason.
    errors: list[str | None] = Field(default_factory=list)
    # The HTTP status /ocr/extract would have answered for item i (None when it
    # succeeded): 413 over the size cap, 422 for input that can never be OCR'd
    # (corrupt / encrypted / empty document, bad base64), 502 when the engine
    # failed, 500 for an unexpected error.
    error_status: list[int | None] = Field(default_factory=list)
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
    """Process up to 10 documents concurrently.

    Size: each document may decode to at most ``OCR_MAX_UPLOAD_BYTES``, and the
    whole request body is bounded like one ``/ocr/extract`` base64 document
    (the documents together, plus envelope room —
    ``app/integrations/body_limit.py``); a larger body is a 413.

    Each document honours its own ``persist_to_kb`` / ``collection_id`` (indexed
    exactly like ``/ocr/extract``). A failed item is ``None`` in ``results`` with
    its reason in ``errors`` and the status ``/ocr/extract`` would have answered
    in ``error_status``; an item whose OCR succeeded but whose
    knowledge-base write failed keeps its result and carries the error.
    """
    from app.api.llm_access import tenant_llm_provider
    from app.providers.guarded_completion import DecisionBudgetExceededError

    provider: Any = None
    _tenant = getattr(request.state, "tenant", None)
    if _tenant is not None:
        provider = await tenant_llm_provider(request, _tenant)

    async def _extract_one(
        doc: OcrRequest,
    ) -> tuple[OcrResponse | None, str | None, int | None]:
        try:
            _check_b64_size(doc.image_base64, doc.pdf_base64)
            res = await _tool.execute(
                image_base64=doc.image_base64,
                pdf_base64=doc.pdf_base64,
                provider=provider,
            )
            _raise_if_unread(res)
            item = OcrResponse(
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
                **_provenance(res),
            )
        except DecisionBudgetExceededError:
            raise  # the whole batch answers 429, not N silent item failures
        except HTTPException as exc:
            return None, str(exc.detail), exc.status_code
        except OcrDocumentTooLargeError as exc:
            return None, str(exc), 413
        except ValueError as exc:
            return None, str(exc), 422
        except Exception as exc:
            _log.warning("Batch OCR item failed: %s", exc)
            return None, "OCR extraction failed.", 500

        # a10-F243-02: per-document persist_to_kb / collection_id were ignored.
        if not doc.persist_to_kb:
            return item, None, None
        try:
            kb_info = await _persist_ocr_to_kb(
                request,
                raw_text=item.raw_text,
                collection_id=doc.collection_id,
                filename=doc.filename,
                engine_used=item.engine_used,
            )
        except HTTPException as exc:
            return item, f"knowledge-base persist failed: {exc.detail}", exc.status_code
        except Exception as exc:
            _log.warning("Batch OCR item KB persist failed: %s", exc)
            return item, "knowledge-base persist failed.", 500
        item.kb_persisted = bool(kb_info.get("kb_persisted", False))
        item.kb_deduplicated = bool(kb_info.get("kb_deduplicated", False))
        item.kb_chunks_ingested = int(kb_info.get("kb_chunks_ingested", 0))
        item.kb_collection_id = doc.collection_id
        return item, None, None

    outcomes = await asyncio.gather(*(_extract_one(doc) for doc in body.documents))
    results = [item for item, _, _ in outcomes]
    errors = [err for _, err, _ in outcomes]
    succeeded = sum(1 for err in errors if err is None)
    return BatchOcrResponse(
        results=results,
        errors=errors,
        error_status=[status for _, _, status in outcomes],
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )
