"""Pinecone MCP server — vector database operations via Pinecone REST API.

Environment:
  PINECONE_API_KEY:     Pinecone API key
  PINECONE_ENVIRONMENT: Pinecone environment (e.g. us-east-1-aws) — used for index host construction
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

PINECONE_BASE = "https://api.pinecone.io"

TOOL_DEFINITIONS = [
    {
        "name": "pinecone_list_indexes",
        "description": "List all Pinecone indexes in the project",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "pinecone_describe_index",
        "description": "Get details about a specific Pinecone index",
        "parameters": {
            "type": "object",
            "properties": {
                "index_name": {"type": "string"},
            },
            "required": ["index_name"],
        },
    },
    {
        "name": "pinecone_query_vectors",
        "description": "Perform a vector similarity search in a Pinecone index",
        "parameters": {
            "type": "object",
            "properties": {
                "index_name": {"type": "string"},
                "vector": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Query vector (floats)",
                },
                "top_k": {"type": "integer", "default": 10},
                "namespace": {"type": "string", "default": ""},
                "filter": {"type": "object", "description": "Metadata filter"},
                "include_metadata": {"type": "boolean", "default": True},
                "include_values": {"type": "boolean", "default": False},
            },
            "required": ["index_name", "vector"],
        },
    },
    {
        "name": "pinecone_upsert_vectors",
        "description": "Upsert vectors into a Pinecone index",
        "parameters": {
            "type": "object",
            "properties": {
                "index_name": {"type": "string"},
                "vectors": {
                    "type": "array",
                    "description": "List of {id, values, metadata} objects",
                    "items": {"type": "object"},
                },
                "namespace": {"type": "string", "default": ""},
            },
            "required": ["index_name", "vectors"],
        },
    },
    {
        "name": "pinecone_delete_vectors",
        "description": "Delete vectors from a Pinecone index by ID or filter",
        "parameters": {
            "type": "object",
            "properties": {
                "index_name": {"type": "string"},
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Vector IDs to delete",
                },
                "filter": {"type": "object", "description": "Delete by metadata filter"},
                "namespace": {"type": "string", "default": ""},
                "delete_all": {"type": "boolean", "default": False},
            },
            "required": ["index_name"],
        },
    },
    {
        "name": "pinecone_fetch_vectors",
        "description": "Fetch vectors by ID from a Pinecone index",
        "parameters": {
            "type": "object",
            "properties": {
                "index_name": {"type": "string"},
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "namespace": {"type": "string", "default": ""},
            },
            "required": ["index_name", "ids"],
        },
    },
]


def _control_headers(api_key: str) -> dict[str, str]:
    return {
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }


async def _get_index_host(
    client: httpx.AsyncClient,
    api_key: str,
    index_name: str,
) -> str | None:
    """Look up the index host URL from the control plane."""
    resp = await client.get(
        f"{PINECONE_BASE}/indexes/{index_name}",
        headers=_control_headers(api_key),
    )
    if resp.status_code == 200:
        data = resp.json()
        return data.get("host") or data.get("status", {}).get("host")
    return None


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("PINECONE_API_KEY", "")
    if not api_key:
        return {"error": "PINECONE_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            ctrl_hdrs = _control_headers(api_key)

            if tool_name == "pinecone_list_indexes":
                resp = await client.get(f"{PINECONE_BASE}/indexes", headers=ctrl_hdrs)
                resp.raise_for_status()
                data = resp.json()
                return {"indexes": data.get("indexes", data)}

            elif tool_name == "pinecone_describe_index":
                resp = await client.get(
                    f"{PINECONE_BASE}/indexes/{arguments['index_name']}",
                    headers=ctrl_hdrs,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name in (
                "pinecone_query_vectors",
                "pinecone_upsert_vectors",
                "pinecone_delete_vectors",
                "pinecone_fetch_vectors",
            ):
                index_name = arguments["index_name"]
                host = await _get_index_host(client, api_key, index_name)
                if not host:
                    env = os.getenv("PINECONE_ENVIRONMENT", "")
                    host = f"https://{index_name}-{env}.svc.{env}.pinecone.io" if env else None
                if not host:
                    return {"error": f"Could not resolve host for index '{index_name}'"}

                index_hdrs = {
                    "Api-Key": api_key,
                    "Content-Type": "application/json",
                }
                index_base = host if host.startswith("http") else f"https://{host}"
                namespace = arguments.get("namespace", "")

                if tool_name == "pinecone_query_vectors":
                    body: dict[str, Any] = {
                        "vector": arguments["vector"],
                        "topK": arguments.get("top_k", 10),
                        "includeMetadata": arguments.get("include_metadata", True),
                        "includeValues": arguments.get("include_values", False),
                    }
                    if namespace:
                        body["namespace"] = namespace
                    if filt := arguments.get("filter"):
                        body["filter"] = filt
                    resp = await client.post(f"{index_base}/query", json=body, headers=index_hdrs)
                    resp.raise_for_status()
                    return resp.json()

                elif tool_name == "pinecone_upsert_vectors":
                    body = {"vectors": arguments["vectors"]}
                    if namespace:
                        body["namespace"] = namespace
                    resp = await client.post(f"{index_base}/vectors/upsert", json=body, headers=index_hdrs)
                    resp.raise_for_status()
                    return resp.json()

                elif tool_name == "pinecone_delete_vectors":
                    body = {}
                    if ids := arguments.get("ids"):
                        body["ids"] = ids
                    if filt := arguments.get("filter"):
                        body["filter"] = filt
                    if arguments.get("delete_all"):
                        body["deleteAll"] = True
                    if namespace:
                        body["namespace"] = namespace
                    resp = await client.post(f"{index_base}/vectors/delete", json=body, headers=index_hdrs)
                    resp.raise_for_status()
                    return {"success": True}

                elif tool_name == "pinecone_fetch_vectors":
                    params: dict[str, Any] = {"ids": arguments["ids"]}
                    if namespace:
                        params["namespace"] = namespace
                    resp = await client.get(f"{index_base}/vectors/fetch", params=params, headers=index_hdrs)
                    resp.raise_for_status()
                    return resp.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("pinecone_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
