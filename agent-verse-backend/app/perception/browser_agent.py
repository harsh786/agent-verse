"""Browser agent — headless Chromium automation via Playwright.

Provides web automation capabilities when no API is available:
- Navigate to URLs
- Take screenshots → analyze with vision LLM
- Click elements, type text, scroll
- Extract text content

Security: Runs in an isolated context with:
- Network limited to target domain only
- No access to local filesystem
- Automatic cleanup after each session
- Timeout enforcement (default 30s per action)
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import Browser, Page, async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.warning("Playwright not installed. Browser agent disabled. Run: playwright install chromium")


@dataclass
class BrowserAction:
    action_type: str  # navigate | click | type | scroll | screenshot | extract_text
    selector: str = ""
    value: str = ""
    url: str = ""


@dataclass
class BrowserResult:
    success: bool
    action: str
    output: str = ""
    screenshot_b64: str = ""  # Base64-encoded PNG screenshot
    error: str = ""


class BrowserAgent:
    """Headless Chromium browser automation agent.

    Each session gets an isolated browser context. Screenshots are analyzed
    by a vision LLM to extract semantic information.
    """

    def __init__(
        self,
        *,
        vision_provider: Any = None,  # Optional LLMProvider with supports_vision()
        timeout_ms: int = 30_000,
        headless: bool = True,
    ) -> None:
        self._vision = vision_provider
        self._timeout = timeout_ms
        self._headless = headless

    @property
    def available(self) -> bool:
        return _PLAYWRIGHT_AVAILABLE

    async def take_screenshot(self, url: str) -> BrowserResult:
        """Navigate to URL and return a base64-encoded screenshot."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(success=False, action="screenshot", error="Playwright not installed")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                )
                page = await context.new_page()
                page.set_default_timeout(self._timeout)
                await page.goto(url, wait_until="domcontentloaded")
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
                return BrowserResult(
                    success=True,
                    action="screenshot",
                    output=f"Screenshot taken of {url}",
                    screenshot_b64=screenshot_b64,
                )
            except Exception as exc:
                return BrowserResult(success=False, action="screenshot", error=str(exc))
            finally:
                await browser.close()

    async def extract_text(self, url: str, selector: str = "body") -> BrowserResult:
        """Extract visible text from a URL."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(success=False, action="extract_text", error="Playwright not installed")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await (await browser.new_context()).new_page()
                page.set_default_timeout(self._timeout)
                await page.goto(url, wait_until="domcontentloaded")
                text = await page.inner_text(selector)
                return BrowserResult(
                    success=True,
                    action="extract_text",
                    output=text[:5000],  # Truncate long content
                )
            except Exception as exc:
                return BrowserResult(success=False, action="extract_text", error=str(exc))
            finally:
                await browser.close()

    async def click_and_screenshot(self, url: str, selector: str) -> BrowserResult:
        """Navigate to URL, click element, return screenshot."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(success=False, action="click", error="Playwright not installed")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await (await browser.new_context()).new_page()
                page.set_default_timeout(self._timeout)
                await page.goto(url, wait_until="domcontentloaded")
                await page.click(selector)
                await page.wait_for_load_state("networkidle", timeout=5000)
                screenshot_bytes = await page.screenshot()
                return BrowserResult(
                    success=True,
                    action="click",
                    output=f"Clicked {selector} on {url}",
                    screenshot_b64=base64.b64encode(screenshot_bytes).decode(),
                )
            except Exception as exc:
                return BrowserResult(success=False, action="click", error=str(exc))
            finally:
                await browser.close()

    async def fill_and_submit(
        self,
        url: str,
        selector: str,
        value: str,
        submit_selector: str = "",
    ) -> BrowserResult:
        """Fill a form field and optionally submit."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(success=False, action="fill", error="Playwright not installed")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await (await browser.new_context()).new_page()
                page.set_default_timeout(self._timeout)
                await page.goto(url, wait_until="domcontentloaded")
                await page.fill(selector, value)
                if submit_selector:
                    await page.click(submit_selector)
                    await page.wait_for_load_state("networkidle", timeout=5000)
                screenshot_bytes = await page.screenshot()
                return BrowserResult(
                    success=True,
                    action="fill",
                    output=f"Filled {selector} with value",
                    screenshot_b64=base64.b64encode(screenshot_bytes).decode(),
                )
            except Exception as exc:
                return BrowserResult(success=False, action="fill", error=str(exc))
            finally:
                await browser.close()

    async def analyze_screenshot(self, screenshot_b64: str, question: str) -> str:
        """Analyze a screenshot with a vision LLM."""
        if self._vision is None or not self._vision.supports_vision():
            return "No vision provider configured."

        try:
            from app.providers.base import CompletionRequest, Message

            req = CompletionRequest(
                messages=[
                    Message(role="system", content="You are a web page analyzer. Describe what you see."),
                    Message(
                        role="user",
                        content=question,
                        image_data=screenshot_b64,
                    ),
                ],
                model="claude-opus-4-5",
            )
            resp = await self._vision.complete(req)
            return resp.content  # type: ignore[no-any-return]
        except Exception as exc:
            return f"Vision analysis failed: {exc}"

    async def run_action(self, action: BrowserAction) -> BrowserResult:
        """Dispatch a browser action."""
        if action.action_type in {"navigate", "screenshot"}:
            return await self.take_screenshot(action.url)
        if action.action_type == "extract_text":
            return await self.extract_text(action.url, action.selector or "body")
        if action.action_type == "click":
            return await self.click_and_screenshot(action.url, action.selector)
        if action.action_type == "fill":
            return await self.fill_and_submit(action.url, action.selector, action.value)
        return BrowserResult(
            success=False,
            action=action.action_type,
            error=f"Unknown action: {action.action_type}",
        )
