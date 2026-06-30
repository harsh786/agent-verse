"""Filestack MCP server — file upload, transformation, and management.

Environment:
  FILESTACK_API_KEY: Filestack API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

FS_BASE = "https://www.filestackapi.com/api"
FS_CDN = "https://cdn.filestackcontent.com"

TOOL_DEFINITIONS = [
    {
        "name": "filestack_upload_file",
        "description": "Upload a file to Filestack from a URL (store URL content)",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Public URL of the file to upload"},
                "filename": {"type": "string", "description": "Optional custom filename"},
                "mimetype": {"type": "string"},
                "path": {"type": "string", "description": "Storage path"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "filestack_transform_image",
        "description": "Apply transformations to an image via Filestack Processing API",
        "parameters": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "Filestack file handle"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "crop": {"type": "boolean", "default": False},
                "format": {"type": "string", "default": "jpg"},
                "quality": {"type": "integer", "default": 80, "description": "1-100"},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "filestack_list_files",
        "description": "List files stored in Filestack cloud storage",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "/"},
                "page_size": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "filestack_delete_file",
        "description": "Delete a file from Filestack by its handle",
        "parameters": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "Filestack file handle"},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "filestack_get_file_info",
        "description": "Get metadata about a file stored in Filestack",
        "parameters": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "filestack_store_url",
        "description": "Store a remote URL as a Filestack file and return its handle",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Source URL to store"},
                "store_location": {
                    "type": "string",
                    "enum": ["s3", "gcs", "azure", "dropbox"],
                    "default": "s3",
                },
            },
            "required": ["url"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("FILESTACK_API_KEY", "")
    if not api_key:
        return {"error": "FILESTACK_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            if tool_name == "filestack_upload_file":
                body: dict[str, Any] = {"url": arguments["url"]}
                if arguments.get("filename"):
                    body["filename"] = arguments["filename"]
                if arguments.get("mimetype"):
                    body["mimetype"] = arguments["mimetype"]
                r = await c.post(f"{FS_BASE}/store/S3?key={api_key}", json=body)
                r.raise_for_status()
                data = r.json()
                return {
                    "handle": data.get("handle"),
                    "url": data.get("url"),
                    "filename": data.get("filename"),
                    "size": data.get("size"),
                }

            elif tool_name == "filestack_transform_image":
                handle = arguments["handle"]
                transforms = []
                if arguments.get("width") or arguments.get("height"):
                    resize = "resize="
                    parts = []
                    if arguments.get("width"):
                        parts.append(f"width:{arguments['width']}")
                    if arguments.get("height"):
                        parts.append(f"height:{arguments['height']}")
                    transforms.append(resize + ",".join(parts))
                if arguments.get("crop"):
                    transforms.append("crop")
                if arguments.get("quality"):
                    transforms.append(f"quality=value:{arguments.get('quality', 80)}")
                transform_str = "/".join(transforms)
                fmt = arguments.get("format", "jpg")
                url = f"{FS_CDN}/{transform_str}/output=format:{fmt}/{handle}"
                return {"url": url, "handle": handle, "transforms": transforms}

            elif tool_name == "filestack_list_files":
                path = arguments.get("path", "/")
                r = await c.get(
                    f"https://cloud.filestackcontent.com/folder/list",
                    params={
                        "apikey": api_key,
                        "path": path,
                        "page_size": arguments.get("page_size", 50),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {"files": data.get("contents", []), "path": path}

            elif tool_name == "filestack_delete_file":
                handle = arguments["handle"]
                r = await c.delete(
                    f"{FS_BASE}/file/{handle}",
                    params={"key": api_key},
                )
                r.raise_for_status()
                return {"deleted": True, "handle": handle}

            elif tool_name == "filestack_get_file_info":
                handle = arguments["handle"]
                r = await c.get(
                    f"{FS_CDN}/fileinfo/{handle}",
                    params={"key": api_key},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "filestack_store_url":
                body = {
                    "url": arguments["url"],
                    "store": {"location": arguments.get("store_location", "s3")},
                }
                r = await c.post(
                    f"https://cloud.filestackcontent.com/store",
                    params={"apikey": api_key},
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "handle": data.get("handle"),
                    "url": data.get("url"),
                    "stored": True,
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("filestack_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
