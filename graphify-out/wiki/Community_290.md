# Community 290

> 23 nodes · cohesion 0.21

## Key Concepts

- **IdDocExtractor** (15 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **id_docs.py** (13 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **normalize_date (multi-format ISO 8601)** (13 connections) — `agent-verse-backend/app/ocr/validators.py`
- **.extract()** (8 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **._extract_aadhaar()** (8 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **validators.py** (8 connections) — `agent-verse-backend/app/ocr/validators.py`
- **_first_match()** (7 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **._extract_pan()** (7 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **._extract_dl()** (6 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **._extract_voter_id()** (6 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **_label_value()** (6 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **._extract_cheque()** (5 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **._extract_passport()** (5 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **mask_aadhaar()** (4 connections) — `agent-verse-backend/app/ocr/validators.py`
- **validate_aadhaar()** (4 connections) — `agent-verse-backend/app/ocr/validators.py`
- **validate_pan()** (4 connections) — `agent-verse-backend/app/ocr/validators.py`
- **ID document field extractor (PAN, Aadhaar, Passport, Driving License).** (1 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **OCR field validators: format checking, checksum validation, date normalization.** (1 connections) — `agent-verse-backend/app/ocr/validators.py`
- **Normalize any date string to ISO 8601 (YYYY-MM-DD). Returns None if unparseable.** (1 connections) — `agent-verse-backend/app/ocr/validators.py`
- **Validate PAN card number format: 5 uppercase letters, 4 digits, 1 uppercase…** (1 connections) — `agent-verse-backend/app/ocr/validators.py`
- **Validate 12-digit Aadhaar number using Verhoeff checksum.** (1 connections) — `agent-verse-backend/app/ocr/validators.py`
- **Mask first 8 digits of Aadhaar: XXXX XXXX 9012.** (1 connections) — `agent-verse-backend/app/ocr/validators.py`
- **Verhoeff checksum Aadhaar validation** (1 connections) — `agent-verse-backend/app/ocr/validators.py`

## Relationships

- [Community 407](Community_407.md) (14 shared connections)
- [Community 380](Community_380.md) (6 shared connections)
- [Community 188](Community_188.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/ocr/extractors/id_docs.py`
- `agent-verse-backend/app/ocr/validators.py`

## Audit Trail

- EXTRACTED: 73 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*