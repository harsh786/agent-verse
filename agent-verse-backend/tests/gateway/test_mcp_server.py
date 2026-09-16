"""Tests for app/gateway/mcp_server/__init__.py — OrgMCPServer (MCP protocol gateway).

Covers:
  - ORG_MCP_TOOLS / MCPTool constants
  - OrgMCPServer.list_tools / call_tool (unknown tool, missing handler, success, error)
  - Service getters (_get_goal_service / _get_knowledge_store / _get_long_term_memory)
  - _get_org_service_ctx (success + exception)
  - Every _tool_* handler's main paths and error branches
  - get_claude_desktop_config
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.mcp_server import ORG_MCP_TOOLS, MCPTool, OrgMCPServer

ORG_ID = "org-mcp-test"


def make_state(**services: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(**services)


# ── ORG_MCP_TOOLS / MCPTool ─────────────────────────────────────────────────


class TestOrgMCPTools:
    def test_all_tools_have_unique_names(self) -> None:
        names = [t.name for t in ORG_MCP_TOOLS]
        assert len(names) == len(set(names))

    def test_all_tools_have_positive_rate_limit(self) -> None:
        for t in ORG_MCP_TOOLS:
            assert t.rate_limit_per_hour > 0

    def test_mcp_tool_defaults(self) -> None:
        tool = MCPTool(name="x", description="d", input_schema={}, output_schema={})
        assert tool.rate_limit_per_hour == 100
        assert tool.requires_scope == "orgs:read"


# ── OrgMCPServer.list_tools / call_tool ──────────────────────────────────────


class TestListTools:
    def test_list_tools_returns_mcp_format(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        tools = server.list_tools()
        assert len(tools) == len(ORG_MCP_TOOLS)
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "inputSchema" in t


class TestCallTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        result = await server.call_tool("nonexistent_tool", {})
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_known_tool_no_handler_returns_not_implemented(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        server._tools["ghost_tool"] = MCPTool(
            name="ghost_tool", description="d", input_schema={}, output_schema={}
        )
        result = await server.call_tool("ghost_tool", {})
        assert result["error"] == "Tool ghost_tool not yet implemented"

    @pytest.mark.asyncio
    async def test_handler_success_wraps_content(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        with patch.object(
            server, "_tool_get_status", new=AsyncMock(return_value={"ok": True})
        ):
            result = await server.call_tool("get_status", {})
        assert result["content"][0]["type"] == "text"
        assert "ok" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        with patch.object(
            server, "_tool_get_status", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            result = await server.call_tool("get_status", {})
        assert result["error"] == "boom"


# ── Service getters ───────────────────────────────────────────────────────────


class TestServiceGetters:
    def test_get_goal_service_from_app_state(self) -> None:
        state = make_state(goal_service="GS")
        server = OrgMCPServer(org_id=ORG_ID, app_state=state)
        assert server._get_goal_service() == "GS"

    def test_get_knowledge_store_from_app_state(self) -> None:
        state = make_state(knowledge_store="KS")
        server = OrgMCPServer(org_id=ORG_ID, app_state=state)
        assert server._get_knowledge_store() == "KS"

    def test_get_long_term_memory_from_app_state(self) -> None:
        state = make_state(long_term_memory="LTM")
        server = OrgMCPServer(org_id=ORG_ID, app_state=state)
        assert server._get_long_term_memory() == "LTM"

    def test_get_goal_service_falls_back_to_app_main_when_no_state(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID, app_state=None)
        from app.main import app as real_app

        expected = getattr(getattr(real_app, "state", None), "goal_service", None)
        assert server._get_goal_service() == expected

    def test_get_knowledge_store_falls_back_to_app_main_when_no_state(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID, app_state=None)
        from app.main import app as real_app

        expected = getattr(getattr(real_app, "state", None), "knowledge_store", None)
        assert server._get_knowledge_store() == expected

    def test_get_long_term_memory_falls_back_to_app_main_when_no_state(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID, app_state=None)
        from app.main import app as real_app

        expected = getattr(getattr(real_app, "state", None), "long_term_memory", None)
        assert server._get_long_term_memory() == expected


# ── _get_org_service_ctx ───────────────────────────────────────────────────


class TestGetOrgServiceCtx:
    @pytest.mark.asyncio
    async def test_returns_service_and_session_on_success(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID, tenant_id="t1")
        fake_session = MagicMock()
        fake_factory = MagicMock(return_value=fake_session)
        fake_org_service = MagicMock()
        with (
            patch("app.db.session.get_session_factory", return_value=fake_factory),
            patch("app.org.service.OrgService", return_value=fake_org_service),
        ):
            result = await server._get_org_service_ctx()
        assert result is not None
        svc, session = result
        assert svc is fake_org_service
        assert session is fake_session

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = await server._get_org_service_ctx()
        assert result is None


# ── _tool_ask_organization ────────────────────────────────────────────────


class TestToolAskOrganization:
    @pytest.mark.asyncio
    async def test_empty_question(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        result = await server._tool_ask_organization({"question": "   "}, {})
        assert result == "Please provide a question."

    @pytest.mark.asyncio
    async def test_no_services_returns_default_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=None))
        monkeypatch.setattr(server, "_get_goal_service", MagicMock(return_value=None))
        result = await server._tool_ask_organization({"question": "What's up?"}, {})
        assert ORG_ID in result

    @pytest.mark.asyncio
    async def test_with_org_context_and_goal_service_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = OrgMCPServer(org_id=ORG_ID, tenant_id="t1")
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        fake_svc.get_org_health = AsyncMock(
            return_value={
                "health_score": 90,
                "active_missions": 2,
                "pending_approvals": 1,
                "total_agents": 5,
            }
        )
        decision = MagicMock(decision_type="strategy", title="Expand", rationale="Because" * 20)
        fake_svc.list_decisions = AsyncMock(return_value=[decision])
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )

        ks = AsyncMock()
        ks.search = AsyncMock(return_value=[{"content": "some knowledge"}])
        monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=ks))

        goal_service = AsyncMock()
        goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-123"})
        monkeypatch.setattr(server, "_get_goal_service", MagicMock(return_value=goal_service))

        result = await server._tool_ask_organization({"question": "How are we doing?"}, {})
        assert "g-123" in result
        fake_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_knowledge_store_exception_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        ks = AsyncMock()
        ks.search = AsyncMock(side_effect=RuntimeError("down"))
        monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=ks))
        monkeypatch.setattr(server, "_get_goal_service", MagicMock(return_value=None))
        result = await server._tool_ask_organization({"question": "test"}, {})
        assert ORG_ID in result

    @pytest.mark.asyncio
    async def test_goal_service_exception_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=None))
        goal_service = AsyncMock()
        goal_service.submit_goal = AsyncMock(side_effect=RuntimeError("fail"))
        monkeypatch.setattr(server, "_get_goal_service", MagicMock(return_value=goal_service))
        result = await server._tool_ask_organization({"question": "test"}, {})
        assert ORG_ID in result


# ── _tool_start_mission ───────────────────────────────────────────────────


class TestToolStartMission:
    @pytest.mark.asyncio
    async def test_empty_description_returns_error(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        result = await server._tool_start_mission({"description": "  "}, {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_db_with_goal_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        goal_service = AsyncMock()
        goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-1", "status": "queued"})
        monkeypatch.setattr(server, "_get_goal_service", MagicMock(return_value=goal_service))
        result = await server._tool_start_mission({"description": "Do something"}, {})
        assert result["mission_id"] == "g-1"
        assert result["status"] == "queued"

    @pytest.mark.asyncio
    async def test_no_db_no_goal_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        monkeypatch.setattr(server, "_get_goal_service", MagicMock(return_value=None))
        result = await server._tool_start_mission({"description": "Do something"}, {})
        assert result["error"] == "no_backend_available"

    @pytest.mark.asyncio
    async def test_with_db_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID, tenant_id="t1")
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        mission = MagicMock(id="mission-uuid-1")
        dispatch = {
            "goal_id": "g-2",
            "topology": "sequential",
            "departments": ["eng"],
            "estimated_cost_usd": 1.5,
        }
        fake_svc.create_mission_and_execute = AsyncMock(return_value=(mission, dispatch))
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_start_mission(
            {"description": "Ship it", "priority": "high"}, {}
        )
        assert result["mission_id"] == "mission-uuid-1"
        assert result["goal_id"] == "g-2"
        assert result["status"] == "active"
        fake_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_db_exception_rolls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        fake_svc.create_mission_and_execute = AsyncMock(side_effect=RuntimeError("db exploded"))
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_start_mission({"description": "Ship it"}, {})
        assert result["status"] == "failed"
        fake_session.rollback.assert_awaited_once()
        fake_session.close.assert_awaited_once()


# ── _tool_get_status ──────────────────────────────────────────────────────


class TestToolGetStatus:
    @pytest.mark.asyncio
    async def test_no_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        result = await server._tool_get_status({}, {})
        assert result["error"] == "db_unavailable"

    @pytest.mark.asyncio
    async def test_with_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        fake_svc.get_org_health = AsyncMock(return_value={"health_score": 80})
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_get_status({}, {})
        assert result["health_score"] == 80
        fake_session.close.assert_awaited_once()


# ── _tool_list_missions ───────────────────────────────────────────────────


class TestToolListMissions:
    @pytest.mark.asyncio
    async def test_no_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        result = await server._tool_list_missions({}, {})
        assert result["missions"] == []
        assert result["error"] == "db_unavailable"

    @pytest.mark.asyncio
    async def test_with_db_status_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        mission = MagicMock(
            id="m1",
            title="Test",
            status="active",
            priority="high",
            created_at=None,
            metadata={"goal_id": "g1"},
        )
        fake_svc.list_missions = AsyncMock(return_value=[mission])
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_list_missions({"status": "all"}, {})
        assert result["total"] == 1
        fake_svc.list_missions.assert_awaited_once_with(org_id=ORG_ID, status=None, limit=20)

    @pytest.mark.asyncio
    async def test_with_db_status_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        fake_svc.list_missions = AsyncMock(return_value=[])
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        await server._tool_list_missions({"status": "active"}, {})
        fake_svc.list_missions.assert_awaited_once_with(org_id=ORG_ID, status="active", limit=20)


# ── _tool_get_mission_result ──────────────────────────────────────────────


class TestToolGetMissionResult:
    @pytest.mark.asyncio
    async def test_missing_mission_id(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        result = await server._tool_get_mission_result({}, {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        result = await server._tool_get_mission_result({"mission_id": "m1"}, {})
        assert result["error"] == "db_unavailable"

    @pytest.mark.asyncio
    async def test_mission_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        fake_svc.get_mission = AsyncMock(return_value=None)
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_get_mission_result({"mission_id": "missing"}, {})
        assert "not found" in result["error"]
        fake_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        mission = MagicMock(
            title="T",
            status="completed",
            objective="Obj",
            expected_outcome="Outcome",
            completed_at=None,
            metadata={"k": "v"},
        )
        fake_svc.get_mission = AsyncMock(return_value=mission)
        task1 = MagicMock(status="completed")
        task2 = MagicMock(status="failed")
        fake_svc.list_tasks = AsyncMock(return_value=[task1, task2])
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_get_mission_result({"mission_id": "m1"}, {})
        assert result["tasks_total"] == 2
        assert result["tasks_completed"] == 1


# ── _tool_list_pending_approvals ──────────────────────────────────────────


class TestToolListPendingApprovals:
    @pytest.mark.asyncio
    async def test_no_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        result = await server._tool_list_pending_approvals({}, {})
        assert result["approvals"] == []

    @pytest.mark.asyncio
    async def test_with_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        event = MagicMock(
            id="e1",
            title="Approve X",
            entity_type="mission",
            entity_id="m1",
            created_at=None,
            payload={},
        )
        fake_svc.list_events = AsyncMock(return_value=[event])
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_list_pending_approvals({}, {})
        assert result["total"] == 1


# ── _tool_approve ─────────────────────────────────────────────────────────


class TestToolApprove:
    @pytest.mark.asyncio
    async def test_missing_approval_id(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        result = await server._tool_approve({}, {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_hitl_available_approves(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_hitl = AsyncMock()
        fake_hitl.approve = AsyncMock(return_value="approved-ok")
        fake_state = types.SimpleNamespace(hitl_gateway=fake_hitl)
        fake_app = types.SimpleNamespace(state=fake_state)
        with patch("app.main.app", fake_app):
            result = await server._tool_approve({"approval_id": "a1"}, {})
        assert result["approved"] is True
        assert "approved-ok" in result["result"]

    @pytest.mark.asyncio
    async def test_hitl_unavailable(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        fake_state = types.SimpleNamespace(hitl_gateway=None)
        fake_app = types.SimpleNamespace(state=fake_state)
        with patch("app.main.app", fake_app):
            result = await server._tool_approve({"approval_id": "a1"}, {})
        assert result["approved"] is False


# ── _tool_search_knowledge ────────────────────────────────────────────────


class TestToolSearchKnowledge:
    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        result = await server._tool_search_knowledge({}, {})
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_ks_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        ks = AsyncMock()
        ks.search = AsyncMock(return_value=[{"content": "abc", "source": "kb", "score": 0.9}])
        monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=ks))
        result = await server._tool_search_knowledge({"query": "test", "top_k": 3}, {})
        assert len(result["results"]) == 1
        ks.search.assert_awaited_once_with("test", top_k=3)

    @pytest.mark.asyncio
    async def test_ks_error_falls_back_to_dept_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        ks = AsyncMock()
        ks.search = AsyncMock(side_effect=RuntimeError("down"))
        monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=ks))

        fake_entry = MagicMock(content="mem content", confidence=0.5)
        fake_dm = AsyncMock()
        fake_dm.retrieve = AsyncMock(return_value=[fake_entry])
        with patch("app.memory.dept_memory.DepartmentMemory", return_value=fake_dm):
            result = await server._tool_search_knowledge({"query": "test"}, {})
        assert result["results"][0]["source"] == "dept_memory"

    @pytest.mark.asyncio
    async def test_ks_none_and_dept_memory_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=None))
        with patch("app.memory.dept_memory.DepartmentMemory", side_effect=RuntimeError("no mem")):
            result = await server._tool_search_knowledge({"query": "test"}, {})
        assert result["error"] == "knowledge_store_unavailable"


# ── _tool_search_memory ───────────────────────────────────────────────────


class TestToolSearchMemory:
    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        result = await server._tool_search_memory({}, {})
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_ltm_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        ltm = AsyncMock()
        ltm.search = AsyncMock(return_value=[{"content": "learned x"}])
        monkeypatch.setattr(server, "_get_long_term_memory", MagicMock(return_value=ltm))
        result = await server._tool_search_memory({"query": "x"}, {})
        assert result["results"][0]["source"] == "long_term_memory"

    @pytest.mark.asyncio
    async def test_ltm_none_falls_back_to_db_decisions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_long_term_memory", MagicMock(return_value=None))
        fake_session = AsyncMock()
        fake_svc = AsyncMock()
        decision = MagicMock(decision_type="strategy", title="Expand market", rationale="growth")
        fake_svc.list_decisions = AsyncMock(return_value=[decision])
        monkeypatch.setattr(
            server, "_get_org_service_ctx", AsyncMock(return_value=(fake_svc, fake_session))
        )
        result = await server._tool_search_memory({"query": "market"}, {})
        assert len(result["results"]) == 1
        fake_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ltm_exception_falls_back_to_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        ltm = AsyncMock()
        ltm.search = AsyncMock(side_effect=RuntimeError("down"))
        monkeypatch.setattr(server, "_get_long_term_memory", MagicMock(return_value=ltm))
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        result = await server._tool_search_memory({"query": "x"}, {})
        assert result["error"] == "memory_unavailable"

    @pytest.mark.asyncio
    async def test_no_ltm_no_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = OrgMCPServer(org_id=ORG_ID)
        monkeypatch.setattr(server, "_get_long_term_memory", MagicMock(return_value=None))
        monkeypatch.setattr(server, "_get_org_service_ctx", AsyncMock(return_value=None))
        result = await server._tool_search_memory({"query": "x"}, {})
        assert result["error"] == "memory_unavailable"


# ── get_claude_desktop_config ─────────────────────────────────────────────


class TestClaudeDesktopConfig:
    def test_config_shape(self) -> None:
        server = OrgMCPServer(org_id="org-12345678")
        config = server.get_claude_desktop_config("https://example.com")
        key = next(iter(config["mcpServers"]))
        assert key.startswith("agentverse-")
        assert config["mcpServers"][key]["env"]["AGENTVERSE_ORG_URL"] == "https://example.com"
        assert config["mcpServers"][key]["command"] == "npx"
