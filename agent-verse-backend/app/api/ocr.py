"""OCR document extraction API."""
from __future__ import annotations

import base64
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
    file: UploadFile | None = File(None),
) -> OcrResponse:
    """Extract text and structured fields from an image or PDF document.

    Accepts either:
    - JSON body with `image_base64` or `pdf_base64`
    - Multipart file upload
    """
    # Get provider from app state if available
    provider: Any = None
    try:
        provider = getattr(request.app.state, "provider", None)
    except Exception:
        pass

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

    return OcrResponse(
        raw_text=result["raw_text"],
        document_type=result["document_type"],
        fields={k: OcrFieldResult(**v) for k, v in result["fields"].items()},
        engine_used=result["engine_used"],
        overall_confidence=result["overall_confidence"],
        page_count=result["page_count"],
    )
