"""Redactor — removes sensitive entities from text."""
from __future__ import annotations
import re
from app.data_classification.schema import DataClass

_REDACT_PATTERNS: dict[DataClass, list[tuple[re.Pattern[str], str]]] = {
    DataClass.PII: [
        (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[REDACTED-PII]"),
        (re.compile(r"\b(\+?1[\-.\s]?)?\(?\d{3}\)?[\-.\s]\d{3}[\-.\s]\d{4}\b"), "[REDACTED-PHONE]"),
    ],
    DataClass.PCI: [
        (re.compile(r"\b4[0-9]{3}[\-\s][0-9]{4}[\-\s][0-9]{4}[\-\s][0-9]{4}\b"), "[REDACTED-CARD]"),
        (re.compile(r"\b5[1-5][0-9]{2}[\-\s][0-9]{4}[\-\s][0-9]{4}[\-\s][0-9]{4}\b"), "[REDACTED-CARD]"),
        (re.compile(r"\b4[0-9]{12}(?:[0-9]{3})?\b"), "[REDACTED-CARD]"),
        (re.compile(r"\b5[1-5][0-9]{14}\b"), "[REDACTED-CARD]"),
    ],
    DataClass.SECRET: [
        (re.compile(r"(?i)(password|passwd|secret|api[_\-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}"),
         r"\1=[REDACTED-SECRET]"),
        (re.compile(r"(sk-proj-|AKIA|ghp_|glpat-)[A-Za-z0-9_\-]{8,}"), "[REDACTED-KEY]"),
    ],
    DataClass.PHI: [(re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]")],
}


class Redactor:
    def redact(self, text: str, classes: list[DataClass] | None = None) -> str:
        if classes is None:
            classes = list(_REDACT_PATTERNS.keys())
        result = text
        for cls in classes:
            for pattern, replacement in _REDACT_PATTERNS.get(cls, []):
                result = pattern.sub(replacement, result)
        return result
