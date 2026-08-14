"""Document type classifier using keyword and regex scoring."""
from __future__ import annotations

import re

from app.ocr.models import DocumentType

_KEYWORDS: dict[DocumentType, list[str]] = {
    DocumentType.PAN_CARD: [
        "permanent account number",
        "income tax",
        "pan",
    ],
    DocumentType.AADHAAR: [
        "uidai",
        "unique identification",
        "aadhaar",
        "आधार",
    ],
    DocumentType.PASSPORT: [
        "republic of india",
        "passport",
        "nationality",
        "place of birth",
    ],
    DocumentType.DRIVING_LICENSE: [
        "driving licence",
        "dl no",
        "transport department",
        "motor vehicle",
    ],
    DocumentType.INVOICE: [
        "invoice",
        "bill to",
        "gst",
        "hsn",
        "total amount",
    ],
    DocumentType.BANK_STATEMENT: [
        "account statement",
        "opening balance",
        "ifsc",
        "closing balance",
    ],
    DocumentType.RECEIPT: [
        "receipt",
        "amount paid",
        "cash memo",
        "payment received",
    ],
    DocumentType.VOTER_ID: [
        "election commission",
        "voter id",
        "epic no",
        "electors photo",
    ],
    DocumentType.GSTIN_CERTIFICATE: [
        "goods and services tax",
        "gstin",
        "certificate of registration",
        "central tax",
        "state tax",
    ],
    DocumentType.BANK_CHEQUE: [
        "pay",
        "a/c payee",
        "cheque no",
        "micr",
        "drawee bank",
    ],
    DocumentType.SALARY_SLIP: [
        "salary slip",
        "payslip",
        "pay stub",
        "gross salary",
        "net pay",
        "basic salary",
        "hra",
        "provident fund",
    ],
    DocumentType.ADDRESS_PROOF: [
        "electricity bill",
        "water bill",
        "utility bill",
        "rent agreement",
        "property tax",
        "municipal corporation",
    ],
}

_REGEX_BOOST: dict[DocumentType, list[str]] = {
    DocumentType.PAN_CARD: [r"[A-Z]{5}\d{4}[A-Z]"],
    DocumentType.AADHAAR: [r"\d{4}\s\d{4}\s\d{4}"],
}

_MIN_SCORE = 2


class DocumentClassifier:
    """Classify a document type from extracted OCR text using keyword/regex scoring."""

    def classify(self, raw_text: str) -> DocumentType:
        """Return the most likely DocumentType for the given OCR text.

        Scoring:
        - +1 per keyword match (case-insensitive substring)
        - +2 per regex pattern match (format-validated)
        - Type with score >= _MIN_SCORE wins; ties broken by highest score
        - Falls back to GENERAL if no type meets threshold
        """
        lower = raw_text.lower()
        scores: dict[DocumentType, int] = {}

        for doc_type, keywords in _KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in lower)
            for pattern in _REGEX_BOOST.get(doc_type, []):
                if re.search(pattern, raw_text):
                    score += 2
            if score > 0:
                scores[doc_type] = score

        if not scores:
            return DocumentType.GENERAL

        best_type = max(scores, key=lambda t: scores[t])
        if scores[best_type] < _MIN_SCORE:
            return DocumentType.GENERAL

        return best_type
