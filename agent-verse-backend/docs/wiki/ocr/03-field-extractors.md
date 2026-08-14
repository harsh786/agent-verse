---
title: "OCR — Field Extractors"
description: "All 12 structured field extractors in AgentVerse OCR: regex patterns, field definitions, and validation integration for every supported document type."
outline: deep
---

# Field Extractors

Once the document type is known, the engine routes to a type-specific extractor via `get_extractor()` (`app/ocr/extractors/__init__.py`). Extractors use regex patterns over the raw OCR text to locate specific fields, then pass each value through the validators.

---

## Factory: `get_extractor()`

```python
# app/ocr/extractors/__init__.py

_ID_TYPES = {
    DocumentType.PAN_CARD, DocumentType.AADHAAR, DocumentType.PASSPORT,
    DocumentType.DRIVING_LICENSE, DocumentType.VOTER_ID, DocumentType.BANK_CHEQUE,
}

_FINANCIAL_TYPES = {
    DocumentType.INVOICE, DocumentType.BANK_STATEMENT, DocumentType.RECEIPT,
    DocumentType.GSTIN_CERTIFICATE, DocumentType.SALARY_SLIP, DocumentType.ADDRESS_PROOF,
}

def get_extractor(document_type: DocumentType, provider: Any = None) -> Any:
    if document_type in _ID_TYPES:
        return IdDocExtractor(document_type)
    if document_type in _FINANCIAL_TYPES:
        return FinancialExtractor(document_type)
    if document_type == DocumentType.GENERAL and provider is not None:
        return LlmStructuredExtractor(provider)   # global any-document
    return GeneralExtractor()   # raw text, no fields
```

The `provider` parameter is passed from `OcrEngine.extract()`. When an LLM provider is configured and the document is `GENERAL`, structured extraction is still possible — just via LLM instead of regex.

---

## Identity Document Extractor (`app/ocr/extractors/id_docs.py`)

`IdDocExtractor` handles all 6 identity document types using type-dispatched regex extraction.

### PAN Card Fields

| Field | Regex | Validation |
|---|---|---|
| `pan_number` | `r'[A-Z]{5}[0-9]{4}[A-Z]'` | `validate_pan()` — format check |
| `name` | Lines following "name" keyword | None (free text) |
| `dob` | `r'\d{1,2}[/\-]\d{1,2}[/\-]\d{4}'` | `normalize_date()` → ISO 8601 |
| `father_name` | Lines following "father's name" | None (free text) |

### Aadhaar Fields

| Field | Regex | Validation |
|---|---|---|
| `aadhaar_number` | `r'\d{4}\s\d{4}\s\d{4}'` | `validate_aadhaar()` (Verhoeff) + `mask_aadhaar()` |
| `name` | Lines before/after "aadhaar" | None |
| `dob` | `r'\d{1,2}[/\-]\d{1,2}[/\-]\d{4}'` | `normalize_date()` |
| `address` | Multi-line capture after "address" | None |

**Masking:** After validation, `masked_value = mask_aadhaar(number)` is set on the `ExtractedField`. The API always returns `masked_value` as the primary `value`.

### Passport Fields

| Field | Regex | Notes |
|---|---|---|
| `passport_number` | `r'[A-Z][0-9]{7}'` | Indian passport format |
| `name` | MRZ line parsing | Surname `<<` Given name pattern |
| `nationality` | Keyword "nationality" followed by value | Free text |
| `dob` | `r'\d{2}/\d{2}/\d{4}'` | Normalized to ISO 8601 |
| `expiry_date` | `r'\d{2}/\d{2}/\d{4}'` after "date of expiry" | Normalized to ISO 8601 |

### Driving License Fields

| Field | Regex | Notes |
|---|---|---|
| `dl_number` | `r'[A-Z]{2}\d{2}\s?\d{11}'` | State code + DL number |
| `name` | After "name" keyword | Free text |
| `dob` | Date pattern near "dob" | ISO 8601 normalized |
| `expiry_date` | Date pattern near "validity" or "expires" | ISO 8601 normalized |

### Voter ID (EPIC) Fields

| Field | Regex | Notes |
|---|---|---|
| `epic_number` | `r'[A-Z]{3}[0-9]{7}'` | Electoral Photo Identity Card number |
| `name` | After "name of elector" | Free text |
| `father_name` | After "father's/husband's name" | Free text |
| `dob` | Date pattern | ISO 8601 normalized |

### Bank Cheque Fields

| Field | Regex | Notes |
|---|---|---|
| `cheque_number` | `r'\d{6}'` | 6-digit cheque number |
| `payee_name` | After "pay" keyword | Free text |
| `amount` | `r'₹[\d,]+(\.\d{2})?'` or `r'\d+[,\d]*\.\d{2}'` | Numeric extraction |
| `micr_code` | `r'\d{9}'` at bottom of cheque | 9-digit MICR code |

