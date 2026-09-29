"""PII analyzer for ingestion pipeline Stage 6 (LAW-06).

The pipeline's Stage 6 was written against Presidio's ``AnalyzerEngine``
interface, but Presidio is not a dependency of this service, so this in-process,
dependency-free analyzer is wired everywhere the pipeline is built.

It redacts **identifiers** — values that identify a person or grant access:
e-mail, phone numbers, payment cards (Luhn-checked, with or without spaces or
dashes), IBAN / bank / routing numbers, US SSN, India PAN / Aadhaar (Verhoeff-
checked) / GSTIN, passports, medical record / NPI / DEA numbers, dates of
birth, and credentials (API keys, JWTs, private keys, inline passwords).

It used to run the agent guardrails' whole catalogue, whose GDPR Article 9
entries are *keyword* signals ("treatment", "condition", "therapy", "DNA",
"fingerprint"). Every knowledge-base document lost those ordinary words
("The water [REDACTED:HEALTH_DATA] plant…"), so searches on them failed, while
real identifiers slipped through: a card written ``4111 1111 1111 1111``, an
Indian mobile number, a PAN or an Aadhaar number went to the embedder and into
the vector store. Keyword categories remain guardrail signals; they are not
redacted from documents.

It exposes the two methods Stage 6 needs:

* ``analyze(text, language)`` → a list of findings (empty = no PII);
* ``redact(text)`` → the text with each finding replaced by
  ``[REDACTED:<CATEGORY>]``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# ── Validators ────────────────────────────────────────────────────────────────


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _luhn_ok(value: str) -> bool:
    digits = _digits(value)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def _aadhaar_ok(value: str) -> bool:
    """Aadhaar: 12 digits, first digit 2-9, Verhoeff checksum."""
    digits = _digits(value)
    if len(digits) != 12 or digits[0] in "01":
        return False
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


# ── Patterns (identifiers only) ───────────────────────────────────────────────


@dataclass(frozen=True)
class _Rule:
    category: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    group: int = 0  # the capture group holding the identifier


def _rx(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# Order matters: more specific identifiers first so a card number is not first
# consumed as a phone number.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "PRIVATE_KEY",
        _rx(
            r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?"
            r"-----END (?:[A-Z]+ )?PRIVATE KEY-----"
        ),
    ),
    _Rule("JWT_TOKEN", _rx(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    _Rule(
        "API_KEY",
        _rx(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    ),
    _Rule(
        "PASSWORD_INLINE",
        _rx(r"\b(?:password|passwd|pwd)\s*[:=]\s*(\S{4,})", re.IGNORECASE),
        group=1,
    ),
    _Rule("EMAIL", _rx(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    _Rule("CREDIT_CARD", _rx(r"\b(?:\d[ -]?){12,18}\d\b"), _luhn_ok),
    _Rule("IBAN", _rx(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")),
    _Rule("SSN", _rx(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    _Rule("AADHAAR", _rx(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b"), _aadhaar_ok),
    _Rule("PAN", _rx(r"\b[A-Z]{3}[PCHABGJLFT][A-Z]\d{4}[A-Z]\b")),
    _Rule("GSTIN", _rx(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")),
    _Rule(
        "BANK_ACCOUNT",
        _rx(r"\b(?:account|acct|a/c)\s*(?:number|no\.?|#)?\s*:?\s*(\d{8,18})\b", re.IGNORECASE),
        group=1,
    ),
    _Rule(
        "ROUTING_NUMBER",
        _rx(
            r"\b(?:ABA|routing\s+number|IFSC)\s*:?\s*([A-Z]{4}0[A-Z0-9]{6}|\d{9})\b",
            re.IGNORECASE,
        ),
        group=1,
    ),
    _Rule(
        "PASSPORT",
        _rx(r"\bpassport\s*(?:number|no\.?|#)?\s*:?\s*([A-Z]{1,2}\d{6,9})\b", re.IGNORECASE),
        group=1,
    ),
    _Rule(
        "MEDICAL_RECORD",
        _rx(
            r"\b(?:MRN|medical\s+record\s+(?:number|no\.?))\s*:?\s*([A-Z0-9]{6,12})\b",
            re.IGNORECASE,
        ),
        group=1,
    ),
    _Rule("NPI", _rx(r"\bNPI\s*:?\s*(\d{10})\b"), group=1),
    _Rule("DEA_NUMBER", _rx(r"\bDEA\s*:?\s*([A-Z]{2}\d{7})\b"), group=1),
    _Rule(
        "DATE_OF_BIRTH",
        _rx(
            r"\b(?:DOB|date\s+of\s+birth|born\s+on)\s*:?\s*"
            r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}"
            r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b",
            re.IGNORECASE,
        ),
        group=1,
    ),
    # Phones last: E.164 / Indian mobile / North-American formats.
    _Rule(
        "PHONE",
        _rx(
            r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?"
            r"\d{2,5}(?:[\s.-]?\d{3,5}){1,2}(?![\w-])"
        ),
        lambda v: 10 <= len(_digits(v)) <= 15
        and (v.lstrip().startswith(("+", "(")) or _digits(v)[0] in "6789"),
    ),
)


@dataclass(frozen=True)
class PIIFinding:
    category: str
    start: int
    end: int


class RegexPIIAnalyzer:
    """Deterministic identifier-only PII analyzer with checksum validation."""

    def analyze(self, text: str, language: str = "en") -> list[PIIFinding]:
        """Return one finding per identifier (empty list when the text is clean)."""
        del language  # patterns are language-agnostic
        return self._find(text)

    def redact(self, text: str) -> str:
        """Return *text* with every identifier replaced by ``[REDACTED:<CATEGORY>]``."""
        out = text
        for f in sorted(self._find(text), key=lambda f: f.start, reverse=True):
            out = f"{out[: f.start]}[REDACTED:{f.category}]{out[f.end :]}"
        return out

    @staticmethod
    def _find(text: str) -> list[PIIFinding]:
        taken: list[tuple[int, int]] = []
        findings: list[PIIFinding] = []
        for rule in _RULES:
            for m in rule.pattern.finditer(text):
                start, end = m.span(rule.group)
                if start < 0:
                    continue
                if any(start < e and end > s for s, e in taken):
                    continue  # already covered by a more specific identifier
                if rule.validator is not None and not rule.validator(m.group(rule.group)):
                    continue
                taken.append((start, end))
                findings.append(PIIFinding(rule.category, start, end))
        return findings


def build_pii_analyzer() -> RegexPIIAnalyzer:
    """The analyzer every ``IngestionPipeline`` construction site wires in."""
    return RegexPIIAnalyzer()
