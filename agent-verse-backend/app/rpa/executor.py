"""RPA executor — executes browser automation commands via Playwright or simulation fallback."""
from __future__ import annotations

import asyncio
import base64
import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class RPAResult:
    success: bool
    output: str = ""
    artifact_url: str | None = None  # base64 screenshot data URI or storage URI
    artifact_name: str | None = None
    duration_ms: float = 0.0
    error: str | None = None


class RPAExecutor:
    """Executes RPA tool calls. Uses Playwright when available, falls back to simulation."""

    def __init__(
        self,
        artifact_store: Any = None,
        session_manager: Any = None,
        headless: bool = True,
        vision_provider: Any = None,
    ) -> None:
        self._playwright_available = self._check_playwright()
        self._headless = headless
        self._artifact_store = artifact_store
        self._session_manager = session_manager
        self._vision_provider = vision_provider

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright  # noqa: F401

            return True
        except ImportError:
            return False

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str | None = None,
        tenant_id: str = "",
        goal_id: str = "",
    ) -> RPAResult:
        """Execute an RPA tool command."""
        start = time.monotonic()
        sid = session_id or uuid.uuid4().hex
        ephemeral = session_id is None

        if self._playwright_available and self._session_manager:
            result = await self._execute_with_playwright(
                tool_name=tool_name,
                arguments=arguments,
                session_id=sid,
                tenant_id=tenant_id,
                goal_id=goal_id,
            )
        elif self._playwright_available:
            result = await self._execute_playwright_standalone(
                tool_name=tool_name,
                arguments=arguments,
                goal_id=goal_id,
            )
        else:
            result = await self._execute_simulation(
                tool_name=tool_name, arguments=arguments
            )

        if ephemeral and self._session_manager:
            await self._session_manager.close(sid, tenant_id)

        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    async def _execute_with_playwright(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
        tenant_id: str,
        goal_id: str,
    ) -> RPAResult:
        """Execute using a stateful Playwright session from session_manager."""
        session = await self._session_manager.get_or_create(session_id, tenant_id)
        page = session.page

        if page is None:
            return await self._execute_simulation(
                tool_name=tool_name, arguments=arguments
            )

        try:
            url = arguments.get("url", "")

            if tool_name == "rpa_open_url":
                if not url:
                    return RPAResult(success=False, error="url argument required")
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                session.current_url = url
                session.touch()
                return RPAResult(
                    success=True,
                    output=f"Navigated to {url} — title: {await page.title()}",
                )

            elif tool_name == "rpa_click":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                selector = arguments.get("selector", "")
                text = arguments.get("text", "")
                try:
                    if text and not selector:
                        await page.get_by_text(text, exact=False).first.click(
                            timeout=5000
                        )
                    elif selector:
                        await page.click(selector, timeout=5000)
                    else:
                        return RPAResult(
                            success=False, error="selector or text required"
                        )
                    screenshot = base64.b64encode(await page.screenshot()).decode()
                    session.touch()
                    return RPAResult(
                        success=True,
                        output=f"Clicked: {selector or text}",
                        artifact_url=f"data:image/png;base64,{screenshot}",
                    )
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_type":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                selector = arguments.get("selector", "")
                text_to_type = arguments.get("text", "")
                if not selector:
                    return RPAResult(
                        success=False, error="selector required for rpa_type"
                    )
                try:
                    await page.fill(selector, text_to_type, timeout=5000)
                    session.touch()
                    return RPAResult(
                        success=True, output=f"Typed into {selector}"
                    )
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_extract_text":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                selector = arguments.get("selector", "body")
                try:
                    text = await page.inner_text(selector, timeout=5000)
                    session.touch()
                    return RPAResult(success=True, output=text[:5000])
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_screenshot":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                name = arguments.get("name", "screenshot")
                screenshot_bytes = await page.screenshot()
                b64 = base64.b64encode(screenshot_bytes).decode()

                # Persist to artifact store if available
                artifact_url = f"data:image/png;base64,{b64}"
                artifact_name = f"{name}.png"

                if self._artifact_store and goal_id:
                    try:
                        artifact = await self._artifact_store.write_bytes(
                            goal_id=goal_id,
                            name=artifact_name,
                            content=screenshot_bytes,
                        )
                        artifact_url = artifact.uri
                        artifact_name = artifact.name
                    except Exception:
                        pass  # Fall back to base64 in response

                # Analyze screenshot with vision provider if available
                vision_analysis = ""
                if self._vision_provider:
                    try:
                        from app.perception.browser_agent import BrowserAgent as _BA
                        _ba = _BA(vision_provider=self._vision_provider)
                        vision_analysis = await _ba.analyze_screenshot(
                            b64,
                            "Describe the main content and purpose of this page.",
                        )
                    except Exception:
                        pass

                output = f"Screenshot captured: {name}"
                if vision_analysis:
                    output += f"\nVision analysis: {vision_analysis}"

                session.touch()
                return RPAResult(
                    success=True,
                    output=output,
                    artifact_url=artifact_url,
                    artifact_name=artifact_name,
                )

            else:
                return await self._execute_simulation(
                    tool_name=tool_name, arguments=arguments
                )

        except Exception as exc:
            return RPAResult(success=False, error=str(exc))

    async def _execute_playwright_standalone(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        goal_id: str = "",
    ) -> RPAResult:
        """Execute using a short-lived Playwright browser (no session manager).

        All 5 RPA tools are fully supported. Browser is opened and closed per call.
        For stateful multi-step workflows, use execute() with a session_id instead.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return await self._execute_simulation(tool_name=tool_name, arguments=arguments)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="AgentVerse-RPA/1.0",
                )
                page = await context.new_page()

                url = arguments.get("url", "")

                if tool_name == "rpa_open_url":
                    if not url:
                        return RPAResult(success=False, error="url argument required")
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    return RPAResult(
                        success=True,
                        output=f"Opened {url} — title: {await page.title()}",
                    )

                elif tool_name == "rpa_click":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    selector = arguments.get("selector", "")
                    text = arguments.get("text", "")
                    if text and not selector:
                        await page.get_by_text(text, exact=False).first.click(
                            timeout=5000
                        )
                    elif selector:
                        await page.click(selector, timeout=5000)
                    else:
                        return RPAResult(
                            success=False,
                            error="selector or text required for rpa_click",
                        )
                    screenshot = base64.b64encode(await page.screenshot()).decode()
                    return RPAResult(
                        success=True,
                        output=f"Clicked: {selector or text}",
                        artifact_url=f"data:image/png;base64,{screenshot}",
                    )

                elif tool_name == "rpa_type":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    selector = arguments.get("selector", "")
                    text_to_type = arguments.get("text", "")
                    if not selector:
                        return RPAResult(
                            success=False,
                            error="selector required for rpa_type",
                        )
                    await page.fill(selector, text_to_type, timeout=5000)
                    return RPAResult(success=True, output=f"Typed into {selector}")

                elif tool_name == "rpa_extract_text":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    selector = arguments.get("selector", "body")
                    text = await page.inner_text(selector, timeout=5000)
                    return RPAResult(success=True, output=text[:5000])

                elif tool_name == "rpa_screenshot":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    name = arguments.get("name", "screenshot")
                    screenshot_bytes = await page.screenshot()
                    b64 = base64.b64encode(screenshot_bytes).decode()

                    artifact_url = f"data:image/png;base64,{b64}"
                    artifact_name = f"{name}.png"

                    if self._artifact_store and goal_id:
                        try:
                            artifact = await self._artifact_store.write_bytes(
                                goal_id=goal_id,
                                name=artifact_name,
                                content=screenshot_bytes,
                            )
                            artifact_url = artifact.uri
                            artifact_name = artifact.name
                        except Exception:
                            pass

                    return RPAResult(
                        success=True,
                        output=f"Screenshot: {name}",
                        artifact_url=artifact_url,
                        artifact_name=artifact_name,
                    )

                else:
                    return await self._execute_simulation(
                        tool_name=tool_name, arguments=arguments
                    )

            except Exception as exc:
                return RPAResult(success=False, error=str(exc))
            finally:
                await browser.close()

    async def _execute_simulation(
        self, *, tool_name: str, arguments: dict[str, Any]
    ) -> RPAResult:
        """Simulated execution when Playwright is not available."""
        # Add small delay to simulate real execution
        await asyncio.sleep(0.1)

        sim_outputs = {
            "rpa_open_url": lambda a: f"[simulated] Opened URL: {a.get('url', '?')}",
            "rpa_click": lambda a: f"[simulated] Clicked: {a.get('selector') or a.get('text', '?')}",
            "rpa_type": lambda a: f"[simulated] Typed '{a.get('text', '')}' into {a.get('selector', '?')}",
            "rpa_extract_text": lambda a: f"[simulated] Extracted text from {a.get('selector', 'body')}: <simulated page content>",
            "rpa_screenshot": lambda a: f"[simulated] Screenshot captured: {a.get('name', 'screenshot')}",
        }

        output_fn = sim_outputs.get(tool_name)
        if output_fn:
            return RPAResult(success=True, output=output_fn(arguments))
        return RPAResult(success=False, error=f"Unknown RPA tool: {tool_name}")
