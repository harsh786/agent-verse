"""Google Cloud Storage MCP server — bucket and object management via GCS JSON API.

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

GCS_BASE = "https://storage.googleapis.com/storage/v1"
GCS_UPLOAD_BASE = "https://storage.googleapis.com/upload/storage/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gcs_list_buckets",
        "description": "List all GCS buckets in a Google Cloud project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Google Cloud project ID"},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "gcs_list_objects",
        "description": "List objects in a GCS bucket with optional prefix filter",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "prefix": {"type": "string", "default": ""},
                "delimiter": {"type": "string", "default": "/"},
                "max_results": {"type": "integer", "default": 100},
                "page_token": {"type": "string"},
            },
            "required": ["bucket"],
        },
    },
    {
        "name": "gcs_get_object",
        "description": "Get metadata for a GCS object",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "object_name": {"type": "string"},
            },
            "required": ["bucket", "object_name"],
        },
    },
    {
        "name": "gcs_download_object",
        "description": "Download a GCS object and return base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "object_name": {"type": "string"},
            },
            "required": ["bucket", "object_name"],
        },
    },
    {
        "name": "gcs_upload_object",
        "description": "Upload an object to a GCS bucket from base64-encoded content",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "object_name": {"type": "string"},
                "content_base64": {"type": "string"},
                "content_type": {"type": "string", "default": "application/octet-stream"},
                "metadata": {"type": "object", "description": "Optional custom metadata"},
            },
            "required": ["bucket", "object_name", "content_base64"],
        },
    },
    {
        "name": "gcs_delete_object",
        "description": "Delete an object from a GCS bucket",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "object_name": {"type": "string"},
            },
            "required": ["bucket", "object_name"],
        },
    },
    {
        "name": "gcs_copy_object",
        "description": "Copy an object within or between GCS buckets",
        "parameters": {
            "type": "object",
            "properties": {
                "source_bucket": {"type": "string"},
                "source_object": {"type": "string"},
                "dest_bucket": {"type": "string"},
                "dest_object": {"type": "string"},
            },
            "required": ["source_bucket", "source_object", "dest_bucket", "dest_object"],
        },
    },
    {
        "name": "gcs_create_bucket",
        "description": "Create a new GCS bucket",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "bucket_name": {"type": "string"},
                "location": {"type": "string", "default": "US"},
                "storage_class": {
                    "type": "string",
                    "enum": ["STANDARD", "NEARLINE", "COLDLINE", "ARCHIVE"],
                    "default": "STANDARD",
                },
            },
            "required": ["project_id", "bucket_name"],
        },
    },
    {
        "name": "gcs_generate_signed_url",
        "description": "Generate a V4 signed URL for temporary public access to a GCS object",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "object_name": {"type": "string"},
                "expiration_seconds": {"type": "integer", "default": 3600},
                "method": {"type": "string", "enum": ["GET", "PUT", "DELETE"], "default": "GET"},
            },
            "required": ["bucket", "object_name"],
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
                scopes=["https://www.googleapis.com/auth/devstorage.full_control"],
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
            if tool_name == "gcs_list_buckets":
                r = await c.get(
                    f"{GCS_BASE}/b",
                    headers=hdrs,
                    params={"project": arguments["project_id"], "maxResults": arguments.get("max_results", 20)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "buckets": [
                        {"name": b["name"], "location": b.get("location", ""),
                         "storage_class": b.get("storageClass", "")}
                        for b in data.get("items", [])
                    ]
                }

            elif tool_name == "gcs_list_objects":
                bucket = arguments["bucket"]
                params: dict[str, Any] = {"maxResults": arguments.get("max_results", 100)}
                if prefix := arguments.get("prefix"):
                    params["prefix"] = prefix
                if delimiter := arguments.get("delimiter"):
                    params["delimiter"] = delimiter
                if pt := arguments.get("page_token"):
                    params["pageToken"] = pt
                r = await c.get(f"{GCS_BASE}/b/{bucket}/o", headers=hdrs, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "objects": [
                        {"name": o["name"], "size": o.get("size", 0),
                         "content_type": o.get("contentType", ""),
                         "updated": o.get("updated", "")}
                        for o in data.get("items", [])
                    ],
                    "prefixes": data.get("prefixes", []),
                    "next_page_token": data.get("nextPageToken"),
                }

            elif tool_name == "gcs_get_object":
                import urllib.parse

                bucket = arguments["bucket"]
                obj = urllib.parse.quote(arguments["object_name"], safe="")
                r = await c.get(f"{GCS_BASE}/b/{bucket}/o/{obj}", headers=hdrs)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gcs_download_object":
                import base64
                import urllib.parse

                bucket = arguments["bucket"]
                obj = urllib.parse.quote(arguments["object_name"], safe="")
                r = await c.get(
                    f"{GCS_BASE}/b/{bucket}/o/{obj}",
                    headers=hdrs,
                    params={"alt": "media"},
                )
                r.raise_for_status()
                return {
                    "bucket": bucket,
                    "object_name": arguments["object_name"],
                    "size_bytes": len(r.content),
                    "content_base64": base64.b64encode(r.content).decode(),
                }

            elif tool_name == "gcs_upload_object":
                import base64

                bucket = arguments["bucket"]
                content = base64.b64decode(arguments["content_base64"])
                content_type = arguments.get("content_type", "application/octet-stream")
                upload_hdrs = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": content_type,
                    "X-Goog-Object-Name": arguments["object_name"],
                }
                if meta := arguments.get("metadata"):
                    for k, v in meta.items():
                        upload_hdrs[f"x-goog-meta-{k}"] = str(v)
                r = await c.post(
                    f"{GCS_UPLOAD_BASE}/b/{bucket}/o",
                    headers=upload_hdrs,
                    params={"uploadType": "media", "name": arguments["object_name"]},
                    content=content,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gcs_delete_object":
                import urllib.parse

                bucket = arguments["bucket"]
                obj = urllib.parse.quote(arguments["object_name"], safe="")
                r = await c.delete(f"{GCS_BASE}/b/{bucket}/o/{obj}", headers=hdrs)
                return {"success": r.status_code == 204}

            elif tool_name == "gcs_copy_object":
                import urllib.parse

                sb = arguments["source_bucket"]
                so = urllib.parse.quote(arguments["source_object"], safe="")
                db = arguments["dest_bucket"]
                do_ = urllib.parse.quote(arguments["dest_object"], safe="")
                r = await c.post(
                    f"{GCS_BASE}/b/{sb}/o/{so}/copyTo/b/{db}/o/{do_}",
                    headers=hdrs,
                    json={},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gcs_create_bucket":
                body = {
                    "name": arguments["bucket_name"],
                    "location": arguments.get("location", "US"),
                    "storageClass": arguments.get("storage_class", "STANDARD"),
                }
                r = await c.post(
                    f"{GCS_BASE}/b",
                    headers=hdrs,
                    params={"project": arguments["project_id"]},
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {"name": data["name"], "location": data.get("location", ""),
                        "storage_class": data.get("storageClass", "")}

            elif tool_name == "gcs_generate_signed_url":
                # Signed URLs require service account credentials — return instructions if not available
                sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
                if not sa_json:
                    return {"error": "GOOGLE_SERVICE_ACCOUNT_JSON required for signed URL generation"}
                try:
                    import datetime

                    from google.auth.transport.requests import Request  # type: ignore[import]
                    from google.cloud import storage as gcs  # type: ignore[import]
                    from google.oauth2 import service_account  # type: ignore[import]

                    sa_info = json.loads(sa_json)
                    creds = service_account.Credentials.from_service_account_info(
                        sa_info,
                        scopes=["https://www.googleapis.com/auth/devstorage.full_control"],
                    )
                    client = gcs.Client(credentials=creds, project=sa_info.get("project_id", ""))
                    bucket_obj = client.bucket(arguments["bucket"])
                    blob = bucket_obj.blob(arguments["object_name"])
                    url = blob.generate_signed_url(
                        expiration=datetime.timedelta(seconds=arguments.get("expiration_seconds", 3600)),
                        method=arguments.get("method", "GET"),
                        version="v4",
                    )
                    return {"signed_url": url, "expires_in_seconds": arguments.get("expiration_seconds", 3600)}
                except ImportError:
                    return {"error": "google-cloud-storage package required for signed URL generation"}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("gcs_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