---

## Financial Document Extractor (`app/ocr/extractors/financial.py`)

`FinancialExtractor` handles all 6 financial types.

### Invoice Fields

| Field | Regex | Notes |
|---|---|---|
| `invoice_number` | `r'(?:invoice|bill)\s*(?:no|number|#)[:\s]*([A-Z0-9\-/]+)'` | Case-insensitive |
| `invoice_date` | Date pattern near "invoice date" | ISO 8601 normalized |
| `vendor_name` | First non-empty line or "from" section | Free text |
| `gstin` | Full GSTIN regex | `validate_gstin()` — checksum |
| `total_amount` | `r'(?:total|grand total)[:\s]*₹?([\d,]+\.?\d{0,2})'` | Numeric |

### Bank Statement Fields

| Field | Regex | Notes |
|---|---|---|
| `account_number` | `r'(?:a/c|account)\s*(?:no|number)[:\s]*(\d+)'` | Numeric |
| `account_holder` | After "name" or "account holder" | Free text |
| `ifsc_code` | `r'[A-Z]{4}0[A-Z0-9]{6}'` | 11-char IFSC |
| `opening_balance` | `r'opening\s*balance[:\s]*₹?([\d,]+\.?\d{0,2})'` | Numeric |
| `closing_balance` | `r'closing\s*balance[:\s]*₹?([\d,]+\.?\d{0,2})'` | Numeric |

### Receipt Fields

| Field | Regex | Notes |
|---|---|---|
| `receipt_number` | `r'receipt\s*(?:no|#)[:\s]*([A-Z0-9\-]+)'` | Alphanumeric |
| `date` | Date pattern | ISO 8601 normalized |
| `merchant_name` | First prominent line | Free text |
| `total_amount` | Total/amount due pattern | Numeric |

### GSTIN Certificate Fields

| Field | Regex | Validation |
|---|---|---|
| `gstin` | Full GSTIN pattern | `validate_gstin()` — Luhn-style checksum |
| `legal_name` | After "legal name of business" | Free text |
| `registration_date` | Date near "effective date of registration" | ISO 8601 |
| `status` | Near "status" (Active/Inactive/Suspended) | Free text |

### Salary Slip Fields

| Field | Regex | Notes |
|---|---|---|
| `employee_name` | After "employee name" | Free text |
| `employee_id` | `r'(?:emp|employee)\s*(?:id|code)[:\s]*([A-Z0-9]+)'` | Alphanumeric |
| `pay_period` | Month+year pattern | Free text |
| `gross_salary` | `r'gross\s*(?:salary|pay)[:\s]*₹?([\d,]+)'` | Numeric |
| `net_pay` | `r'net\s*(?:pay|salary|take home)[:\s]*₹?([\d,]+)'` | Numeric |

### Address Proof Fields

| Field | Regex | Notes |
|---|---|---|
| `name` | Prominent name line | Free text |
| `address` | Multi-line address block | Free text, multi-line |
| `pincode` | `r'\b[1-9]\d{5}\b'` | 6-digit Indian pincode |
| `document_ref` | Bill number / reference pattern | Free text |

---

## Extractor Return Format

Every extractor returns `dict[str, ExtractedField]`:

```python
{
    "pan_number": ExtractedField(
        name="pan_number",
        value="ABCDE1234F",
        confidence=0.95,
        is_valid=True,
        masked_value=None,  # only set for Aadhaar
    ),
    "name": ExtractedField(
        name="name",
        value="RAHUL SHARMA",
        confidence=0.82,
        is_valid=True,
    ),
}
```

A field not found in the text is **not included** in the dict (no null/empty fields). This keeps the response clean and avoids agents reasoning about empty values.

---

## Real-World Example: Multi-Page Bank Statement

**Situation:** A 6-page bank statement PDF needs account details and balance extraction.

```
Page 1: "STATE BANK OF INDIA\nSTATEMENT OF ACCOUNT\n
         Account No: 1234567890\nIFSC: SBIN0001234\n
         Opening Balance: ₹45,200.00"

Page 6: "Closing Balance: ₹62,450.00"

Combined text → FinancialExtractor(BANK_STATEMENT):
  account_number = "1234567890" (conf: 0.93)
  ifsc_code = "SBIN0001234" (conf: 0.95)
  opening_balance = "45200.00" (conf: 0.90)
  closing_balance = "62450.00" (conf: 0.88)
```

Cross-page extraction works because the full text from all pages is joined before extraction.
