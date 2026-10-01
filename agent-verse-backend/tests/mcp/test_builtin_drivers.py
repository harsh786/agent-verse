"""BUILTIN-DRIVERS: the MySQL and Snowflake built-ins have their drivers installed.

aiomysql and snowflake-connector-python were missing, so both built-ins only
ever answered "not installed" / advertised an ``unavailable`` tool.
"""

from __future__ import annotations

import importlib

import pytest

from app.mcp.servers import mysql_server, snowflake_server


@pytest.mark.parametrize("module", ["aiomysql", "snowflake.connector"])
def test_driver_imports(module: str) -> None:
    importlib.import_module(module)


def test_snowflake_advertises_its_real_tools() -> None:
    tools = snowflake_server.get_tools()
    assert tools == snowflake_server.TOOL_DEFINITIONS
    assert all(t["name"] != "unavailable" for t in tools)


async def test_mysql_no_longer_reports_a_missing_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing listens on port 1: the driver runs and fails to connect.
    monkeypatch.setenv("MYSQL_MCP_URL", "mysql://u:p@127.0.0.1:1/db")
    result = await mysql_server.call_tool("mysql_list_tables", {})
    assert "not installed" not in result["error"]
    assert "connect" in result["error"].lower()
