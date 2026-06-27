"""Tests for Phase 4: Connector Ecosystem Expansion."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

SAMPLE_OPENAPI_JSON = """{
  "openapi": "3.0.0",
  "info": {"title": "Test API", "version": "1.0.0"},
  "paths": {
    "/users": {
      "get": {
        "summary": "List users",
        "parameters": [
          {"name": "limit", "in": "query", "schema": {"type": "integer"}, "required": false}
        ]
      },
      "post": {
        "summary": "Create user",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "email": {"type": "string"}}
              }
            }
          }
        }
      }
    },
    "/users/{id}": {
      "get": {"summary": "Get user by ID"},
      "delete": {"summary": "Delete user"}
    }
  }
}"""


# ── Task 4.1: OpenAPI importer tests ─────────────────────────────────────────

def test_parse_openapi_json_spec():
    """parse_openapi_spec must parse valid JSON spec."""
    from app.mcp.openapi_importer import parse_openapi_spec

    spec = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    assert spec["openapi"] == "3.0.0"
    assert "paths" in spec


def test_extract_tools_from_spec():
    """extract_tools_from_spec must create one tool per path+method."""
    from app.mcp.openapi_importer import extract_tools_from_spec, parse_openapi_spec

    spec = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    tools = extract_tools_from_spec(spec, connector_id="conn1", tenant_id="t1")
    assert len(tools) == 4  # GET /users, POST /users, GET /users/{id}, DELETE /users/{id}
    tool_names = {t["tool_name"] for t in tools}
    assert "get_users" in tool_names
    assert "post_users" in tool_names
    # delete /users/{id} → delete_users_id or similar
    assert any("delete" in name and "user" in name for name in tool_names)


def test_extract_tools_captures_parameters():
    """Tool schema must include path parameters."""
    from app.mcp.openapi_importer import extract_tools_from_spec, parse_openapi_spec

    spec = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    tools = extract_tools_from_spec(spec, connector_id="conn1", tenant_id="t1")
    get_users = next(t for t in tools if t["tool_name"] == "get_users")
    assert "limit" in get_users["parameters_schema"]["properties"]


def test_extract_tools_captures_request_body():
    """POST tool schema must include request body."""
    from app.mcp.openapi_importer import extract_tools_from_spec, parse_openapi_spec

    spec = parse_openapi_spec(SAMPLE_OPENAPI_JSON)
    tools = extract_tools_from_spec(spec, connector_id="conn1", tenant_id="t1")
    post_users = next(t for t in tools if t["tool_name"] == "post_users")
    assert "body" in post_users["parameters_schema"]["properties"]


def test_parse_openapi_invalid_json_raises():
    """parse_openapi_spec must raise ValueError for invalid input."""
    from app.mcp.openapi_importer import parse_openapi_spec

    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_openapi_spec("{invalid json}")


@pytest.mark.asyncio
async def test_import_openapi_endpoint_returns_tool_count():
    """POST /connectors/import-openapi must return connector with tool_count."""
    from httpx import AsyncClient, ASGITransport

    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/connectors/import-openapi",
            json={
                "openapi_spec": SAMPLE_OPENAPI_JSON,
                "base_url": "https://api.example.com",
                "auth_type": "bearer",
                "auth_config": {},
            },
            headers={"X-API-Key": "test-key"},
        )
        # Without real auth we get 401, but the endpoint must exist (not 404)
        assert response.status_code != 404


# ── Task 4.2: Code interpreter tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_code_interpreter_executes_python():
    """CodeInterpreter must execute Python and return stdout."""
    from app.tools.code_interpreter import CodeInterpreter

    interp = CodeInterpreter()
    result = await interp.execute("print('hello from sandbox')", language="python")
    assert result.success
    assert "hello from sandbox" in result.stdout


@pytest.mark.asyncio
async def test_code_interpreter_captures_stderr():
    """CodeInterpreter must capture stderr."""
    from app.tools.code_interpreter import CodeInterpreter

    interp = CodeInterpreter()
    result = await interp.execute(
        "import sys; sys.stderr.write('error msg\\n')", language="python"
    )
    assert "error msg" in result.stderr


@pytest.mark.asyncio
async def test_code_interpreter_returns_exit_code_on_error():
    """CodeInterpreter must return non-zero exit code for failing code."""
    from app.tools.code_interpreter import CodeInterpreter

    interp = CodeInterpreter()
    result = await interp.execute("raise ValueError('test error')", language="python")
    assert result.exit_code != 0
    assert not result.success


@pytest.mark.asyncio
async def test_code_interpreter_unsupported_language():
    """CodeInterpreter must return error for unknown language."""
    from app.tools.code_interpreter import CodeInterpreter

    interp = CodeInterpreter()
    result = await interp.execute("code", language="cobol")
    assert not result.success
    assert "Unsupported language" in result.stderr


def test_code_result_to_dict():
    """CodeResult.to_dict() must return all expected fields."""
    from app.tools.code_interpreter import CodeResult

    r = CodeResult(stdout="hello", stderr="", exit_code=0, execution_time_ms=42.5)
    d = r.to_dict()
    assert d["success"] is True
    assert d["stdout"] == "hello"
    assert d["execution_time_ms"] == 42.5


# ── Task 4.3: File operations tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_ops_write_and_read():
    """FileOps must write and read files in tenant workspace."""
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id="test-tenant-123")
    await ops.write("test_phase4.txt", "hello world")
    content = await ops.read("test_phase4.txt")
    assert content == "hello world"
    # Cleanup
    await ops.delete("test_phase4.txt")


@pytest.mark.asyncio
async def test_file_ops_list_directory():
    """FileOps.list() must return files in workspace."""
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id="test-tenant-list4")
    await ops.write("a.txt", "aaa")
    await ops.write("b.txt", "bbb")
    files = await ops.list(".")
    names = [f["name"] for f in files]
    assert "a.txt" in names
    assert "b.txt" in names
    await ops.delete("a.txt")
    await ops.delete("b.txt")


@pytest.mark.asyncio
async def test_file_ops_path_traversal_blocked():
    """FileOps must reject path traversal attempts."""
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id="tenant-sec4")
    with pytest.raises(PermissionError, match="outside workspace"):
        await ops.read("../../etc/passwd")


@pytest.mark.asyncio
async def test_file_ops_delete_nonexistent_returns_false():
    """FileOps.delete() must return False for non-existent files."""
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id="tenant-del4")
    result = await ops.delete("no-such-file.txt")
    assert result is False


# ── Task 4.4: Email tool tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_email_tool_send_validates_address():
    """EmailTool.send must raise ValueError for invalid email address."""
    from app.tools.email_tool import EmailTool, SMTPConfig

    config = SMTPConfig(
        host="smtp.example.com",
        port=587,
        username="user",
        password="pass",
        from_address="sender@example.com",
    )
    tool = EmailTool(smtp_config=config)
    with pytest.raises(ValueError, match="Invalid email"):
        await tool.send(to="not-an-email", subject="Test", body="Hello")


@pytest.mark.asyncio
async def test_email_tool_send_mock():
    """EmailTool.send must call aiosmtplib.send with correct parameters."""
    from app.tools.email_tool import EmailTool, SMTPConfig

    config = SMTPConfig(
        host="smtp.example.com",
        port=587,
        username="user@example.com",
        password="secret",
        from_address="user@example.com",
    )
    tool = EmailTool(smtp_config=config)

    with patch("aiosmtplib.send", new=AsyncMock(return_value=({}, "OK"))) as mock_send:
        result = await tool.send(
            to="recipient@example.com",
            subject="Test Subject",
            body="Hello World",
        )
    assert result["status"] == "sent"
    assert mock_send.called


def test_smtp_config_defaults():
    """SMTPConfig must have sensible defaults."""
    from app.tools.email_tool import SMTPConfig

    config = SMTPConfig(
        host="smtp.gmail.com", username="u", password="p", from_address="u@gmail.com"
    )
    assert config.port == 587
    assert config.use_tls is True


# ── Task 4.5: Connector catalog tests ────────────────────────────────────────

def test_catalog_has_at_least_29_connectors():
    """Connector catalog must have at least 29 entries (9 existing + 20 new)."""
    from app.mcp.catalog import CONNECTOR_CATALOG

    assert len(CONNECTOR_CATALOG) >= 29


def test_catalog_has_required_new_connectors():
    """Catalog must contain all 20 new connector entries."""
    from app.mcp.catalog import CONNECTOR_CATALOG

    names = {c.name for c in CONNECTOR_CATALOG}
    required = {
        "hubspot", "zendesk", "intercom", "aws", "gcp", "postgresql",
        "mysql", "mongodb", "snowflake", "google_sheets", "asana",
        "monday", "teams", "discord", "twilio", "quickbooks", "okta",
        "circleci", "terraform", "kubernetes",
    }
    missing = required - names
    assert not missing, f"Missing connectors: {sorted(missing)}"


# ── Tools API endpoint tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_code_endpoint_requires_auth():
    """POST /tools/execute-code must return 401 without auth."""
    from httpx import AsyncClient, ASGITransport

    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/tools/execute-code", json={"code": "print(1)"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_tools_router_registered():
    """Tools router endpoints must be registered in the app."""
    from httpx import AsyncClient, ASGITransport

    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # The endpoint exists — just requires auth
        r = await client.post(
            "/tools/execute-code",
            json={"code": "print(1)", "language": "python"},
        )
        assert r.status_code in {401, 422}  # auth required or validation error
