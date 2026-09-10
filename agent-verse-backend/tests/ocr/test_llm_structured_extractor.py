"""Tests for LlmStructuredExtractor — global document field extraction."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ocr.extractors import get_extractor
from app.ocr.extractors.general import GeneralExtractor, LlmStructuredExtractor
from app.ocr.models import DocumentType


def test_general_extractor_returns_empty():
    ext = GeneralExtractor()
    assert ext.extract("anything") == {}


def test_get_extractor_returns_general_without_provider():
    ext = get_extractor(DocumentType.GENERAL)
    assert isinstance(ext, GeneralExtractor)


def test_get_extractor_returns_llm_extractor_with_provider():
    mock_provider = MagicMock()
    ext = get_extractor(DocumentType.GENERAL, provider=mock_provider)
    assert isinstance(ext, LlmStructuredExtractor)


def test_get_extractor_non_general_ignores_provider():
    from app.ocr.extractors.id_docs import IdDocExtractor

    mock_provider = MagicMock()
    ext = get_extractor(DocumentType.PAN_CARD, provider=mock_provider)
    assert isinstance(ext, IdDocExtractor)


@pytest.mark.asyncio
async def test_llm_extractor_parses_json_response():
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(
            content="""{
        "invoice_number": {"value": "INV-2026-001", "confidence": 0.95},
        "total_amount": {"value": "1500.00", "confidence": 0.9},
        "vendor_name": {"value": "Acme Corp", "confidence": 0.85}
    }"""
        )
    )

    ext = LlmStructuredExtractor(provider=mock_provider)
    fields = await ext.extract_async("Invoice INV-2026-001 from Acme Corp total $1500")

    assert "invoice_number" in fields
    assert fields["invoice_number"].value == "INV-2026-001"
    assert fields["invoice_number"].confidence == 0.95
    assert "total_amount" in fields
    assert "vendor_name" in fields


@pytest.mark.asyncio
async def test_llm_extractor_handles_markdown_fences():
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(
            content="""```json
{
    "name": {"value": "John Doe", "confidence": 0.9},
    "dob": {"value": "1990-01-01", "confidence": 0.85}
}
```"""
        )
    )

    ext = LlmStructuredExtractor(provider=mock_provider)
    fields = await ext.extract_async("Name: John Doe  DOB: 1990-01-01")
    assert "name" in fields
    assert "dob" in fields


@pytest.mark.asyncio
async def test_llm_extractor_handles_invalid_json():
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(content="Sorry, I cannot extract fields.")
    )

    ext = LlmStructuredExtractor(provider=mock_provider)
    fields = await ext.extract_async("some text")
    assert fields == {}


@pytest.mark.asyncio
async def test_llm_extractor_handles_empty_text():
    mock_provider = MagicMock()
    ext = LlmStructuredExtractor(provider=mock_provider)
    fields = await ext.extract_async("")
    assert fields == {}
    # Provider should not be called for empty text
    mock_provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_llm_extractor_handles_provider_exception():
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(side_effect=Exception("Provider error"))

    ext = LlmStructuredExtractor(provider=mock_provider)
    fields = await ext.extract_async("some text")
    assert fields == {}  # Graceful fallback


@pytest.mark.asyncio
async def test_llm_extractor_normalizes_keys():
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(
            content="""{
        "Invoice Number": {"value": "001", "confidence": 0.9},
        "issue-date": {"value": "2026-01-01", "confidence": 0.8}
    }"""
        )
    )

    ext = LlmStructuredExtractor(provider=mock_provider)
    fields = await ext.extract_async("Invoice Number: 001")
    assert "invoice_number" in fields  # spaces → underscores
    assert "issue_date" in fields  # hyphens → underscores


@pytest.mark.asyncio
async def test_llm_extractor_flat_string_values():
    """LLM sometimes returns flat strings instead of dicts."""
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(
            content="""{
        "name": "John Smith",
        "age": "35"
    }"""
        )
    )

    ext = LlmStructuredExtractor(provider=mock_provider)
    fields = await ext.extract_async("Name: John Smith  Age: 35")
    assert "name" in fields
    assert fields["name"].value == "John Smith"
    assert fields["name"].confidence == 0.7  # default for flat strings
