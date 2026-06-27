"""Microsoft OneDrive MCP server — file management via Microsoft Graph API.

Environment variables:
  ONEDRIVE_ACCESS_TOKEN: Microsoft Graph OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

TOOL_DEFINITIONS = [
    {
        "name": "onedrive_list_root",
        "description": "List items at the root of the signed-in user's OneDrive",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "default": 100, "description": "Max items to return"},
            },
        },
    },
    {
        "name": "onedrive_list_folder",
        "description": "List items inside a OneDrive folder by item ID or path",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "OneDrive item ID"},
                "path": {"type": "string", "description": "Path relative to root e.g. /Documents/reports"},
                "top": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "onedrive_get_item",
        "description": "Get metadata for a OneDrive item by ID or path",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
    {
        "name": "onedrive_download_file",
        "description": "Download a OneDrive file and return base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
    {
        "name": "onedrive_upload_file",
        "description": "Upload a small file (<4MB) to OneDrive from base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Destination path including filename e.g. /Documents/report.pdf",
                },
                "content_base64": {"type": "string"},
                "content_type": {"type": "string", "default": "application/octet-stream"},
                "conflict_behavior": {
                    "type": "string",
                    "enum": ["fail", "replace", "rename"],
                    "default": "replace",
                },
            },
            "required": ["path", "content_base64"],
        },
    },
    {
        "name": "onedrive_delete_item",
        "description": "Delete a OneDrive file or folder",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
    {
        "name": "onedrive_create_folder",
        "description": "Create a new folder in OneDrive",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_item_id": {"type": "string", "description": "Parent folder item ID ('root' for root)"},
                "parent_path": {"type": "string", "description": "Parent folder path"},
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "onedrive_search",
        "description": "Search for files and folders in OneDrive",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "onedrive_share_item",
        "description": "Create a sharing link for a OneDrive item",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "link_type": {
                    "type": "string",
                    "enum": ["view", "edit", "embed"],
                    "default": "view",
                },
                "scope": {
                    "type": "string",
                    "enum": ["anonymous", "organization"],
                    "default": "anonymous",
                },
                "expiration_date_time": {
                    "type": "string",
                    "description": "ISO8601 expiry (e.g. 2024-12-31T23:59:59Z)",
                },
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "onedrive_move_item",
        "description": "Move a OneDrive item to a different folder",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "destination_folder_id": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["item_id", "destination_folder_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _item_url(item_id: str | None, path: str | None) -> str:
    if item_id:
        return f"{GRAPH_BASE}/me/drive/items/{item_id}"
    if path:
        clean_path = path.lstrip("/")
        return f"{GRAPH_BASE}/me/drive/root:/{clean_path}:"
    return f"{GRAPH_BASE}/me/drive/root"


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("ONEDRIVE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "ONEDRIVE_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            if tool_name == "onedrive_list_root":
                r = await c.get(
                    f"{GRAPH_BASE}/me/drive/root/children",
                    headers=hdrs,
                    params={"$top": arguments.get("top", 100)},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "onedrive_list_folder":
                item_id = arguments.get("item_id")
                path = arguments.get("path")
                if item_id:
                    url = f"{GRAPH_BASE}/me/drive/items/{item_id}/children"
                elif path:
                    clean = path.lstrip("/")
                    url = f"{GRAPH_BASE}/me/drive/root:/{clean}:/children"
                else:
                    url = f"{GRAPH_BASE}/me/drive/root/children"
                r = await c.get(url, headers=hdrs, params={"$top": arguments.get("top", 100)})
                r.raise_for_status()
                return r.json()

            elif tool_name == "onedrive_get_item":
                item_id = arguments.get("item_id")
                path = arguments.get("path")
                url = _item_url(item_id, path)
                r = await c.get(url, headers=hdrs)
                r.raise_for_status()
                return r.json()

            elif tool_name == "onedrive_download_file":
                import base64

                item_id = arguments.get("item_id")
                path = arguments.get("path")
                url = _item_url(item_id, path) + "/content"
                r = await c.get(url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=True)
                r.raise_for_status()
                return {
                    "size_bytes": len(r.content),
                    "content_base64": base64.b64encode(r.content).decode(),
                }

            elif tool_name == "onedrive_upload_file":
                import base64

                path = arguments["path"].lstrip("/")
                content = base64.b64decode(arguments["content_base64"])
                content_type = arguments.get("content_type", "application/octet-stream")
                conflict = arguments.get("conflict_behavior", "replace")
                url = f"{GRAPH_BASE}/me/drive/root:/{path}:/content"
                r = await c.put(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": content_type,
                        "@microsoft.graph.conflictBehavior": conflict,
                    },
                    content=content,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "onedrive_delete_item":
                item_id = arguments.get("item_id")
                path = arguments.get("path")
                url = _item_url(item_id, path)
                r = await c.delete(url, headers=hdrs)
                return {"success": r.status_code == 204}

            elif tool_name == "onedrive_create_folder":
                parent_id = arguments.get("parent_item_id", "root")
                parent_path = arguments.get("parent_path")
                if parent_path:
                    clean = parent_path.lstrip("/")
                    url = f"{GRAPH_BASE}/me/drive/root:/{clean}:/children"
                else:
                    url = f"{GRAPH_BASE}/me/drive/items/{parent_id}/children"
                body: dict[str, Any] = {
                    "name": arguments["name"],
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "rename",
                }
                r = await c.post(url, headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "onedrive_search":
                r = await c.get(
                    f"{GRAPH_BASE}/me/drive/root/search(q='{arguments['query']}')",
                    headers=hdrs,
                    params={"$top": arguments.get("top", 20)},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "onedrive_share_item":
                item_id = arguments["item_id"]
                body = {
                    "type": arguments.get("link_type", "view"),
                    "scope": arguments.get("scope", "anonymous"),
                }
                if exp := arguments.get("expiration_date_time"):
                    body["expirationDateTime"] = exp
                r = await c.post(
                    f"{GRAPH_BASE}/me/drive/items/{item_id}/createLink",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "onedrive_move_item":
                item_id = arguments["item_id"]
                dest_id = arguments["destination_folder_id"]
                body = {"parentReference": {"id": dest_id}}
                if new_name := arguments.get("new_name"):
                    body["name"] = new_name
                r = await c.patch(
                    f"{GRAPH_BASE}/me/drive/items/{item_id}",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("onedrive_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
