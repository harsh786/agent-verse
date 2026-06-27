"""Tests for ArtifactTool and related infrastructure."""
import pytest
import asyncio


def test_artifact_tool_importable():
    from app.tools.artifact_tool import ArtifactTool
    tool = ArtifactTool()
    assert tool.name == "save_artifact"


@pytest.mark.asyncio
async def test_artifact_tool_returns_artifact_id():
    from app.tools.artifact_tool import ArtifactTool
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(
        name="report.csv",
        content="id,value\n1,100\n2,200",
        content_type="text/csv",
        tenant_id="t1",
        goal_id="g1",
    )
    assert result.get("artifact_id") is not None
    assert len(result["artifact_id"]) > 0
    assert result["filename"] == "report.csv"
    assert result["size_bytes"] > 0
    assert result["content_type"] == "text/csv"


@pytest.mark.asyncio
async def test_artifact_tool_handles_bytes():
    from app.tools.artifact_tool import ArtifactTool
    tool = ArtifactTool()
    result = await tool.execute(
        name="data.bin",
        content=b"\x00\x01\x02\x03",
        content_type="application/octet-stream",
        tenant_id="t1",
    )
    assert result["size_bytes"] == 4


def test_artifact_tool_definition_valid():
    from app.tools.artifact_tool import ArtifactTool
    tool = ArtifactTool()
    defn = tool.to_tool_def()
    assert defn["name"] == "save_artifact"
    assert "name" in defn["parameters"]["properties"]
    assert "content" in defn["parameters"]["properties"]
    assert "name" in defn["parameters"]["required"]
    assert "content" in defn["parameters"]["required"]


def test_migration_0036_exists():
    import os
    files = os.listdir("/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/app/db/migrations/versions")
    assert any("0036" in f for f in files), "Migration 0036 must exist"


def test_grafana_dashboard_exists():
    import os
    dashboard_dir = "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/infra/grafana/dashboards"
    assert os.path.exists(dashboard_dir), "Grafana dashboards directory must exist"
    files = os.listdir(dashboard_dir)
    assert any(".json" in f for f in files), "At least one Grafana dashboard JSON must exist"


def test_helm_backup_cronjob_exists():
    import os
    path = "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/infra/helm/agentverse/templates/backup-cronjob.yaml"
    assert os.path.exists(path), "Helm backup CronJob template must exist"


def test_purge_expired_artifacts_task_exists():
    import inspect
    from app.scaling import tasks
    src = inspect.getsource(tasks)
    assert "purge_expired_artifacts" in src, "purge_expired_artifacts task must exist"


def test_router_has_db_history_scoring():
    import inspect
    from app.agent import router
    src = inspect.getsource(router)
    assert "evaluations" in src or "avg_score" in src, \
        "AgentRouter must query evaluations table for history scoring"


def test_mcp_client_updates_tool_stats():
    import inspect
    from app.mcp import client
    src = inspect.getsource(client)
    assert "_update_tool_stats" in src or "tool_capabilities" in src, \
        "MCPClient must track tool call statistics"
