"""PII analyzer for ingestion pipeline Stage 6 (LAW-06).

The pipeline's Stage 6 was written against Presidio's ``AnalyzerEngine``
interface, but Presidio is not a dependency of this service and nothing ever
constructed an analyzer — so ``IngestionPipeline._pii`` was always ``None`` and
Stage 6 was skipped for every document: PII flowed straight to the embedder and
into the vector store regardless of a Source's ``pii_action``.

``RegexPIIAnalyzer`` is the in-process, dependency-free analyzer that is now
wired everywhere the pipeline is built. It reuses the platform's single PII
pattern catalogue (``app.intelligence.guardrail_patterns.PII_PATTERNS`` via
``PIIDetector``: SSN, card numbers, IBAN, bank/routing numbers, MRN/NPI/DEA,
dates of birth, …) so ingestion and the agent guardrails agree on what PII is.

It exposes the two methods Stage 6 needs:

* ``analyze(text, language)`` → a list of findings (empty = no PII), the same
  shape contract as Presidio's analyzer (truthy list when PII is present);
* ``redact(text)`` → the text with each finding replaced by
  ``[REDACTED:<CATEGORY>]``.
"""

from __future__ import annotations

from typing import Any


class RegexPIIAnalyzer:
    """Deterministic regex PII analyzer backed by the shared guardrail patterns."""

    def __init__(self) -> None:
        from app.intelligence.guardrail_engine import PIIDetector

        self._detector = PIIDetector(redact=True)

    def analyze(self, text: str, language: str = "en") -> list[Any]:
        """Return one finding per PII match (empty list when the text is clean)."""
        del language  # patterns are language-agnostic
        violations, _ = self._detector.scan(text)
        return list(violations)

    def redact(self, text: str) -> str:
        """Return *text* with every PII span replaced by a ``[REDACTED:…]`` token."""
        _, redacted = self._detector.scan(text)
        return redacted


def build_pii_analyzer() -> RegexPIIAnalyzer:
    """The analyzer every ``IngestionPipeline`` construction site wires in."""
    return RegexPIIAnalyzer()
