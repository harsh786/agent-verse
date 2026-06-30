"""Yandex MCP server — translation, disk storage, metrica analytics, and geocoding.

Environment:
  YANDEX_API_KEY: Yandex API key for translation and maps
  YANDEX_OAUTH_TOKEN: Yandex OAuth token for Disk and Metrica
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TRANSLATE_URL = "https://translate.api.cloud.yandex.net/translate/v2/translate"
DISK_URL = "https://cloud-api.yandex.net/v1/disk"
METRICA_URL = "https://api-metrika.yandex.net/stat/v1"
MAPS_URL = "https://geocode-maps.yandex.ru/1.x"
SEARCH_URL = "https://yandex.com/search/xml"

TOOL_DEFINITIONS = [
    {
        "name": "yandex_translate",
        "description": "Translate text between languages using Yandex Translate",
        "parameters": {
            "type": "object",
            "properties": {
                "texts": {"type": "array", "description": "Array of text strings to translate", "items": {"type": "string"}},
                "target_language_code": {"type": "string", "description": "Target language code (e.g. en, ru, de)"},
                "source_language_code": {"type": "string", "description": "Source language code (auto-detect if omitted)"},
            },
            "required": ["texts", "target_language_code"],
        },
    },
    {
        "name": "yandex_search",
        "description": "Search the web using Yandex Search API",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "lr": {"type": "integer", "description": "Region code for search results"},
                "limit": {"type": "integer", "description": "Maximum search results to return"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "yandex_list_disk_files",
        "description": "List files and folders in Yandex Disk (cloud storage)",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Disk path to list (default: /)"},
                "limit": {"type": "integer", "description": "Maximum items to return"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "yandex_upload_to_disk",
        "description": "Get an upload URL for uploading a file to Yandex Disk",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Target path on Yandex Disk"},
                "overwrite": {"type": "boolean", "description": "Overwrite if file exists"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "yandex_get_metrica_stats",
        "description": "Get Yandex Metrica website analytics statistics",
        "parameters": {
            "type": "object",
            "properties": {
                "counter_id": {"type": "integer", "description": "Metrica counter ID"},
                "metrics": {"type": "string", "description": "Comma-separated metrics (e.g. ym:s:visits,ym:s:users)"},
                "date1": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "date2": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["counter_id", "metrics"],
        },
    },
    {
        "name": "yandex_geocode",
        "description": "Geocode an address or reverse geocode coordinates using Yandex Maps",
        "parameters": {
            "type": "object",
            "properties": {
                "geocode": {"type": "string", "description": "Address string or 'longitude,latitude' for reverse geocoding"},
                "lang": {"type": "string", "description": "Response language (e.g. en_US, ru_RU)"},
                "results": {"type": "integer", "description": "Maximum results to return"},
            },
            "required": ["geocode"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("YANDEX_API_KEY", "")
    oauth_token = os.getenv("YANDEX_OAUTH_TOKEN", "")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "yandex_translate":
                if not api_key:
                    return {"error": "YANDEX_API_KEY not configured"}
                r = await client.post(
                    TRANSLATE_URL,
                    headers={"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"},
                    json={
                        "texts": arguments["texts"],
                        "targetLanguageCode": arguments["target_language_code"],
                        "sourceLanguageCode": arguments.get("source_language_code"),
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "yandex_search":
                if not api_key:
                    return {"error": "YANDEX_API_KEY not configured"}
                r = await client.get(
                    SEARCH_URL,
                    params={
                        "key": api_key,
                        "query": arguments["query"],
                        "lr": arguments.get("lr", 213),
                        "l10n": "en",
                        "results": arguments.get("limit", 10),
                    },
                )
                r.raise_for_status()
                return {"raw": r.text[:2000]}

            if tool_name == "yandex_list_disk_files":
                if not oauth_token:
                    return {"error": "YANDEX_OAUTH_TOKEN not configured"}
                params: dict[str, Any] = {"path": arguments.get("path", "/")}
                if "limit" in arguments:
                    params["limit"] = arguments["limit"]
                if "offset" in arguments:
                    params["offset"] = arguments["offset"]
                r = await client.get(
                    f"{DISK_URL}/resources",
                    headers={"Authorization": f"OAuth {oauth_token}"},
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "yandex_upload_to_disk":
                if not oauth_token:
                    return {"error": "YANDEX_OAUTH_TOKEN not configured"}
                r = await client.get(
                    f"{DISK_URL}/resources/upload",
                    headers={"Authorization": f"OAuth {oauth_token}"},
                    params={"path": arguments["path"], "overwrite": str(arguments.get("overwrite", False)).lower()},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "yandex_get_metrica_stats":
                if not oauth_token:
                    return {"error": "YANDEX_OAUTH_TOKEN not configured"}
                params = {
                    "id": arguments["counter_id"],
                    "metrics": arguments["metrics"],
                    "date1": arguments.get("date1", "today"),
                    "date2": arguments.get("date2", "today"),
                }
                r = await client.get(
                    f"{METRICA_URL}/data",
                    headers={"Authorization": f"OAuth {oauth_token}"},
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "yandex_geocode":
                params = {
                    "apikey": api_key,
                    "geocode": arguments["geocode"],
                    "format": "json",
                    "lang": arguments.get("lang", "en_US"),
                    "results": arguments.get("results", 1),
                }
                r = await client.get(MAPS_URL, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
