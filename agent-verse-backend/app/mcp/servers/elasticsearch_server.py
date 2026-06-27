"""Elasticsearch MCP server — search and manage Elasticsearch indices.

Environment:
  ELASTICSEARCH_URL:      Base URL (e.g. https://my-cluster.es.io:9200)
  ELASTICSEARCH_API_KEY:  API key for authentication (preferred)
  ELASTICSEARCH_USER:     Basic auth username (fallback)
  ELASTICSEARCH_PASSWORD: Basic auth password (fallback)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "elasticsearch_search",
        "description": "Search documents in an Elasticsearch index",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "string", "description": "Index name or pattern"},
                "query": {"type": "object", "description": "Elasticsearch query DSL"},
                "size": {"type": "integer", "default": 10},
                "from_": {"type": "integer", "default": 0, "description": "Offset for pagination"},
                "sort": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["index"],
        },
    },
    {
        "name": "elasticsearch_index_document",
        "description": "Index (create or replace) a document in Elasticsearch",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "id": {"type": "string", "description": "Document ID (auto-generated if omitted)"},
                "document": {"type": "object", "description": "Document body"},
            },
            "required": ["index", "document"],
        },
    },
    {
        "name": "elasticsearch_get_document",
        "description": "Get a document by ID from an Elasticsearch index",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "id": {"type": "string"},
            },
            "required": ["index", "id"],
        },
    },
    {
        "name": "elasticsearch_delete_document",
        "description": "Delete a document by ID from an Elasticsearch index",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "id": {"type": "string"},
            },
            "required": ["index", "id"],
        },
    },
    {
        "name": "elasticsearch_list_indices",
        "description": "List all Elasticsearch indices with health and document counts",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "default": "*"},
            },
        },
    },
    {
        "name": "elasticsearch_create_index",
        "description": "Create an Elasticsearch index with optional mappings and settings",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "mappings": {"type": "object"},
                "settings": {"type": "object"},
            },
            "required": ["index"],
        },
    },
    {
        "name": "elasticsearch_bulk_index",
        "description": "Bulk index multiple documents into Elasticsearch",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "documents": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of documents to index",
                },
            },
            "required": ["index", "documents"],
        },
    },
]


def _client() -> tuple[str, httpx.AsyncClient]:
    base = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    auth = None

    if api_key := os.getenv("ELASTICSEARCH_API_KEY"):
        headers["Authorization"] = f"ApiKey {api_key}"
    elif (user := os.getenv("ELASTICSEARCH_USER")) and (
        pwd := os.getenv("ELASTICSEARCH_PASSWORD")
    ):
        auth = (user, pwd)

    return base, httpx.AsyncClient(headers=headers, auth=auth, timeout=30.0)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("ELASTICSEARCH_URL", "")
    if not base_url:
        return {"error": "ELASTICSEARCH_URL not configured"}

    base, client = _client()

    try:
        async with client:
            if tool_name == "elasticsearch_search":
                index = arguments["index"]
                body: dict[str, Any] = {}
                if query := arguments.get("query"):
                    body["query"] = query
                body["size"] = arguments.get("size", 10)
                body["from"] = arguments.get("from_", 0)
                if sort := arguments.get("sort"):
                    body["sort"] = sort
                resp = await client.post(f"{base}/{index}/_search", json=body)
                resp.raise_for_status()
                data = resp.json()
                hits = data.get("hits", {})
                return {
                    "total": hits.get("total", {}).get("value", 0),
                    "hits": [
                        {"_id": h["_id"], "_score": h.get("_score"), **h["_source"]}
                        for h in hits.get("hits", [])
                    ],
                }

            elif tool_name == "elasticsearch_index_document":
                index = arguments["index"]
                doc_id = arguments.get("id")
                if doc_id:
                    resp = await client.put(f"{base}/{index}/_doc/{doc_id}", json=arguments["document"])
                else:
                    resp = await client.post(f"{base}/{index}/_doc", json=arguments["document"])
                resp.raise_for_status()
                data = resp.json()
                return {"_id": data["_id"], "result": data["result"], "index": data["_index"]}

            elif tool_name == "elasticsearch_get_document":
                resp = await client.get(f"{base}/{arguments['index']}/_doc/{arguments['id']}")
                resp.raise_for_status()
                data = resp.json()
                return {"_id": data["_id"], "found": data.get("found", False), "source": data.get("_source")}

            elif tool_name == "elasticsearch_delete_document":
                resp = await client.delete(f"{base}/{arguments['index']}/_doc/{arguments['id']}")
                resp.raise_for_status()
                data = resp.json()
                return {"_id": data["_id"], "result": data["result"]}

            elif tool_name == "elasticsearch_list_indices":
                pattern = arguments.get("pattern", "*")
                resp = await client.get(
                    f"{base}/_cat/indices/{pattern}",
                    params={"format": "json", "h": "index,health,status,docs.count,store.size"},
                )
                resp.raise_for_status()
                return {"indices": resp.json()}

            elif tool_name == "elasticsearch_create_index":
                body = {}
                if mappings := arguments.get("mappings"):
                    body["mappings"] = mappings
                if settings := arguments.get("settings"):
                    body["settings"] = settings
                resp = await client.put(f"{base}/{arguments['index']}", json=body)
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "elasticsearch_bulk_index":
                index = arguments["index"]
                lines: list[str] = []
                import json
                for doc in arguments["documents"]:
                    lines.append(json.dumps({"index": {"_index": index}}))
                    lines.append(json.dumps(doc))
                bulk_body = "\n".join(lines) + "\n"
                resp = await client.post(
                    f"{base}/_bulk",
                    content=bulk_body,
                    headers={"Content-Type": "application/x-ndjson"},
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "took": data.get("took"),
                    "errors": data.get("errors"),
                    "items_count": len(data.get("items", [])),
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("elasticsearch_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
