"""Dispatch-level tests for cloud/infra MCP servers.

Covers: AWS S3, IAM, Lambda, CloudWatch (boto3),
        Kubernetes (httpx), GCS, Google Drive, Sheets, Calendar, Docs,
        Google Analytics, Google Ads, Google Search Console.
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


_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "AKIATEST",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "AWS_REGION": "us-east-1",
}

# ---------------------------------------------------------------------------
# AWS S3 (boto3 via run_in_executor → patch _client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_list_buckets():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.list_buckets.return_value = {"Buckets": [{"Name": "my-bucket", "CreationDate": None}]}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_list_buckets", {})
    assert "buckets" in result
    assert result["buckets"][0]["name"] == "my-bucket"


@pytest.mark.asyncio
async def test_s3_list_objects():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "file.txt", "Size": 100, "LastModified": None, "ETag": '"abc"', "StorageClass": "STANDARD"}],
        "CommonPrefixes": [],
        "IsTruncated": False,
        "KeyCount": 1,
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_list_objects", {"bucket": "my-bucket"})
    assert "objects" in result
    assert result["objects"][0]["key"] == "file.txt"


@pytest.mark.asyncio
async def test_s3_get_object():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    body_mock = MagicMock()
    body_mock.read.return_value = b"Hello, world!"
    mock_s3.get_object.return_value = {
        "Body": body_mock,
        "ContentType": "text/plain",
        "ContentLength": 13,
        "LastModified": None,
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_get_object", {"bucket": "my-bucket", "key": "file.txt"})
    assert result["content"] == "Hello, world!"
    assert result["encoding"] == "utf-8"


@pytest.mark.asyncio
async def test_s3_put_object():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {"ETag": '"abc123"', "VersionId": None}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool(
            "s3_put_object",
            {"bucket": "my-bucket", "key": "file.txt", "content": "Hello"},
        )
    assert result["key"] == "file.txt"


@pytest.mark.asyncio
async def test_s3_delete_object():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.delete_object.return_value = {}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_delete_object", {"bucket": "my-bucket", "key": "file.txt"})
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_s3_generate_presigned_url():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/my-bucket/file.txt?X-Amz-Signature=abc"
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool(
            "s3_generate_presigned_url", {"bucket": "my-bucket", "key": "file.txt"}
        )
    assert "url" in result
    assert "s3.amazonaws.com" in result["url"]


@pytest.mark.asyncio
async def test_s3_create_bucket():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.create_bucket.return_value = {}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_create_bucket", {"bucket": "new-bucket"})
    assert result["created"] is True


@pytest.mark.asyncio
async def test_s3_unknown_tool():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_nonexistent_tool", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# AWS IAM (boto3 via run_in_executor → patch _client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iam_list_users():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.list_users.return_value = {"Users": [{"UserName": "alice", "UserId": "AID123", "Arn": "arn:aws:iam::123:user/alice", "CreateDate": None}]}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool("iam_list_users", {})
    assert "users" in result


@pytest.mark.asyncio
async def test_iam_list_roles():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.list_roles.return_value = {"Roles": [{"RoleName": "MyRole", "RoleId": "ROLE1", "Arn": "arn:aws:iam::123:role/MyRole", "CreateDate": None}]}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool("iam_list_roles", {})
    assert "roles" in result


@pytest.mark.asyncio
async def test_iam_create_user():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.create_user.return_value = {"User": {"UserName": "bob", "UserId": "AID456", "Arn": "arn:aws:iam::123:user/bob", "CreateDate": None}}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool("iam_create_user", {"username": "bob"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_iam_attach_policy():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.attach_user_policy.return_value = {}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        # Must provide user_name, role_name, or group_name
        result = await call_tool(
            "iam_attach_policy",
            {"user_name": "bob", "policy_arn": "arn:aws:iam::aws:policy/ReadOnlyAccess"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_iam_list_policies():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.list_policies.return_value = {"Policies": [{"PolicyName": "ReadOnly", "PolicyId": "P1", "Arn": "arn:aws:iam::aws:policy/ReadOnly", "CreateDate": None}]}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool("iam_list_policies", {})
    assert "policies" in result


@pytest.mark.asyncio
async def test_iam_get_user():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.get_user.return_value = {"User": {"UserName": "alice", "UserId": "AID123", "Arn": "arn:aws:iam::123:user/alice", "CreateDate": None}}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool("iam_get_user", {"username": "alice"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_iam_list_attached_user_policies():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    mock_iam.list_attached_user_policies.return_value = {"AttachedPolicies": [{"PolicyName": "ReadOnly", "PolicyArn": "arn:aws:iam::aws:policy/ReadOnly"}]}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool("iam_list_attached_user_policies", {"username": "alice"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# AWS Lambda (boto3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lambda_list_functions():
    from app.mcp.servers.aws_lambda_server import call_tool

    mock_lam = MagicMock()
    mock_lam.list_functions.return_value = {"Functions": [{"FunctionName": "my-fn", "Runtime": "python3.12", "Handler": "handler.main", "FunctionArn": "arn:...", "LastModified": "2024-01-01"}]}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_lambda_server._client", return_value=mock_lam):
        result = await call_tool("lambda_list_functions", {})
    assert "functions" in result


@pytest.mark.asyncio
async def test_lambda_invoke_function():
    import json
    from app.mcp.servers.aws_lambda_server import call_tool

    mock_lam = MagicMock()
    payload_bytes = json.dumps({"result": "ok"}).encode()
    mock_lam.invoke.return_value = {
        "StatusCode": 200,
        "Payload": MagicMock(read=MagicMock(return_value=payload_bytes)),
        "FunctionError": None,
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_lambda_server._client", return_value=mock_lam):
        result = await call_tool("lambda_invoke_function", {"function_name": "my-fn"})
    assert result["status_code"] == 200


@pytest.mark.asyncio
async def test_lambda_get_function():
    from app.mcp.servers.aws_lambda_server import call_tool

    mock_lam = MagicMock()
    mock_lam.get_function.return_value = {
        "Configuration": {"FunctionName": "my-fn", "FunctionArn": "arn:...", "Runtime": "python3.12", "Handler": "handler.main", "CodeSize": 1024, "LastModified": "2024-01-01", "MemorySize": 128, "Timeout": 30, "State": "Active"},
        "Code": {"Location": "https://s3.amazonaws.com/..."}
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_lambda_server._client", return_value=mock_lam):
        result = await call_tool("lambda_get_function", {"function_name": "my-fn"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_lambda_update_function_code():
    from app.mcp.servers.aws_lambda_server import call_tool

    mock_lam = MagicMock()
    mock_lam.update_function_code.return_value = {"FunctionName": "my-fn", "FunctionArn": "arn:...", "LastModified": "2024-01-02", "CodeSize": 2048}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_lambda_server._client", return_value=mock_lam):
        result = await call_tool(
            "lambda_update_function_code",
            {"function_name": "my-fn", "s3_bucket": "code-bucket", "s3_key": "my-fn.zip"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_lambda_list_aliases():
    from app.mcp.servers.aws_lambda_server import call_tool

    mock_lam = MagicMock()
    mock_lam.list_aliases.return_value = {"Aliases": [{"Name": "live", "AliasArn": "arn:...", "FunctionVersion": "$LATEST", "Description": ""}]}
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_lambda_server._client", return_value=mock_lam):
        result = await call_tool("lambda_list_aliases", {"function_name": "my-fn"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# AWS CloudWatch (boto3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloudwatch_get_metric_data():
    from app.mcp.servers.aws_cloudwatch_server import call_tool
    from datetime import datetime, timezone

    # CloudWatch uses _cw_client() not _client()
    # Timestamp must be a datetime object (server calls .isoformat())
    mock_cw = MagicMock()
    mock_cw.get_metric_statistics.return_value = {
        "Datapoints": [{"Timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc), "Average": 50.0, "Unit": "Percent"}],
        "Label": "CPUUtilization",
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_cloudwatch_server._cw_client", return_value=mock_cw):
        result = await call_tool(
            "cloudwatch_get_metric_data",
            {
                "namespace": "AWS/EC2",
                "metric_name": "CPUUtilization",
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-02T00:00:00",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_cloudwatch_list_alarms():
    from app.mcp.servers.aws_cloudwatch_server import call_tool

    mock_cw = MagicMock()
    mock_cw.describe_alarms.return_value = {
        "MetricAlarms": [{"AlarmName": "HighCPU", "StateValue": "ALARM", "AlarmDescription": "", "Namespace": "AWS/EC2", "MetricName": "CPUUtilization", "Threshold": 80.0, "ComparisonOperator": "GreaterThanThreshold"}]
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_cloudwatch_server._cw_client", return_value=mock_cw):
        result = await call_tool("cloudwatch_list_alarms", {})
    assert "alarms" in result


@pytest.mark.asyncio
async def test_cloudwatch_put_metric_alarm():
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
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_cloudwatch_get_log_events():
    from app.mcp.servers.aws_cloudwatch_server import call_tool

    # Log events use _logs_client(); server args are log_group_name, log_stream_name
    mock_cw = MagicMock()
    mock_cw.get_log_events.return_value = {
        "events": [{"timestamp": 1704067200000, "message": "INFO: Request processed", "ingestionTime": 1704067201000}],
        "nextForwardToken": None,
    }
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_cloudwatch_server._logs_client", return_value=mock_cw):
        result = await call_tool(
            "cloudwatch_get_log_events",
            {"log_group_name": "/aws/lambda/my-fn", "log_stream_name": "2024/01/01/[$LATEST]abc"},
        )
    assert "error" not in result


# ---------------------------------------------------------------------------
# Kubernetes (httpx)
# ---------------------------------------------------------------------------

_K8S = {
    "KUBE_API_SERVER": "https://k8s.example.com:6443",
    "KUBE_TOKEN": "k8s-token",
    "KUBE_NAMESPACE": "default",
}


@pytest.mark.asyncio
async def test_k8s_list_pods():
    from app.mcp.servers.kubernetes_server import call_tool

    data = {"items": [{"metadata": {"name": "pod-1", "namespace": "default", "uid": "uid1", "creationTimestamp": None, "labels": {}}, "status": {"phase": "Running", "podIP": "10.0.0.1", "conditions": []}, "spec": {"nodeName": "node1", "containers": [{"name": "app", "image": "nginx:latest"}]}}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_list_pods", {})
    assert "pods" in result


@pytest.mark.asyncio
async def test_k8s_get_pod():
    from app.mcp.servers.kubernetes_server import call_tool

    data = {"metadata": {"name": "pod-1", "namespace": "default", "uid": "uid1", "creationTimestamp": None, "labels": {}}, "status": {"phase": "Running", "podIP": "10.0.0.1", "conditions": []}, "spec": {"nodeName": "node1", "containers": []}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_get_pod", {"name": "pod-1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_k8s_delete_pod():
    from app.mcp.servers.kubernetes_server import call_tool

    mc = mk_client(delete=make_resp(data={"kind": "Pod", "metadata": {"name": "pod-1"}}))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_delete_pod", {"name": "pod-1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_k8s_list_deployments():
    from app.mcp.servers.kubernetes_server import call_tool

    data = {"items": [{"metadata": {"name": "my-app", "namespace": "default", "labels": {}}, "spec": {"replicas": 3, "selector": {"matchLabels": {}}}, "status": {"readyReplicas": 3, "availableReplicas": 3, "updatedReplicas": 3}}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_list_deployments", {})
    assert "deployments" in result


@pytest.mark.asyncio
async def test_k8s_scale_deployment():
    from app.mcp.servers.kubernetes_server import call_tool

    data = {"spec": {"replicas": 5}}
    mc = mk_client(patch=make_resp(data=data))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "k8s_scale_deployment", {"name": "my-app", "replicas": 5}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_k8s_list_services():
    from app.mcp.servers.kubernetes_server import call_tool

    data = {"items": [{"metadata": {"name": "my-svc", "namespace": "default"}, "spec": {"type": "ClusterIP", "clusterIP": "10.96.0.1", "ports": []}, "status": {"loadBalancer": {}}}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_list_services", {})
    assert "services" in result


@pytest.mark.asyncio
async def test_k8s_list_namespaces():
    from app.mcp.servers.kubernetes_server import call_tool

    data = {"items": [{"metadata": {"name": "default"}, "status": {"phase": "Active"}}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_list_namespaces", {})
    assert "namespaces" in result


@pytest.mark.asyncio
async def test_k8s_get_logs():
    from app.mcp.servers.kubernetes_server import call_tool

    # Server uses arguments["pod_name"] not arguments["name"]
    mc = mk_client()
    mc.get.return_value.text = "INFO: Started\nINFO: Running"
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_get_logs", {"pod_name": "pod-1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_k8s_missing_env():
    from app.mcp.servers.kubernetes_server import call_tool

    with patch.dict("os.environ", {"KUBE_API_SERVER": ""}):
        os.environ.pop("KUBE_API_SERVER", None)
        result = await call_tool("k8s_list_pods", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Cloud Storage (httpx)
# ---------------------------------------------------------------------------

_GCS = {"GOOGLE_ACCESS_TOKEN": "gcs-tok"}  # GCS uses GOOGLE_ACCESS_TOKEN


@pytest.mark.asyncio
async def test_gcs_list_buckets():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    data = {"items": [{"id": "my-bucket", "name": "my-bucket", "location": "US", "storageClass": "STANDARD", "timeCreated": "2024-01-01"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gcs_list_buckets", {"project_id": "test-project"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gcs_list_objects():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    data = {"items": [{"name": "file.txt", "size": "100", "updated": "2024-01-01", "contentType": "text/plain", "md5Hash": "abc"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gcs_list_objects", {"bucket": "my-bucket"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gcs_get_object():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    data = {"name": "file.txt", "size": "100", "updated": "2024-01-01", "contentType": "text/plain", "md5Hash": "abc"}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gcs_get_object", {"bucket": "my-bucket", "object_name": "file.txt"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gcs_upload_object():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    import base64
    # GCS upload uses "content_base64" not "content"
    data = {"name": "new-file.txt", "size": "5", "updated": "2024-01-01", "contentType": "text/plain", "md5Hash": "xyz"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gcs_upload_object",
            {
                "bucket": "my-bucket",
                "object_name": "new-file.txt",
                "content_base64": base64.b64encode(b"Hello").decode(),
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gcs_delete_object():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gcs_delete_object", {"bucket": "my-bucket", "object_name": "file.txt"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gcs_create_bucket():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    # Server uses "bucket_name" not "bucket", plus "project_id"
    data = {"id": "new-bucket", "name": "new-bucket", "location": "US", "storageClass": "STANDARD"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gcs_create_bucket", {"bucket_name": "new-bucket", "project_id": "test-project"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gcs_missing_env():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)
        result = await call_tool("gcs_list_buckets", {"project_id": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Drive (httpx)
# ---------------------------------------------------------------------------

_GDRIVE = {"GOOGLE_ACCESS_TOKEN": "gdrive-tok"}


@pytest.mark.asyncio
async def test_drive_list_files():
    from app.mcp.servers.google_drive_server import call_tool

    data = {"files": [{"id": "f1", "name": "Doc.pdf", "mimeType": "application/pdf", "size": "1024", "modifiedTime": "2024-01-01", "webViewLink": "url"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_list_files", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_drive_get_file():
    from app.mcp.servers.google_drive_server import call_tool

    data = {"id": "f1", "name": "Doc.pdf", "mimeType": "application/pdf", "size": "1024", "modifiedTime": "2024-01-01", "webViewLink": "url", "parents": []}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_get_file", {"file_id": "f1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_drive_search_files():
    from app.mcp.servers.google_drive_server import call_tool

    data = {"files": [{"id": "f1", "name": "Report.pdf", "mimeType": "application/pdf", "size": "2048", "modifiedTime": "2024-01-01", "webViewLink": "url"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_search_files", {"query": "Report"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_drive_create_folder():
    from app.mcp.servers.google_drive_server import call_tool

    data = {"id": "folder1", "name": "My Folder", "mimeType": "application/vnd.google-apps.folder", "webViewLink": "url"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_create_folder", {"name": "My Folder"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_drive_delete_file():
    from app.mcp.servers.google_drive_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_delete_file", {"file_id": "f1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_drive_missing_env():
    from app.mcp.servers.google_drive_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        result = await call_tool("drive_list_files", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Sheets (httpx)
# ---------------------------------------------------------------------------

_GSHEETS = {"GOOGLE_ACCESS_TOKEN": "sheets-tok"}


@pytest.mark.asyncio
async def test_sheets_read_range():
    from app.mcp.servers.google_sheets_server import call_tool

    data = {"range": "Sheet1!A1:B2", "values": [["Name", "Score"], ["Alice", "95"]]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sheets_read_range",
            {"spreadsheet_id": "ss1", "range": "Sheet1!A1:B2"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sheets_write_range():
    from app.mcp.servers.google_sheets_server import call_tool

    data = {"spreadsheetId": "ss1", "updatedRange": "Sheet1!A1:B2", "updatedCells": 4}
    mc = mk_client(put=make_resp(data=data))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sheets_write_range",
            {"spreadsheet_id": "ss1", "range": "Sheet1!A1:B2", "values": [["Name", "Score"]]},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sheets_append_rows():
    from app.mcp.servers.google_sheets_server import call_tool

    data = {"spreadsheetId": "ss1", "updates": {"updatedRange": "Sheet1!A3:B3", "updatedCells": 2}}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sheets_append_rows",
            {"spreadsheet_id": "ss1", "range": "Sheet1!A1", "values": [["New Row"]]},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sheets_create_spreadsheet():
    from app.mcp.servers.google_sheets_server import call_tool

    data = {"spreadsheetId": "ss2", "properties": {"title": "New Sheet"}, "spreadsheetUrl": "url"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sheets_create_spreadsheet", {"title": "New Sheet"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sheets_list_sheets():
    from app.mcp.servers.google_sheets_server import call_tool

    data = {"spreadsheetId": "ss1", "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1", "index": 0}}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sheets_list_sheets", {"spreadsheet_id": "ss1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Calendar (httpx)
# ---------------------------------------------------------------------------

_GCAL = {"GOOGLE_ACCESS_TOKEN": "cal-tok"}


@pytest.mark.asyncio
async def test_calendar_list_events():
    from app.mcp.servers.google_calendar_server import call_tool

    data = {"items": [{"id": "e1", "summary": "Team Meeting", "start": {"dateTime": "2024-01-15T10:00:00Z"}, "end": {"dateTime": "2024-01-15T11:00:00Z"}, "htmlLink": "url", "status": "confirmed"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GCAL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendar_list_events", {"calendar_id": "primary"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendar_get_event():
    from app.mcp.servers.google_calendar_server import call_tool

    data = {"id": "e1", "summary": "Meeting", "start": {"dateTime": "2024-01-15T10:00:00Z"}, "end": {"dateTime": "2024-01-15T11:00:00Z"}, "htmlLink": "url", "description": "desc", "location": "", "status": "confirmed", "attendees": []}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GCAL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "calendar_get_event", {"calendar_id": "primary", "event_id": "e1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendar_create_event():
    from app.mcp.servers.google_calendar_server import call_tool

    data = {"id": "e2", "summary": "New Event", "htmlLink": "url", "start": {"dateTime": "2024-01-20T10:00:00Z"}, "end": {"dateTime": "2024-01-20T11:00:00Z"}}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GCAL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "calendar_create_event",
            {
                "calendar_id": "primary",
                "summary": "New Event",
                "start": "2024-01-20T10:00:00Z",
                "end": "2024-01-20T11:00:00Z",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendar_delete_event():
    from app.mcp.servers.google_calendar_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _GCAL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "calendar_delete_event", {"calendar_id": "primary", "event_id": "e1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendar_list_calendars():
    from app.mcp.servers.google_calendar_server import call_tool

    data = {"items": [{"id": "primary", "summary": "My Calendar", "description": "", "primary": True, "accessRole": "owner"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GCAL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendar_list_calendars", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendar_missing_env():
    from app.mcp.servers.google_calendar_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        result = await call_tool("calendar_list_events", {"calendar_id": "primary"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Docs (httpx)
# ---------------------------------------------------------------------------

_GDOCS = {"GOOGLE_ACCESS_TOKEN": "docs-tok"}


@pytest.mark.asyncio
async def test_docs_get_document():
    from app.mcp.servers.google_docs_server import call_tool

    data = {"documentId": "doc1", "title": "My Doc", "body": {"content": []}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GDOCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("docs_get_document", {"document_id": "doc1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docs_create_document():
    from app.mcp.servers.google_docs_server import call_tool

    data = {"documentId": "doc2", "title": "New Doc", "revisionId": "1"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GDOCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("docs_create_document", {"title": "New Doc"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docs_batch_update():
    from app.mcp.servers.google_docs_server import call_tool

    data = {"documentId": "doc1", "replies": []}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GDOCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "docs_batch_update",
            {"document_id": "doc1", "requests": [{"insertText": {"text": "Hello", "location": {"index": 1}}}]},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_docs_insert_text():
    from app.mcp.servers.google_docs_server import call_tool

    data = {"documentId": "doc1", "replies": [{}]}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GDOCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "docs_insert_text",
            {"document_id": "doc1", "text": "New text", "index": 1},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_docs_missing_env():
    from app.mcp.servers.google_docs_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        result = await call_tool("docs_get_document", {"document_id": "doc1"})
    assert "error" in result
