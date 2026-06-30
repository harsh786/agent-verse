"""Google Slides MCP server — presentation management via Google Slides API v1.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 bearer token with presentations scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SLIDES_BASE = "https://slides.googleapis.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "slides_list_presentations",
        "description": "List Google Slides presentations in Drive",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "slides_create_presentation",
        "description": "Create a new Google Slides presentation",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "slides_get_slide",
        "description": "Get details of a specific slide in a presentation",
        "parameters": {
            "type": "object",
            "properties": {
                "presentation_id": {"type": "string"},
                "slide_index": {
                    "type": "integer",
                    "default": 0,
                    "description": "Zero-based slide index",
                },
            },
            "required": ["presentation_id"],
        },
    },
    {
        "name": "slides_add_slide",
        "description": "Add a new slide to a presentation",
        "parameters": {
            "type": "object",
            "properties": {
                "presentation_id": {"type": "string"},
                "insertion_index": {"type": "integer", "default": 0},
                "layout": {
                    "type": "string",
                    "enum": [
                        "BLANK",
                        "CAPTION_ONLY",
                        "TITLE",
                        "TITLE_AND_BODY",
                        "TITLE_AND_TWO_COLUMNS",
                        "TITLE_ONLY",
                        "SECTION_HEADER",
                        "SECTION_TITLE_AND_DESCRIPTION",
                        "ONE_COLUMN_TEXT",
                        "MAIN_POINT",
                        "BIG_NUMBER",
                    ],
                    "default": "BLANK",
                },
            },
            "required": ["presentation_id"],
        },
    },
    {
        "name": "slides_update_text",
        "description": "Replace text in a presentation shape/placeholder",
        "parameters": {
            "type": "object",
            "properties": {
                "presentation_id": {"type": "string"},
                "find_text": {"type": "string", "description": "Text to find and replace"},
                "replace_text": {"type": "string", "description": "Replacement text"},
                "match_case": {"type": "boolean", "default": False},
            },
            "required": ["presentation_id", "find_text", "replace_text"],
        },
    },
    {
        "name": "slides_export_presentation",
        "description": "Get an export URL for a Google Slides presentation",
        "parameters": {
            "type": "object",
            "properties": {
                "presentation_id": {"type": "string"},
                "export_format": {
                    "type": "string",
                    "enum": ["pdf", "pptx", "odp", "txt"],
                    "default": "pdf",
                },
            },
            "required": ["presentation_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "slides_list_presentations":
                r = await c.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers=hdrs,
                    params={
                        "q": "mimeType='application/vnd.google-apps.presentation'",
                        "pageSize": arguments.get("max_results", 20),
                        "fields": "files(id,name,createdTime,modifiedTime)",
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "presentations": [
                        {
                            "id": f.get("id"),
                            "name": f.get("name"),
                            "created_time": f.get("createdTime"),
                            "modified_time": f.get("modifiedTime"),
                        }
                        for f in data.get("files", [])
                    ]
                }

            elif tool_name == "slides_create_presentation":
                r = await c.post(
                    f"{SLIDES_BASE}/presentations",
                    headers=hdrs,
                    json={"title": arguments["title"]},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "presentation_id": data.get("presentationId"),
                    "title": data.get("title"),
                    "slide_count": len(data.get("slides", [])),
                    "created": True,
                }

            elif tool_name == "slides_get_slide":
                pres_id = arguments["presentation_id"]
                r = await c.get(
                    f"{SLIDES_BASE}/presentations/{pres_id}",
                    headers=hdrs,
                    params={"fields": "slides,title"},
                )
                r.raise_for_status()
                data = r.json()
                slides = data.get("slides", [])
                idx = arguments.get("slide_index", 0)
                if idx >= len(slides):
                    return {"error": f"Slide index {idx} out of range (total: {len(slides)})"}
                slide = slides[idx]
                return {
                    "slide_id": slide.get("objectId"),
                    "index": idx,
                    "total_slides": len(slides),
                    "elements": [
                        {
                            "id": el.get("objectId"),
                            "type": el.get("shape", {}).get("shapeType"),
                        }
                        for el in slide.get("pageElements", [])
                    ],
                }

            elif tool_name == "slides_add_slide":
                pres_id = arguments["presentation_id"]
                r = await c.post(
                    f"{SLIDES_BASE}/presentations/{pres_id}:batchUpdate",
                    headers=hdrs,
                    json={
                        "requests": [
                            {
                                "insertSlide": {
                                    "insertionIndex": arguments.get("insertion_index", 0),
                                    "slideLayoutReference": {
                                        "predefinedLayout": arguments.get("layout", "BLANK")
                                    },
                                }
                            }
                        ]
                    },
                )
                r.raise_for_status()
                return {
                    "presentation_id": pres_id,
                    "inserted_at": arguments.get("insertion_index", 0),
                    "added": True,
                }

            elif tool_name == "slides_update_text":
                pres_id = arguments["presentation_id"]
                r = await c.post(
                    f"{SLIDES_BASE}/presentations/{pres_id}:batchUpdate",
                    headers=hdrs,
                    json={
                        "requests": [
                            {
                                "replaceAllText": {
                                    "containsText": {
                                        "text": arguments["find_text"],
                                        "matchCase": arguments.get("match_case", False),
                                    },
                                    "replaceText": arguments["replace_text"],
                                }
                            }
                        ]
                    },
                )
                r.raise_for_status()
                data = r.json()
                replies = data.get("replies", [{}])
                occurrences = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0)
                return {
                    "presentation_id": pres_id,
                    "occurrences_changed": occurrences,
                    "updated": True,
                }

            elif tool_name == "slides_export_presentation":
                pres_id = arguments["presentation_id"]
                fmt = arguments.get("export_format", "pdf")
                mime_map = {
                    "pdf": "application/pdf",
                    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "odp": "application/vnd.oasis.opendocument.presentation",
                    "txt": "text/plain",
                }
                mime_type = mime_map.get(fmt, "application/pdf")
                export_url = f"https://www.googleapis.com/drive/v3/files/{pres_id}/export?mimeType={mime_type}"
                return {
                    "presentation_id": pres_id,
                    "export_format": fmt,
                    "export_url": export_url,
                    "note": "Use the export_url with Authorization header to download",
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("slides_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
