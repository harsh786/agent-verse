"""Perception module — multimodal inputs, browser automation, page analysis."""
from app.perception.browser_agent import BrowserAction, BrowserAgent, BrowserResult
from app.perception.multimodal import ImageAttachment, PerceptionInput, resize_image_b64
from app.perception.page_analyzer import PageAnalysis, PageAnalyzer

__all__ = [
    "BrowserAction",
    "BrowserAgent",
    "BrowserResult",
    "ImageAttachment",
    "PageAnalysis",
    "PageAnalyzer",
    "PerceptionInput",
    "resize_image_b64",
]
