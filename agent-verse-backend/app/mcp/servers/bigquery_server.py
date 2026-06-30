"""Google BigQuery MCP server — data warehouse queries and management.

Environment:
  BIGQUERY_ACCESS_TOKEN: OAuth2 bearer token for BigQuery API
  BIGQUERY_PROJECT_ID:   GCP project ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BIGQUERY_BASE = "https://bigquery.googleapis.com/bigquery/v2"

TOOL_DEFINITIONS = [
    {
        "name": "bq_run_query",
        "description": "Run a SQL query against BigQuery and return results",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Standard SQL query"},
                "max_results": {"type": "integer", "default": 100},
                "timeout_ms": {"type": "integer", "default": 30000},
                "use_legacy_sql": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "bq_list_datasets",
        "description": "List all datasets in the BigQuery project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Overrides BIGQUERY_PROJECT_ID"},
                "max_results": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "bq_list_tables",
        "description": "List tables within a BigQuery dataset",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "project_id": {"type": "string"},
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["dataset_id"],
        },
    },
    {
        "name": "bq_get_table_schema",
        "description": "Get the schema (columns and types) of a BigQuery table",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "table_id": {"type": "string"},
                "project_id": {"type": "string"},
            },
            "required": ["dataset_id", "table_id"],
        },
    },
    {
        "name": "bq_create_dataset",
        "description": "Create a new BigQuery dataset",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "location": {"type": "string", "default": "US"},
                "description": {"type": "string"},
                "project_id": {"type": "string"},
            },
            "required": ["dataset_id"],
        },
    },
    {
        "name": "bq_insert_rows",
        "description": "Stream insert rows into a BigQuery table",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "table_id": {"type": "string"},
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of row objects to insert",
                },
                "project_id": {"type": "string"},
            },
            "required": ["dataset_id", "table_id", "rows"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("BIGQUERY_ACCESS_TOKEN", "")
    if not token:
        return {"error": "BIGQUERY_ACCESS_TOKEN not configured"}

    project_id = arguments.get("project_id") or os.getenv("BIGQUERY_PROJECT_ID", "")
    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            if tool_name == "bq_run_query":
                body = {
                    "query": arguments["query"],
                    "maxResults": arguments.get("max_results", 100),
                    "timeoutMs": arguments.get("timeout_ms", 30000),
                    "useLegacySql": arguments.get("use_legacy_sql", False),
                }
                r = await c.post(
                    f"{BIGQUERY_BASE}/projects/{project_id}/queries",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                schema = data.get("schema", {}).get("fields", [])
                rows = []
                for row in data.get("rows", []):
                    record = {}
                    for i, cell in enumerate(row.get("f", [])):
                        col_name = schema[i]["name"] if i < len(schema) else str(i)
                        record[col_name] = cell.get("v")
                    rows.append(record)
                return {
                    "rows": rows,
                    "total_rows": data.get("totalRows"),
                    "job_complete": data.get("jobComplete", False),
                }

            elif tool_name == "bq_list_datasets":
                params: dict[str, Any] = {"maxResults": arguments.get("max_results", 50)}
                r = await c.get(
                    f"{BIGQUERY_BASE}/projects/{project_id}/datasets",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "datasets": [
                        {
                            "id": ds.get("datasetReference", {}).get("datasetId"),
                            "location": ds.get("location"),
                        }
                        for ds in data.get("datasets", [])
                    ]
                }

            elif tool_name == "bq_list_tables":
                dataset_id = arguments["dataset_id"]
                r = await c.get(
                    f"{BIGQUERY_BASE}/projects/{project_id}/datasets/{dataset_id}/tables",
                    headers=hdrs,
                    params={"maxResults": arguments.get("max_results", 50)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "tables": [
                        {
                            "id": t.get("tableReference", {}).get("tableId"),
                            "type": t.get("type"),
                        }
                        for t in data.get("tables", [])
                    ]
                }

            elif tool_name == "bq_get_table_schema":
                dataset_id = arguments["dataset_id"]
                table_id = arguments["table_id"]
                r = await c.get(
                    f"{BIGQUERY_BASE}/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}",
                    headers=hdrs,
                    params={"fields": "schema,numRows,numBytes"},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "fields": data.get("schema", {}).get("fields", []),
                    "num_rows": data.get("numRows"),
                    "num_bytes": data.get("numBytes"),
                }

            elif tool_name == "bq_create_dataset":
                dataset_id = arguments["dataset_id"]
                body: dict[str, Any] = {
                    "datasetReference": {
                        "projectId": project_id,
                        "datasetId": dataset_id,
                    },
                    "location": arguments.get("location", "US"),
                }
                if arguments.get("description"):
                    body["description"] = arguments["description"]
                r = await c.post(
                    f"{BIGQUERY_BASE}/projects/{project_id}/datasets",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return {"dataset_id": dataset_id, "created": True}

            elif tool_name == "bq_insert_rows":
                dataset_id = arguments["dataset_id"]
                table_id = arguments["table_id"]
                body = {
                    "rows": [
                        {"json": row} for row in arguments["rows"]
                    ]
                }
                r = await c.post(
                    f"{BIGQUERY_BASE}/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}/insertAll",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                errors = data.get("insertErrors", [])
                return {
                    "inserted": len(arguments["rows"]) - len(errors),
                    "errors": errors,
                    "success": len(errors) == 0,
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("bq_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
