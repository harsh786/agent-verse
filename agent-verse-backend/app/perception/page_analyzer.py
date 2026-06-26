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


class PageAnalyzer:
    """Analyze web pages using BrowserAgent and optionally a vision LLM."""

    def __init__(self, *, browser_agent: BrowserAgent | None = None) -> None:
        self._browser = browser_agent or BrowserAgent()

    async def analyze_url(
        self,
        url: str,
        *,
        question: str = "What is the main purpose and content of this page?",
        extract_text: bool = True,
        take_screenshot: bool = True,
    ) -> PageAnalysis:
        """Fully analyze a URL: screenshot + text + LLM analysis."""
        analysis = PageAnalysis(url=url)

        if take_screenshot:
            ss_result = await self._browser.take_screenshot(url)
            if ss_result.success:
                analysis.screenshot_b64 = ss_result.screenshot_b64
                # LLM vision analysis
                if self._browser._vision is not None:
                    analysis.llm_analysis = await self._browser.analyze_screenshot(
                        ss_result.screenshot_b64, question
                    )

        if extract_text:
            text_result = await self._browser.extract_text(url)
            if text_result.success:
                analysis.text_content = text_result.output

        analysis.success = bool(analysis.screenshot_b64 or analysis.text_content)
        if not analysis.success:
            analysis.error = "Could not extract content from URL"

        return analysis

    async def analyze_multiple(
        self, urls: list[str], question: str = ""
    ) -> list[PageAnalysis]:
        """Analyze multiple URLs concurrently."""
        import asyncio
        tasks = [
            self.analyze_url(url, question=question or "What is on this page?")
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
