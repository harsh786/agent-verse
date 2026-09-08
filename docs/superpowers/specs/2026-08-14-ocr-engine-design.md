# OCR Engine Design Spec
**Date:** 2026-08-14  
**Status:** Approved  
**Author:** Brainstorming session

---

## Problem Statement

AgentVerse has `TaskType.OCR` defined in `app/ai_router/models.py` and routed in `app/ai_router/router.py`, but no backing implementation exists. The `app/multimodal/pipeline.py` handles image description via LLM vision but does **not** perform structured field extraction from documents. This means KYC flows, invoice processing, merchant onboarding, and any document-intensive workflow cannot extract structured data from uploaded files.

---

## Goals

1. Implement a real OCR engine that extracts text from images and PDFs.
2. Classify the document type automatically using keyword/regex heuristics (no LLM call required for classification).
3. Return both raw text and best-effort structured fields per document type.
4. Work offline/free-first via Tesseract; fall back to LLM vision when Tesseract is unavailable or confidence is low.
5. Wire into the existing `ai_router` OCR task type and expose as an agent-callable tool.

---

## Non-Goals

- No cloud OCR APIs (AWS Textract, Google Document AI) in this iteration.
- No bounding-box / layout-aware extraction (tables).
- No fine-tuned models or training pipelines.
- No multi-language Tesseract models beyond `eng` by default.

---

## Architecture: Approach 2 — Layered `app/ocr/` Package

### Package Structure

```
agent-verse-backend/app/ocr/
  __init__.py
  models.py            # OcrResult, DocumentType, ExtractedField dataclasses
  engine.py            # OcrEngine: Tesseract primary → LLM vision fallback
  classifier.py        # DocumentClassifier: keyword/regex → DocumentType
  extractors/
    __init__.py
    base.py            # OcrExtractor Protocol
    id_docs.py         # PAN, Aadhaar, Passport, Driving License
    financial.py       # Invoice, bank statement, receipt
    general.py         # General fallback: raw text, no structured fields

agent-verse-backend/app/tools/ocr_tool.py   # tool name: "extract_document"
agent-verse-backend/app/api/ocr.py          # POST /ocr/extract
```

### Data Models (`app/ocr/models.py`)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

class DocumentType(str, Enum):
    PAN_CARD         = "pan_card"
    AADHAAR          = "aadhaar"
    PASSPORT         = "passport"
    DRIVING_LICENSE  = "driving_license"
    INVOICE          = "invoice"
    BANK_STATEMENT   = "bank_statement"
    RECEIPT          = "receipt"
    GENERAL          = "general"

@dataclass
class ExtractedField:
    name: str
    value: str
    confidence: float  # 0.0–1.0

@dataclass
class OcrResult:
    raw_text: str
    document_type: DocumentType
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    engine_used: Literal["tesseract", "llm_vision"] = "tesseract"
    overall_confidence: float = 0.0
    page_count: int = 1
```

### OCR Engine (`app/ocr/engine.py`)

Decision logic:

```
input (image_bytes | pdf_bytes)
  │
  ├─ Try pytesseract (optional dep)
  │    ├─ confidence >= 0.6  →  return (text, confidence, "tesseract")
  │    └─ confidence < 0.6   →  fall through to LLM
  │
  └─ ImportError or low confidence
       └─ LLM vision via app.providers (existing base)
            └─ return (text, 0.85, "llm_vision")
```

Key constants:
- `CONFIDENCE_THRESHOLD = 0.6`
- PDF rendering: `pdf2image` converts each page to a Pillow image, then OCR per page.

### Document Classifier (`app/ocr/classifier.py`)

Pure text pattern matching — zero LLM cost:

| Type | Keyword patterns | Regex patterns |
|------|-----------------|----------------|
| `pan_card` | "permanent account number", "income tax dept" | `/[A-Z]{5}\d{4}[A-Z]/` |
| `aadhaar` | "uidai", "unique identification authority", "aadhaar" | `/\d{4}\s\d{4}\s\d{4}/` |
| `passport` | "republic of india", "passport no", "nationality", "place of birth" | `/[A-Z]\d{7}/` |
| `driving_license` | "driving licence", "dl no", "transport dept" | `/[A-Z]{2}\d{2}\s\d{11}/` |
| `invoice` | "invoice", "bill to", "gst", "hsn", "total amount" | - |
| `bank_statement` | "account statement", "opening balance", "ifsc", "closing balance" | - |
| `receipt` | "receipt", "amount paid", "payment received", "cash memo" | - |

Falls through to `general` if no pattern matches above threshold (≥2 keyword hits).

### Extractors (`app/ocr/extractors/`)

Each extractor implements the `OcrExtractor` Protocol:

```python
class OcrExtractor(Protocol):
    def extract(self, raw_text: str) -> dict[str, ExtractedField]: ...
