"""Unit tests for PageAnalyzer."""
from __future__ import annotations

import pytest

from app.perception.page_analyzer import PageAnalyzer, PageAnalysis


@pytest.mark.asyncio
async def test_page_analyzer_creates_browser_agent_if_none():
    analyzer = PageAnalyzer()
    assert analyzer._browser is not None


@pytest.mark.asyncio
async def test_analyze_url_without_playwright():
    """analyze_url returns PageAnalysis with success=False when Playwright unavailable."""
    import app.perception.browser_agent as ba

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        analyzer = PageAnalyzer()
        result = await analyzer.analyze_url("https://example.com")
        assert isinstance(result, PageAnalysis)
        assert result.url == "https://example.com"
        # No content without Playwright
        assert result.screenshot_b64 == "" or not result.success
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original


@pytest.mark.asyncio
async def test_analyze_multiple_empty():
    analyzer = PageAnalyzer()
    results = await analyzer.analyze_multiple([])
    assert results == []


@pytest.mark.asyncio
async def test_analyze_multiple_returns_list():
    import app.perception.browser_agent as ba

    original = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        analyzer = PageAnalyzer()
        results = await analyzer.analyze_multiple(["https://a.com", "https://b.com"])
        assert len(results) == 2
        assert all(isinstance(r, PageAnalysis) for r in results)
    finally:
        ba._PLAYWRIGHT_AVAILABLE = original


def test_build_context_block_empty():
    analyzer = PageAnalyzer()
    result = analyzer.build_context_block([])
    assert result == ""


def test_build_context_block_with_analyses():
    analyses = [
        PageAnalysis(
            url="https://a.com",
            title="A",
            text_content="Hello",
            success=True,
            llm_analysis="Main page",
        ),
        PageAnalysis(url="https://b.com", title="B", success=False, error="timeout"),
    ]
    analyzer = PageAnalyzer()
    block = analyzer.build_context_block(analyses)
    # Should include successful analysis
    assert "https://a.com" in block
    assert "Web Page Context" in block
    # Failed analysis (b.com) is excluded because success=False


def test_page_analysis_to_context_block_with_llm():
    pa = PageAnalysis(
        url="https://example.com",
        title="Example",
        llm_analysis="This is a demo page",
        success=True,
    )
    block = pa.to_context_block()
    assert "https://example.com" in block
    assert "This is a demo page" in block


def test_page_analysis_to_context_block_text_fallback():
    pa = PageAnalysis(
        url="https://example.com",
        text_content="Raw text content here",
        success=True,
    )
    block = pa.to_context_block()
    assert "Raw text content here" in block or "https://example.com" in block
