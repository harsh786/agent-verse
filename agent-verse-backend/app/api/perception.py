"""Perception API — browser screenshots, image analysis, multimodal goal input."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter(prefix="/perception", tags=["perception"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _browser_agent(request: Request) -> Any:
    """Return browser agent from app.state, creating lazily if needed."""
    agent = getattr(request.app.state, "browser_agent", None)
    if agent is None:
        from app.perception.browser_agent import BrowserAgent
        agent = BrowserAgent()
        request.app.state.browser_agent = agent
    return agent


def _page_analyzer(request: Request) -> Any:
    analyzer = getattr(request.app.state, "page_analyzer", None)
    if analyzer is None:
        from app.perception.page_analyzer import PageAnalyzer
        analyzer = PageAnalyzer(browser_agent=_browser_agent(request))
        request.app.state.page_analyzer = analyzer
    return analyzer


@router.get("/status")
async def get_perception_status(request: Request) -> dict[str, Any]:
    """Return Playwright availability and vision provider status."""
    _require_tenant(request)
    from app.perception.browser_agent import _PLAYWRIGHT_AVAILABLE
    vision_provider = getattr(request.app.state, "embedder", None)
    return {
        "playwright_available": _PLAYWRIGHT_AVAILABLE,
        "vision_available": vision_provider is not None,
        "browser_actions": ["screenshot", "extract_text", "click", "fill", "navigate"],
        "image_formats": ["png", "jpeg", "webp"],
    }


class ScreenshotRequest(BaseModel):
    url: str
    full_page: bool = False


@router.post("/screenshot")
async def capture_screenshot(request: Request, body: ScreenshotRequest) -> dict[str, Any]:
    """Capture a headless browser screenshot of a URL."""
    _require_tenant(request)
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    agent = _browser_agent(request)
    result = await agent.take_screenshot(body.url)
    return {
        "success": result.success,
        "url": body.url,
        "screenshot_b64": result.screenshot_b64,
        "error": result.error or None,
    }


class AnalyzeRequest(BaseModel):
    screenshot_b64: str = ""
    url: str = ""
    question: str = "What is the main purpose and content of this page?"


@router.post("/analyze")
async def analyze_page(request: Request, body: AnalyzeRequest) -> dict[str, Any]:
    """Analyze a screenshot or URL with the vision LLM."""
    _require_tenant(request)

    agent = _browser_agent(request)

    screenshot_b64 = body.screenshot_b64
    if not screenshot_b64 and body.url:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
        ss_result = await agent.take_screenshot(body.url)
        if not ss_result.success:
            raise HTTPException(status_code=502, detail=f"Screenshot failed: {ss_result.error}")
        screenshot_b64 = ss_result.screenshot_b64

    if not screenshot_b64:
        raise HTTPException(status_code=400, detail="Either screenshot_b64 or url is required")

    analysis = await agent.analyze_screenshot(screenshot_b64, body.question)
    return {
        "analysis": analysis,
        "question": body.question,
        "screenshot_provided": bool(body.screenshot_b64),
    }


class ExtractRequest(BaseModel):
    url: str
    selector: str = "body"


@router.post("/extract")
async def extract_text(request: Request, body: ExtractRequest) -> dict[str, Any]:
    """Extract visible text content from a URL."""
    _require_tenant(request)
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    agent = _browser_agent(request)
    result = await agent.extract_text(body.url, body.selector)
    return {
        "success": result.success,
        "url": body.url,
        "selector": body.selector,
        "text": result.output if result.success else "",
        "char_count": len(result.output) if result.success else 0,
        "error": result.error or None,
    }


class GoalWithImageRequest(BaseModel):
    goal: str
    image_b64: str = ""          # Base64 image data (without data URI prefix)
    image_url: str = ""          # URL to screenshot and attach
    image_description: str = ""  # Human description of the image
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None


@router.post("/goal-with-image", status_code=202)
async def submit_goal_with_image(
    request: Request, body: GoalWithImageRequest
) -> dict[str, Any]:
    """Submit a goal with an image attachment for multimodal agent execution."""
    tenant = _require_tenant(request)

    # Build the enriched goal text
    enriched_goal = body.goal
    image_context = ""

    if body.image_url:
        if not body.image_url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400, detail="image_url must start with http:// or https://"
            )
        # Capture screenshot of the URL
        agent = _browser_agent(request)
        ss_result = await agent.take_screenshot(body.image_url)
        if ss_result.success:
            image_context = f"\n[Visual context: screenshot of {body.image_url}]"
            if ss_result.screenshot_b64:
                # Analyze the screenshot if vision is available
                vision_text = await agent.analyze_screenshot(
                    ss_result.screenshot_b64,
                    f"Briefly describe what you see on this page that is relevant to: {body.goal}",
                )
                if vision_text and "No vision provider" not in vision_text:
                    image_context += f"\nPage analysis: {vision_text}"

    if body.image_b64:
        image_context += (
            f"\n[Image attached: {body.image_description or 'user-provided image'}]"
        )

    if image_context:
        enriched_goal = f"{body.goal}\n{image_context}"

    # Submit via GoalService
    goal_svc = getattr(request.app.state, "goal_service", None)
    if goal_svc is None:
        raise HTTPException(status_code=503, detail="GoalService not available")

    result: dict[str, Any] = await goal_svc.submit_goal(
        goal=enriched_goal,
        priority=body.priority,
        dry_run=body.dry_run,
        tenant_ctx=tenant,
        agent_id=body.agent_id,
    )
    result["has_visual_context"] = bool(image_context)
    result["original_goal"] = body.goal
    return result