```

Field extraction uses regex on `raw_text` from OCR; confidence is set to `0.5` as a base (regex match) or `0.9` for format-validated fields.

**`id_docs.py`** extracts:
- PAN: `pan_number`, `name`, `date_of_birth`, `father_name`
- Aadhaar: `aadhaar_number`, `name`, `date_of_birth`, `address`
- Passport: `passport_number`, `surname`, `given_names`, `nationality`, `dob`, `expiry_date`
- DL: `dl_number`, `name`, `dob`, `valid_to`, `vehicle_classes`

**`financial.py`** extracts:
- Invoice: `invoice_number`, `invoice_date`, `vendor_name`, `total_amount`, `gst_number`
- Bank statement: `account_number`, `account_holder`, `ifsc_code`, `opening_balance`, `closing_balance`
- Receipt: `receipt_number`, `date`, `amount`, `payment_mode`

**`general.py`**: returns empty dict (raw text is the value).

### Tool Integration (`app/tools/ocr_tool.py`)

```python
class OcrDocumentTool:
    name = "extract_document"
    description = (
        "Extract text and structured fields from any document image or PDF. "
        "Accepts file_path, image_base64, or pdf_base64. Returns raw_text, "
        "document_type, and field key-value pairs."
    )

    async def execute(
        self,
        *,
        file_path: str = "",
        image_base64: str = "",
        pdf_base64: str = "",
    ) -> dict: ...
```

### REST API (`app/api/ocr.py`)

```
POST /ocr/extract
Content-Type: multipart/form-data  (file upload)
  OR
Content-Type: application/json     {"image_base64": "...", "filename": "pan.jpg"}
  OR
                                   {"pdf_base64": "...", "filename": "invoice.pdf"}

Response 200:
{
  "raw_text": "PERMANENT ACCOUNT NUMBER...",
  "document_type": "pan_card",
  "fields": {
    "pan_number": {"value": "ABCDE1234F", "confidence": 0.97},
    "name": {"value": "HARSH KUMAR", "confidence": 0.91}
  },
  "engine_used": "tesseract",
  "overall_confidence": 0.92,
  "page_count": 1
}
```

Auth: requires `X-API-Key` tenant header (existing `TenantMiddleware`).

---

## Dependencies

| Package | Type | Notes |
|---------|------|-------|
| `pytesseract` | Optional | Wraps Tesseract binary; graceful ImportError fallback |
| `Pillow` | Optional (likely already installed) | Image processing |
| `pdf2image` | Optional | Requires `poppler` system package in Docker |
| `app.providers` (existing) | Required | LLM vision fallback, no new dep |

`pyproject.toml` optional extras group:
```toml
[project.optional-dependencies]
ocr = ["pytesseract>=0.3.13", "Pillow>=10.0.0", "pdf2image>=1.17.0"]
```

---

## Integration Points

| File | Change |
|------|--------|
| `app/ai_router/router.py` | Wire `TaskType.OCR` → `OcrEngine.extract()` |
| `app/tools/__init__.py` | Register `OcrDocumentTool` |
| `app/main.py` | Include `app/api/ocr.py` router |
| `pyproject.toml` | Add `ocr` optional extras |
| `agent-verse-backend/Dockerfile` | `apt-get install -y tesseract-ocr poppler-utils` |

---

## Test Strategy

- **Unit tests** (`tests/ocr/`):
  - `test_classifier.py`: fixture raw texts → assert correct `DocumentType`
  - `test_extractors.py`: fixture OCR outputs → assert field extraction
  - `test_engine.py`: mock `pytesseract`; test fallback when confidence < 0.6
  - `test_ocr_tool.py`: mock engine; assert tool output shape

- **API tests** (`tests/api/test_ocr.py`):
  - POST with base64 PNG → assert 200 + `document_type` + `fields`
  - POST missing input → assert 422

- **No integration tests** (Tesseract requires system binary; unit tests mock it).

---

## Security Considerations

- File uploads: validate `Content-Type`, reject non-image/non-PDF MIME types.
- Base64 input: enforce max size (10MB decoded).
- Never log raw document content (may contain PII — Aadhaar numbers, PAN, etc.).
- OCR output is not stored; callers must store results themselves.

---

## Open Questions / Assumptions

1. **Assumption**: `pytesseract` + Tesseract binary will be available in the Docker image. If not, the engine silently falls back to LLM vision with no error.
2. **Assumption**: LLM vision fallback uses whatever provider is configured for the tenant (falls back to `FakeProvider` in tests).
3. **Out of scope**: Storing OCR results in the database or associating them with goals/sessions.
