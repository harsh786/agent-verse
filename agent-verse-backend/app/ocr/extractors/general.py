"""General fallback extractor — returns raw text and optional LLM-structured fields."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.ocr.models import ExtractedField

_log = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Analyze this document text and extract all key-value fields you can identify.

Rules:
1. Return ONLY a JSON object — no explanation, no markdown fences.
2. Keys must be snake_case field names (e.g. "invoice_number", "total_amount", "issue_date").
3. Each value must be an object: {{"value": "<extracted value>", "confidence": <0.0-1.0>}}
4. confidence: 0.9 = very clear, 0.5 = inferred (reflects clarity in the source text)
5. Only include fields that are clearly present. Do NOT guess.
6. If no fields can be extracted, return {{}}

Document text:
{raw_text}

JSON output:"""


class GeneralExtractor:
    """Fallback extractor — returns empty fields for unrecognized documents."""

    def extract(self, raw_text: str) -> dict[str, ExtractedField]:
        return {}


class LlmStructuredExtractor:
    """Use an LLM to extract structured key-value fields from any document type.

    This extractor is used when document_type is GENERAL and a provider is available.
    It works on any language, any document type, worldwide.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def extract_async(self, raw_text: str) -> dict[str, ExtractedField]:
        """Async extraction using LLM provider."""
        if not raw_text.strip():
            return {}

        try:
            from app.providers.base import (  # type: ignore[import-untyped]
                CompletionRequest,
                Message,
            )

            prompt = _EXTRACTION_PROMPT.format(raw_text=raw_text[:4000])  # limit context
            req = CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="default",
            )
            response = await self._provider.complete(req)
            return self._parse_response(response.content)
        except Exception as exc:
            _log.warning("LLM structured extraction failed: %s", exc)
            return {}

    def extract(self, raw_text: str) -> dict[str, ExtractedField]:
        """Sync wrapper — returns empty (use extract_async for real results)."""
        return {}

    @staticmethod
    def _parse_response(content: str) -> dict[str, ExtractedField]:
        """Parse LLM JSON response into ExtractedField dict."""
        # Strip markdown fences if present
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            _log.debug("LLM returned non-JSON: %s", text[:200])
            return {}

        if not isinstance(data, dict):
            return {}

        fields: dict[str, ExtractedField] = {}
        for key, val in data.items():
            if not isinstance(key, str):
                continue
            if isinstance(val, dict):
                value = str(val.get("value", "")).strip()
                confidence = float(val.get("confidence", 0.7))
            elif isinstance(val, str):
                value = val.strip()
                confidence = 0.7
            else:
                continue

            if value:
                # Normalize key to snake_case
                safe_key = key.lower().replace(" ", "_").replace("-", "_")
                fields[safe_key] = ExtractedField(
                    name=safe_key,
                    value=value,
                    confidence=min(max(confidence, 0.0), 1.0),
                )

        return fields
