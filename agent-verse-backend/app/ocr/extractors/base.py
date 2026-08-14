"""OcrExtractor protocol."""
from __future__ import annotations

from typing import Protocol

from app.ocr.models import ExtractedField


class OcrExtractor(Protocol):
    def extract(self, raw_text: str) -> dict[str, ExtractedField]: ...
