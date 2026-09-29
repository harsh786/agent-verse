"""Page analyzer — extract structured data from web pages using BrowserAgent + LLM."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.perception.browser_agent import BrowserAgent

logger = logging.getLogger(__name__)


@dataclass
class PageAnalysis:
    url: str
    title: str = ""
    text_content: str = ""
    screenshot_b64: str = ""
    llm_analysis: str = ""
    success: bool = False
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_context_block(self) -> str:
        """Format for injection into planner prompt."""
        parts = [f"### Page: {self.url}"]
        if self.title:
            parts.append(f"Title: {self.title}")
        if self.llm_analysis:
            parts.append(f"Analysis:\n{self.llm_analysis}")
        elif self.text_content:
            parts.append(f"Content (truncated):\n{self.text_content[:1000]}")
        return "\n".join(parts)


class VisionUnavailableError(RuntimeError):
    """Vision analysis was required but no vision-capable provider is configured."""


class PageAnalyzer:
    """Analyze web pages using BrowserAgent and optionally a vision LLM.

    Vision analysis is never faked: without a vision-capable provider
    ``llm_analysis`` stays empty (``metadata["vision"] == "unavailable"``), and a
    provider error is recorded as an error (``metadata["vision"] == "failed"``)
    rather than returned as the analysis text. Callers that need an analysis
    pass ``require_vision=True`` to get :class:`VisionUnavailableError` instead.
    """

    def __init__(self, *, browser_agent: BrowserAgent | None = None) -> None:
        self._browser = browser_agent or BrowserAgent()

    @property
    def vision_available(self) -> bool:
        """True when the browser agent has a vision-capable provider."""
        return bool(self._browser.has_vision)

    def _require_vision(self) -> None:
        if not self.vision_available:
            raise VisionUnavailableError("No vision-capable provider is configured")

    async def analyze_url(
        self,
        url: str,
        *,
        question: str = "What is the main purpose and content of this page?",
        extract_text: bool = True,
        take_screenshot: bool = True,
        require_vision: bool = False,
    ) -> PageAnalysis:
        """Fully analyze a URL: screenshot + text + LLM analysis.

        With ``require_vision`` a missing vision provider raises
        :class:`VisionUnavailableError` before the page is loaded, and a failed
        vision analysis makes the result unsuccessful.
        """
        if require_vision:
            self._require_vision()
        analysis = PageAnalysis(url=url)
        vision_error = ""

        if take_screenshot:
            ss_result = await self._browser.take_screenshot(url)
            if not ss_result.success:
                vision_error = f"Screenshot failed: {ss_result.error or 'unknown error'}"
            else:
                analysis.screenshot_b64 = ss_result.screenshot_b64
                if not self.vision_available:
                    analysis.metadata["vision"] = "unavailable"
                else:
                    try:
                        analysis.llm_analysis = await self._browser.analyze_screenshot(
                            ss_result.screenshot_b64, question, raise_errors=True
                        )
                        analysis.metadata["vision"] = "ok"
                    except Exception as exc:
                        logger.warning("Vision analysis failed for %s: %s", url, exc)
                        analysis.metadata["vision"] = "failed"
                        vision_error = f"Vision analysis failed: {exc}"

        if extract_text:
            text_result = await self._browser.extract_text(url)
            if text_result.success:
                analysis.text_content = text_result.output

        analysis.success = bool(analysis.screenshot_b64 or analysis.text_content)
        if require_vision and not analysis.llm_analysis:
            analysis.success = False
            analysis.error = vision_error or "No screenshot was captured to analyze"
        elif not analysis.success:
            analysis.error = "Could not extract content from URL"
        elif analysis.metadata.get("vision") == "failed":
            analysis.error = vision_error

        return analysis

    async def analyze_multiple(
        self, urls: list[str], question: str = "", *, require_vision: bool = False
    ) -> list[PageAnalysis]:
        """Analyze multiple URLs concurrently.

        With ``require_vision`` a missing vision provider raises
        :class:`VisionUnavailableError` before any page is loaded.
        """
        import asyncio

        if require_vision:
            self._require_vision()
        tasks = [
            self.analyze_url(
                url,
                question=question or "What is on this page?",
                require_vision=require_vision,
            )
            for url in urls
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    def build_context_block(self, analyses: list[PageAnalysis]) -> str:
        """Build a multi-page context block for the planner prompt."""
        if not analyses:
            return ""
        parts = ["## Web Page Context"]
        for a in analyses:
            if a.success:
                parts.append(a.to_context_block())
        return "\n\n".join(parts)
