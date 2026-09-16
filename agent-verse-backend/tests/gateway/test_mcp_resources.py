"""Tests for app/gateway/mcp_server/resources.py — MCP Resources + Prompts.

Covers:
  - MCPResource / MCPResourceContent / MCPPromptArgument / MCPPrompt / MCPPromptMessage dataclasses
  - OrgMCPResources.list_resources / read_resource (status, missions, approvals, memory, unknown)
  - OrgMCPPrompts.list_prompts / get_prompt (every named branch + default fallback)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.gateway.mcp_server.resources import (
    MCPPrompt,
    MCPPromptArgument,
    MCPPromptMessage,
    MCPResource,
    MCPResourceContent,
    OrgMCPPrompts,
    OrgMCPResources,
)

ORG_ID = "org-res-test"


class TestDataclasses:
    def test_mcp_resource_defaults(self) -> None:
        r = MCPResource(uri="org://x/status", name="n", description="d")
        assert r.mime_type == "application/json"
        assert r.template is False

    def test_mcp_prompt_argument_defaults(self) -> None:
        a = MCPPromptArgument(name="goal", description="d")
        assert a.required is True

    def test_mcp_prompt_message(self) -> None:
        m = MCPPromptMessage(role="user", content="hi")
        assert m.role == "user"
        assert m.content == "hi"

    def test_mcp_resource_content_optional_fields(self) -> None:
        c = MCPResourceContent(uri="x", mime_type="application/json")
        assert c.text is None
        assert c.blob is None

    def test_mcp_prompt_default_arguments(self) -> None:
        p = MCPPrompt(name="p", description="d")
        assert p.arguments == []


class TestListResources:
    def test_list_resources_formats_uri_with_org_id(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        listing = resources.list_resources()
        assert len(listing) == len(OrgMCPResources.ORG_RESOURCES)
        assert all(ORG_ID in r["uri"] for r in listing)
        assert all("mimeType" in r for r in listing)

    def test_dept_placeholder_preserved(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        listing = resources.list_resources()
        dept_uri = next(r["uri"] for r in listing if "dept" in r["uri"])
        assert "{dept_id}" in dept_uri


class TestReadResourceStatus:
    @pytest.mark.asyncio
    async def test_status_no_org_service(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        content = await resources.read_resource(f"org://{ORG_ID}/status")
        data = json.loads(content.text)
        assert data["org_id"] == ORG_ID
        assert content.mime_type == "application/json"

    @pytest.mark.asyncio
    async def test_status_with_org_service(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        svc = AsyncMock()
        svc.get_org_health = AsyncMock(return_value={"health_score": 77})
        content = await resources.read_resource(f"org://{ORG_ID}/status", org_service=svc)
        data = json.loads(content.text)
        assert data["health_score"] == 77

    @pytest.mark.asyncio
    async def test_status_org_service_exception_falls_back(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        svc = AsyncMock()
        svc.get_org_health = AsyncMock(side_effect=RuntimeError("db down"))
        content = await resources.read_resource(f"org://{ORG_ID}/status", org_service=svc)
        data = json.loads(content.text)
        assert data["status"] == "active"

    @pytest.mark.asyncio
    async def test_analytics_health_uses_status_reader(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        content = await resources.read_resource(f"org://{ORG_ID}/analytics/health")
        data = json.loads(content.text)
        assert data["org_id"] == ORG_ID


class TestReadResourceMissions:
    @pytest.mark.asyncio
    async def test_missions_no_org_service(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        content = await resources.read_resource(f"org://{ORG_ID}/missions")
        data = json.loads(content.text)
        assert data["missions"] == []

    @pytest.mark.asyncio
    async def test_missions_with_org_service(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        svc = AsyncMock()
        svc.list_missions = AsyncMock(return_value=[{"id": "m1"}])
        content = await resources.read_resource(f"org://{ORG_ID}/missions", org_service=svc)
        data = json.loads(content.text)
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_missions_org_service_exception(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        svc = AsyncMock()
        svc.list_missions = AsyncMock(side_effect=RuntimeError("fail"))
        content = await resources.read_resource(f"org://{ORG_ID}/missions", org_service=svc)
        data = json.loads(content.text)
        assert data["missions"] == []


class TestReadResourceApprovalsAndMemory:
    @pytest.mark.asyncio
    async def test_approvals_pending(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        content = await resources.read_resource(f"org://{ORG_ID}/approvals/pending")
        data = json.loads(content.text)
        assert data["approvals"] == []

    @pytest.mark.asyncio
    async def test_memory_org_scope(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        content = await resources.read_resource(f"org://{ORG_ID}/memory/org")
        data = json.loads(content.text)
        assert data["scope"] == "org"

    @pytest.mark.asyncio
    async def test_memory_dept_scope(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        content = await resources.read_resource(f"org://{ORG_ID}/memory/dept/eng")
        data = json.loads(content.text)
        assert data["scope"] == "dept/eng"

    @pytest.mark.asyncio
    async def test_unknown_resource(self) -> None:
        resources = OrgMCPResources(org_id=ORG_ID)
        content = await resources.read_resource(f"org://{ORG_ID}/nonsense")
        data = json.loads(content.text)
        assert "error" in data


class TestOrgMCPPromptsListing:
    def test_list_prompts_shape(self) -> None:
        prompts = OrgMCPPrompts(org_id=ORG_ID)
        listing = prompts.list_prompts()
        assert len(listing) == len(OrgMCPPrompts.ORG_PROMPTS)
        for p in listing:
            assert "name" in p
            assert "arguments" in p


class TestGetPrompt:
    @pytest.mark.asyncio
    async def test_analyze_mission_risk(self) -> None:
        prompts = OrgMCPPrompts(org_id=ORG_ID)
        msgs = await prompts.get_prompt("analyze_mission_risk", {"goal": "Ship v2"})
        assert msgs[0].role == "user"
        assert "Ship v2" in msgs[0].content

    @pytest.mark.asyncio
    async def test_summarize_daily_activity_default_hours(self) -> None:
        prompts = OrgMCPPrompts(org_id=ORG_ID)
        msgs = await prompts.get_prompt("summarize_daily_activity", {})
        assert "24" in msgs[0].content

    @pytest.mark.asyncio
    async def test_suggest_next_actions(self) -> None:
        prompts = OrgMCPPrompts(org_id=ORG_ID)
        msgs = await prompts.get_prompt("suggest_next_actions", {"role": "cto"})
        assert "cto" in msgs[0].content

    @pytest.mark.asyncio
    async def test_generate_mission_brief(self) -> None:
        prompts = OrgMCPPrompts(org_id=ORG_ID)
        msgs = await prompts.get_prompt(
            "generate_mission_brief", {"mission_id": "m1", "audience": "technical"}
        )
        assert "m1" in msgs[0].content
        assert "technical" in msgs[1].content

    @pytest.mark.asyncio
    async def test_explain_model_usage_has_no_dedicated_branch(self) -> None:
        """explain_model_usage is listed but get_prompt() has no explicit branch for it,
        so it should fall through to the generic 'not found' default."""
        prompts = OrgMCPPrompts(org_id=ORG_ID)
        msgs = await prompts.get_prompt("explain_model_usage", {"department": "eng"})
        assert "not found" in msgs[1].content

    @pytest.mark.asyncio
    async def test_unknown_prompt_default(self) -> None:
        prompts = OrgMCPPrompts(org_id=ORG_ID)
        msgs = await prompts.get_prompt("totally_unknown", {"a": 1})
        assert "not found" in msgs[1].content
