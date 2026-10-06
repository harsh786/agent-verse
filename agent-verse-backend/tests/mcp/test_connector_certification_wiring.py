"""a02-F031-02: connector certification is reachable from the app.

``app/mcp/certification.py`` / ``certification_manifest.py`` were referenced
only by their tests. They are now served by ``GET /connectors/certification/
targets`` and ``POST /connectors/certification/run`` and driven by
``agentverse connectors-certify``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.mcp.certification_manifest import CONNECTOR_CERTIFICATION_TARGETS
from app.mcp.client import ToolCallResult, ToolDefinition
from app.mcp.registry import MCPServerConfig
from tests.api.test_connectors_extra2 import _CTX, _VALID_KEY, _make_app, _make_registry

H = {"X-API-Key": _VALID_KEY}


def test_targets_list_the_manifest() -> None:
    resp = TestClient(_make_app()).get("/connectors/certification/targets", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert [t["connector"] for t in body] == sorted(CONNECTOR_CERTIFICATION_TARGETS)
    assert all("required_secrets" not in t and "live_env" not in t for t in body)


def test_static_certification_runs() -> None:
    tc = TestClient(_make_app())
    ok = tc.post("/connectors/certification/run", json={"connector": "jira"}, headers=H)
    assert ok.status_code == 200 and ok.json()["status"] == "passed"
    unknown = tc.post("/connectors/certification/run", json={"connector": "nope"}, headers=H)
    assert unknown.json()["status"] == "failed"


def test_mocked_certification_exercises_the_callers_connector() -> None:
    reg = _make_registry()
    sid = asyncio.run(
        reg.register(MCPServerConfig(name="jira", url="https://x.atlassian.net"), tenant_ctx=_CTX)
    )
    mcp = MagicMock()
    mcp.discover_tools = AsyncMock(
        return_value=[ToolDefinition(name="jira_search_issues", description="")]
    )
    mcp.call_tool = AsyncMock(
        return_value=ToolCallResult(tool_name="jira_search_issues", success=True, output={})
    )
    tc = TestClient(_make_app(registry=reg, mcp_client=mcp))
    resp = tc.post(
        "/connectors/certification/run",
        json={"connector": "jira", "level": "mocked", "server_id": sid},
        headers=H,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "passed"
    assert mcp.call_tool.await_args.kwargs["tenant_ctx"].tenant_id == _CTX.tenant_id
    other = tc.post(
        "/connectors/certification/run",
        json={"connector": "jira", "level": "mocked", "server_id": "someone-elses"},
        headers=H,
    )
    assert other.status_code == 404
    missing = tc.post(
        "/connectors/certification/run",
        json={"connector": "jira", "level": "mocked"},
        headers=H,
    )
    assert missing.status_code == 422


def test_cli_certifies_every_target_and_exits_nonzero_on_failure(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENTVERSE_API_KEY", "k")
    targets = [{"connector": "jira"}, {"connector": "github"}]
    results = {
        "jira": {"status": "passed", "checks": [{"name": "manifest", "status": "passed"}]},
        "github": {"status": "failed", "checks": [], "warnings": ["no read tool"]},
    }
    with (
        patch("app.cli.main._get", return_value=targets),
        patch("app.cli.main._post", side_effect=lambda _u, _k, b: results[b["connector"]]),
        patch("app.cli.main._base_url", return_value="http://av"),
    ):
        out = CliRunner().invoke(cli_app, ["connectors-certify"])
    assert out.exit_code == 1
    assert "PASS  jira" in out.output and "FAIL  github" in out.output
    assert "no read tool" in out.output
