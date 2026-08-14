"""General fallback extractor — no structured fields."""
from __future__ import annotations

from app.ocr.models import ExtractedField


class GeneralExtractor:
    def extract(self, raw_text: str) -> dict[str, ExtractedField]:
        return {}
