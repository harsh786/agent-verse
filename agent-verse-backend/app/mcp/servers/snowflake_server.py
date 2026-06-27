"""Snowflake MCP server — execute queries against Snowflake data warehouse.

Environment:
  SNOWFLAKE_ACCOUNT:    Snowflake account identifier (e.g. xy12345.us-east-1)
  SNOWFLAKE_USER:       Username
  SNOWFLAKE_PASSWORD:   Password
  SNOWFLAKE_WAREHOUSE:  Virtual warehouse name (optional)
  SNOWFLAKE_DATABASE:   Default database (optional)
  SNOWFLAKE_SCHEMA:     Default schema (default: PUBLIC)
  SNOWFLAKE_ALLOW_WRITES: Set to "true" to allow DML (default: false)

Note: Snowflake connector is synchronous; calls are run in a thread pool.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "snowflake_query",
        "description": "Execute a SELECT query against Snowflake",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL SELECT query"},
                "limit": {"type": "integer", "default": 1000, "description": "Max rows to return"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "snowflake_execute",
        "description": "Execute any SQL statement in Snowflake (requires SNOWFLAKE_ALLOW_WRITES=true for DML)",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL statement to execute"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "snowflake_list_tables",
        "description": "List tables in the current or specified Snowflake database/schema",
        "parameters": {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
                "schema": {"type": "string"},
            },
        },
    },
    {
        "name": "snowflake_describe_table",
        "description": "Describe the columns of a Snowflake table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "snowflake_show_databases",
        "description": "List all Snowflake databases accessible to the current user",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]

_REQUIRED_ENV = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    for env in _REQUIRED_ENV:
        if not os.getenv(env):
            return {"error": f"{env} not configured"}

    allow_writes = os.getenv("SNOWFLAKE_ALLOW_WRITES", "false").lower() == "true"

    try:
        import snowflake.connector  # type: ignore[import]

        def _sync() -> dict[str, Any]:
            conn = snowflake.connector.connect(
                account=os.getenv("SNOWFLAKE_ACCOUNT"),
                user=os.getenv("SNOWFLAKE_USER"),
                password=os.getenv("SNOWFLAKE_PASSWORD"),
                warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", ""),
                database=os.getenv("SNOWFLAKE_DATABASE", ""),
                schema=os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
            )
            try:
                cur = conn.cursor(snowflake.connector.DictCursor)

                if tool_name == "snowflake_query":
                    sql = arguments["sql"].strip()
                    if not allow_writes and not sql.upper().startswith("SELECT"):
                        return {
                            "error": (
                                "Only SELECT queries allowed. "
                                "Set SNOWFLAKE_ALLOW_WRITES=true for DML."
                            )
                        }
                    cur.execute(sql)
                    limit = arguments.get("limit", 1000)
                    rows = cur.fetchmany(limit)
                    return {"rows": rows, "row_count": len(rows)}

                elif tool_name == "snowflake_execute":
                    if not allow_writes:
                        sql = arguments["sql"].strip()
                        if not sql.upper().startswith("SELECT"):
                            return {
                                "error": (
                                    "Write operations disabled. "
                                    "Set SNOWFLAKE_ALLOW_WRITES=true."
                                )
                            }
                    cur.execute(arguments["sql"])
                    rows = cur.fetchall() or []
                    return {"rows": rows, "row_count": cur.rowcount}

                elif tool_name == "snowflake_list_tables":
                    db = arguments.get("database", os.getenv("SNOWFLAKE_DATABASE", ""))
                    schema = arguments.get("schema", os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"))
                    query = "SHOW TABLES"
                    if db and schema:
                        query += f" IN SCHEMA {db}.{schema}"
                    elif db:
                        query += f" IN DATABASE {db}"
                    cur.execute(query)
                    rows = cur.fetchall() or []
                    return {"tables": [r.get("name", "") for r in rows]}

                elif tool_name == "snowflake_describe_table":
                    cur.execute(f"DESCRIBE TABLE {arguments['table_name']}")
                    return {"columns": cur.fetchall() or []}

                elif tool_name == "snowflake_show_databases":
                    cur.execute("SHOW DATABASES")
                    rows = cur.fetchall() or []
                    return {"databases": [r.get("name", "") for r in rows]}

                else:
                    return {"error": f"Unknown tool: {tool_name}"}
            finally:
                conn.close()

        return await asyncio.get_running_loop().run_in_executor(None, _sync)

    except ImportError:
        return {"error": "snowflake-connector-python not installed: pip install snowflake-connector-python"}
    except Exception as exc:
        logger.exception("snowflake_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
