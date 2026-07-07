"""DataClassifier — regex-based classification. No data enters prompts until classified."""
from __future__ import annotations
import re
import uuid
from app.data_classification.schema import DataClass, DataClassification

_PATTERNS: list[tuple[DataClass, re.Pattern[str]]] = [
    (DataClass.SECRET, re.compile(
        r"(?i)(sk-proj-[a-zA-Z0-9]+|AKIA[A-Z0-9]{16}|"
        r"ghp_[a-zA-Z0-9]{36}|glpat-[a-zA-Z0-9_\-]{20,}|"
        r"xoxb-[0-9]+-[a-zA-Z0-9]+|"
        r"(?:password|passwd|secret|api[_\-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,})"
    )),
    (DataClass.PCI, re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|"
        r"4[0-9]{3}[\-\s][0-9]{4}[\-\s][0-9]{4}[\-\s][0-9]{4}|"
        r"5[1-5][0-9]{2}[\-\s][0-9]{4}[\-\s][0-9]{4}[\-\s][0-9]{4})\b"
    )),
    (DataClass.PHI, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (DataClass.PII, re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    (DataClass.PII, re.compile(r"\b(\+?1[\-.\s]?)?\(?\d{3}\)?[\-.\s]\d{3}[\-.\s]\d{4}\b")),
    (DataClass.SOURCE_CODE, re.compile(
        r"(?m)^(?:def |class |import |from .+ import |function |const |let |var |public class )"
    )),
    (DataClass.INTERNAL, re.compile(
        r"(?i)\b(internal|confidential|proprietary|q[1-4]\s+revenue|forecast|roadmap)\b"
    )),
]


class DataClassifier:
    def classify(self, text: str, data_id: str | None = None) -> DataClassification:
        found: list[DataClass] = []
        entities: list[str] = []
        for data_class, pattern in _PATTERNS:
            matches = pattern.findall(text)
            if matches:
                found.append(data_class)
                entities.extend(str(m)[:50] for m in matches[:3])
        if not found:
            found = [DataClass.PUBLIC]
        if DataClass.SECRET in found or DataClass.PHI in found or DataClass.PCI in found:
            retention = "regulated"
            blocked = ["external_tool", "webhook", "logging"]
        elif DataClass.PII in found:
            retention = "short"
            blocked = ["external_tool"]
        else:
            retention = "default"
            blocked = []
        return DataClassification(
            data_id=data_id or uuid.uuid4().hex,
            classes=found, detected_entities=entities,
            retention_policy=retention,
            allowed_sinks=["tenant_user", "audit_log"],
            blocked_sinks=blocked,
        )

    def classify_or_safe_fallback(self, text: str) -> DataClassification:
        try:
            return self.classify(text)
        except Exception:
            return DataClassification(
                data_id=uuid.uuid4().hex,
                classes=[DataClass.INTERNAL],
                retention_policy="default",
                blocked_sinks=["external_tool"],
            )
