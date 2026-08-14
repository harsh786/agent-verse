# OCR Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a real OCR engine for AgentVerse that extracts text + structured fields from any document image or PDF, using Tesseract locally with LLM vision fallback.

**Architecture:** New `app/ocr/` package with layered engine → classifier → extractor pipeline. Thin `app/tools/ocr_tool.py` wraps it as an agent-callable tool. `app/api/ocr.py` exposes a REST endpoint. Wires into the existing `ai_router` OCR task type.

**Tech Stack:** Python 3.12, pytesseract (optional), Pillow, pdf2image (optional), FastAPI, existing `app.providers` for LLM vision fallback.

**Spec:** `docs/superpowers/specs/2026-08-14-ocr-engine-design.md`

---

## File Map

### New files
| File | Responsibility |
|------|---------------|
| `app/ocr/__init__.py` | Package init, re-exports |
| `app/ocr/models.py` | `OcrResult`, `DocumentType`, `ExtractedField` |
| `app/ocr/engine.py` | `OcrEngine`: Tesseract → LLM vision fallback |
| `app/ocr/classifier.py` | `DocumentClassifier`: keyword/regex → `DocumentType` |
| `app/ocr/extractors/__init__.py` | Extractor registry |
| `app/ocr/extractors/base.py` | `OcrExtractor` Protocol |
| `app/ocr/extractors/id_docs.py` | PAN, Aadhaar, Passport, Driving License |
| `app/ocr/extractors/financial.py` | Invoice, bank statement, receipt |
| `app/ocr/extractors/general.py` | Fallback extractor (no structured fields) |
| `app/tools/ocr_tool.py` | `OcrDocumentTool` (tool name: `extract_document`) |
| `app/api/ocr.py` | `POST /ocr/extract` FastAPI router |
| `tests/ocr/__init__.py` | Test package |
| `tests/ocr/test_models.py` | Model dataclass tests |
| `tests/ocr/test_classifier.py` | Classifier unit tests |
| `tests/ocr/test_extractors.py` | Extractor unit tests |
| `tests/ocr/test_engine.py` | Engine unit tests (mocked pytesseract) |
| `tests/ocr/test_ocr_tool.py` | Tool unit tests |
| `tests/api/test_ocr_api.py` | API integration tests |

### Modified files
| File | Change |
|------|--------|
| `app/ai_router/router.py` | Wire `TaskType.OCR` → `OcrEngine` |
| `app/tools/__init__.py` | Register `OcrDocumentTool` |
| `app/main.py` | Include OCR router |
| `pyproject.toml` | Add `ocr` optional extras |
| `agent-verse-backend/Dockerfile` | Add `tesseract-ocr poppler-utils` apt packages |

---

## Phase 1 — Data Models

