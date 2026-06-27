"""Google Drive MCP server — file management via Drive API v3.

Environment variables (one required):
  GOOGLE_ACCESS_TOKEN:         OAuth2 bearer token
  GOOGLE_SERVICE_ACCOUNT_JSON: JSON string of a service-account key file
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

DRIVE_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

TOOL_DEFINITIONS = [
    {
        "name": "drive_list_files",
        "description": "List files in Google Drive with optional query filter",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Drive query string, e.g. \"name contains 'report' and mimeType='application/pdf'\"",
                    "default": "",
                },
                "page_size": {"type": "integer", "default": 20},
                "order_by": {"type": "string", "default": "modifiedTime desc"},
                "fields": {
                    "type": "string",
                    "default": "files(id,name,mimeType,size,modifiedTime,parents)",
                },
            },
        },
    },
    {
        "name": "drive_get_file",
        "description": "Get metadata for a specific Google Drive file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "fields": {
                    "type": "string",
                    "default": "id,name,mimeType,size,modifiedTime,parents,webViewLink",
                },
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "drive_download_file",
        "description": "Download the binary content of a Drive file (returns base64)",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "drive_upload_file",
        "description": "Upload a new file to Google Drive from base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "File name"},
                "content_base64": {"type": "string", "description": "Base64-encoded file content"},
                "mime_type": {"type": "string", "default": "application/octet-stream"},
                "parent_folder_id": {"type": "string", "description": "Parent folder ID (optional)"},
            },
            "required": ["name", "content_base64"],
        },
    },
    {
        "name": "drive_create_folder",
        "description": "Create a new folder in Google Drive",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_folder_id": {"type": "string", "description": "Parent folder ID (optional)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "drive_move_file",
        "description": "Move a file to a different folder in Google Drive",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "new_parent_id": {"type": "string", "description": "ID of the destination folder"},
            },
            "required": ["file_id", "new_parent_id"],
        },
    },
    {
        "name": "drive_share_file",
        "description": "Share a Drive file with a user, group, or make it public",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "role": {
                    "type": "string",
                    "enum": ["reader", "commenter", "writer", "owner"],
                    "default": "reader",
                },
                "type": {
                    "type": "string",
                    "enum": ["user", "group", "domain", "anyone"],
                    "default": "user",
                },
                "email_address": {"type": "string", "description": "Email for user/group type"},
            },
            "required": ["file_id", "type", "role"],
        },
    },
    {
        "name": "drive_delete_file",
        "description": "Permanently delete a file or folder from Google Drive",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "drive_search_files",
        "description": "Full-text search across all files in Google Drive",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "page_size": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
]


def _google_token() -> str:
    direct = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if direct:
        return direct
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if sa_json:
        try:
            from google.auth.transport.requests import Request  # type: ignore[import]
            from google.oauth2 import service_account  # type: ignore[import]

            creds = service_account.Credentials.from_service_account_info(
                json.loads(sa_json),
                scopes=["https://www.googleapis.com/auth/drive"],
            )
            creds.refresh(Request())
            return creds.token  # type: ignore[return-value]
        except Exception:
            logger.debug("google_service_account_refresh_failed", exc_info=True)
    return ""


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = _google_token()
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN or GOOGLE_SERVICE_ACCOUNT_JSON required"}

    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            if tool_name == "drive_list_files":
                params: dict[str, Any] = {
                    "pageSize": arguments.get("page_size", 20),
                    "orderBy": arguments.get("order_by", "modifiedTime desc"),
                    "fields": arguments.get(
                        "fields", "files(id,name,mimeType,size,modifiedTime,parents)"
                    ),
                }
                if q := arguments.get("query", ""):
                    params["q"] = q
                r = await c.get(f"{DRIVE_BASE}/files", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "drive_get_file":
                fid = arguments["file_id"]
                fields = arguments.get(
                    "fields", "id,name,mimeType,size,modifiedTime,parents,webViewLink"
                )
                r = await c.get(
                    f"{DRIVE_BASE}/files/{fid}",
                    headers=hdrs,
                    params={"fields": fields},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "drive_download_file":
                fid = arguments["file_id"]
                r = await c.get(
                    f"{DRIVE_BASE}/files/{fid}",
                    headers=hdrs,
                    params={"alt": "media"},
                )
                r.raise_for_status()
                import base64

                return {
                    "file_id": fid,
                    "size_bytes": len(r.content),
                    "content_base64": base64.b64encode(r.content).decode(),
                }

            elif tool_name == "drive_upload_file":
                import base64

                content = base64.b64decode(arguments["content_base64"])
                metadata: dict[str, Any] = {
                    "name": arguments["name"],
                    "mimeType": arguments.get("mime_type", "application/octet-stream"),
                }
                if pid := arguments.get("parent_folder_id"):
                    metadata["parents"] = [pid]
                # Use multipart upload
                boundary = "boundary_agentverse_drive_upload"
                meta_json = json.dumps(metadata).encode()
                body = (
                    f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                    + meta_json
                    + f"\r\n--{boundary}\r\nContent-Type: {arguments.get('mime_type', 'application/octet-stream')}\r\n\r\n".encode()
                    + content
                    + f"\r\n--{boundary}--".encode()
                )
                upload_hdrs = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                }
                r = await c.post(
                    f"{DRIVE_UPLOAD_BASE}/files",
                    headers=upload_hdrs,
                    params={"uploadType": "multipart"},
                    content=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "drive_create_folder":
                metadata = {
                    "name": arguments["name"],
                    "mimeType": "application/vnd.google-apps.folder",
                }
                if pid := arguments.get("parent_folder_id"):
                    metadata["parents"] = [pid]
                r = await c.post(f"{DRIVE_BASE}/files", headers=hdrs, json=metadata)
                r.raise_for_status()
                data = r.json()
                return {"folder_id": data["id"], "name": data["name"]}

            elif tool_name == "drive_move_file":
                fid = arguments["file_id"]
                new_parent = arguments["new_parent_id"]
                # First get current parents
                r = await c.get(
                    f"{DRIVE_BASE}/files/{fid}",
                    headers=hdrs,
                    params={"fields": "parents"},
                )
                r.raise_for_status()
                old_parents = ",".join(r.json().get("parents", []))
                r = await c.patch(
                    f"{DRIVE_BASE}/files/{fid}",
                    headers=hdrs,
                    params={"addParents": new_parent, "removeParents": old_parents, "fields": "id,parents"},
                    json={},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "drive_share_file":
                fid = arguments["file_id"]
                perm: dict[str, Any] = {
                    "role": arguments.get("role", "reader"),
                    "type": arguments["type"],
                }
                if email := arguments.get("email_address"):
                    perm["emailAddress"] = email
                r = await c.post(
                    f"{DRIVE_BASE}/files/{fid}/permissions",
                    headers=hdrs,
                    json=perm,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "drive_delete_file":
                fid = arguments["file_id"]
                r = await c.delete(f"{DRIVE_BASE}/files/{fid}", headers=hdrs)
                return {"success": r.status_code == 204, "file_id": fid}

            elif tool_name == "drive_search_files":
                q = f"fullText contains '{arguments['query']}'"
                params = {
                    "q": q,
                    "pageSize": arguments.get("page_size", 10),
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                }
                r = await c.get(f"{DRIVE_BASE}/files", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("drive_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
