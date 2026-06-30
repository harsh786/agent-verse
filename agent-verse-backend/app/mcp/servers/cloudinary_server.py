"""Cloudinary MCP server — media upload, transformation, and management.

Environment:
  CLOUDINARY_CLOUD_NAME: Cloudinary cloud name
  CLOUDINARY_API_KEY:    Cloudinary API key
  CLOUDINARY_API_SECRET: Cloudinary API secret
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "cloudinary_upload_image",
        "description": "Upload an image to Cloudinary from a URL or base64 string",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Public URL or base64 data URI"},
                "public_id": {"type": "string", "description": "Optional custom public ID"},
                "folder": {"type": "string", "description": "Target folder path"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to apply",
                },
                "overwrite": {"type": "boolean", "default": True},
            },
            "required": ["file"],
        },
    },
    {
        "name": "cloudinary_list_resources",
        "description": "List media resources (images, videos, raw files) in Cloudinary",
        "parameters": {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": ["image", "video", "raw"],
                    "default": "image",
                },
                "max_results": {"type": "integer", "default": 30},
                "prefix": {"type": "string"},
                "folder": {"type": "string"},
            },
        },
    },
    {
        "name": "cloudinary_delete_resource",
        "description": "Delete a media resource from Cloudinary",
        "parameters": {
            "type": "object",
            "properties": {
                "public_id": {"type": "string"},
                "resource_type": {
                    "type": "string",
                    "enum": ["image", "video", "raw"],
                    "default": "image",
                },
            },
            "required": ["public_id"],
        },
    },
    {
        "name": "cloudinary_transform_image",
        "description": "Generate a transformed image URL (resize, crop, effects)",
        "parameters": {
            "type": "object",
            "properties": {
                "public_id": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "crop": {
                    "type": "string",
                    "enum": ["fill", "fit", "crop", "scale", "thumb"],
                    "default": "fill",
                },
                "format": {"type": "string", "default": "jpg"},
                "quality": {"type": "string", "default": "auto"},
                "effect": {"type": "string", "description": "e.g. grayscale, sepia"},
            },
            "required": ["public_id"],
        },
    },
    {
        "name": "cloudinary_list_folders",
        "description": "List folders in Cloudinary",
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "default": "", "description": "Parent folder path"},
            },
        },
    },
    {
        "name": "cloudinary_get_usage_stats",
        "description": "Get account usage statistics (storage, bandwidth, requests)",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Optional date YYYY-MM-DD"},
            },
        },
    },
]


def _base() -> str:
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    return f"https://api.cloudinary.com/v1_1/{cloud_name}"


def _sign(params: dict[str, Any], api_secret: str) -> str:
    """Generate Cloudinary API signature."""
    sorted_params = "&".join(
        f"{k}={v}"
        for k, v in sorted(params.items())
        if k not in ("file", "api_key", "resource_type", "cloud_name")
    )
    return hashlib.sha256(f"{sorted_params}{api_secret}".encode()).hexdigest()


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("CLOUDINARY_API_KEY", "")
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "")
    if not api_key:
        return {"error": "CLOUDINARY_API_KEY not configured"}

    base = _base()

    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            if tool_name == "cloudinary_upload_image":
                timestamp = int(time.time())
                params: dict[str, Any] = {"timestamp": timestamp}
                if arguments.get("public_id"):
                    params["public_id"] = arguments["public_id"]
                if arguments.get("folder"):
                    params["folder"] = arguments["folder"]
                if arguments.get("overwrite") is not None:
                    params["overwrite"] = str(arguments["overwrite"]).lower()
                if arguments.get("tags"):
                    params["tags"] = ",".join(arguments["tags"])
                params["signature"] = _sign(params, api_secret)
                params["api_key"] = api_key
                params["file"] = arguments["file"]
                r = await c.post(f"{base}/image/upload", data=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "public_id": data.get("public_id"),
                    "url": data.get("secure_url"),
                    "format": data.get("format"),
                    "width": data.get("width"),
                    "height": data.get("height"),
                    "bytes": data.get("bytes"),
                }

            elif tool_name == "cloudinary_list_resources":
                resource_type = arguments.get("resource_type", "image")
                params = {
                    "max_results": arguments.get("max_results", 30),
                    "resource_type": resource_type,
                }
                if arguments.get("prefix"):
                    params["prefix"] = arguments["prefix"]
                if arguments.get("folder"):
                    params["prefix"] = arguments["folder"]
                r = await c.get(
                    f"{base}/resources/{resource_type}",
                    params=params,
                    auth=(api_key, api_secret),
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "resources": [
                        {
                            "public_id": res.get("public_id"),
                            "url": res.get("secure_url"),
                            "format": res.get("format"),
                            "bytes": res.get("bytes"),
                        }
                        for res in data.get("resources", [])
                    ]
                }

            elif tool_name == "cloudinary_delete_resource":
                resource_type = arguments.get("resource_type", "image")
                timestamp = int(time.time())
                params = {
                    "public_id": arguments["public_id"],
                    "timestamp": timestamp,
                }
                params["signature"] = _sign(params, api_secret)
                params["api_key"] = api_key
                r = await c.post(
                    f"{base}/{resource_type}/destroy",
                    data=params,
                )
                r.raise_for_status()
                data = r.json()
                return {"result": data.get("result"), "deleted": data.get("result") == "ok"}

            elif tool_name == "cloudinary_transform_image":
                cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
                public_id = arguments["public_id"]
                transforms = []
                if arguments.get("width"):
                    transforms.append(f"w_{arguments['width']}")
                if arguments.get("height"):
                    transforms.append(f"h_{arguments['height']}")
                if arguments.get("crop"):
                    transforms.append(f"c_{arguments.get('crop', 'fill')}")
                if arguments.get("quality"):
                    transforms.append(f"q_{arguments.get('quality', 'auto')}")
                if arguments.get("effect"):
                    transforms.append(f"e_{arguments['effect']}")
                transform_str = ",".join(transforms)
                fmt = arguments.get("format", "jpg")
                url = f"https://res.cloudinary.com/{cloud_name}/image/upload/{transform_str}/{public_id}.{fmt}"
                return {"url": url, "public_id": public_id, "transforms": transforms}

            elif tool_name == "cloudinary_list_folders":
                folder = arguments.get("folder", "")
                endpoint = f"{base}/folders/{folder}" if folder else f"{base}/folders"
                r = await c.get(endpoint, auth=(api_key, api_secret))
                r.raise_for_status()
                data = r.json()
                return {"folders": data.get("folders", [])}

            elif tool_name == "cloudinary_get_usage_stats":
                endpoint = f"{base}/usage"
                params = {}
                if arguments.get("date"):
                    params["date"] = arguments["date"]
                r = await c.get(endpoint, auth=(api_key, api_secret), params=params)
                r.raise_for_status()
                return r.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("cloudinary_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