### Task 1.1 — Create `app/ocr/models.py`
- [ ] Create `app/ocr/__init__.py` (empty)
- [ ] Create `app/ocr/models.py` with:
  ```python
  from __future__ import annotations
  from dataclasses import dataclass, field
  from enum import Enum
  from typing import Literal

  class DocumentType(str, Enum):
      PAN_CARD = "pan_card"
      AADHAAR = "aadhaar"
      PASSPORT = "passport"
      DRIVING_LICENSE = "driving_license"
      INVOICE = "invoice"
      BANK_STATEMENT = "bank_statement"
      RECEIPT = "receipt"
      GENERAL = "general"

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
- [ ] Create `tests/ocr/__init__.py` (empty)
- [ ] Create `tests/ocr/test_models.py` — test enum values, dataclass defaults
- [ ] Run: `cd agent-verse-backend && uv run pytest tests/ocr/test_models.py -q --no-cov`
- [ ] Verify: all tests pass
- [ ] Commit: `feat(ocr): add OcrResult, DocumentType, ExtractedField models`

---

## Phase 2 — Document Classifier

### Task 2.1 — Implement `app/ocr/classifier.py`
- [ ] Create `app/ocr/classifier.py` with `DocumentClassifier` class
- [ ] Implement `classify(raw_text: str) -> DocumentType` using keyword scoring:
  - Score each `DocumentType` against its keyword list
  - Type with score ≥ 2 wins; ties broken by score descending
  - Fallback: `DocumentType.GENERAL`
- [ ] Keyword map (minimum viable, add more patterns per type):
  ```python
  _KEYWORDS: dict[DocumentType, list[str]] = {
      DocumentType.PAN_CARD: [
          "permanent account number", "income tax", "pan"
      ],
      DocumentType.AADHAAR: [
          "uidai", "unique identification", "aadhaar", "आधार"
      ],
      DocumentType.PASSPORT: [
          "republic of india", "passport", "nationality", "place of birth"
      ],
      DocumentType.DRIVING_LICENSE: [
          "driving licence", "dl no", "transport department", "motor vehicle"
      ],
      DocumentType.INVOICE: [
          "invoice", "bill to", "gst", "hsn", "total amount"
      ],
      DocumentType.BANK_STATEMENT: [
          "account statement", "opening balance", "ifsc", "closing balance"
      ],
      DocumentType.RECEIPT: [
          "receipt", "amount paid", "cash memo", "payment received"
      ],
  }
  ```
- [ ] Implement regex secondary check for PAN (`[A-Z]{5}\d{4}[A-Z]`) and Aadhaar (`\d{4}\s\d{4}\s\d{4}`)

### Task 2.2 — Test classifier
- [ ] Create `tests/ocr/test_classifier.py`
- [ ] Write fixtures with sample raw text for each document type (use short strings, not real docs)
- [ ] Tests:
  - `test_classify_pan_card` — text containing "permanent account number ABCDE1234F"
  - `test_classify_aadhaar` — text containing "uidai 1234 5678 9012"
  - `test_classify_passport` — text containing "republic of india passport"
  - `test_classify_invoice` — text containing "invoice bill to gst"
  - `test_classify_general` — text containing "hello world nothing special"
- [ ] Run: `uv run pytest tests/ocr/test_classifier.py -q --no-cov`
- [ ] Verify: all pass
- [ ] Commit: `feat(ocr): add DocumentClassifier with keyword/regex scoring`

---

## Phase 3 — Extractors

### Task 3.1 — Base extractor protocol and general extractor
- [ ] Create `app/ocr/extractors/__init__.py` — import all extractors, export `get_extractor(doc_type)` factory
- [ ] Create `app/ocr/extractors/base.py`:
  ```python
  from typing import Protocol
  from app.ocr.models import ExtractedField

  class OcrExtractor(Protocol):
      def extract(self, raw_text: str) -> dict[str, ExtractedField]: ...
  ```
- [ ] Create `app/ocr/extractors/general.py` — returns `{}` (no fields for unknown docs)

### Task 3.2 — ID document extractor
- [ ] Create `app/ocr/extractors/id_docs.py` with `IdDocExtractor`
- [ ] Implement regex extraction for each ID doc type using `document_type` hint:
  ```
  PAN:
    pan_number     → r'[A-Z]{5}\d{4}[A-Z]'
    name           → line after "Name" label
    date_of_birth  → r'\d{2}/\d{2}/\d{4}'

  Aadhaar:
    aadhaar_number → r'\d{4}\s\d{4}\s\d{4}'
    name           → first non-number line
    date_of_birth  → r'DOB[:\s]+(\d{2}/\d{2}/\d{4})'

  Passport:
    passport_number → r'[A-Z]\d{7}'
    surname         → MRZ line 1 parsing or label extraction
    dob             → r'\d{2}/\d{2}/\d{4}'
    expiry_date     → second r'\d{2}/\d{2}/\d{4}' match

  Driving License:
    dl_number      → r'[A-Z]{2}\d{2}\s\d{11}' or r'DL-\S+'
    name           → after "Name:" label
    valid_to       → after "Valid Till:" label
  ```
- [ ] Confidence: 0.9 for format-validated regex match, 0.6 for label-based extraction, 0.0 if not found

### Task 3.3 — Financial document extractor
- [ ] Create `app/ocr/extractors/financial.py` with `FinancialExtractor`
- [ ] Implement extraction for invoice, bank statement, receipt:
  ```
  Invoice:
    invoice_number → r'Invoice\s*[#No.:]*\s*(\S+)'
    invoice_date   → r'\d{2}[/-]\d{2}[/-]\d{4}'
    total_amount   → r'Total[:\s]+(?:Rs\.?|INR)?\s*([\d,]+\.?\d*)'
    gst_number     → r'\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}[Z]{1}[A-Z\d]{1}'

  Bank Statement:
    account_number → r'A/c\s*[#No.:]*\s*(\d+)'
    ifsc_code      → r'IFSC[:\s]+([A-Z]{4}0[A-Z0-9]{6})'
    opening_balance → r'Opening\s+Balance[:\s]+([\d,]+\.?\d*)'
    closing_balance → r'Closing\s+Balance[:\s]+([\d,]+\.?\d*)'

  Receipt:
    receipt_number → r'Receipt\s*[#No.:]*\s*(\S+)'
    amount         → r'Amount[:\s]+(?:Rs\.?|INR)?\s*([\d,]+\.?\d*)'
  ```

### Task 3.4 — Test extractors
- [ ] Create `tests/ocr/test_extractors.py`
- [ ] Fixture strings (short, realistic text snippets — not real PII):
  - PAN text: `"PERMANENT ACCOUNT NUMBER ABCDE1234F\nName: TEST USER\nDOB: 01/01/1990"`
  - Invoice text: `"Invoice No: INV-001\nTotal: Rs. 1,500.00\nGST: 29ABCDE1234F1Z5"`
  - Aadhaar text: `"UIDAI\n1234 5678 9012\nTest Name\nDOB: 01/01/1990"`
- [ ] Assertions: field `value` matches expected, `confidence` > 0
- [ ] Run: `uv run pytest tests/ocr/test_extractors.py -q --no-cov`
- [ ] Verify: all pass
- [ ] Commit: `feat(ocr): add id_docs and financial extractors with regex field extraction`

---

## Phase 4 — OCR Engine

### Task 4.1 — Implement `app/ocr/engine.py`
- [ ] Create `app/ocr/engine.py` with `OcrEngine` class
- [ ] Implement `async def extract(self, *, image_bytes=None, pdf_bytes=None, provider=None) -> OcrResult`:
  ```python
  async def extract(
      self,
      *,
      image_bytes: bytes | None = None,
      pdf_bytes: bytes | None = None,
      provider: Any = None,
  ) -> OcrResult:
      pages = self._to_images(image_bytes=image_bytes, pdf_bytes=pdf_bytes)
      raw_texts = []
      for page_img in pages:
          text, conf, engine = await self._ocr_page(page_img, provider=provider)
          raw_texts.append((text, conf, engine))
      raw_text = "\n\n".join(t for t, _, _ in raw_texts)
      overall_conf = sum(c for _, c, _ in raw_texts) / max(len(raw_texts), 1)
      engine_used = raw_texts[0][2] if raw_texts else "tesseract"
      doc_type = self._classifier.classify(raw_text)
      extractor = get_extractor(doc_type)
      fields = extractor.extract(raw_text)
      return OcrResult(
          raw_text=raw_text,
          document_type=doc_type,
          fields=fields,
          engine_used=engine_used,
          overall_confidence=overall_conf,
          page_count=len(pages),
      )
  ```
- [ ] Implement `_ocr_page(img, provider) -> tuple[str, float, str]`:
  - Try `import pytesseract; pytesseract.image_to_data(...)` to get confidence
  - If `ImportError` or `conf < CONFIDENCE_THRESHOLD` → call `_llm_vision_ocr(img, provider)`
- [ ] Implement `_to_images(image_bytes, pdf_bytes) -> list[PIL.Image]`:
  - image_bytes: `Image.open(io.BytesIO(image_bytes))`
  - pdf_bytes: try `from pdf2image import convert_from_bytes`; graceful fallback to empty list if not installed
- [ ] Implement `_llm_vision_ocr(img, provider) -> tuple[str, float, str]`:
  - Encode image as base64
  - Call provider with vision prompt: `"Extract all text from this image. Return only the text, no commentary."`
  - Return `(response.content, 0.85, "llm_vision")`

### Task 4.2 — Test engine
- [ ] Create `tests/ocr/test_engine.py`
- [ ] Mock `pytesseract.image_to_data` to return high-confidence result
- [ ] Test: Tesseract path taken when confidence >= 0.6
- [ ] Mock `pytesseract.image_to_data` to return low-confidence result
- [ ] Test: LLM vision fallback triggered when confidence < 0.6
- [ ] Test: `ImportError` on pytesseract → LLM vision fallback triggered
- [ ] Test: PDF input renders pages (mock pdf2image)
- [ ] Run: `uv run pytest tests/ocr/test_engine.py -q --no-cov`
- [ ] Verify: all pass
- [ ] Commit: `feat(ocr): implement OcrEngine with Tesseract→LLM vision fallback`

---

## Phase 5 — Tool Integration

### Task 5.1 — Create `app/tools/ocr_tool.py`
- [ ] Create `app/tools/ocr_tool.py`:
  ```python
  class OcrDocumentTool:
      name = "extract_document"
      description = (
          "Extract text and structured fields from any document image or PDF. "
          "Accepts file_path (local path), image_base64, or pdf_base64. "
          "Returns raw_text, document_type, and structured fields."
      )

      def __init__(self, ocr_engine: OcrEngine | None = None) -> None:
          self._engine = ocr_engine or OcrEngine()

      async def execute(
          self,
          *,
          file_path: str = "",
          image_base64: str = "",
          pdf_base64: str = "",
          provider: Any = None,
      ) -> dict:
          image_bytes, pdf_bytes = self._resolve_input(
              file_path=file_path,
              image_base64=image_base64,
              pdf_base64=pdf_base64,
          )
          result = await self._engine.extract(
              image_bytes=image_bytes,
              pdf_bytes=pdf_bytes,
              provider=provider,
          )
          return {
              "raw_text": result.raw_text,
              "document_type": result.document_type.value,
              "fields": {
                  k: {"value": v.value, "confidence": v.confidence}
                  for k, v in result.fields.items()
              },
              "engine_used": result.engine_used,
              "overall_confidence": result.overall_confidence,
              "page_count": result.page_count,
          }
  ```
- [ ] Implement `_resolve_input`: read file if `file_path`, decode base64 otherwise; raise `ValueError` if all empty
- [ ] Security: validate file_path is not outside allowed directories (use `Path.resolve()` check)
- [ ] Max size: reject decoded bytes > 10MB

### Task 5.2 — Register tool and wire ai_router
- [ ] Open `app/tools/__init__.py`, add `OcrDocumentTool` to exports
- [ ] Open `app/ai_router/router.py`, find the `TaskType.OCR` branch:
  ```python
  elif task_type == TaskType.OCR:
      if ModelCapability.OCR in m.capabilities
  ```
  Replace stub with call to `OcrDocumentTool` or note the router is for model selection (tool handles execution)
- [ ] Run existing tool tests: `uv run pytest tests/tools/ -q --no-cov`
- [ ] Verify: no regressions

### Task 5.3 — Test tool
- [ ] Create `tests/ocr/test_ocr_tool.py`
- [ ] Mock `OcrEngine.extract` to return a fixture `OcrResult`
- [ ] Test: `file_path` input reads file and passes bytes correctly
- [ ] Test: `image_base64` input decodes and passes correctly
- [ ] Test: all-empty input raises `ValueError`
- [ ] Test: file > 10MB raises `ValueError`
- [ ] Run: `uv run pytest tests/ocr/test_ocr_tool.py -q --no-cov`
- [ ] Verify: all pass
- [ ] Commit: `feat(ocr): add OcrDocumentTool with file/base64 input resolution`

---

## Phase 6 — REST API

### Task 6.1 — Create `app/api/ocr.py`
- [ ] Create `app/api/ocr.py` with FastAPI router:
  ```python
  router = APIRouter(prefix="/ocr", tags=["ocr"])

  class OcrRequest(BaseModel):
      image_base64: str = ""
      pdf_base64: str = ""
      filename: str = "document"

  @router.post("/extract", response_model=OcrResponse)
  async def extract_document(
      request: OcrRequest | None = None,
      file: UploadFile | None = File(None),
      tenant: Tenant = Depends(get_tenant),
  ) -> OcrResponse: ...
  ```
- [ ] Support both JSON body and multipart form-data upload
- [ ] Validate: at least one of `image_base64`, `pdf_base64`, or `file` must be provided
- [ ] Validate MIME type for `file` upload: only `image/*` and `application/pdf`
- [ ] Return `OcrResponse` Pydantic model matching the JSON shape from the spec

### Task 6.2 — Register router in `app/main.py`
- [ ] Open `app/main.py`, add `from app.api.ocr import router as ocr_router`
- [ ] Add `app.include_router(ocr_router)` alongside existing routers
- [ ] Run: `uv run python -c "from app.main import create_app; create_app()"`
- [ ] Verify: no import errors

### Task 6.3 — API tests
- [ ] Create `tests/api/test_ocr_api.py`
- [ ] Use `TestClient` (same pattern as other API tests in `tests/api/`)
- [ ] Mock `OcrEngine.extract` to return fixture result
- [ ] Test: POST JSON with `image_base64` → 200 with expected shape
- [ ] Test: POST multipart with `file` → 200 with expected shape
- [ ] Test: POST empty body → 422
- [ ] Test: POST with non-image MIME type → 422
- [ ] Run: `uv run pytest tests/api/test_ocr_api.py -q --no-cov`
- [ ] Verify: all pass
- [ ] Commit: `feat(ocr): add POST /ocr/extract REST endpoint`

---

## Phase 7 — Dependencies & Docker

### Task 7.1 — Update `pyproject.toml`
- [ ] Open `agent-verse-backend/pyproject.toml`
- [ ] Add optional extras group:
  ```toml
  [project.optional-dependencies]
  ocr = [
      "pytesseract>=0.3.13",
      "Pillow>=10.0.0",
      "pdf2image>=1.17.0",
  ]
  ```
- [ ] Verify: `uv sync --extra ocr` resolves without conflicts (run if Tesseract binary available)

### Task 7.2 — Update Dockerfile
- [ ] Open `agent-verse-backend/Dockerfile`
- [ ] Add system packages in the `apt-get install` layer:
  ```dockerfile
  RUN apt-get update && apt-get install -y \
      tesseract-ocr \
      poppler-utils \
      && rm -rf /var/lib/apt/lists/*
  ```
- [ ] Verify: Dockerfile syntax is valid (`docker build --dry-run` or visual check)

### Task 7.3 — Final test run
- [ ] Run full test suite: `cd agent-verse-backend && uv run pytest tests/ocr/ tests/api/test_ocr_api.py -q --no-cov`
- [ ] Verify: all new tests pass
- [ ] Run existing suite: `uv run pytest tests/ --ignore=tests/real_e2e --ignore=tests/integration -q --no-cov --tb=short 2>&1 | tail -10`
- [ ] Verify: no regressions (same pass count as before)
- [ ] Commit: `feat(ocr): add optional pytesseract/pdf2image deps and Dockerfile packages`

---

## Phase 8 — Final Integration Commit

- [ ] Review all modified files one last time
- [ ] Run: `uv run ruff check app/ocr/ app/tools/ocr_tool.py app/api/ocr.py`
- [ ] Fix any lint issues
- [ ] Run: `uv run mypy app/ocr/ app/tools/ocr_tool.py app/api/ocr.py`
- [ ] Fix any type errors
- [ ] Commit: `feat(ocr): complete OCR engine integration — Tesseract + LLM vision fallback`

---

## Summary of Deliverables

| Deliverable | Location |
|-------------|----------|
| OCR package | `app/ocr/` (8 files) |
| OCR tool | `app/tools/ocr_tool.py` |
| REST API | `app/api/ocr.py` |
| Tests | `tests/ocr/` (6 files) + `tests/api/test_ocr_api.py` |
| Spec | `docs/superpowers/specs/2026-08-14-ocr-engine-design.md` |

Estimated test count: ~35 new tests across all phases.
