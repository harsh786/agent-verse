"""Final targeted tests - 4th batch - to push all remaining servers above 80%.

Key strategies:
1. Test boto3/asyncpg ImportError paths for AWS/DB servers
2. Test optional parameter branches  
3. Test error handlers (HTTP, generic)
4. Test remaining uncovered tool branches
"""
from __future__ import annotations

import sys
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
    m.headers = MagicMock()
    m.headers.get = MagicMock(return_value="application/json")
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


_AWS_ENV = {"AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": "secret", "AWS_REGION": "us-east-1"}


# ---------------------------------------------------------------------------
# AWS S3 – boto3 ImportError path + additional branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_boto3_missing():
    from app.mcp.servers.aws_s3_server import call_tool

    # Simulate boto3 not installed by setting sys.modules["boto3"] = None
    with patch.dict("sys.modules", {"boto3": None}):
        result = await call_tool("s3_list_buckets", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_s3_list_objects_with_prefix():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "prefix/file.txt", "Size": 100, "LastModified": None, "ETag": '"abc"', "StorageClass": "STANDARD"}],
        "CommonPrefixes": [],
        "IsTruncated": False,
        "KeyCount": 1,
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_list_objects", {"bucket": "my-bucket", "prefix": "prefix/", "delimiter": ""})
    assert "objects" in result


@pytest.mark.asyncio
async def test_s3_get_object_binary():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    body_mock = MagicMock()
    body_mock.read.return_value = bytes([0xFF, 0xFE, 0xAB, 0xCD])  # non-UTF8 bytes
    mock_s3.get_object.return_value = {
        "Body": body_mock,
        "ContentType": "application/octet-stream",
        "ContentLength": 4,
        "LastModified": None,
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_get_object", {"bucket": "my-bucket", "key": "binary.bin"})
    assert result["encoding"] == "base64"


@pytest.mark.asyncio
async def test_s3_create_bucket_non_us_east():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.create_bucket.return_value = {}
    with patch.dict("os.environ", {**_AWS_ENV, "AWS_REGION": "eu-west-1"}), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_create_bucket", {"bucket": "eu-bucket", "region": "eu-west-1"})
    assert result["created"] is True


# ---------------------------------------------------------------------------
# AWS IAM – boto3 ImportError + additional tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iam_boto3_missing():
    from app.mcp.servers.aws_iam_server import call_tool

    with patch.dict("sys.modules", {"boto3": None}):
        result = await call_tool("iam_list_users", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_iam_attach_policy_role():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.attach_role_policy.return_value = {}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool(
            "iam_attach_policy",
            {"role_name": "MyRole", "policy_arn": "arn:aws:iam::aws:policy/ReadOnlyAccess"},
        )
    assert "error" not in result


# ---------------------------------------------------------------------------
# AWS Lambda – boto3 ImportError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lambda_boto3_missing():
    from app.mcp.servers.aws_lambda_server import call_tool

    with patch.dict("sys.modules", {"boto3": None}):
        result = await call_tool("lambda_list_functions", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_lambda_invoke_with_payload():
    import json
    from app.mcp.servers.aws_lambda_server import call_tool

    mock_lam = MagicMock()
    payload_bytes = json.dumps({"statusCode": 200, "body": "ok"}).encode()
    mock_lam.invoke.return_value = {
        "StatusCode": 200,
        "Payload": MagicMock(read=MagicMock(return_value=payload_bytes)),
        "FunctionError": None,
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_lambda_server._client", return_value=mock_lam):
        result = await call_tool("lambda_invoke_function", {
            "function_name": "my-fn",
            "payload": {"action": "test"},
            "invocation_type": "RequestResponse",
        })
    assert result["status_code"] == 200


# ---------------------------------------------------------------------------
# AWS CloudWatch – remaining tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloudwatch_boto3_missing():
    from app.mcp.servers.aws_cloudwatch_server import call_tool

    with patch.dict("sys.modules", {"boto3": None}):
        result = await call_tool("cloudwatch_list_alarms", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_cloudwatch_put_alarm_with_optional():
    from app.mcp.servers.aws_cloudwatch_server import call_tool

    mock_cw = MagicMock()
    mock_cw.put_metric_alarm.return_value = {}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_cloudwatch_server._cw_client", return_value=mock_cw):
        result = await call_tool(
            "cloudwatch_put_metric_alarm",
            {
                "alarm_name": "HighCPU",
                "namespace": "AWS/EC2",
                "metric_name": "CPUUtilization",
                "threshold": 80.0,
                "comparison_operator": "GreaterThanThreshold",
                "evaluation_periods": 2,
                "period": 300,
                "alarm_actions": ["arn:aws:sns:us-east-1:123:alert"],
                "dimensions": [{"Name": "InstanceId", "Value": "i-12345"}],
            },
        )
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Cloud Storage – remaining branches
# ---------------------------------------------------------------------------

_GCS = {"GOOGLE_ACCESS_TOKEN": "gcs-tok"}


@pytest.mark.asyncio
async def test_gcs_list_objects_with_prefix():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"name": "folder/file.txt", "size": "100", "updated": "2024-01-01", "contentType": "text/plain", "md5Hash": "abc"}]}))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gcs_list_objects", {"bucket": "my-bucket", "prefix": "folder/", "max_results": 10})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gcs_http_error_handling():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    mc = mk_client()
    mc.get = AsyncMock(side_effect=Exception("Connection refused"))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gcs_list_buckets", {"project_id": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Calendly – remaining branches
