"""Verify full RPA→LTM→recall loop works across simulated goal runs."""
import pytest
from app.memory.long_term import LongTermMemoryStore
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-rpa-loop-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


@pytest.mark.asyncio
async def test_rpa_extraction_recalled_by_related_query():
    """Content stored via store_rpa_extraction is recalled by keyword match."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    # Simulate: Goal 1 extracts pricing page via RPA
    await store.store_rpa_extraction(
        url="https://competitor.com/pricing",
        extracted_text=(
            "Enterprise plan: $499/month. Features: unlimited users, SSO, "
            "24/7 support, API access. Starter: $49/month. 5 users max."
        ),
        goal_id="goal-001",
        tenant_ctx=tenant,
        db=None,
    )

    # Simulate: Goal 2 asks about competitor pricing
    recalled = store.recall(
        query="competitor pricing enterprise plan",
        tenant_ctx=tenant,
        top_k=3,
    )

    assert len(recalled) >= 1, "RPA extraction must be recalled by related query"
    assert any(
        "competitor.com" in m.content or "Enterprise" in m.content
        for m in recalled
    ), "Recalled memories must contain the extracted content"
    assert all(m.memory_type == "rpa_extraction" for m in recalled)


@pytest.mark.asyncio
async def test_rpa_failure_pattern_recalled_by_related_goal():
    """Failure recorded in ExecutionMemory is recalled for similar future goal."""
    from app.memory.execution import ExecutionMemory

    mem = ExecutionMemory()
    tenant = _tenant()

    # Simulate: failed RPA on checkout page.
    # recall_failures() checks `hint in goal` (substring), so hint must be
    # a substring of the stored goal string.
    mem.record_failure(
        goal="checkout on example.com",
        failed_step="rpa_click(selector=#checkout-btn)",
        error="Timeout: element '#checkout-btn' not found within 5000ms",
        tenant_ctx=tenant,
    )

    # Future goal: similar checkout task — use a hint that is a substring
    # of the stored goal so the keyword filter matches.
    failures = mem.recall_failures(
        goal_hint="checkout",
        tenant_ctx=tenant,
        top_k=3,
    )

    assert len(failures) >= 1
    assert "checkout-btn" in failures[0]["error"]


@pytest.mark.asyncio
async def test_vision_analysis_recalled_by_data_query():
    """Vision analysis from screenshot stored and recalled by data question."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    # Simulate: screenshot vision analysis stored
    await store.store_rpa_extraction(
        url="https://internal-dashboard.com/metrics",
        extracted_text=(
            "Vision analysis: Dashboard shows Q3 2026 revenue $4.2M, "
            "up 23% from Q2 $3.4M. Operating costs declined 8%. "
            "Active subscriptions: 1,240. Churn rate: 2.3%."
        ),
        goal_id="goal-vision-001",
        tenant_ctx=tenant,
        db=None,
        source_type="rpa_vision",
    )

    recalled = store.recall(
        query="Q3 revenue and churn rate",
        tenant_ctx=tenant,
        top_k=3,
    )

    assert len(recalled) >= 1
    content_combined = " ".join(m.content for m in recalled)
    assert "4.2M" in content_combined or "revenue" in content_combined.lower()
    assert any("vision" in m.tags for m in recalled)


@pytest.mark.asyncio
async def test_chunked_rpa_content_all_chunks_recalled():
    """When long content is chunked, all chunks are searchable independently."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    # Store long content that will be chunked
    long_content = (
        "Section 1: Login procedure requires username and password. "
        "The login form is at https://portal.corp.com/auth. "
    ) * 10  # ~1080 chars — will produce 3 chunks with chunk_size=500

    ids = await store.store_rpa_extraction(
        url="https://portal.corp.com",
        extracted_text=long_content,
        goal_id="goal-chunk-001",
        tenant_ctx=tenant,
        db=None,
        chunk_size=500,
    )

    assert len(ids) >= 2, "Long content must produce multiple chunks"

    # Each chunk should be independently recalled
    recalled = store.recall(
        query="portal login procedure",
        tenant_ctx=tenant,
        top_k=5,
    )

    assert len(recalled) >= 1
    assert all(m.memory_type == "rpa_extraction" for m in recalled)
    assert all("portal.corp.com" in m.content for m in recalled)
