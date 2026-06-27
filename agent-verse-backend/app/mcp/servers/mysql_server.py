"""MySQL MCP server — execute queries against a configured database.

Environment:
  MYSQL_MCP_URL: Connection URL (e.g. mysql://user:pass@host:3306/db)
  MYSQL_MCP_ALLOW_WRITES: Set to "true" to allow DML beyond SELECT (default: false)

Security:
  Only SELECT by default. DML requires MYSQL_MCP_ALLOW_WRITES=true.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "mysql_query",
        "description": "Execute a SQL SELECT query against the configured MySQL database",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT query to execute",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "mysql_execute",
        "description": "Execute any SQL statement (requires MYSQL_MCP_ALLOW_WRITES=true)",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL statement to execute",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "mysql_list_tables",
        "description": "List all tables in the connected MySQL database",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "mysql_describe_table",
        "description": "Get column definitions for a specific MySQL table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Name of the table to describe",
                },
            },
            "required": ["table_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("MYSQL_MCP_URL", "")
    if not url:
        return {"error": "MYSQL_MCP_URL not configured"}

    allow_writes = os.getenv("MYSQL_MCP_ALLOW_WRITES", "false").lower() == "true"

    try:
        import aiomysql  # type: ignore[import]

        parsed = urlparse(url)
        conn = await aiomysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username,
            password=parsed.password or "",
            db=(parsed.path or "/").lstrip("/"),
            autocommit=True,
        )
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if tool_name == "mysql_query":
                    sql = arguments["sql"].strip()
                    if not allow_writes and not sql.upper().startswith("SELECT"):
                        return {
                            "error": (
                                "Only SELECT queries are allowed. "
                                "Set MYSQL_MCP_ALLOW_WRITES=true for DML."
                            )
                        }
                    await cur.execute(sql)
                    rows = await cur.fetchall()
                    return {"rows": list(rows)[:1000], "count": len(rows)}

                elif tool_name == "mysql_execute":
                    if not allow_writes:
                        return {
                            "error": (
                                "Write operations are disabled. "
                                "Set MYSQL_MCP_ALLOW_WRITES=true to enable."
                            )
                        }
                    sql = arguments["sql"].strip()
                    await cur.execute(sql)
                    return {"affected_rows": cur.rowcount, "success": True}

                elif tool_name == "mysql_list_tables":
                    await cur.execute("SHOW TABLES")
                    rows = await cur.fetchall()
                    return {"tables": [list(r.values())[0] for r in rows]}

                elif tool_name == "mysql_describe_table":
                    table = arguments["table_name"]
                    await cur.execute(f"DESCRIBE `{table}`")
                    rows = await cur.fetchall()
                    return {"columns": list(rows), "table": table}

                else:
                    return {"error": f"Unknown tool: {tool_name}"}
        finally:
            conn.close()

    except ImportError:
        return {"error": "aiomysql not installed: pip install aiomysql"}
    except Exception as exc:
        logger.exception("mysql_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
