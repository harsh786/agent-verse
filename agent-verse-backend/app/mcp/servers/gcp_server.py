"""GCP MCP server — Google Cloud Platform operations.

Environment variables:
  GCP_PROJECT_ID: GCP project ID
  GCP_API_KEY: Google API key (for public GCS operations)
  GOOGLE_APPLICATION_CREDENTIALS: Path to service account JSON (for full access)
  GOOGLE_SERVICE_ACCOUNT_JSON: JSON string of service account key (alternative to file path)

Uses Bearer token from service account JSON when available; falls back to API key for
simple GCS operations that support it.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GCS_BASE = "https://storage.googleapis.com/storage/v1"
COMPUTE_BASE = "https://compute.googleapis.com/compute/v1"
FUNCTIONS_BASE = "https://cloudfunctions.googleapis.com/v1"
PUBSUB_BASE = "https://pubsub.googleapis.com/v1"
BQ_BASE = "https://bigquery.googleapis.com/bigquery/v2"
SM_BASE = "https://serviceusage.googleapis.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gcp_list_buckets",
        "description": "List all GCS buckets in a Google Cloud project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "GCP project ID (overrides GCP_PROJECT_ID env var)",
                },
                "max_results": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "gcp_list_objects",
        "description": "List objects in a GCS bucket with optional prefix filter",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "prefix": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 100},
            },
            "required": ["bucket"],
        },
    },
    {
        "name": "gcp_get_object",
        "description": "Get metadata (and optionally content) for a GCS object",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "object_name": {"type": "string"},
                "download": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, download content (text preview up to 4KB)",
                },
            },
            "required": ["bucket", "object_name"],
        },
    },
    {
        "name": "gcp_list_instances",
        "description": "List Compute Engine VM instances in a zone",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "zone": {
                    "type": "string",
                    "description": "GCP zone, e.g. 'us-central1-a'",
                    "default": "us-central1-a",
                },
            },
        },
    },
    {
        "name": "gcp_list_functions",
        "description": "List Cloud Functions in a GCP project and region",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "location": {
                    "type": "string",
                    "description": "Region, e.g. 'us-central1' or '-' for all",
                    "default": "-",
                },
            },
        },
    },
    {
        "name": "gcp_list_topics",
        "description": "List Pub/Sub topics in a GCP project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "page_size": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "gcp_run_bigquery",
        "description": "Run a BigQuery SQL query and return results",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "query": {"type": "string", "description": "Standard SQL query"},
                "max_results": {"type": "integer", "default": 100},
                "use_legacy_sql": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gcp_list_services",
        "description": "List enabled GCP services (APIs) for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "page_size": {"type": "integer", "default": 50},
                "filter": {
                    "type": "string",
                    "description": "Filter expression, e.g. 'state:ENABLED'",
                    "default": "state:ENABLED",
                },
            },
        },
    },
]


def _get_access_token() -> str | None:
    """Obtain a Google OAuth2 access token from available credentials.

    Priority:
    1. GOOGLE_SERVICE_ACCOUNT_JSON env var (JSON string)
    2. GOOGLE_APPLICATION_CREDENTIALS env var (path to JSON file)
    3. GCP_API_KEY (returned as-is for API key auth; callers must use ?key= param)
    """
    sa_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    sa_json_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

    sa_data: dict[str, Any] | None = None
    if sa_json_str:
        try:
            sa_data = json.loads(sa_json_str)
        except json.JSONDecodeError:
            logger.warning("gcp_server: GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON")
    elif sa_json_path and os.path.isfile(sa_json_path):
        try:
            with open(sa_json_path) as fh:
                sa_data = json.load(fh)
        except Exception as exc:
            logger.warning("gcp_server: could not read service account file: %s", exc)

    if sa_data:
        try:
            import google.auth.transport.requests  # type: ignore[import-untyped]
            import google.oauth2.service_account  # type: ignore[import-untyped]

            creds = google.oauth2.service_account.Credentials.from_service_account_info(
                sa_data,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            req = google.auth.transport.requests.Request()
            creds.refresh(req)
            return creds.token  # type: ignore[return-value]
        except ImportError:
            # google-auth not installed — fall through
            logger.debug("gcp_server: google-auth not available, falling back to API key")
        except Exception as exc:
            logger.warning("gcp_server: failed to obtain access token: %s", exc)

    return None


def _auth_headers(token: str | None) -> dict[str, str]:
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _api_key_param() -> dict[str, str]:
    key = os.getenv("GCP_API_KEY", "")
    return {"key": key} if key else {}


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    project_id = arguments.get("project_id") or os.getenv("GCP_PROJECT_ID", "")
    token = _get_access_token()

    if not token and not os.getenv("GCP_API_KEY", ""):
        return {
            "error": (
                "No GCP credentials found. Set GOOGLE_APPLICATION_CREDENTIALS, "
                "GOOGLE_SERVICE_ACCOUNT_JSON, or GCP_API_KEY."
            )
        }

    auth_hdrs = _auth_headers(token)
    api_key_params = _api_key_param() if not token else {}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # ── GCS: list buckets ────────────────────────────────────────────────
            if tool_name == "gcp_list_buckets":
                if not project_id:
                    return {"error": "project_id is required (or set GCP_PROJECT_ID)"}
                resp = await client.get(
                    f"{GCS_BASE}/b",
                    headers=auth_hdrs,
                    params={
                        "project": project_id,
                        "maxResults": arguments.get("max_results", 50),
                        **api_key_params,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "buckets": [
                        {"name": b["name"], "location": b.get("location", "")}
                        for b in data.get("items", [])
                    ]
                }

            # ── GCS: list objects ────────────────────────────────────────────────
            elif tool_name == "gcp_list_objects":
                bucket = arguments["bucket"]
                params: dict[str, Any] = {
                    "maxResults": arguments.get("max_results", 100),
                    **api_key_params,
                }
                if arguments.get("prefix"):
                    params["prefix"] = arguments["prefix"]
                resp = await client.get(
                    f"{GCS_BASE}/b/{bucket}/o",
                    headers=auth_hdrs,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "objects": [
                        {
                            "name": o["name"],
                            "size": o.get("size", ""),
                            "updated": o.get("updated", ""),
                            "content_type": o.get("contentType", ""),
                        }
                        for o in data.get("items", [])
                    ]
                }

            # ── GCS: get object ──────────────────────────────────────────────────
            elif tool_name == "gcp_get_object":
                bucket = arguments["bucket"]
                obj = arguments["object_name"]
                import urllib.parse

                encoded_obj = urllib.parse.quote(obj, safe="")

                if arguments.get("download"):
                    resp = await client.get(
                        f"{GCS_BASE}/b/{bucket}/o/{encoded_obj}",
                        headers=auth_hdrs,
                        params={"alt": "media", **api_key_params},
                    )
                    resp.raise_for_status()
                    return {
                        "content": resp.text[:4096],
                        "truncated": len(resp.text) > 4096,
                    }
                else:
                    resp = await client.get(
                        f"{GCS_BASE}/b/{bucket}/o/{encoded_obj}",
                        headers=auth_hdrs,
                        params=api_key_params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return {
                        "name": data.get("name"),
                        "size": data.get("size"),
                        "content_type": data.get("contentType"),
                        "updated": data.get("updated"),
                        "md5": data.get("md5Hash"),
                    }

            # ── Compute: list instances ──────────────────────────────────────────
            elif tool_name == "gcp_list_instances":
                if not project_id:
                    return {"error": "project_id is required (or set GCP_PROJECT_ID)"}
                zone = arguments.get("zone", "us-central1-a")
                resp = await client.get(
                    f"{COMPUTE_BASE}/projects/{project_id}/zones/{zone}/instances",
                    headers=auth_hdrs,
                    params=api_key_params,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "instances": [
                        {
                            "name": i["name"],
                            "status": i.get("status", ""),
                            "machine_type": i.get("machineType", "").split("/")[-1],
                            "zone": i.get("zone", "").split("/")[-1],
                        }
                        for i in data.get("items", [])
                    ]
                }

            # ── Cloud Functions: list ────────────────────────────────────────────
            elif tool_name == "gcp_list_functions":
                if not project_id:
                    return {"error": "project_id is required (or set GCP_PROJECT_ID)"}
                location = arguments.get("location", "-")
                resp = await client.get(
                    f"{FUNCTIONS_BASE}/projects/{project_id}/locations/{location}/functions",
                    headers=auth_hdrs,
                    params=api_key_params,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "functions": [
                        {
                            "name": f["name"].split("/")[-1],
                            "status": f.get("status", ""),
                            "runtime": f.get("runtime", ""),
                            "entry_point": f.get("entryPoint", ""),
                        }
                        for f in data.get("functions", [])
                    ]
                }

            # ── Pub/Sub: list topics ─────────────────────────────────────────────
            elif tool_name == "gcp_list_topics":
                if not project_id:
                    return {"error": "project_id is required (or set GCP_PROJECT_ID)"}
                resp = await client.get(
                    f"{PUBSUB_BASE}/projects/{project_id}/topics",
                    headers=auth_hdrs,
                    params={
                        "pageSize": arguments.get("page_size", 50),
                        **api_key_params,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "topics": [
                        t["name"].split("/")[-1] for t in data.get("topics", [])
                    ]
                }

            # ── BigQuery: run query ──────────────────────────────────────────────
            elif tool_name == "gcp_run_bigquery":
                if not project_id:
                    return {"error": "project_id is required (or set GCP_PROJECT_ID)"}
                body = {
                    "query": arguments["query"],
                    "maxResults": arguments.get("max_results", 100),
                    "useLegacySql": arguments.get("use_legacy_sql", False),
                    "timeoutMs": 30000,
                }
                resp = await client.post(
                    f"{BQ_BASE}/projects/{project_id}/queries",
                    headers={**auth_hdrs, "Content-Type": "application/json"},
                    params=api_key_params,
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                schema = [
                    f["name"] for f in data.get("schema", {}).get("fields", [])
                ]
                rows = [
                    {
                        schema[i]: cell.get("v")
                        for i, cell in enumerate(row.get("f", []))
                    }
                    for row in data.get("rows", [])
                ]
                return {
                    "rows": rows,
                    "total_rows": data.get("totalRows"),
                    "job_complete": data.get("jobComplete"),
                }

            # ── Service Usage: list enabled services ─────────────────────────────
            elif tool_name == "gcp_list_services":
                if not project_id:
                    return {"error": "project_id is required (or set GCP_PROJECT_ID)"}
                resp = await client.get(
                    f"{SM_BASE}/projects/{project_id}/services",
                    headers=auth_hdrs,
                    params={
                        "pageSize": arguments.get("page_size", 50),
                        "filter": arguments.get("filter", "state:ENABLED"),
                        **api_key_params,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "services": [
                        {
                            "name": s["name"].split("/")[-1],
                            "state": s.get("state", ""),
                        }
                        for s in data.get("services", [])
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        logger.error(
            "gcp_http_error tool=%s status=%s body=%s",
            tool_name,
            exc.response.status_code,
            exc.response.text[:500],
        )
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.error("gcp_call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}
