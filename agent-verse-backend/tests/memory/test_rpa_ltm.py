"""Verify RPA extractions are stored in LTM with correct typing and chunking."""
import pytest

from app.memory.long_term import LongTermMemoryStore
from app.tenancy.context import PlanTier, TenantContext


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-rpa-ltm-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


@pytest.mark.asyncio
async def test_store_rpa_extraction_short_content():
    """Content <= chunk_size stored as single LTM entry with rpa_extraction type."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    ids = await store.store_rpa_extraction(
        url="https://example.com/dashboard",
        extracted_text="Revenue: $4.2M. Active users: 12,400. Status: healthy.",
        goal_id="goal-rpa-001",
        tenant_ctx=tenant,
        db=None,
    )

    assert len(ids) == 1
    memories = store.list_all(tenant_ctx=tenant)
    assert len(memories) == 1
    m = memories[0]
    assert m.memory_type == "rpa_extraction"
    assert "https://example.com/dashboard" in m.content
    assert "Revenue" in m.content
    assert "rpa" in m.tags
    assert "web-extraction" in m.tags


@pytest.mark.asyncio
async def test_store_rpa_extraction_long_content_chunks():
    """Content > chunk_size is split into multiple LTM entries."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    long_text = "Row data: " + ("Lorem ipsum dolor sit amet. " * 50)  # ~1400 chars

    ids = await store.store_rpa_extraction(
        url="https://example.com/report",
        extracted_text=long_text,
        goal_id="goal-rpa-002",
        tenant_ctx=tenant,
        db=None,
        chunk_size=400,
    )

    assert len(ids) >= 2, f"Long text should produce multiple chunks, got {len(ids)}"
    memories = store.list_all(tenant_ctx=tenant)
    assert all(m.memory_type == "rpa_extraction" for m in memories)
    assert all("https://example.com/report" in m.content for m in memories)


@pytest.mark.asyncio
async def test_store_rpa_extraction_ignores_short_noise():
    """Content < 50 chars is too short and should not be stored."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    ids = await store.store_rpa_extraction(
        url="https://example.com",
        extracted_text="OK",
        goal_id="goal-rpa-003",
        tenant_ctx=tenant,
        db=None,
    )

    assert ids == [], "Short noise content must not be stored"


@pytest.mark.asyncio
async def test_store_rpa_vision_analysis():
    """Vision analysis from rpa_screenshot stored with vision tag."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    ids = await store.store_rpa_extraction(
        url="https://example.com/dashboard",
        extracted_text="Vision analysis: The dashboard shows Q3 2026 revenue of $4.2M "
                       "with a 23% increase from Q2. Operating costs trending down.",
        goal_id="goal-rpa-004",
        tenant_ctx=tenant,
        db=None,
        source_type="rpa_vision",
    )

    assert len(ids) == 1
    memories = store.list_all(tenant_ctx=tenant)
    m = next((m for m in memories if "vision" in m.tags), None)
    assert m is not None
    assert m.memory_type == "rpa_extraction"
    assert "vision" in m.tags
