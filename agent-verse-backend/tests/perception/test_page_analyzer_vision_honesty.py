"""PageAnalyzer must never report the "no vision" string as an analysis.

Regression: ``analyze_url`` only checked ``browser._vision is not None``. A
configured provider without vision support made ``analyze_screenshot`` return
"No vision provider configured." (and a provider error returned
"Vision analysis failed: ..."), which was stored as ``llm_analysis`` and served
by /perception/batch-analyze as a successful analysis.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.perception.browser_agent import BrowserAgent, BrowserResult
from app.perception.page_analyzer import PageAnalyzer, VisionUnavailableError


class _Provider:
    def __init__(self, *, vision: bool, fail: bool = False) -> None:
        self._vision = vision
        self._fail = fail

    def supports_vision(self) -> bool:
        return self._vision

    async def complete(self, request: Any) -> Any:
        if self._fail:
            raise RuntimeError("provider rate limited")
        from app.providers.base import CompletionResponse

        return CompletionResponse(content="A pricing table.", model=request.model)


def _agent(provider: Any) -> BrowserAgent:
    agent = BrowserAgent(vision_provider=provider)
    agent.take_screenshot = AsyncMock(  # type: ignore[method-assign]
        return_value=BrowserResult(success=True, action="screenshot", screenshot_b64="img")
    )
    agent.extract_text = AsyncMock(  # type: ignore[method-assign]
        return_value=BrowserResult(success=True, action="extract_text", output="page text")
    )
    return agent


async def test_non_vision_provider_never_yields_placeholder_analysis() -> None:
    analyzer = PageAnalyzer(browser_agent=_agent(_Provider(vision=False)))

    result = await analyzer.analyze_url("https://example.com")

    assert result.llm_analysis == ""
    assert "No vision provider" not in result.to_context_block()
    assert result.metadata["vision"] == "unavailable"


async def test_require_vision_without_provider_raises_before_browsing() -> None:
    agent = _agent(None)
    analyzer = PageAnalyzer(browser_agent=agent)

    assert analyzer.vision_available is False
    with pytest.raises(VisionUnavailableError):
        await analyzer.analyze_multiple(["https://a.com"], require_vision=True)
    agent.take_screenshot.assert_not_called()


async def test_require_vision_with_non_vision_provider_raises() -> None:
    analyzer = PageAnalyzer(browser_agent=_agent(_Provider(vision=False)))

    with pytest.raises(VisionUnavailableError):
        await analyzer.analyze_url("https://a.com", require_vision=True)


async def test_vision_provider_failure_is_an_error_not_the_analysis() -> None:
    analyzer = PageAnalyzer(browser_agent=_agent(_Provider(vision=True, fail=True)))

    result = await analyzer.analyze_url("https://example.com", require_vision=True)

    assert result.llm_analysis == ""
    assert result.success is False
    assert "provider rate limited" in result.error
    assert result.metadata["vision"] == "failed"


async def test_vision_provider_success_is_reported_as_analysis() -> None:
    analyzer = PageAnalyzer(browser_agent=_agent(_Provider(vision=True)))

    [result] = await analyzer.analyze_multiple(["https://example.com"], require_vision=True)

    assert result.success is True
    assert result.llm_analysis == "A pricing table."
    assert result.metadata["vision"] == "ok"