# ---------------------------------------------------------------------------

_CALY = {"CALENDLY_ACCESS_TOKEN": "caly-tok"}


@pytest.mark.asyncio
async def test_calendly_list_event_types_with_org():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": [{"uri": "et/1", "name": "30 min", "duration": 30, "active": True, "scheduling_url": "url", "type": "StandardEventType"}], "pagination": {}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_list_event_types", {"user_uri": "u/abc", "organization_uri": "org/xyz"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_list_scheduled_events_with_filters():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": [], "pagination": {}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_list_scheduled_events", {
            "user_uri": "u/abc",
            "min_start_time": "2024-01-01T00:00:00Z",
            "max_start_time": "2024-12-31T23:59:59Z",
            "status": "active",
        })
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_http_error():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client()
    mc.get = AsyncMock(side_effect=httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=MagicMock(status_code=401, text="Unauthorized")))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_get_user", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# MySQL – asyncio DictCursor usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mysql_execute_select_with_mock():
    from app.mcp.servers.mysql_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=[{"id": 1, "name": "Alice"}])
    mock_cursor.description = [("id", None, None, None, None, None, None), ("name", None, None, None, None, None, None)]
    mock_cursor.rowcount = 1

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

    with patch.dict("os.environ", {"MYSQL_MCP_URL": "mysql://root:pass@localhost/db", "MYSQL_MCP_ALLOW_WRITES": "true"}), \
         patch.dict("sys.modules", {"aiomysql": mock_aiomysql}):
        result = await call_tool("mysql_execute", {"sql": "SELECT id, name FROM users", "allow_writes": False})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mysql_missing_url():
    from app.mcp.servers.mysql_server import call_tool

    with patch.dict("os.environ", {"MYSQL_MCP_URL": ""}):
        os.environ.pop("MYSQL_MCP_URL", None)
        result = await call_tool("mysql_list_tables", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Postgres – DML allowed + error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_list_tables_schema():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[{"table_name": "users"}, {"table_name": "products"}])
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_list_tables", {"schema": "myschema"})
    assert result["schema"] == "myschema"


@pytest.mark.asyncio
async def test_postgres_describe_table_empty():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_describe_table", {"table_name": "empty_table"})
    assert result["columns"] == []
    assert result["table"] == "empty_table"


# ---------------------------------------------------------------------------
# Prometheus – remaining uncovered branches
# ---------------------------------------------------------------------------

_PROM = {"PROMETHEUS_URL": "https://prom.example.com", "PROMETHEUS_TOKEN": "prom-tok"}


@pytest.mark.asyncio
async def test_prometheus_query_with_time():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"resultType": "vector", "result": []}}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_query", {"query": "up", "time": "2024-01-01T00:00:00Z"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_list_metrics_with_match():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": ["http_requests_total"]}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_list_metrics", {"match": "http_requests"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_http_error():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client()
    mc.get = AsyncMock(side_effect=httpx.HTTPStatusError("Server Error", request=MagicMock(), response=MagicMock(status_code=500, text="Internal Server Error")))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_query", {"query": "up"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Snowflake – additional mock coverage
