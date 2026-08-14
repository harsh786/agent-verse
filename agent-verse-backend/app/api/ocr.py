"""OCR document extraction API."""
from __future__ import annotations

import asyncio
import base64
import contextlib
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
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


@router.post(
    "/extract",
    response_model=OcrResponse,
    summary="Extract text and structured fields from a document",
)
async def extract_document(
    request: Request,
    file: UploadFile | None = File(None),  # noqa: B008
) -> OcrResponse:
    """Extract text and structured fields from an image or PDF document.

    Accepts either:
    - JSON body with `image_base64` or `pdf_base64`
    - Multipart file upload
    """
    # Get provider from app state if available
    provider: Any = None
    with contextlib.suppress(Exception):
        provider = getattr(request.app.state, "provider", None)

    try:
        if file is not None:
            content = await file.read()
            mime = file.content_type or ""
            if not (mime.startswith("image/") or mime == "application/pdf"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported file type: {mime}. Only images and PDFs are accepted.",
                )
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
