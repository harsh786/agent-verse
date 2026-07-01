import pytest

from app.mcp.certification import run_mocked_certification, run_static_certification


def test_static_certification_passes_for_jira_manifest() -> None:
    result = run_static_certification("jira")

    assert result["connector"] == "jira"
    assert result["level"] == "static"
    assert result["status"] == "passed"
    assert {check["name"] for check in result["checks"]} >= {"manifest", "auth", "read_tool"}
    assert result["warnings"] == []
    assert isinstance(result["duration_ms"], int)


def test_static_certification_fails_for_unknown_connector() -> None:
    result = run_static_certification("unknown")

    assert result["connector"] == "unknown"
    assert result["level"] == "static"
    assert result["status"] == "failed"
    assert result["checks"] == [{"name": "manifest", "status": "failed"}]
    assert result["warnings"] == ["Unknown connector: unknown"]


@pytest.mark.asyncio
async def test_mocked_certification_uses_mcp_client_tool_call() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.tool_call: dict[str, object] | None = None

        async def discover_tools(self, *, server_id, tenant_ctx):
            return [type("Tool", (), {"name": "jira_search_issues"})()]

        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            self.tool_call = {
                "server_id": server_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "tenant_ctx": tenant_ctx,
            }
            return type("Result", (), {"success": True, "output": {"issues": []}, "error": ""})()

    client = FakeClient()
    tenant_ctx = type("Tenant", (), {"tenant_id": "tenant-1"})()

    result = await run_mocked_certification(
        "jira",
        mcp_client=client,
        server_id="jira-1",
        tenant_ctx=tenant_ctx,
    )

    assert result["connector"] == "jira"
    assert result["level"] == "mocked"
    assert result["status"] == "passed"
    assert result["checks"][-1]["name"] == "read_call"
    assert client.tool_call == {
        "server_id": "jira-1",
        "tool_name": "jira_search_issues",
        "arguments": {
            "jql": "assignee = currentUser() AND created >= -26w ORDER BY created DESC",
            "max_results": 10,
        },
        "tenant_ctx": tenant_ctx,
    }


@pytest.mark.asyncio
async def test_mocked_certification_fails_when_read_tool_is_missing() -> None:
    class FakeClient:
        async def discover_tools(self, *, server_id, tenant_ctx):
            return [type("Tool", (), {"name": "other_tool"})()]

        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            raise AssertionError("read tool should not be called when discovery fails")

    result = await run_mocked_certification(
        "jira",
        mcp_client=FakeClient(),
        server_id="jira-1",
        tenant_ctx=type("Tenant", (), {"tenant_id": "tenant-1"})(),
    )

    assert result["status"] == "failed"
    assert result["checks"] == [{"name": "tool_discovery", "status": "failed"}]


@pytest.mark.asyncio
async def test_mocked_certification_fails_for_discover_exception() -> None:
    class FakeClient:
        async def discover_tools(self, *, server_id, tenant_ctx):
            raise RuntimeError("discovery unavailable")

        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            raise AssertionError("read tool should not be called when discovery raises")

    result = await run_mocked_certification(
        "jira",
        mcp_client=FakeClient(),
        server_id="jira-1",
        tenant_ctx=type("Tenant", (), {"tenant_id": "tenant-1"})(),
    )

    assert result["status"] == "failed"
    assert result["checks"] == [{"name": "tool_discovery", "status": "failed"}]
    assert result["warnings"] == ["Tool discovery failed: discovery unavailable"]


@pytest.mark.asyncio
async def test_mocked_certification_fails_for_call_exception() -> None:
    class FakeClient:
        async def discover_tools(self, *, server_id, tenant_ctx):
            return [type("Tool", (), {"name": "jira_search_issues"})()]

        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            raise RuntimeError("call unavailable")

    result = await run_mocked_certification(
        "jira",
        mcp_client=FakeClient(),
        server_id="jira-1",
        tenant_ctx=type("Tenant", (), {"tenant_id": "tenant-1"})(),
    )

    assert result["status"] == "failed"
    assert result["checks"] == [
        {"name": "tool_discovery", "status": "passed"},
        {"name": "read_call", "status": "failed"},
    ]
    assert result["warnings"] == ["Tool call failed: call unavailable"]


@pytest.mark.asyncio
async def test_mocked_certification_fails_for_unsuccessful_tool_call() -> None:
    class FakeClient:
        async def discover_tools(self, *, server_id, tenant_ctx):
            return [type("Tool", (), {"name": "jira_search_issues"})()]

        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            return type("Result", (), {"success": False, "error": "permission denied"})()

    result = await run_mocked_certification(
        "jira",
        mcp_client=FakeClient(),
        server_id="jira-1",
        tenant_ctx=type("Tenant", (), {"tenant_id": "tenant-1"})(),
    )

    assert result["status"] == "failed"
    assert result["checks"] == [
        {"name": "tool_discovery", "status": "passed"},
        {"name": "read_call", "status": "failed"},
    ]
    assert result["warnings"] == ["Tool call failed: permission denied"]


@pytest.mark.asyncio
async def test_mocked_certification_fails_when_successful_tool_call_outputs_error() -> None:
    class FakeClient:
        async def discover_tools(self, *, server_id, tenant_ctx):
            return [type("Tool", (), {"name": "jira_search_issues"})()]

        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            return type(
                "Result",
                (),
                {"success": True, "output": {"error": "mock server unavailable"}, "error": ""},
            )()

    result = await run_mocked_certification(
        "jira",
        mcp_client=FakeClient(),
        server_id="jira-1",
        tenant_ctx=type("Tenant", (), {"tenant_id": "tenant-1"})(),
    )

    assert result["status"] == "failed"
    assert result["checks"] == [
        {"name": "tool_discovery", "status": "passed"},
        {"name": "read_call", "status": "failed"},
    ]
    assert result["warnings"] == ["Tool call failed: mock server unavailable"]
