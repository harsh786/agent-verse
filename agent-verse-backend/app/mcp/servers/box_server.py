"""Box MCP server — file management via Box Content API.

Environment variables:
  BOX_ACCESS_TOKEN: Box OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BOX_BASE = "https://api.box.com/2.0"
BOX_UPLOAD_BASE = "https://upload.box.com/api/2.0"

TOOL_DEFINITIONS = [
    {
        "name": "box_list_folder",
        "description": "List items in a Box folder",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "default": "0", "description": "Folder ID (use '0' for root)"},
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
                "fields": {"type": "string", "default": "id,name,type,size,modified_at"},
            },
        },
    },
    {
        "name": "box_get_file",
        "description": "Get metadata for a Box file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "fields": {"type": "string", "default": "id,name,type,size,modified_at,parent,created_by"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "box_download_file",
        "description": "Download a Box file and return base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "box_upload_file",
        "description": "Upload a new file to Box from base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content_base64": {"type": "string"},
                "parent_folder_id": {"type": "string", "default": "0"},
                "content_type": {"type": "string", "default": "application/octet-stream"},
            },
            "required": ["name", "content_base64"],
        },
    },
    {
        "name": "box_delete_file",
        "description": "Delete a Box file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "box_create_folder",
        "description": "Create a new folder in Box",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_folder_id": {"type": "string", "default": "0"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "box_search",
        "description": "Search for files and folders in Box",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "type": {"type": "string", "enum": ["file", "folder", "web_link"], "default": "file"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "ancestor_folder_ids": {
                    "type": "string",
                    "description": "Comma-separated folder IDs to scope search",
                },
                "content_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Search in: name, description, file_content, comments, tags",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "box_create_shared_link",
        "description": "Create a shared link for a Box file or folder",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "item_type": {"type": "string", "enum": ["files", "folders"], "default": "files"},
                "access": {
                    "type": "string",
                    "enum": ["open", "company", "collaborators"],
                    "default": "open",
                },
                "unshared_at": {"type": "string", "description": "ISO8601 expiry date-time"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "box_copy_file",
        "description": "Copy a Box file to another folder",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "destination_folder_id": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["file_id", "destination_folder_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("BOX_ACCESS_TOKEN", "")
    if not token:
        return {"error": "BOX_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            if tool_name == "box_list_folder":
                fid = arguments.get("folder_id", "0")
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 100),
                    "offset": arguments.get("offset", 0),
                    "fields": arguments.get("fields", "id,name,type,size,modified_at"),
                }
                r = await c.get(
                    f"{BOX_BASE}/folders/{fid}/items",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "box_get_file":
                fid = arguments["file_id"]
                params = {"fields": arguments.get("fields", "id,name,type,size,modified_at,parent,created_by")}
                r = await c.get(f"{BOX_BASE}/files/{fid}", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "box_download_file":
                import base64

                fid = arguments["file_id"]
                r = await c.get(
                    f"{BOX_BASE}/files/{fid}/content",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=True,
                )
                r.raise_for_status()
                return {
                    "file_id": fid,
                    "size_bytes": len(r.content),
                    "content_base64": base64.b64encode(r.content).decode(),
                }

            elif tool_name == "box_upload_file":
                import base64
                import json as json_mod

                content = base64.b64decode(arguments["content_base64"])
                attributes = {
                    "name": arguments["name"],
                    "parent": {"id": arguments.get("parent_folder_id", "0")},
                }
                r = await c.post(
                    f"{BOX_UPLOAD_BASE}/files/content",
                    headers={"Authorization": f"Bearer {token}"},
                    files={
                        "attributes": (None, json_mod.dumps(attributes), "application/json"),
                        "file": (
                            arguments["name"],
                            content,
                            arguments.get("content_type", "application/octet-stream"),
                        ),
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "box_delete_file":
                fid = arguments["file_id"]
                r = await c.delete(f"{BOX_BASE}/files/{fid}", headers=hdrs)
                return {"success": r.status_code == 204}

            elif tool_name == "box_create_folder":
                body: dict[str, Any] = {
                    "name": arguments["name"],
                    "parent": {"id": arguments.get("parent_folder_id", "0")},
                }
                r = await c.post(f"{BOX_BASE}/folders", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "box_search":
                params = {
                    "query": arguments["query"],
                    "type": arguments.get("type", "file"),
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if afids := arguments.get("ancestor_folder_ids"):
                    params["ancestor_folder_ids"] = afids
                if ct := arguments.get("content_types"):
                    params["content_types"] = ",".join(ct)
                r = await c.get(f"{BOX_BASE}/search", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "box_create_shared_link":
                item_id = arguments["item_id"]
                item_type = arguments.get("item_type", "files")
                shared_link: dict[str, Any] = {
                    "access": arguments.get("access", "open"),
                }
                if unshared := arguments.get("unshared_at"):
                    shared_link["unshared_at"] = unshared
                r = await c.put(
                    f"{BOX_BASE}/{item_type}/{item_id}",
                    headers=hdrs,
                    json={"shared_link": shared_link},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "box_copy_file":
                fid = arguments["file_id"]
                body = {"parent": {"id": arguments["destination_folder_id"]}}
                if new_name := arguments.get("new_name"):
                    body["name"] = new_name
                r = await c.post(f"{BOX_BASE}/files/{fid}/copy", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("box_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