# ---------------------------------------------------------------------------

_SF_ENV = {"SNOWFLAKE_ACCOUNT": "xy12345", "SNOWFLAKE_USER": "user", "SNOWFLAKE_PASSWORD": "pass"}


@pytest.mark.asyncio
async def test_snowflake_list_tables_with_db():
    from app.mcp.servers.snowflake_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = MagicMock()
    mock_cursor.fetchall = MagicMock(return_value=[{"name": "USERS"}, {"name": "ORDERS"}])
    mock_cursor.description = [("name",)]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.close = MagicMock()

    mock_sf_connector = MagicMock()
    mock_sf_connector.connect = MagicMock(return_value=mock_conn)
    mock_sf = MagicMock()
    mock_sf.connector = mock_sf_connector

    with patch.dict("os.environ", _SF_ENV), \
         patch.dict("sys.modules", {"snowflake": mock_sf, "snowflake.connector": mock_sf_connector}):
        result = await call_tool("snowflake_list_tables", {"database": "MYDB", "schema": "PUBLIC"})
    assert result is not None


@pytest.mark.asyncio
async def test_snowflake_missing_env():
    from app.mcp.servers.snowflake_server import call_tool

    with patch.dict("os.environ", {}, clear=False):
        for k in ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]:
            os.environ.pop(k, None)
        result = await call_tool("snowflake_query", {"sql": "SELECT 1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Search Console – remaining paths
# ---------------------------------------------------------------------------

_GSC = {"GOOGLE_ACCESS_TOKEN": "gsc-tok"}


@pytest.mark.asyncio
async def test_gsc_missing_token():
    from app.mcp.servers.google_search_console_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        result = await call_tool("gsc_list_sites", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_gsc_query_analytics_with_filter():
    from app.mcp.servers.google_search_console_server import call_tool

    mc = mk_client(post=make_resp(data={"rows": [], "responseAggregationType": "auto"}))
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gsc_query_search_analytics", {
            "site_url": "https://example.com/",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "dimensions": ["query", "page"],
            "row_limit": 100,
        })
    assert "error" not in result


@pytest.mark.asyncio
async def test_gsc_http_error():
    from app.mcp.servers.google_search_console_server import call_tool

    mc = mk_client()
    mc.get = AsyncMock(side_effect=httpx.HTTPStatusError("Auth Error", request=MagicMock(), response=MagicMock(status_code=403, text="Forbidden")))
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gsc_list_sites", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# OpenAI – remaining branches
# ---------------------------------------------------------------------------

_OAI = {"OPENAI_API_KEY": "sk-test-oai"}


@pytest.mark.asyncio
async def test_openai_chat_with_system():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "chatcmpl-2", "choices": [{"message": {"role": "assistant", "content": "I'm Claude"}, "finish_reason": "stop"}], "usage": {"total_tokens": 20}}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_chat_completion", {
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Who are you?"}
            ],
            "model": "gpt-4o",
            "temperature": 0.7,
            "max_tokens": 100,
        })
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_missing_key():
    from app.mcp.servers.openai_server import call_tool

    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        os.environ.pop("OPENAI_API_KEY", None)
        result = await call_tool("openai_chat_completion", {"messages": [{"role": "user", "content": "hi"}]})
    assert "error" in result


@pytest.mark.asyncio
async def test_openai_http_error():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client()
    mc.post = AsyncMock(side_effect=httpx.HTTPStatusError("Rate Limit", request=MagicMock(), response=MagicMock(status_code=429, text="Too Many Requests")))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_chat_completion", {"messages": [{"role": "user", "content": "hi"}]})
    assert "error" in result


# ---------------------------------------------------------------------------
# LinkedIn Ads – remaining branches + error
# ---------------------------------------------------------------------------

_LI = {"LINKEDIN_ACCESS_TOKEN": "li-tok"}


@pytest.mark.asyncio
async def test_linkedin_ads_http_error():
    from app.mcp.servers.linkedin_ads_server import call_tool

    mc = mk_client()
    mc.get = AsyncMock(side_effect=httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=MagicMock(status_code=403, text="Forbidden")))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_ads_list_accounts", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Perplexity – all tools
