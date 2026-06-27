"""Tests for DevOps & cloud MCP connector servers.

These tests verify that all server modules are importable and expose the
expected TOOL_DEFINITIONS interface without requiring live credentials.
"""
from __future__ import annotations


def test_devops_servers_importable():
    from app.mcp.servers import (
        gitlab_server,
        bitbucket_server,
        jenkins_server,
        vercel_server,
        netlify_server,
        digitalocean_server,
        kubernetes_server,
        aws_lambda_server,
        aws_s3_server,
        azure_devops_server,
    )

    for s in [
        gitlab_server,
        bitbucket_server,
        jenkins_server,
        vercel_server,
        netlify_server,
        digitalocean_server,
        kubernetes_server,
        aws_lambda_server,
        aws_s3_server,
        azure_devops_server,
    ]:
        assert hasattr(s, "TOOL_DEFINITIONS"), f"{s.__name__} missing TOOL_DEFINITIONS"
        assert len(s.TOOL_DEFINITIONS) >= 5, (
            f"{s.__name__} has only {len(s.TOOL_DEFINITIONS)} tools, expected >= 5"
        )


def test_additional_servers_importable():
    """Heroku and Docker servers are also created and meet the interface."""
    from app.mcp.servers import heroku_server, docker_server

    for s in [heroku_server, docker_server]:
        assert hasattr(s, "TOOL_DEFINITIONS"), f"{s.__name__} missing TOOL_DEFINITIONS"
        assert len(s.TOOL_DEFINITIONS) >= 5, (
            f"{s.__name__} has only {len(s.TOOL_DEFINITIONS)} tools, expected >= 5"
        )


def test_k8s_has_scale_deployment():
    from app.mcp.servers.kubernetes_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "k8s_scale_deployment" in names
    assert "k8s_get_logs" in names
    assert "k8s_restart_deployment" in names
    assert "k8s_apply_manifest" in names
    assert "k8s_list_namespaces" in names


def test_lambda_invoke_tool():
    from app.mcp.servers.aws_lambda_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "lambda_invoke_function" in names
    assert "lambda_list_functions" in names
    assert "lambda_get_function" in names
    assert "lambda_list_aliases" in names
    assert "lambda_get_logs" in names


def test_s3_tools_present():
    from app.mcp.servers.aws_s3_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "s3_list_buckets" in names
    assert "s3_list_objects" in names
    assert "s3_get_object" in names
    assert "s3_put_object" in names
    assert "s3_delete_object" in names
    assert "s3_generate_presigned_url" in names
    assert "s3_create_bucket" in names


def test_cloudwatch_tools_present():
    from app.mcp.servers.aws_cloudwatch_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "cloudwatch_get_metric_data" in names
    assert "cloudwatch_list_alarms" in names
    assert "cloudwatch_put_metric_alarm" in names
    assert "cloudwatch_get_log_events" in names
    assert "cloudwatch_filter_log_events" in names


def test_iam_tools_present():
    from app.mcp.servers.aws_iam_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "iam_list_users" in names
    assert "iam_list_roles" in names
    assert "iam_create_user" in names
    assert "iam_attach_policy" in names
    assert "iam_list_policies" in names


def test_gitlab_tools_present():
    from app.mcp.servers.gitlab_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "gitlab_list_projects" in names
    assert "gitlab_list_issues" in names
    assert "gitlab_create_issue" in names
    assert "gitlab_list_merge_requests" in names
    assert "gitlab_trigger_pipeline" in names
    assert "gitlab_add_comment" in names


def test_azure_devops_tools_present():
    from app.mcp.servers.azure_devops_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "azure_list_repos" in names
    assert "azure_list_pull_requests" in names
    assert "azure_create_pull_request" in names
    assert "azure_list_work_items" in names
    assert "azure_create_work_item" in names
    assert "azure_list_pipelines" in names
    assert "azure_run_pipeline" in names


def test_all_tools_have_required_schema_fields():
    """Every tool definition must have name, description, and parameters."""
    from app.mcp.servers import (
        gitlab_server,
        bitbucket_server,
        jenkins_server,
        vercel_server,
        netlify_server,
        heroku_server,
        digitalocean_server,
        kubernetes_server,
        docker_server,
        aws_lambda_server,
        aws_s3_server,
        aws_cloudwatch_server,
        aws_iam_server,
        azure_devops_server,
    )

    all_servers = [
        gitlab_server,
        bitbucket_server,
        jenkins_server,
        vercel_server,
        netlify_server,
        heroku_server,
        digitalocean_server,
        kubernetes_server,
        docker_server,
        aws_lambda_server,
        aws_s3_server,
        aws_cloudwatch_server,
        aws_iam_server,
        azure_devops_server,
    ]

    for srv in all_servers:
        for tool in srv.TOOL_DEFINITIONS:
            assert "name" in tool, f"{srv.__name__}: tool missing 'name'"
            assert "description" in tool, f"{srv.__name__}: {tool.get('name')} missing 'description'"
            assert "parameters" in tool, f"{srv.__name__}: {tool.get('name')} missing 'parameters'"
            assert tool["parameters"].get("type") == "object", (
                f"{srv.__name__}: {tool.get('name')} parameters must have type=object"
            )


def test_all_servers_have_call_tool():
    """Every server must expose an async call_tool function."""
    import asyncio
    import inspect
    from app.mcp.servers import (
        gitlab_server,
        bitbucket_server,
        jenkins_server,
        vercel_server,
        netlify_server,
        heroku_server,
        digitalocean_server,
        kubernetes_server,
        docker_server,
        aws_lambda_server,
        aws_s3_server,
        aws_cloudwatch_server,
        aws_iam_server,
        azure_devops_server,
    )

    all_servers = [
        gitlab_server,
        bitbucket_server,
        jenkins_server,
        vercel_server,
        netlify_server,
        heroku_server,
        digitalocean_server,
        kubernetes_server,
        docker_server,
        aws_lambda_server,
        aws_s3_server,
        aws_cloudwatch_server,
        aws_iam_server,
        azure_devops_server,
    ]

    for srv in all_servers:
        assert hasattr(srv, "call_tool"), f"{srv.__name__} missing call_tool"
        assert inspect.iscoroutinefunction(srv.call_tool), (
            f"{srv.__name__}.call_tool must be async"
        )
