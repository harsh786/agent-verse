"""Comprehensive tests for app/perception/page_analyzer.py and app/perception/multimodal.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.perception.page_analyzer import PageAnalysis, PageAnalyzer
from app.perception.browser_agent import BrowserResult


# ── PageAnalysis.to_context_block ─────────────────────────────────────────────


def test_context_block_with_all_fields() -> None:
    analysis = PageAnalysis(
        url="https://example.com",
        title="Example Domain",
        llm_analysis="This is a placeholder page for illustrative examples.",
        success=True,
    )
    block = analysis.to_context_block()
    assert "example.com" in block
    assert "Example Domain" in block
    assert "placeholder page" in block


def test_context_block_with_text_content_no_analysis() -> None:
    analysis = PageAnalysis(
        url="https://example.com",
        text_content="Raw page text content here",
        success=True,
    )
    block = analysis.to_context_block()
    assert "Raw page text" in block
    assert "### Page:" in block


def test_context_block_analysis_takes_precedence_over_text() -> None:
    analysis = PageAnalysis(
        url="https://example.com",
        text_content="raw text",
        llm_analysis="LLM summary",
        success=True,
    )
    block = analysis.to_context_block()
    assert "LLM summary" in block
    # text_content should not appear if llm_analysis is set
    assert "raw text" not in block


def test_context_block_empty_analysis() -> None:
    analysis = PageAnalysis(url="https://example.com")
    block = analysis.to_context_block()
    assert "### Page:" in block


def test_context_block_text_truncated_at_1000() -> None:
    long_text = "x" * 2000
    analysis = PageAnalysis(url="https://example.com", text_content=long_text, success=True)
    block = analysis.to_context_block()
    # The block shouldn't include more than 1000 chars of raw text
    assert len(block) < 1200  # 1000 text + some header overhead


# ── PageAnalyzer.analyze_url ──────────────────────────────────────────────────


async def test_analyze_url_screenshot_and_text_success() -> None:
    mock_browser = MagicMock()
    mock_browser._vision = None

    mock_browser.take_screenshot = AsyncMock(
        return_value=BrowserResult(
            success=True,
            action="screenshot",
            output="Screenshot taken",
            screenshot_b64="base64_screen_data",
        )
    )
    mock_browser.extract_text = AsyncMock(
        return_value=BrowserResult(
            success=True,
            action="extract_text",
            output="Page text content here",
        )
    )

    analyzer = PageAnalyzer(browser_agent=mock_browser)
    result = await analyzer.analyze_url("https://example.com")

    assert result.success is True
    assert result.screenshot_b64 == "base64_screen_data"
    assert result.text_content == "Page text content here"
    assert result.error == ""


async def test_analyze_url_screenshot_fails_text_succeeds() -> None:
    mock_browser = MagicMock()
    mock_browser._vision = None

    mock_browser.take_screenshot = AsyncMock(
        return_value=BrowserResult(success=False, action="screenshot", error="Timeout")
    )
    mock_browser.extract_text = AsyncMock(
        return_value=BrowserResult(success=True, action="extract_text", output="some text")
    )

    analyzer = PageAnalyzer(browser_agent=mock_browser)
    result = await analyzer.analyze_url("https://example.com")

    # Text was extracted → success=True
    assert result.success is True
    assert result.screenshot_b64 == ""
    assert result.text_content == "some text"


async def test_analyze_url_both_fail_returns_failure() -> None:
    mock_browser = MagicMock()
    mock_browser._vision = None

    mock_browser.take_screenshot = AsyncMock(
        return_value=BrowserResult(success=False, action="screenshot", error="No network")
    )
    mock_browser.extract_text = AsyncMock(
        return_value=BrowserResult(success=False, action="extract_text", error="No network")
    )

    analyzer = PageAnalyzer(browser_agent=mock_browser)
    result = await analyzer.analyze_url("https://example.com")

    assert result.success is False
    assert "Could not extract" in result.error


async def test_analyze_url_with_vision_calls_analyze_screenshot() -> None:
    mock_browser = MagicMock()
    mock_browser._vision = MagicMock()  # Non-None vision

    mock_browser.take_screenshot = AsyncMock(
        return_value=BrowserResult(
            success=True,
            action="screenshot",
            screenshot_b64="img_data",
        )
    )
    mock_browser.analyze_screenshot = AsyncMock(return_value="Page shows a login form.")
    mock_browser.extract_text = AsyncMock(
        return_value=BrowserResult(success=False, action="extract_text", error="skip")
    )

    analyzer = PageAnalyzer(browser_agent=mock_browser)
    result = await analyzer.analyze_url("https://example.com", question="What is on screen?")

    mock_browser.analyze_screenshot.assert_called_once()
    assert result.llm_analysis == "Page shows a login form."


async def test_analyze_url_extract_text_skipped_when_disabled() -> None:
    mock_browser = MagicMock()
    mock_browser._vision = None
    mock_browser.take_screenshot = AsyncMock(
        return_value=BrowserResult(success=True, action="screenshot", screenshot_b64="img")
    )
    mock_browser.extract_text = AsyncMock()

    analyzer = PageAnalyzer(browser_agent=mock_browser)
    result = await analyzer.analyze_url("https://example.com", extract_text=False)

    mock_browser.extract_text.assert_not_called()
    assert result.text_content == ""


async def test_analyze_url_screenshot_skipped_when_disabled() -> None:
    mock_browser = MagicMock()
    mock_browser._vision = None
    mock_browser.take_screenshot = AsyncMock()
    mock_browser.extract_text = AsyncMock(
        return_value=BrowserResult(success=True, action="extract_text", output="text")
    )

    analyzer = PageAnalyzer(browser_agent=mock_browser)
    result = await analyzer.analyze_url("https://example.com", take_screenshot=False)

    mock_browser.take_screenshot.assert_not_called()
    assert result.screenshot_b64 == ""


# ── PageAnalyzer.analyze_multiple ────────────────────────────────────────────


async def test_analyze_multiple_runs_concurrently() -> None:
    mock_browser = MagicMock()
    mock_browser._vision = None

    call_count = 0

    async def mock_screenshot(url: str) -> BrowserResult:
        nonlocal call_count
        call_count += 1
        return BrowserResult(
            success=True,
            action="screenshot",
            screenshot_b64=f"img_{url[-1]}",
        )

    async def mock_extract(url: str, selector: str = "body") -> BrowserResult:
        return BrowserResult(success=True, action="extract_text", output=f"text for {url}")

    mock_browser.take_screenshot = mock_screenshot
    mock_browser.extract_text = mock_extract

    urls = ["https://a.com", "https://b.com", "https://c.com"]
    analyzer = PageAnalyzer(browser_agent=mock_browser)
    results = await analyzer.analyze_multiple(urls)

    assert len(results) == 3
    assert all(isinstance(r, PageAnalysis) for r in results)
    assert call_count == 3


async def test_analyze_multiple_empty_list() -> None:
    analyzer = PageAnalyzer(browser_agent=MagicMock())
    results = await analyzer.analyze_multiple([])
    assert results == []


# ── PageAnalyzer.build_context_block ─────────────────────────────────────────


def test_build_context_block_empty_list() -> None:
    analyzer = PageAnalyzer(browser_agent=MagicMock())
    block = analyzer.build_context_block([])
    assert block == ""


def test_build_context_block_all_failed() -> None:
    analyses = [
        PageAnalysis(url="https://a.com", success=False),
        PageAnalysis(url="https://b.com", success=False),
    ]
    analyzer = PageAnalyzer(browser_agent=MagicMock())
    block = analyzer.build_context_block(analyses)
    # All failed — the header might still appear but no page blocks
    assert "a.com" not in block
    assert "b.com" not in block


def test_build_context_block_mixed_success_and_failure() -> None:
    analyses = [
        PageAnalysis(url="https://a.com", success=True, llm_analysis="About A"),
        PageAnalysis(url="https://b.com", success=False, error="404"),
        PageAnalysis(url="https://c.com", success=True, text_content="Content of C"),
    ]
    analyzer = PageAnalyzer(browser_agent=MagicMock())
    block = analyzer.build_context_block(analyses)

    assert "a.com" in block
    assert "About A" in block
    assert "c.com" in block
    assert "b.com" not in block  # failed, should be excluded


def test_build_context_block_includes_header() -> None:
    analyses = [PageAnalysis(url="https://x.com", success=True, text_content="hi")]
    analyzer = PageAnalyzer(browser_agent=MagicMock())
    block = analyzer.build_context_block(analyses)
    assert "## Web Page Context" in block


# ── Multimodal perception ─────────────────────────────────────────────────────


class TestPerceptionInput:
    def test_has_visual_context_with_images(self) -> None:
        from app.perception.multimodal import ImageAttachment, PerceptionInput

        pi = PerceptionInput(
            goal_text="analyze this",
            images=[ImageAttachment(data_b64="abc", mime_type="image/png")],
        )
        assert pi.has_visual_context() is True

    def test_has_visual_context_with_urls(self) -> None:
        from app.perception.multimodal import PerceptionInput

        pi = PerceptionInput(goal_text="analyze", urls=["https://example.com"])
        assert pi.has_visual_context() is True

    def test_has_visual_context_empty(self) -> None:
        from app.perception.multimodal import PerceptionInput

        pi = PerceptionInput(goal_text="just text, no images")
        assert pi.has_visual_context() is False

    def test_to_prompt_context_no_visual(self) -> None:
        from app.perception.multimodal import PerceptionInput

        pi = PerceptionInput(goal_text="no images")
        assert pi.to_prompt_context() == ""

    def test_to_prompt_context_with_image_and_url(self) -> None:
        from app.perception.multimodal import ImageAttachment, PerceptionInput

        pi = PerceptionInput(
            goal_text="analyze",
            images=[ImageAttachment(data_b64="xyz", description="screenshot")],
            urls=["https://target.com"],
        )
        ctx = pi.to_prompt_context()
        assert "screenshot" in ctx
        assert "target.com" in ctx
        assert "## Visual Context" in ctx


class TestImageAttachment:
    def test_from_data_uri_png(self) -> None:
        from app.perception.multimodal import ImageAttachment

        uri = "data:image/png;base64,abc123=="
        img = ImageAttachment.from_data_uri(uri, description="test")
        assert img.mime_type == "image/png"
        assert img.data_b64 == "abc123=="
        assert img.description == "test"

    def test_from_data_uri_jpeg(self) -> None:
        from app.perception.multimodal import ImageAttachment

        uri = "data:image/jpeg;base64,/9j/4AAQ"
        img = ImageAttachment.from_data_uri(uri)
        assert img.mime_type == "image/jpeg"

    def test_from_data_uri_raw_base64(self) -> None:
        from app.perception.multimodal import ImageAttachment

        img = ImageAttachment.from_data_uri("rawbase64data")
        assert img.data_b64 == "rawbase64data"

    def test_to_data_uri(self) -> None:
        from app.perception.multimodal import ImageAttachment

        img = ImageAttachment(data_b64="abc123", mime_type="image/png")
        uri = img.to_data_uri()
        assert uri == "data:image/png;base64,abc123"

    def test_byte_size_valid_base64(self) -> None:
        import base64

        from app.perception.multimodal import ImageAttachment

        content = b"hello world"
        encoded = base64.b64encode(content).decode()
        img = ImageAttachment(data_b64=encoded)
        assert img.byte_size() == len(content)

    def test_byte_size_invalid_base64_returns_zero(self) -> None:
        from app.perception.multimodal import ImageAttachment

        img = ImageAttachment(data_b64="not-valid-base64!!!")
        assert img.byte_size() == 0


class TestResizeImageB64:
    def test_small_image_not_resized(self) -> None:
        import base64

        from app.perception.multimodal import resize_image_b64

        small = base64.b64encode(b"x" * 100).decode()
        result = resize_image_b64(small, max_size=1_000_000)
        assert result == small

    def test_invalid_base64_returns_original(self) -> None:
        from app.perception.multimodal import resize_image_b64

        original = "not-valid-base64!!"
        result = resize_image_b64(original)
        assert result == original
