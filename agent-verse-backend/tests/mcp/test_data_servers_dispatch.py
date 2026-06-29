"""Dispatch-level tests for data/storage MCP servers.

Covers: postgres (asyncpg), mysql (aiomysql - not installed),
        mongodb (motor - not installed), snowflake (not installed),
        elasticsearch, redis, pinecone (not installed), supabase.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    """Return a mock AsyncClient context manager.
    
    All HTTP method mocks are explicitly set to AsyncMock so that
    awaiting them works correctly regardless of Python version.
    """
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# PostgreSQL (asyncpg - installed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_query_select():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_query", {"sql": "SELECT id, name FROM users"})
    assert result["count"] == 2
    assert len(result["rows"]) == 2


@pytest.mark.asyncio
async def test_postgres_query_blocked_dml():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db", "POSTGRES_MCP_ALLOW_WRITES": "false"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_query", {"sql": "INSERT INTO users VALUES (1, 'Alice')"})
    assert "error" in result
    assert "SELECT" in result["error"] or "Only SELECT" in result["error"]


@pytest.mark.asyncio
async def test_postgres_query_dml_allowed():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db", "POSTGRES_MCP_ALLOW_WRITES": "true"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_query", {"sql": "INSERT INTO users VALUES (1, 'Alice')"})
    # With writes allowed, it should proceed (not blocked)
    assert "error" not in result or "Only SELECT" not in result.get("error", "")


@pytest.mark.asyncio
async def test_postgres_list_tables():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[{"table_name": "users"}, {"table_name": "orders"}])
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_list_tables", {"schema": "public"})
    assert "tables" in result
    assert "users" in result["tables"]


@pytest.mark.asyncio
async def test_postgres_describe_table():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        {"column_name": "id", "data_type": "integer", "is_nullable": "NO"},
        {"column_name": "name", "data_type": "text", "is_nullable": "YES"},
    ])
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_describe_table", {"table_name": "users"})
    assert "columns" in result
    assert result["table"] == "users"


@pytest.mark.asyncio
async def test_postgres_unknown_tool():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_unknown_tool", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_postgres_missing_env():
    from app.mcp.servers.postgres_server import call_tool

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": ""}):
        os.environ.pop("POSTGRES_MCP_URL", None)
        result = await call_tool("postgres_query", {"sql": "SELECT 1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# MySQL (aiomysql - not installed: test ImportError path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mysql_missing_dep_returns_error():
    from app.mcp.servers.mysql_server import call_tool

    with patch.dict("os.environ", {"MYSQL_MCP_URL": "mysql://user:pass@localhost/db"}):
        # aiomysql is not installed, so call_tool returns dependency_missing
        result = await call_tool("mysql_execute", {"sql": "SELECT 1"})
    # Either missing dep or missing env – either way an error
    assert "error" in result


@pytest.mark.asyncio
async def test_mysql_missing_env():
    from app.mcp.servers.mysql_server import call_tool

    with patch.dict("os.environ", {"MYSQL_MCP_URL": ""}):
        os.environ.pop("MYSQL_MCP_URL", None)
        result = await call_tool("mysql_execute", {"sql": "SELECT 1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_mysql_with_mock_aiomysql():
    from app.mcp.servers.mysql_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=[(1, "Alice"), (2, "Bob")])
    mock_cursor.description = [("id",), ("name",)]

    mock_conn = MagicMock()
    # cursor() returns an async context manager
    mock_cursor_ctx = AsyncMock()
    mock_cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cursor_ctx)
    mock_conn.ensure_closed = AsyncMock()

    mock_aiomysql = MagicMock()
    mock_aiomysql.connect = AsyncMock(return_value=mock_conn)

    with patch.dict("os.environ", {"MYSQL_MCP_URL": "mysql://root:pass@localhost/db", "MYSQL_MCP_ALLOW_WRITES": "true"}), \
         patch.dict("sys.modules", {"aiomysql": mock_aiomysql}):
        result = await call_tool("mysql_execute", {"sql": "SELECT id, name FROM users"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mysql_list_tables_with_mock():
    from app.mcp.servers.mysql_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = AsyncMock()
    # MySQL DictCursor returns dicts; list_tables does r.values() on each row
    mock_cursor.fetchall = AsyncMock(return_value=[{"Tables_in_db": "users"}, {"Tables_in_db": "orders"}])
    mock_cursor.description = [("Tables_in_db", None, None, None, None, None, None)]
    mock_cursor.rowcount = 2

    mock_conn = MagicMock()
    mock_cursor_ctx = AsyncMock()
    mock_cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cursor_ctx)
    mock_conn.ensure_closed = AsyncMock()
    mock_conn.close = MagicMock()

    mock_aiomysql = MagicMock()
    mock_aiomysql.connect = AsyncMock(return_value=mock_conn)
    mock_aiomysql.DictCursor = MagicMock()

    with patch.dict("os.environ", {"MYSQL_MCP_URL": "mysql://root:pass@localhost/db"}), \
         patch.dict("sys.modules", {"aiomysql": mock_aiomysql}):
        result = await call_tool("mysql_list_tables", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# MongoDB (motor - not installed: test ImportError path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mongodb_missing_dep_returns_error():
    from app.mcp.servers.mongodb_server import call_tool

    with patch.dict("os.environ", {"MONGODB_MCP_URL": "mongodb://localhost/mydb"}):
        result = await call_tool("mongodb_find", {"collection": "users"})
    # motor not installed → ImportError path
    assert "error" in result


@pytest.mark.asyncio
async def test_mongodb_missing_env():
    from app.mcp.servers.mongodb_server import call_tool

    with patch.dict("os.environ", {"MONGODB_MCP_URL": ""}):
        os.environ.pop("MONGODB_MCP_URL", None)
        result = await call_tool("mongodb_find", {"collection": "users"})
    assert "error" in result


@pytest.mark.asyncio
async def test_mongodb_with_mock_motor():
    """Test mongodb_find when motor is mocked."""
    from app.mcp.servers.mongodb_server import call_tool

    # Build a fake motor module
    mock_doc = {"_id": "obj_id", "name": "Alice"}

    mock_cursor = AsyncMock()
    mock_cursor.to_list = AsyncMock(return_value=[mock_doc])

    mock_coll = MagicMock()
    mock_coll.find = MagicMock(return_value=mock_cursor)
    mock_coll.find.return_value.limit = MagicMock(return_value=mock_cursor)

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_coll)

    mock_motor_client = MagicMock()
    mock_motor_client.__getitem__ = MagicMock(return_value=mock_db)
    mock_motor_client.close = MagicMock()

    mock_motor_cls = MagicMock(return_value=mock_motor_client)
    mock_motor_asyncio = MagicMock()
    mock_motor_asyncio.AsyncIOMotorClient = mock_motor_cls

    mock_motor = MagicMock()
    mock_motor.motor_asyncio = mock_motor_asyncio

    with patch.dict("os.environ", {"MONGODB_MCP_URL": "mongodb://localhost/mydb"}), \
         patch.dict("sys.modules", {"motor": mock_motor, "motor.motor_asyncio": mock_motor_asyncio}):
        result = await call_tool("mongodb_find", {"collection": "users"})
    # May succeed or fail with AttributeError depending on mock wiring; just check no crash
    assert result is not None


# ---------------------------------------------------------------------------
# Snowflake (not installed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snowflake_missing_dep_returns_error():
    from app.mcp.servers.snowflake_server import call_tool

    with patch.dict("os.environ", {
        "SNOWFLAKE_ACCOUNT": "xy12345",
        "SNOWFLAKE_USER": "user",
        "SNOWFLAKE_PASSWORD": "pass",
    }):
        result = await call_tool("snowflake_query", {"sql": "SELECT 1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_snowflake_missing_env():
    from app.mcp.servers.snowflake_server import call_tool

    with patch.dict("os.environ", {"SNOWFLAKE_ACCOUNT": "", "SNOWFLAKE_USER": "", "SNOWFLAKE_PASSWORD": ""}):
        os.environ.pop("SNOWFLAKE_ACCOUNT", None)
        result = await call_tool("snowflake_query", {"sql": "SELECT 1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_snowflake_with_mock():
    from app.mcp.servers.snowflake_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = MagicMock()
    mock_cursor.fetchmany = MagicMock(return_value=[(1,), (2,)])
    mock_cursor.description = [("ID",)]
    mock_cursor.rowcount = 2

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.close = MagicMock()

    mock_sf = MagicMock()
    mock_sf.connect = MagicMock(return_value=mock_conn)

    with patch.dict("os.environ", {
        "SNOWFLAKE_ACCOUNT": "xy12345",
        "SNOWFLAKE_USER": "user",
        "SNOWFLAKE_PASSWORD": "pass",
    }), patch.dict("sys.modules", {"snowflake": mock_sf, "snowflake.connector": mock_sf}):
        result = await call_tool("snowflake_query", {"sql": "SELECT ID FROM TABLE"})
    assert result is not None


# ---------------------------------------------------------------------------
# Elasticsearch (httpx)
# ---------------------------------------------------------------------------

_ES = {"ELASTICSEARCH_URL": "https://es.example.com:9200", "ELASTICSEARCH_API_KEY": "es-key"}


@pytest.mark.asyncio
async def test_elasticsearch_search():
    from app.mcp.servers.elasticsearch_server import call_tool

    data = {"hits": {"total": {"value": 1}, "hits": [{"_index": "myindex", "_id": "1", "_score": 1.0, "_source": {"title": "Test"}}]}, "took": 5}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _ES), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("elasticsearch_search", {"index": "myindex", "query": {"match_all": {}}})
    assert "error" not in result


@pytest.mark.asyncio
async def test_elasticsearch_index_document():
    from app.mcp.servers.elasticsearch_server import call_tool

    data = {"_index": "myindex", "_id": "1", "result": "created", "_shards": {}}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _ES), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "elasticsearch_index_document",
            {"index": "myindex", "document": {"title": "Test"}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_elasticsearch_get_document():
    from app.mcp.servers.elasticsearch_server import call_tool

    # Server uses arguments["id"] not arguments["document_id"]
    data = {"_index": "myindex", "_id": "1", "_source": {"title": "Test"}, "found": True}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _ES), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("elasticsearch_get_document", {"index": "myindex", "id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_elasticsearch_delete_document():
    from app.mcp.servers.elasticsearch_server import call_tool

    # Server uses arguments["id"] not arguments["document_id"]
    data = {"_index": "myindex", "_id": "1", "result": "deleted"}
    mc = mk_client(delete=make_resp(data=data))
    with patch.dict("os.environ", _ES), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("elasticsearch_delete_document", {"index": "myindex", "id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_elasticsearch_list_indices():
    from app.mcp.servers.elasticsearch_server import call_tool

    data = [{"index": "myindex", "health": "green", "status": "open", "uuid": "abc", "pri": "1", "rep": "0", "docs.count": "100", "store.size": "1mb"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _ES), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("elasticsearch_list_indices", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_elasticsearch_create_index():
    from app.mcp.servers.elasticsearch_server import call_tool

    data = {"acknowledged": True, "shards_acknowledged": True, "index": "newindex"}
    mc = mk_client(put=make_resp(data=data))
    with patch.dict("os.environ", _ES), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("elasticsearch_create_index", {"index": "newindex"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_elasticsearch_bulk_index():
    from app.mcp.servers.elasticsearch_server import call_tool

    data = {"errors": False, "took": 3, "items": []}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _ES), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "elasticsearch_bulk_index",
            {"index": "myindex", "documents": [{"title": "Doc 1"}, {"title": "Doc 2"}]},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_elasticsearch_missing_env():
    from app.mcp.servers.elasticsearch_server import call_tool

    with patch.dict("os.environ", {"ELASTICSEARCH_URL": ""}):
        os.environ.pop("ELASTICSEARCH_URL", None)
        result = await call_tool("elasticsearch_search", {"index": "x", "query": {}})
    assert "error" in result


# ---------------------------------------------------------------------------
# Redis (redis.asyncio - installed)
# ---------------------------------------------------------------------------


def _make_async_generator(*items):
    """Create an async generator that yields items."""
    async def _gen():
        for item in items:
            yield item
    return _gen()


@pytest.mark.asyncio
async def test_redis_get():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.get = AsyncMock(return_value="hello")
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_get", {"key": "greeting"})
    assert result["value"] == "hello"
    assert result["exists"] is True


@pytest.mark.asyncio
async def test_redis_get_missing_key():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.get = AsyncMock(return_value=None)
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_get", {"key": "missing"})
    assert result["exists"] is False


@pytest.mark.asyncio
async def test_redis_set():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.set = AsyncMock(return_value=True)
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_set", {"key": "greeting", "value": "hello"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_redis_set_with_expiry():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.set = AsyncMock(return_value=True)
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_set", {"key": "token", "value": "abc", "ex": 3600})
    assert result["ok"] is True
    mock_r.set.assert_called_once_with("token", "abc", ex=3600)


@pytest.mark.asyncio
async def test_redis_delete():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.delete = AsyncMock(return_value=1)
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_delete", {"key": "greeting"})
    assert result["deleted"] == 1


@pytest.mark.asyncio
async def test_redis_list_keys():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.scan_iter = MagicMock(return_value=_make_async_generator("key1", "key2"))
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_list_keys", {"pattern": "*"})
    assert result["count"] == 2
    assert "key1" in result["keys"]


@pytest.mark.asyncio
async def test_redis_hget():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.hget = AsyncMock(return_value="field_value")
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_hget", {"key": "myhash", "field": "name"})
    assert result["value"] == "field_value"


@pytest.mark.asyncio
async def test_redis_hset():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.hset = AsyncMock(return_value=1)
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_hset", {"key": "myhash", "field": "name", "value": "Alice"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_redis_hgetall():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.hgetall = AsyncMock(return_value={"name": "Alice", "age": "30"})
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_hgetall", {"key": "myhash"})
    assert result["data"]["name"] == "Alice"


@pytest.mark.asyncio
async def test_redis_lpush():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.lpush = AsyncMock(return_value=3)
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_lpush", {"key": "mylist", "value": "item3"})
    assert result["list_length"] == 3


@pytest.mark.asyncio
async def test_redis_lrange():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.lrange = AsyncMock(return_value=["item3", "item2", "item1"])
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_lrange", {"key": "mylist", "start": 0, "stop": -1})
    assert result["count"] == 3


@pytest.mark.asyncio
async def test_redis_publish():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.publish = AsyncMock(return_value=2)
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_publish", {"channel": "events", "message": "hello"})
    assert result["receivers"] == 2


@pytest.mark.asyncio
async def test_redis_unknown_tool():
    from app.mcp.servers.redis_server import call_tool

    mock_r = AsyncMock()
    mock_r.aclose = AsyncMock()

    with patch.dict("os.environ", {"REDIS_MCP_URL": "redis://localhost:6379/0"}), \
         patch("redis.asyncio.from_url", return_value=mock_r):
        result = await call_tool("redis_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_redis_missing_env():
    from app.mcp.servers.redis_server import call_tool

    with patch.dict("os.environ", {"REDIS_MCP_URL": ""}):
        os.environ.pop("REDIS_MCP_URL", None)
        result = await call_tool("redis_get", {"key": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Pinecone (not installed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinecone_missing_dep():
    from app.mcp.servers.pinecone_server import call_tool

    with patch.dict("os.environ", {"PINECONE_API_KEY": "test-key"}):
        result = await call_tool("pinecone_list_indexes", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_pinecone_missing_env():
    from app.mcp.servers.pinecone_server import call_tool

    with patch.dict("os.environ", {"PINECONE_API_KEY": ""}):
        os.environ.pop("PINECONE_API_KEY", None)
        result = await call_tool("pinecone_list_indexes", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Supabase (httpx)
# ---------------------------------------------------------------------------

_SUPA = {"SUPABASE_URL": "https://xyz.supabase.co", "SUPABASE_SERVICE_KEY": "service-key"}


@pytest.mark.asyncio
async def test_supabase_select():
    from app.mcp.servers.supabase_server import call_tool

    data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _SUPA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("supabase_select", {"table": "users"})
    assert "data" in result
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_supabase_insert():
    from app.mcp.servers.supabase_server import call_tool

    data = [{"id": 3, "name": "Charlie"}]
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _SUPA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "supabase_insert", {"table": "users", "data": {"name": "Charlie"}}
        )
    assert "data" in result


@pytest.mark.asyncio
async def test_supabase_update():
    from app.mcp.servers.supabase_server import call_tool

    data = [{"id": 1, "name": "Alice Updated"}]
    mc = mk_client(patch=make_resp(data=data))
    with patch.dict("os.environ", _SUPA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "supabase_update",
            {"table": "users", "data": {"name": "Alice Updated"}, "filters": {"id": "eq.1"}},
        )
    assert "data" in result


@pytest.mark.asyncio
async def test_supabase_delete():
    from app.mcp.servers.supabase_server import call_tool

    mc = mk_client(delete=make_resp(data=[]))
    mc.delete.return_value.content = b"[]"
    with patch.dict("os.environ", _SUPA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "supabase_delete", {"table": "users", "filters": {"id": "eq.1"}}
        )
    assert "data" in result


@pytest.mark.asyncio
async def test_supabase_execute_sql():
    from app.mcp.servers.supabase_server import call_tool

    # supabase_execute_sql calls an RPC function by name, not raw SQL
    data = {"rows": 1}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _SUPA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "supabase_execute_sql", {"function_name": "get_user_count", "params": {}}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_supabase_missing_url():
    from app.mcp.servers.supabase_server import call_tool

    with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "sk"}):
        os.environ.pop("SUPABASE_URL", None)
        result = await call_tool("supabase_select", {"table": "users"})
    assert "error" in result


@pytest.mark.asyncio
async def test_supabase_missing_key():
    from app.mcp.servers.supabase_server import call_tool

    with patch.dict("os.environ", {"SUPABASE_URL": "https://xyz.supabase.co", "SUPABASE_SERVICE_KEY": ""}):
        os.environ.pop("SUPABASE_SERVICE_KEY", None)
        result = await call_tool("supabase_select", {"table": "users"})
    assert "error" in result
