"""Multimodal perception — handles image + text inputs for goals.

Allows goals to carry visual context (screenshots, diagrams, documents)
that the vision LLM can analyze before planning.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field


@dataclass
class PerceptionInput:
    """Represents a goal with optional visual context."""
    goal_text: str
    images: list[ImageAttachment] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)   # URLs to screenshot before planning

    def has_visual_context(self) -> bool:
        return bool(self.images or self.urls)

    def to_prompt_context(self) -> str:
        """Format for injection into planner system prompt."""
        if not self.has_visual_context():
            return ""
        parts = ["\n## Visual Context"]
        for i, img in enumerate(self.images):
            parts.append(f"- Image {i+1}: {img.description or 'attached image'}")
        for url in self.urls:
            parts.append(f"- URL to analyze: {url}")
        return "\n".join(parts)


@dataclass
class ImageAttachment:
    data_b64: str          # Base64-encoded image data
    mime_type: str = "image/png"
    description: str = ""  # Optional human-provided description
    width: int = 0
    height: int = 0

    @classmethod
    def from_data_uri(cls, data_uri: str, description: str = "") -> ImageAttachment:
        """Parse a data URI like 'data:image/png;base64,...'."""
        if data_uri.startswith("data:"):
            header, data = data_uri.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "")
            return cls(data_b64=data, mime_type=mime_type, description=description)
        return cls(data_b64=data_uri, description=description)

    def to_data_uri(self) -> str:
        return f"data:{self.mime_type};base64,{self.data_b64}"

    def byte_size(self) -> int:
        """Approximate byte size of the decoded image."""
        try:
            return len(base64.b64decode(self.data_b64))
        except Exception:
            return 0


def resize_image_b64(data_b64: str, max_size: int = 1_000_000) -> str:
    """Resize a base64 image if it exceeds max_size bytes. Returns new base64."""
    try:
        raw = base64.b64decode(data_b64)
        if len(raw) <= max_size:
            return data_b64
        # Try to import Pillow for resizing
        try:
            from PIL import Image  # type: ignore[import]
            img = Image.open(io.BytesIO(raw))
            # Scale down proportionally
            ratio = (max_size / len(raw)) ** 0.5
            new_w = max(1, int(img.width * ratio))
            new_h = max(1, int(img.height * ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            # Pillow not installed — return original
            return data_b64
    except Exception:
        return data_b64