# ---------------------------------------------------------------------------

_PERP = {"PERPLEXITY_API_KEY": "perp-key"}


@pytest.mark.asyncio
async def test_perplexity_chat():
    from app.mcp.servers.perplexity_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "req2", "choices": [{"message": {"role": "assistant", "content": "Answer"}}], "citations": ["https://source.com"]}))
    with patch.dict("os.environ", _PERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("perplexity_chat", {"messages": [{"role": "user", "content": "What is AI?"}]})
    assert "error" not in result


@pytest.mark.asyncio
async def test_perplexity_reasoning():
    from app.mcp.servers.perplexity_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "req3", "choices": [{"message": {"role": "assistant", "content": "Reasoning response"}}]}))
    with patch.dict("os.environ", _PERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("perplexity_reasoning", {"question": "Prove the Pythagorean theorem"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_perplexity_http_error():
    from app.mcp.servers.perplexity_server import call_tool

    mc = mk_client()
    mc.post = AsyncMock(side_effect=httpx.HTTPStatusError("Error", request=MagicMock(), response=MagicMock(status_code=500, text="Server Error")))
    with patch.dict("os.environ", _PERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("perplexity_search", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# TikTok – remaining tools
# ---------------------------------------------------------------------------

_TK = {"TIKTOK_ACCESS_TOKEN": "tiktok-tok"}


@pytest.mark.asyncio
async def test_tiktok_search_videos():
    from app.mcp.servers.tiktok_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"videos": [{"id": "v2", "title": "Funny Cat", "view_count": 500000, "create_time": 1704067200}]}, "error": {"code": "ok"}}))
    with patch.dict("os.environ", _TK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tiktok_search_videos", {"keyword": "cats"})
    assert result is not None


@pytest.mark.asyncio
async def test_tiktok_list_campaigns():
    from app.mcp.servers.tiktok_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"list": [{"campaign_id": "cam1", "campaign_name": "Q1 Campaign", "status": "ENABLE"}]}, "code": 0}))
    with patch.dict("os.environ", _TK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tiktok_list_campaigns", {"advertiser_id": "adv1"})
    assert result is not None


@pytest.mark.asyncio
async def test_tiktok_missing_env():
    from app.mcp.servers.tiktok_server import call_tool

    with patch.dict("os.environ", {"TIKTOK_ACCESS_TOKEN": ""}):
        os.environ.pop("TIKTOK_ACCESS_TOKEN", None)
        result = await call_tool("tiktok_get_user_info", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Pinecone – all tools (with mock to simulate installed)
# ---------------------------------------------------------------------------

_PIN = {"PINECONE_API_KEY": "pinecone-key"}


@pytest.mark.asyncio
async def test_pinecone_with_mock_client():
    from app.mcp.servers.pinecone_server import call_tool

    mock_pinecone = MagicMock()
    mock_index = MagicMock()
    mock_pinecone.Pinecone.return_value.list_indexes.return_value = [
        MagicMock(name="my-index", dimension=1536, metric="cosine", status=MagicMock(state="Ready"))
    ]
    mock_pinecone.Pinecone.return_value.Index.return_value = mock_index
    mock_index.describe_index_stats.return_value = MagicMock(total_vector_count=100, dimension=1536, namespaces={})
    mock_index.query.return_value = MagicMock(matches=[])
    mock_index.upsert.return_value = MagicMock(upserted_count=1)

    with patch.dict("os.environ", _PIN), \
         patch.dict("sys.modules", {"pinecone": mock_pinecone}):
        r1 = await call_tool("pinecone_list_indexes", {})
        r2 = await call_tool("pinecone_describe_index", {"index_name": "my-index"})
        r3 = await call_tool("pinecone_query_vectors", {"index_name": "my-index", "vector": [0.1, 0.2, 0.3], "top_k": 5})
        r4 = await call_tool("pinecone_upsert_vectors", {"index_name": "my-index", "vectors": [{"id": "v1", "values": [0.1, 0.2]}]})

    for r in [r1, r2, r3, r4]:
        assert r is not None


@pytest.mark.asyncio
async def test_pinecone_delete_vectors():
    from app.mcp.servers.pinecone_server import call_tool

    mock_pinecone = MagicMock()
    mock_index = MagicMock()
    mock_pinecone.Pinecone.return_value.Index.return_value = mock_index
    mock_index.delete.return_value = {}

    with patch.dict("os.environ", _PIN), \
         patch.dict("sys.modules", {"pinecone": mock_pinecone}):
        r = await call_tool("pinecone_delete_vectors", {"index_name": "my-index", "ids": ["v1", "v2"]})
    assert r is not None


@pytest.mark.asyncio
async def test_pinecone_fetch_vectors():
    from app.mcp.servers.pinecone_server import call_tool

    mock_pinecone = MagicMock()
    mock_index = MagicMock()
    mock_pinecone.Pinecone.return_value.Index.return_value = mock_index
    mock_index.fetch.return_value = MagicMock(vectors={"v1": MagicMock(id="v1", values=[0.1, 0.2])})

    with patch.dict("os.environ", _PIN), \
         patch.dict("sys.modules", {"pinecone": mock_pinecone}):
        r = await call_tool("pinecone_fetch_vectors", {"index_name": "my-index", "ids": ["v1"]})
    assert r is not None


# ---------------------------------------------------------------------------
# MongoDB – more tools via mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mongodb_count_documents_mock():
    from app.mcp.servers.mongodb_server import call_tool

    mock_coll = MagicMock()
    mock_coll.count_documents = AsyncMock(return_value=42)

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
        result = await call_tool("mongodb_count", {"collection": "users", "query": {"active": True}})
    assert result is not None


@pytest.mark.asyncio
async def test_mongodb_update_one_mock():
    from app.mcp.servers.mongodb_server import call_tool

    mock_result = MagicMock()
    mock_result.matched_count = 1
    mock_result.modified_count = 1
    mock_result.upserted_id = None

    mock_coll = MagicMock()
    mock_coll.update_one = AsyncMock(return_value=mock_result)

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
        result = await call_tool("mongodb_update_one", {
            "collection": "users",
            "filter": {"_id": "user1"},
            "update": {"name": "Bob Updated"},
        })
    assert result is not None


# ---------------------------------------------------------------------------
# Docker – remaining response processing branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docker_container_logs_text():
    from app.mcp.servers.docker_server import call_tool

    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = "2024-01-01 INFO app started\n2024-01-01 INFO serving requests"
        result = await call_tool("docker_container_logs", {"container_id": "abc123", "tail": 100})
    assert "error" not in result
    assert "logs" in result


@pytest.mark.asyncio
async def test_docker_inspect_with_full_data():
    from app.mcp.servers.docker_server import call_tool

    data = {
        "Id": "abc123",
        "Name": "/my-container",
        "State": {"Running": True, "Status": "running", "Pid": 1234, "ExitCode": 0},
        "Config": {"Image": "nginx:latest", "Env": ["PATH=/usr/bin"], "Cmd": ["nginx", "-g", "daemon off;"]},
        "NetworkSettings": {"IPAddress": "172.17.0.2", "Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}},
        "Mounts": [{"Source": "/data", "Destination": "/app/data", "Mode": "rw"}],
    }
    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = data
        result = await call_tool("docker_inspect_container", {"container_id": "abc123"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Xero – additional branches with optional params
# ---------------------------------------------------------------------------

_XERO = {"XERO_ACCESS_TOKEN": "xero-tok", "XERO_TENANT_ID": "tenant-123"}


@pytest.mark.asyncio
async def test_xero_list_invoices_filtered():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(get=make_resp(data={"Invoices": [{"InvoiceID": "inv1", "InvoiceNumber": "INV-001", "Status": "DRAFT"}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_list_invoices", {"status": "DRAFT", "contact_id": "c1", "date_from": "2024-01-01"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_create_contact_full():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(post=make_resp(data={"Contacts": [{"ContactID": "c3", "Name": "Full Corp", "EmailAddress": "a@full.com"}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_create_contact", {"name": "Full Corp", "email": "a@full.com", "phone": "+1234567890"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_list_bank_transactions_filtered():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(get=make_resp(data={"BankTransactions": []}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_list_bank_transactions", {"bank_account_id": "ba1", "from_date": "2024-01-01"})
    assert "error" not in result
