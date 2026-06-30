"""Unit tests for the batch-5 MCP server integrations (servers 1–25).

Covers: amazon_ses, amazon_sqs, apache_kafka, bigquery, cloudflare,
        cloudinary, firebase, figma, filestack, loom, sonarqube, bitly,
        typeform, jotform, surveymonkey, formstack, signnow, evernote,
        microsoft_excel, microsoft_outlook, microsoft_onenote, microsoft_todo,
        google_forms, google_slides, google_tasks.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_resp(
    status: int = 200,
    data: Any = None,
    content_type: str = "application/json",
) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.headers = MagicMock()
    m.headers.get = MagicMock(return_value=content_type)
    m.raise_for_status = MagicMock()
    return m


def http_err(status: int = 400) -> MagicMock:
    resp = make_resp(status=status, data={"error": "bad request"})
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    )
    return resp


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    default = make_resp()
    for method in ("get", "post", "put", "patch", "delete", "request"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, default)))
    return mc


# ---------------------------------------------------------------------------
# 1. Amazon SES — boto3 path
# ---------------------------------------------------------------------------

_AWS = {
    "AWS_ACCESS_KEY_ID": "AKID",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "AWS_REGION": "us-east-1",
}


def _mock_boto3_ses(responses: dict[str, Any]) -> MagicMock:
    """Return a mock boto3 SES client whose methods return the given dicts."""
    c = MagicMock()
    for method, ret in responses.items():
        getattr(c, method).return_value = ret
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = c
    return mock_boto3


@pytest.mark.asyncio
async def test_ses_no_key():
    from app.mcp.servers.amazon_ses_server import call_tool

    with patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": ""}):
        result = await call_tool("ses_send_email", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_ses_no_boto3():
    from app.mcp.servers.amazon_ses_server import call_tool

    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": None}):
        result = await call_tool("ses_send_email", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_ses_send_email():
    from app.mcp.servers.amazon_ses_server import call_tool

    mock_b3 = _mock_boto3_ses({"send_email": {"MessageId": "msg-1"}})
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool(
            "ses_send_email",
            {
                "source": "a@example.com",
                "to_addresses": ["b@example.com"],
                "subject": "Hello",
                "body_text": "World",
            },
        )
    assert result.get("sent") is True


@pytest.mark.asyncio
async def test_ses_list_identities():
    from app.mcp.servers.amazon_ses_server import call_tool

    mock_b3 = _mock_boto3_ses({"list_identities": {"Identities": ["a@example.com"]}})
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool("ses_list_identities", {})
    assert "identities" in result


@pytest.mark.asyncio
async def test_ses_get_send_statistics():
    from app.mcp.servers.amazon_ses_server import call_tool

    mock_b3 = _mock_boto3_ses(
        {"get_send_statistics": {"SendDataPoints": [{"DeliveryAttempts": 10, "Bounces": 1, "Complaints": 0, "Rejects": 0}]}}
    )
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool("ses_get_send_statistics", {})
    assert "data_points" in result


@pytest.mark.asyncio
async def test_ses_unknown_tool():
    from app.mcp.servers.amazon_ses_server import call_tool

    mock_b3 = _mock_boto3_ses({})
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool("ses_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 2. Amazon SQS — boto3 path
# ---------------------------------------------------------------------------


def _mock_boto3_sqs(responses: dict[str, Any]) -> MagicMock:
    c = MagicMock()
    for method, ret in responses.items():
        getattr(c, method).return_value = ret
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = c
    return mock_boto3


@pytest.mark.asyncio
async def test_sqs_no_key():
    from app.mcp.servers.amazon_sqs_server import call_tool

    with patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": ""}):
        result = await call_tool("sqs_list_queues", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_sqs_send_message():
    from app.mcp.servers.amazon_sqs_server import call_tool

    mock_b3 = _mock_boto3_sqs({"send_message": {"MessageId": "msg-abc", "MD5OfMessageBody": "md5"}})
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool(
            "sqs_send_message",
            {"queue_url": "https://sqs.us-east-1.amazonaws.com/123/my-q", "message_body": "hello"},
        )
    assert result.get("sent") is True


@pytest.mark.asyncio
async def test_sqs_receive_messages():
    from app.mcp.servers.amazon_sqs_server import call_tool

    mock_b3 = _mock_boto3_sqs(
        {"receive_message": {"Messages": [{"MessageId": "m1", "ReceiptHandle": "rh1", "Body": "msg", "MD5OfBody": "d41d"}]}}
    )
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool(
            "sqs_receive_messages", {"queue_url": "https://sqs.us-east-1.amazonaws.com/123/my-q"}
        )
    assert len(result["messages"]) == 1


@pytest.mark.asyncio
async def test_sqs_list_queues():
    from app.mcp.servers.amazon_sqs_server import call_tool

    mock_b3 = _mock_boto3_sqs({"list_queues": {"QueueUrls": ["https://sqs.us-east-1.amazonaws.com/123/q1"]}})
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool("sqs_list_queues", {})
    assert len(result["queue_urls"]) == 1


@pytest.mark.asyncio
async def test_sqs_create_queue_fifo():
    from app.mcp.servers.amazon_sqs_server import call_tool

    mock_b3 = _mock_boto3_sqs({"create_queue": {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/my-q.fifo"}})
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool("sqs_create_queue", {"queue_name": "my-q", "fifo": True})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_sqs_unknown_tool():
    from app.mcp.servers.amazon_sqs_server import call_tool

    mock_b3 = _mock_boto3_sqs({})
    with patch.dict("os.environ", _AWS), patch.dict("sys.modules", {"boto3": mock_b3}):
        result = await call_tool("sqs_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 3. Apache Kafka
# ---------------------------------------------------------------------------

_KAFKA = {"KAFKA_API_KEY": "key", "KAFKA_API_SECRET": "secret", "KAFKA_REST_ENDPOINT": "https://kafka.test"}


@pytest.mark.asyncio
async def test_kafka_no_key():
    from app.mcp.servers.apache_kafka_server import call_tool

    with patch.dict("os.environ", {"KAFKA_API_KEY": ""}):
        result = await call_tool("kafka_list_topics", {"cluster_id": "lkc-1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_kafka_list_topics():
    from app.mcp.servers.apache_kafka_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"topic_name": "events"}]}))
    with patch.dict("os.environ", _KAFKA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("kafka_list_topics", {"cluster_id": "lkc-1"})
    assert "topics" in result


@pytest.mark.asyncio
async def test_kafka_create_topic():
    from app.mcp.servers.apache_kafka_server import call_tool

    mc = mk_client(post=make_resp(data={"topic_name": "orders", "partitions_count": 3}))
    with patch.dict("os.environ", _KAFKA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "kafka_create_topic",
            {"cluster_id": "lkc-1", "topic_name": "orders", "partitions_count": 3},
        )
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_kafka_delete_topic():
    from app.mcp.servers.apache_kafka_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _KAFKA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("kafka_delete_topic", {"cluster_id": "lkc-1", "topic_name": "old"})
    assert result.get("deleted") is True


@pytest.mark.asyncio
async def test_kafka_unknown_tool():
    from app.mcp.servers.apache_kafka_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _KAFKA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("kafka_nonexistent", {"cluster_id": "lkc-1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_kafka_http_error():
    from app.mcp.servers.apache_kafka_server import call_tool

    mc = mk_client(get=http_err(401))
    with patch.dict("os.environ", _KAFKA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("kafka_list_topics", {"cluster_id": "lkc-1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# 4. BigQuery
# ---------------------------------------------------------------------------

_BQ = {"BIGQUERY_ACCESS_TOKEN": "tok", "BIGQUERY_PROJECT_ID": "my-project"}


@pytest.mark.asyncio
async def test_bq_no_token():
    from app.mcp.servers.bigquery_server import call_tool

    with patch.dict("os.environ", {"BIGQUERY_ACCESS_TOKEN": ""}):
        result = await call_tool("bq_run_query", {"query": "SELECT 1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_bq_run_query():
    from app.mcp.servers.bigquery_server import call_tool

    schema = [{"name": "n"}, {"name": "v"}]
    rows = [{"f": [{"v": "foo"}, {"v": "42"}]}]
    mc = mk_client(post=make_resp(data={"schema": {"fields": schema}, "rows": rows, "jobComplete": True, "totalRows": "1"}))
    with patch.dict("os.environ", _BQ), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bq_run_query", {"query": "SELECT n, v FROM t"})
    assert result["job_complete"] is True
    assert len(result["rows"]) == 1


@pytest.mark.asyncio
async def test_bq_list_datasets():
    from app.mcp.servers.bigquery_server import call_tool

    mc = mk_client(get=make_resp(data={"datasets": [{"datasetReference": {"datasetId": "ds1"}, "location": "US"}]}))
    with patch.dict("os.environ", _BQ), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bq_list_datasets", {})
    assert len(result["datasets"]) == 1


@pytest.mark.asyncio
async def test_bq_insert_rows():
    from app.mcp.servers.bigquery_server import call_tool

    mc = mk_client(post=make_resp(data={"insertErrors": []}))
    with patch.dict("os.environ", _BQ), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "bq_insert_rows",
            {"dataset_id": "ds", "table_id": "tbl", "rows": [{"a": 1}, {"a": 2}]},
        )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_bq_unknown_tool():
    from app.mcp.servers.bigquery_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _BQ), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bq_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 5. Cloudflare
# ---------------------------------------------------------------------------

_CF = {"CLOUDFLARE_API_TOKEN": "cf-tok"}


@pytest.mark.asyncio
async def test_cf_no_token():
    from app.mcp.servers.cloudflare_server import call_tool

    with patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": ""}):
        result = await call_tool("cf_list_zones", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_cf_list_zones():
    from app.mcp.servers.cloudflare_server import call_tool

    mc = mk_client(get=make_resp(data={"result": [{"id": "z1", "name": "example.com", "status": "active", "plan": {"name": "Free"}}]}))
    with patch.dict("os.environ", _CF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("cf_list_zones", {})
    assert len(result["zones"]) == 1


@pytest.mark.asyncio
async def test_cf_purge_cache_all():
    from app.mcp.servers.cloudflare_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True}))
    with patch.dict("os.environ", _CF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("cf_purge_cache", {"zone_id": "z1", "purge_everything": True})
    assert result.get("purged") is True


@pytest.mark.asyncio
async def test_cf_create_dns_record():
    from app.mcp.servers.cloudflare_server import call_tool

    mc = mk_client(post=make_resp(data={"result": {"id": "rec1", "name": "www.example.com", "type": "A"}}))
    with patch.dict("os.environ", _CF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "cf_create_dns_record",
            {"zone_id": "z1", "type": "A", "name": "www", "content": "1.2.3.4"},
        )
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_cf_unknown_tool():
    from app.mcp.servers.cloudflare_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _CF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("cf_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 6. Cloudinary
# ---------------------------------------------------------------------------

_CLD = {
    "CLOUDINARY_CLOUD_NAME": "mycloud",
    "CLOUDINARY_API_KEY": "cld-key",
    "CLOUDINARY_API_SECRET": "cld-secret",
}


@pytest.mark.asyncio
async def test_cloudinary_no_key():
    from app.mcp.servers.cloudinary_server import call_tool

    with patch.dict("os.environ", {"CLOUDINARY_API_KEY": ""}):
        result = await call_tool("cloudinary_list_resources", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_cloudinary_list_resources():
    from app.mcp.servers.cloudinary_server import call_tool

    mc = mk_client(get=make_resp(data={"resources": [{"public_id": "img1", "secure_url": "https://res.cloudinary.com/x", "format": "jpg", "bytes": 100}]}))
    with patch.dict("os.environ", _CLD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("cloudinary_list_resources", {"resource_type": "image"})
    assert len(result["resources"]) == 1


@pytest.mark.asyncio
async def test_cloudinary_transform_image():
    from app.mcp.servers.cloudinary_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _CLD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "cloudinary_transform_image",
            {"public_id": "my_img", "width": 300, "height": 200, "crop": "fill"},
        )
    assert "url" in result
    assert "w_300" in result["url"]


@pytest.mark.asyncio
async def test_cloudinary_get_usage_stats():
    from app.mcp.servers.cloudinary_server import call_tool

    mc = mk_client(get=make_resp(data={"storage": {"usage": 1024}}))
    with patch.dict("os.environ", _CLD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("cloudinary_get_usage_stats", {})
    assert "storage" in result


@pytest.mark.asyncio
async def test_cloudinary_unknown_tool():
    from app.mcp.servers.cloudinary_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _CLD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("cloudinary_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 7. Firebase
# ---------------------------------------------------------------------------

_FB = {"FIREBASE_ACCESS_TOKEN": "fb-tok", "FIREBASE_PROJECT_ID": "my-proj"}


@pytest.mark.asyncio
async def test_firebase_no_token():
    from app.mcp.servers.firebase_server import call_tool

    with patch.dict("os.environ", {"FIREBASE_ACCESS_TOKEN": ""}):
        result = await call_tool("firestore_get_document", {"collection": "c", "document_id": "d1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_firestore_get_document():
    from app.mcp.servers.firebase_server import call_tool

    doc_data = {"fields": {"name": {"stringValue": "Alice"}, "age": {"integerValue": "30"}}, "createTime": "2024-01-01T00:00:00Z"}
    mc = mk_client(get=make_resp(data=doc_data))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firestore_get_document", {"collection": "users", "document_id": "u1"})
    assert result["data"]["name"] == "Alice"


@pytest.mark.asyncio
async def test_firestore_create_document():
    from app.mcp.servers.firebase_server import call_tool

    mc = mk_client(post=make_resp(data={"name": "projects/p/databases/(default)/documents/users/newid123", "createTime": "2024-01-01"}))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firestore_create_document", {"collection": "users", "data": {"name": "Bob"}})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_firestore_delete_document():
    from app.mcp.servers.firebase_server import call_tool

    mc = mk_client(delete=make_resp(status=200))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firestore_delete_document", {"collection": "users", "document_id": "u1"})
    assert result.get("deleted") is True


@pytest.mark.asyncio
async def test_firebase_unknown_tool():
    from app.mcp.servers.firebase_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firebase_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 8. Figma
# ---------------------------------------------------------------------------

_FG = {"FIGMA_ACCESS_TOKEN": "fg-tok"}


@pytest.mark.asyncio
async def test_figma_no_token():
    from app.mcp.servers.figma_server import call_tool

    with patch.dict("os.environ", {"FIGMA_ACCESS_TOKEN": ""}):
        result = await call_tool("figma_list_files", {"project_id": "p1"})
    assert "error" in result


@pytest.mark.asyncio
async def test_figma_list_files():
    from app.mcp.servers.figma_server import call_tool

    mc = mk_client(get=make_resp(data={"files": [{"key": "abc", "name": "Design System", "last_modified": "2024-01-01", "thumbnail_url": "https://x.com/t"}]}))
    with patch.dict("os.environ", _FG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("figma_list_files", {"project_id": "proj1"})
    assert len(result["files"]) == 1


@pytest.mark.asyncio
async def test_figma_get_components():
    from app.mcp.servers.figma_server import call_tool

    mc = mk_client(get=make_resp(data={"meta": {"components": [{"key": "comp1", "name": "Button", "description": ""}]}}))
    with patch.dict("os.environ", _FG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("figma_get_components", {"file_key": "abc123"})
    assert len(result["components"]) == 1


@pytest.mark.asyncio
async def test_figma_unknown_tool():
    from app.mcp.servers.figma_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _FG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("figma_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 9. Filestack
# ---------------------------------------------------------------------------

_FSK = {"FILESTACK_API_KEY": "fsk-key"}


@pytest.mark.asyncio
async def test_filestack_no_key():
    from app.mcp.servers.filestack_server import call_tool

    with patch.dict("os.environ", {"FILESTACK_API_KEY": ""}):
        result = await call_tool("filestack_get_file_info", {"handle": "abc"})
    assert "error" in result


@pytest.mark.asyncio
async def test_filestack_get_file_info():
    from app.mcp.servers.filestack_server import call_tool

    mc = mk_client(get=make_resp(data={"filename": "img.png", "size": 1024, "mimetype": "image/png"}))
    with patch.dict("os.environ", _FSK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("filestack_get_file_info", {"handle": "abc123"})
    assert result.get("filename") == "img.png"


@pytest.mark.asyncio
async def test_filestack_transform_image():
    from app.mcp.servers.filestack_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _FSK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "filestack_transform_image",
            {"handle": "abc", "width": 400, "height": 300},
        )
    assert "url" in result
    assert "abc" in result["url"]


@pytest.mark.asyncio
async def test_filestack_unknown_tool():
    from app.mcp.servers.filestack_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _FSK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("filestack_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 10. Loom
# ---------------------------------------------------------------------------

_LM = {"LOOM_API_KEY": "loom-key"}


@pytest.mark.asyncio
async def test_loom_no_key():
    from app.mcp.servers.loom_server import call_tool

    with patch.dict("os.environ", {"LOOM_API_KEY": ""}):
        result = await call_tool("loom_list_videos", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_loom_list_videos():
    from app.mcp.servers.loom_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "v1", "title": "Demo", "duration": 120, "created_at": "2024-01-01", "share_url": "https://loom.com/v1"}]}))
    with patch.dict("os.environ", _LM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loom_list_videos", {})
    assert len(result["videos"]) == 1


@pytest.mark.asyncio
async def test_loom_get_video_transcript():
    from app.mcp.servers.loom_server import call_tool

    mc = mk_client(get=make_resp(data={"transcript": [{"text": "Hello world", "start": 0}], "language": "en"}))
    with patch.dict("os.environ", _LM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loom_get_video_transcript", {"video_id": "v1"})
    assert result["language"] == "en"


@pytest.mark.asyncio
async def test_loom_unknown_tool():
    from app.mcp.servers.loom_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _LM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loom_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 11. SonarQube
# ---------------------------------------------------------------------------

_SQ = {"SONARQUBE_TOKEN": "sq-tok", "SONARQUBE_URL": "https://sonarcloud.io"}


@pytest.mark.asyncio
async def test_sq_no_token():
    from app.mcp.servers.sonarqube_server import call_tool

    with patch.dict("os.environ", {"SONARQUBE_TOKEN": ""}):
        result = await call_tool("sq_list_projects", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_sq_list_projects():
    from app.mcp.servers.sonarqube_server import call_tool

    mc = mk_client(get=make_resp(data={"components": [{"key": "my-proj", "name": "My Project", "visibility": "public", "lastAnalysisDate": "2024-01-01"}], "paging": {"total": 1}}))
    with patch.dict("os.environ", _SQ), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sq_list_projects", {})
    assert len(result["projects"]) == 1


@pytest.mark.asyncio
async def test_sq_get_quality_gate():
    from app.mcp.servers.sonarqube_server import call_tool

    mc = mk_client(get=make_resp(data={"projectStatus": {"status": "OK", "conditions": []}}))
    with patch.dict("os.environ", _SQ), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sq_get_quality_gate", {"project_key": "my-proj"})
    assert result["status"] == "OK"


@pytest.mark.asyncio
async def test_sq_unknown_tool():
    from app.mcp.servers.sonarqube_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SQ), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sq_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 12. Bitly
# ---------------------------------------------------------------------------

_BT = {"BITLY_ACCESS_TOKEN": "bitly-tok"}


@pytest.mark.asyncio
async def test_bitly_no_token():
    from app.mcp.servers.bitly_server import call_tool

    with patch.dict("os.environ", {"BITLY_ACCESS_TOKEN": ""}):
        result = await call_tool("bitly_shorten_url", {"long_url": "https://example.com"})
    assert "error" in result


@pytest.mark.asyncio
async def test_bitly_shorten_url():
    from app.mcp.servers.bitly_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "bit.ly/abc123", "link": "https://bit.ly/abc123", "long_url": "https://example.com", "created_at": "2024-01-01T00:00:00+0000"}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bitly_shorten_url", {"long_url": "https://example.com"})
    assert result["link"] == "https://bit.ly/abc123"


@pytest.mark.asyncio
async def test_bitly_get_click_metrics():
    from app.mcp.servers.bitly_server import call_tool

    mc = mk_client(get=make_resp(data={"link_clicks": [{"date": "2024-01-01", "clicks": 100}], "unit": "day", "units": 30}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bitly_get_click_metrics", {"bitlink_id": "bit.ly/abc123"})
    assert len(result["link_clicks"]) == 1


@pytest.mark.asyncio
async def test_bitly_unknown_tool():
    from app.mcp.servers.bitly_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bitly_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 13. Typeform
# ---------------------------------------------------------------------------

_TF = {"TYPEFORM_ACCESS_TOKEN": "tf-tok"}


@pytest.mark.asyncio
async def test_typeform_no_token():
    from app.mcp.servers.typeform_server import call_tool

    with patch.dict("os.environ", {"TYPEFORM_ACCESS_TOKEN": ""}):
        result = await call_tool("typeform_list_forms", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_typeform_list_forms():
    from app.mcp.servers.typeform_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": "form1", "title": "Survey", "last_updated_at": "2024-01-01"}], "total_items": 1}))
    with patch.dict("os.environ", _TF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("typeform_list_forms", {})
    assert result["total_items"] == 1


@pytest.mark.asyncio
async def test_typeform_create_form():
    from app.mcp.servers.typeform_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "new-form", "title": "My Form", "_links": {"display": "https://form.typeform.com/to/new-form"}}))
    with patch.dict("os.environ", _TF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("typeform_create_form", {"title": "My Form"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_typeform_unknown_tool():
    from app.mcp.servers.typeform_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _TF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("typeform_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 14. JotForm
# ---------------------------------------------------------------------------

_JF = {"JOTFORM_API_KEY": "jf-key"}


@pytest.mark.asyncio
async def test_jotform_no_key():
    from app.mcp.servers.jotform_server import call_tool

    with patch.dict("os.environ", {"JOTFORM_API_KEY": ""}):
        result = await call_tool("jotform_list_forms", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_jotform_list_forms():
    from app.mcp.servers.jotform_server import call_tool

    mc = mk_client(get=make_resp(data={"content": [{"id": "form1", "title": "Contact", "status": "ENABLED", "count": "5", "created_at": "2024-01-01"}], "resultSet": {"count": 1}}))
    with patch.dict("os.environ", _JF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jotform_list_forms", {})
    assert len(result["forms"]) == 1


@pytest.mark.asyncio
async def test_jotform_get_submissions():
    from app.mcp.servers.jotform_server import call_tool

    mc = mk_client(get=make_resp(data={"content": [{"id": "sub1", "answers": {}}], "resultSet": {"count": 1}}))
    with patch.dict("os.environ", _JF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jotform_get_submissions", {"form_id": "form1"})
    assert len(result["submissions"]) == 1


@pytest.mark.asyncio
async def test_jotform_unknown_tool():
    from app.mcp.servers.jotform_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _JF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jotform_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 15. SurveyMonkey
# ---------------------------------------------------------------------------

_SM = {"SURVEYMONKEY_ACCESS_TOKEN": "sm-tok"}


@pytest.mark.asyncio
async def test_sm_no_token():
    from app.mcp.servers.surveymonkey_server import call_tool

    with patch.dict("os.environ", {"SURVEYMONKEY_ACCESS_TOKEN": ""}):
        result = await call_tool("sm_list_surveys", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_sm_list_surveys():
    from app.mcp.servers.surveymonkey_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "s1", "title": "NPS", "response_count": 42, "date_modified": "2024-01-01"}], "total": 1}))
    with patch.dict("os.environ", _SM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sm_list_surveys", {})
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_sm_create_survey():
    from app.mcp.servers.surveymonkey_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "new-survey", "title": "Product Feedback", "href": "https://api.surveymonkey.com/v3/surveys/new-survey"}))
    with patch.dict("os.environ", _SM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sm_create_survey", {"title": "Product Feedback"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_sm_unknown_tool():
    from app.mcp.servers.surveymonkey_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sm_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 16. Formstack
# ---------------------------------------------------------------------------

_FS2 = {"FORMSTACK_API_KEY": "fs-key"}


@pytest.mark.asyncio
async def test_formstack_no_key():
    from app.mcp.servers.formstack_server import call_tool

    with patch.dict("os.environ", {"FORMSTACK_API_KEY": ""}):
        result = await call_tool("formstack_list_forms", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_formstack_list_forms():
    from app.mcp.servers.formstack_server import call_tool

    mc = mk_client(get=make_resp(data={"forms": [{"id": "f1", "name": "Contact Us", "submissions": 5, "views": 100, "url": "https://x.formstack.com/f1"}], "total": 1}))
    with patch.dict("os.environ", _FS2), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("formstack_list_forms", {})
    assert len(result["forms"]) == 1


@pytest.mark.asyncio
async def test_formstack_create_form():
    from app.mcp.servers.formstack_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "new-f", "name": "New Form", "url": "https://x.formstack.com/new-f"}))
    with patch.dict("os.environ", _FS2), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("formstack_create_form", {"name": "New Form"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_formstack_unknown_tool():
    from app.mcp.servers.formstack_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _FS2), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("formstack_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 17. SignNow
# ---------------------------------------------------------------------------

_SN = {"SIGNNOW_ACCESS_TOKEN": "sn-tok"}


@pytest.mark.asyncio
async def test_signnow_no_token():
    from app.mcp.servers.signnow_server import call_tool

    with patch.dict("os.environ", {"SIGNNOW_ACCESS_TOKEN": ""}):
        result = await call_tool("signnow_list_documents", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_signnow_list_documents():
    from app.mcp.servers.signnow_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "doc1", "document_name": "Contract.pdf", "status": "pending", "updated": "2024-01-01"}]))
    with patch.dict("os.environ", _SN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("signnow_list_documents", {})
    assert len(result["documents"]) == 1


@pytest.mark.asyncio
async def test_signnow_check_document_status():
    from app.mcp.servers.signnow_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "doc1", "status": "completed", "updated": "2024-01-01", "signatures": [{"id": "s1"}], "pending_invites": []}))
    with patch.dict("os.environ", _SN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("signnow_check_document_status", {"document_id": "doc1"})
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_signnow_unknown_tool():
    from app.mcp.servers.signnow_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("signnow_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 18. Evernote
# ---------------------------------------------------------------------------

_EV = {"EVERNOTE_ACCESS_TOKEN": "ev-tok"}


@pytest.mark.asyncio
async def test_evernote_no_token():
    from app.mcp.servers.evernote_server import call_tool

    with patch.dict("os.environ", {"EVERNOTE_ACCESS_TOKEN": ""}):
        result = await call_tool("evernote_list_notebooks", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_evernote_list_notebooks():
    from app.mcp.servers.evernote_server import call_tool

    mc = mk_client(get=make_resp(data=[{"guid": "nb1", "name": "Personal", "defaultNotebook": True}]))
    with patch.dict("os.environ", _EV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("evernote_list_notebooks", {})
    assert len(result["notebooks"]) == 1


@pytest.mark.asyncio
async def test_evernote_create_note():
    from app.mcp.servers.evernote_server import call_tool

    mc = mk_client(post=make_resp(data={"guid": "note1", "title": "My Note"}))
    with patch.dict("os.environ", _EV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("evernote_create_note", {"title": "My Note", "content": "Hello"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_evernote_unknown_tool():
    from app.mcp.servers.evernote_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _EV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("evernote_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 19. Microsoft Excel
# ---------------------------------------------------------------------------

_MS = {"MICROSOFT_ACCESS_TOKEN": "ms-tok"}


@pytest.mark.asyncio
async def test_excel_no_token():
    from app.mcp.servers.microsoft_excel_server import call_tool

    with patch.dict("os.environ", {"MICROSOFT_ACCESS_TOKEN": ""}):
        result = await call_tool("excel_list_workbooks", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_excel_get_worksheet():
    from app.mcp.servers.microsoft_excel_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "ws1", "name": "Sheet1", "position": 0, "visibility": "Visible"}]}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("excel_get_worksheet", {"item_id": "item1"})
    assert len(result["worksheets"]) == 1


@pytest.mark.asyncio
async def test_excel_read_range():
    from app.mcp.servers.microsoft_excel_server import call_tool

    mc = mk_client(get=make_resp(data={"values": [["A", "B"], [1, 2]], "rowCount": 2, "columnCount": 2, "address": "Sheet1!A1:B2"}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("excel_read_range", {"item_id": "i1", "sheet_name": "Sheet1", "range_address": "A1:B2"})
    assert result["row_count"] == 2


@pytest.mark.asyncio
async def test_excel_unknown_tool():
    from app.mcp.servers.microsoft_excel_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("excel_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 20. Microsoft Outlook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_no_token():
    from app.mcp.servers.microsoft_outlook_server import call_tool

    with patch.dict("os.environ", {"MICROSOFT_ACCESS_TOKEN": ""}):
        result = await call_tool("outlook_list_messages", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_outlook_list_messages():
    from app.mcp.servers.microsoft_outlook_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "m1", "subject": "Hello", "from": {"emailAddress": {"address": "a@b.com"}}, "receivedDateTime": "2024-01-01", "isRead": False}]}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("outlook_list_messages", {})
    assert len(result["messages"]) == 1


@pytest.mark.asyncio
async def test_outlook_send_email():
    from app.mcp.servers.microsoft_outlook_server import call_tool

    send_resp = make_resp(status=202)
    send_resp.raise_for_status = MagicMock()
    mc = mk_client(post=send_resp)
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "outlook_send_email",
            {"subject": "Test", "to_recipients": ["b@example.com"], "body": "Hi"},
        )
    assert result.get("sent") is True


@pytest.mark.asyncio
async def test_outlook_unknown_tool():
    from app.mcp.servers.microsoft_outlook_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("outlook_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 21. Microsoft OneNote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_onenote_no_token():
    from app.mcp.servers.microsoft_onenote_server import call_tool

    with patch.dict("os.environ", {"MICROSOFT_ACCESS_TOKEN": ""}):
        result = await call_tool("onenote_list_notebooks", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_onenote_list_notebooks():
    from app.mcp.servers.microsoft_onenote_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "nb1", "displayName": "Work", "lastModifiedDateTime": "2024-01-01"}]}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onenote_list_notebooks", {})
    assert len(result["notebooks"]) == 1


@pytest.mark.asyncio
async def test_onenote_list_pages():
    from app.mcp.servers.microsoft_onenote_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "p1", "title": "Meeting Notes", "lastModifiedDateTime": "2024-01-01", "contentUrl": "https://graph.microsoft.com/v1.0/me/onenote/pages/p1/content"}]}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onenote_list_pages", {"section_id": "s1"})
    assert len(result["pages"]) == 1


@pytest.mark.asyncio
async def test_onenote_unknown_tool():
    from app.mcp.servers.microsoft_onenote_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onenote_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 22. Microsoft To Do
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_todo_no_token():
    from app.mcp.servers.microsoft_todo_server import call_tool

    with patch.dict("os.environ", {"MICROSOFT_ACCESS_TOKEN": ""}):
        result = await call_tool("todo_list_task_lists", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_todo_list_task_lists():
    from app.mcp.servers.microsoft_todo_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "tl1", "displayName": "Work", "isOwner": True, "isShared": False}]}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todo_list_task_lists", {})
    assert len(result["lists"]) == 1


@pytest.mark.asyncio
async def test_todo_create_task():
    from app.mcp.servers.microsoft_todo_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "t1", "title": "Buy groceries", "status": "notStarted"}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todo_create_task", {"list_id": "tl1", "title": "Buy groceries"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_todo_complete_task():
    from app.mcp.servers.microsoft_todo_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "t1", "status": "completed"}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todo_complete_task", {"list_id": "tl1", "task_id": "t1"})
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_todo_unknown_tool():
    from app.mcp.servers.microsoft_todo_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todo_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 23. Google Forms
# ---------------------------------------------------------------------------

_GG = {"GOOGLE_ACCESS_TOKEN": "gg-tok"}


@pytest.mark.asyncio
async def test_gforms_no_token():
    from app.mcp.servers.google_forms_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        result = await call_tool("gforms_list_forms", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_gforms_list_forms():
    from app.mcp.servers.google_forms_server import call_tool

    mc = mk_client(get=make_resp(data={"files": [{"id": "form1", "name": "Survey", "createdTime": "2024-01-01", "modifiedTime": "2024-01-02"}]}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gforms_list_forms", {})
    assert len(result["forms"]) == 1


@pytest.mark.asyncio
async def test_gforms_create_form():
    from app.mcp.servers.google_forms_server import call_tool

    mc = mk_client(post=make_resp(data={"formId": "new-form", "info": {"title": "My Survey"}, "responderUri": "https://docs.google.com/forms/d/new-form/viewform"}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gforms_create_form", {"title": "My Survey"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_gforms_get_responses():
    from app.mcp.servers.google_forms_server import call_tool

    mc = mk_client(get=make_resp(data={"responses": [{"responseId": "r1"}]}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gforms_get_form_responses", {"form_id": "form1"})
    assert result["total_responses"] == 1


@pytest.mark.asyncio
async def test_gforms_unknown_tool():
    from app.mcp.servers.google_forms_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gforms_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 24. Google Slides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slides_no_token():
    from app.mcp.servers.google_slides_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        result = await call_tool("slides_list_presentations", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_slides_list_presentations():
    from app.mcp.servers.google_slides_server import call_tool

    mc = mk_client(get=make_resp(data={"files": [{"id": "pres1", "name": "Q1 Review", "createdTime": "2024-01-01", "modifiedTime": "2024-01-02"}]}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slides_list_presentations", {})
    assert len(result["presentations"]) == 1


@pytest.mark.asyncio
async def test_slides_create_presentation():
    from app.mcp.servers.google_slides_server import call_tool

    mc = mk_client(post=make_resp(data={"presentationId": "pres-new", "title": "New Deck", "slides": [{"objectId": "s1"}]}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slides_create_presentation", {"title": "New Deck"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_slides_export_presentation():
    from app.mcp.servers.google_slides_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "slides_export_presentation",
            {"presentation_id": "pres1", "export_format": "pdf"},
        )
    assert "export_url" in result
    assert "pres1" in result["export_url"]


@pytest.mark.asyncio
async def test_slides_unknown_tool():
    from app.mcp.servers.google_slides_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slides_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# 25. Google Tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gtasks_no_token():
    from app.mcp.servers.google_tasks_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        result = await call_tool("gtasks_list_task_lists", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_gtasks_list_task_lists():
    from app.mcp.servers.google_tasks_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": "tl1", "title": "My Tasks", "updated": "2024-01-01"}]}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gtasks_list_task_lists", {})
    assert len(result["task_lists"]) == 1


@pytest.mark.asyncio
async def test_gtasks_create_task():
    from app.mcp.servers.google_tasks_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "t1", "title": "Write tests", "status": "needsAction"}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gtasks_create_task", {"tasklist_id": "@default", "title": "Write tests"})
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_gtasks_complete_task():
    from app.mcp.servers.google_tasks_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "t1", "status": "completed"}))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gtasks_complete_task", {"tasklist_id": "@default", "task_id": "t1"})
    assert result.get("completed") is True


@pytest.mark.asyncio
async def test_gtasks_delete_task():
    from app.mcp.servers.google_tasks_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gtasks_delete_task", {"tasklist_id": "@default", "task_id": "t1"})
    assert result.get("deleted") is True


@pytest.mark.asyncio
async def test_gtasks_unknown_tool():
    from app.mcp.servers.google_tasks_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gtasks_nonexistent", {})
    assert "error" in result
