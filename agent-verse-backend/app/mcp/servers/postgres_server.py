"""PostgreSQL MCP server — execute queries against a configured database.

Environment:
  POSTGRES_MCP_URL: Connection URL (e.g. postgresql://user:pass@host:5432/db)
Security:
  Only SELECT by default. DML requires POSTGRES_MCP_ALLOW_WRITES=true.
"""
from __future__ import annotations

import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "postgres_query",
        "description": "Execute a SQL SELECT query against the configured database",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT query",
                },
                "params": {
                    "type": "object",
                    "description": "Query parameters",
                    "default": {},
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "postgres_list_tables",
        "description": "List all tables in the database",
        "parameters": {
            "type": "object",
            "properties": {
                "schema": {"type": "string", "default": "public"},
            },
        },
    },
    {
        "name": "postgres_describe_table",
        "description": "Get schema/columns of a specific table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
            },
            "required": ["table_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    db_url = os.getenv("POSTGRES_MCP_URL", "")
    if not db_url:
        return {"error": "POSTGRES_MCP_URL not configured"}

    allow_writes = os.getenv("POSTGRES_MCP_ALLOW_WRITES", "false").lower() == "true"

    try:
        import asyncpg  # type: ignore[import]

        conn = await asyncpg.connect(db_url)
        try:
            if tool_name == "postgres_query":
                sql = arguments["sql"].strip()
                # Block non-SELECT unless writes are explicitly allowed
                if not allow_writes and not sql.upper().startswith("SELECT"):
                    return {
                        "error": (
                            "Only SELECT queries are allowed. "
                            "Set POSTGRES_MCP_ALLOW_WRITES=true for DML."
                        )
                    }
                rows = await conn.fetch(
                    sql,
                    *list((arguments.get("params") or {}).values()),
                )
                return {
                    "rows": [dict(r) for r in rows[:1000]],
                    "count": len(rows),
                }

            elif tool_name == "postgres_list_tables":
                schema = arguments.get("schema", "public")
                rows = await conn.fetch(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = $1 ORDER BY table_name",
                    schema,
                )
                return {"tables": [r["table_name"] for r in rows], "schema": schema}

            elif tool_name == "postgres_describe_table":
                rows = await conn.fetch(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = $1 ORDER BY ordinal_position",
                    arguments["table_name"],
                )
                return {
                    "columns": [dict(r) for r in rows],
                    "table": arguments["table_name"],
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}
        finally:
            await conn.close()

    except ImportError:
        return {"error": "asyncpg not installed: pip install asyncpg"}
    except Exception as exc:
        return {"error": str(exc)}
