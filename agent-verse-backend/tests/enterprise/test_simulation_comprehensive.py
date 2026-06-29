"""Comprehensive tests for app/enterprise/simulation.py and app/enterprise/marketplace.py."""
from __future__ import annotations

import pytest

from app.enterprise.simulation import MockMCPClient, SimulationRun, SimulationRunner
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="sim-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


# ── MockMCPClient ─────────────────────────────────────────────────────────────


async def test_mock_client_discover_tools_empty() -> None:
    client = MockMCPClient()
    result = await client.discover_tools("server-1", T)
    assert result == []


async def test_mock_client_discover_all_tools_empty() -> None:
    client = MockMCPClient()
    result = await client.discover_all_tools(T)
    assert result == []


async def test_mock_client_call_tool_with_string_mock() -> None:
    client = MockMCPClient(mock_responses={"list_issues": "Issue #1: Bug in login"})
    result = await client.call_tool("github", "list_issues", {}, T)
    assert result["simulated"] is True
    assert "Issue #1" in result["content"][0]["text"]


async def test_mock_client_call_tool_with_dict_mock() -> None:
    client = MockMCPClient(mock_tools={"create_issue": {"id": "JIRA-123", "status": "open"}})
    result = await client.call_tool("jira", "create_issue", {}, T)
    assert result["simulated"] is True
    assert "JIRA-123" in result["content"][0]["text"]


async def test_mock_client_call_tool_full_key_lookup() -> None:
    client = MockMCPClient(mock_responses={"github.list_repos": "repo1, repo2"})
    result = await client.call_tool("github", "list_repos", {}, T)
    assert "repo1" in result["content"][0]["text"]


async def test_mock_client_call_tool_no_mock_returns_simulated() -> None:
    client = MockMCPClient()
    result = await client.call_tool("slack", "send_message", {}, T)
    assert result["simulated"] is True
    assert "no mock for send_message" in result["content"][0]["text"]


async def test_mock_client_was_hit_after_call() -> None:
    client = MockMCPClient(mock_responses={"list_issues": "some issues"})
    await client.call_tool("github", "list_issues", {}, T)
    assert client.was_hit("list_issues") is True
    assert client.was_hit("github.list_issues") is True


async def test_mock_client_was_hit_not_called_returns_false() -> None:
    client = MockMCPClient()
    assert client.was_hit("not_called") is False


async def test_mock_client_was_hit_none_returns_false() -> None:
    client = MockMCPClient()
    assert client.was_hit(None) is False


async def test_mock_client_accepts_mock_tools_alias() -> None:
    """mock_tools= and mock_responses= are aliases."""
    client = MockMCPClient(mock_tools={"ping": "pong"})
    result = await client.call_tool("any", "ping", {}, T)
    assert "pong" in result["content"][0]["text"]


# ── SimulationRun dataclass ───────────────────────────────────────────────────


def test_simulation_run_defaults() -> None:
    run = SimulationRun()
    assert run.run_id  # auto-generated
    assert run.status == "pending"
    assert run.goal == ""
    assert run.mock_tools == {}
    assert run.steps_executed == []
    assert run.tools_called == []
    assert run.mock_tools_used == []
    assert run.cost_estimate == 0.0
    assert run.used_real_llm is False


def test_simulation_run_unique_ids() -> None:
    r1 = SimulationRun()
    r2 = SimulationRun()
    assert r1.run_id != r2.run_id


# ── SimulationRunner._build_plan ─────────────────────────────────────────────


def test_build_plan_always_has_analysis_step() -> None:
    runner = SimulationRunner()
    plan = runner._build_plan("fix bugs", {})
    descriptions = [s["description"] for s in plan]
    assert any("analys" in d.lower() or "goal" in d.lower() for d in descriptions)


def test_build_plan_always_has_verify_step() -> None:
    runner = SimulationRunner()
    plan = runner._build_plan("deploy service", {})
    descriptions = [s["description"] for s in plan]
    assert any("verif" in d.lower() or "complet" in d.lower() for d in descriptions)


def test_build_plan_includes_mock_tool_step() -> None:
    runner = SimulationRunner()
    mock_tools = {"github.list_issues": "issues data"}
    plan = runner._build_plan("fix bugs", mock_tools)
    tool_steps = [s for s in plan if s.get("tool")]
    assert len(tool_steps) == 1
    assert tool_steps[0]["tool"] == "github.list_issues"


def test_build_plan_multiple_mock_tools() -> None:
    runner = SimulationRunner()
    mock_tools = {
        "github.create_pr": "pr created",
        "jira.update_issue": "issue updated",
        "slack.send_message": "notification sent",
    }
    plan = runner._build_plan("release update", mock_tools)
    tool_steps = [s for s in plan if s.get("tool")]
    assert len(tool_steps) == 3


def test_build_plan_verify_keyword_adds_step() -> None:
    runner = SimulationRunner()
    plan = runner._build_plan("verify the system is healthy", {})
    descriptions = [s["description"].lower() for s in plan]
    assert any("verif" in d or "check" in d for d in descriptions)


def test_build_plan_report_keyword_adds_step() -> None:
    runner = SimulationRunner()
    plan = runner._build_plan("generate a report for the quarter", {})
    descriptions = [s["description"].lower() for s in plan]
    assert any("report" in d or "summary" in d for d in descriptions)


def test_build_plan_notify_keyword_adds_step() -> None:
    runner = SimulationRunner()
    plan = runner._build_plan("notify the team when complete", {})
    descriptions = [s["description"].lower() for s in plan]
    assert any("notif" in d or "message" in d or "alert" in d for d in descriptions)


def test_build_plan_no_duplicate_steps_for_covered_keywords() -> None:
    """If mock tools already cover the keyword, extra step should not be added."""
    runner = SimulationRunner()
    # slack.send_message covers "notify" keyword
    mock_tools = {"slack.send_message": "sent"}
    plan = runner._build_plan("notify the team", mock_tools)
    # Count notification-related steps
    notify_steps = [s for s in plan if "notif" in s.get("description", "").lower()
                    or "message" in s.get("description", "").lower()]
    # Should not add an extra notification step since slack is covered
    tool_notifications = [s for s in notify_steps if s.get("tool")]
    assert len(tool_notifications) <= 1


# ── SimulationRunner.start — stub mode (no provider) ─────────────────────────


async def test_simulation_runner_start_stub_no_provider() -> None:
    runner = SimulationRunner()
    run = await runner.start(goal="Fix all JIRA bugs", tenant_ctx=T)

    assert run.status == "completed"
    assert run.goal == "Fix all JIRA bugs"
    assert len(run.steps_executed) > 0
    assert run.used_real_llm is False


async def test_simulation_runner_start_stub_with_mock_tools() -> None:
    runner = SimulationRunner()
    run = await runner.start(
        goal="Fetch GitHub issues and create Jira tickets",
        mock_tools={
            "github.list_issues": "Issue #1, Issue #2",
            "jira.create_issue": "Created JIRA-100",
        },
        tenant_ctx=T,
    )

    assert run.status == "completed"
    assert "github.list_issues" in run.mock_tools_used
    assert len(run.steps_executed) >= 2


async def test_simulation_runner_start_result_has_required_keys() -> None:
    runner = SimulationRunner()
    run = await runner.start(goal="test goal", tenant_ctx=T)

    assert "goal" in run.result
    assert "status" in run.result
    assert "steps" in run.result
    assert "cost_usd" in run.result
    assert "simulated_steps" in run.result
    assert "outcome" in run.result
    assert run.result["note"] == "Simulation complete — no real tools were called"


async def test_simulation_runner_start_cost_is_positive() -> None:
    runner = SimulationRunner()
    run = await runner.start(goal="do something", tenant_ctx=T)
    assert run.cost_estimate >= 0


async def test_simulation_runner_get_after_start() -> None:
    runner = SimulationRunner()
    run = await runner.start(goal="test", tenant_ctx=T)
    fetched = runner.get(run_id=run.run_id, tenant_ctx=T)
    assert fetched is not None
    assert fetched.run_id == run.run_id


async def test_simulation_runner_get_missing_returns_none() -> None:
    runner = SimulationRunner()
    result = runner.get(run_id="ghost-run-id", tenant_ctx=T)
    assert result is None


async def test_simulation_runner_list_runs_empty() -> None:
    runner = SimulationRunner()
    assert runner.list_runs(tenant_ctx=T) == []


async def test_simulation_runner_list_runs_after_start() -> None:
    runner = SimulationRunner()
    await runner.start(goal="first goal", tenant_ctx=T)
    await runner.start(goal="second goal", tenant_ctx=T)
    runs = runner.list_runs(tenant_ctx=T)
    assert len(runs) == 2


# ── run_streaming — stub mode ──────────────────────────────────────────────────


async def test_run_streaming_yields_started_and_complete_events() -> None:
    runner = SimulationRunner()
    events = []
    async for event in runner.run_streaming(goal="do stuff", tenant_ctx=T):
        events.append(event)

    types = [e["type"] for e in events]
    assert "simulation_started" in types
    assert "simulation_complete" in types


async def test_run_streaming_first_event_is_simulation_started() -> None:
    runner = SimulationRunner()
    first_event = None
    async for event in runner.run_streaming(goal="test", tenant_ctx=T):
        first_event = event
        break
    assert first_event is not None
    assert first_event["type"] == "simulation_started"
    assert first_event["goal"] == "test"


async def test_run_streaming_step_events_have_step_number() -> None:
    runner = SimulationRunner()
    step_events = []
    async for event in runner.run_streaming(goal="deploy service", tenant_ctx=T):
        if event["type"] == "step_completed":
            step_events.append(event)

    for e in step_events:
        assert "step_number" in e
        assert e["step_number"] >= 1


async def test_run_streaming_complete_event_has_metadata() -> None:
    runner = SimulationRunner()
    complete_event = None
    async for event in runner.run_streaming(goal="test", tenant_ctx=T):
        if event["type"] == "simulation_complete":
            complete_event = event
    assert complete_event is not None
    assert "total_steps" in complete_event
    assert "total_cost" in complete_event
    assert complete_event["used_real_llm"] is False


async def test_run_streaming_with_mock_tools() -> None:
    runner = SimulationRunner()
    mock_hit_events = []
    async for event in runner.run_streaming(
        goal="fetch github issues",
        mock_tools={"github.list_issues": "Issue list here"},
        tenant_ctx=T,
    ):
        if event.get("mock_hit"):
            mock_hit_events.append(event)

    assert len(mock_hit_events) >= 1


async def test_run_streaming_stub_info_event_no_provider() -> None:
    runner = SimulationRunner()
    info_events = []
    async for event in runner.run_streaming(goal="test", tenant_ctx=T):
        if event["type"] == "simulation_info":
            info_events.append(event)
    # Should emit an info event explaining stub mode
    assert len(info_events) >= 1
    assert any("stub" in e["message"].lower() or "no llm" in e["message"].lower()
               for e in info_events)


async def test_run_streaming_max_steps_respected() -> None:
    runner = SimulationRunner()
    step_events = []
    async for event in runner.run_streaming(goal="a" * 200, max_steps=2, tenant_ctx=T):
        if event["type"] == "step_completed":
            step_events.append(event)
    assert len(step_events) <= 2


# ── Marketplace ───────────────────────────────────────────────────────────────


class TestMarketplace:
    def test_browse_all_templates(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        templates = mp.browse(tenant_ctx=T)
        assert len(templates) >= 8

    def test_browse_by_domain(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        software = mp.browse(domain="software", tenant_ctx=T)
        assert all(t["domain"] == "software" for t in software)
        assert len(software) >= 1

    def test_browse_by_query(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        results = mp.browse(query="bug", tenant_ctx=T)
        assert len(results) >= 1
        assert all("bug" in t["name"].lower() or "bug" in t["description"].lower()
                   for t in results)

    def test_browse_no_match_returns_empty(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        results = mp.browse(query="xyzzy_nonexistent_12345", tenant_ctx=T)
        assert results == []

    def test_get_template_found(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        tmpl = mp.get_template(template_id="tpl-bug-fix")
        assert tmpl is not None
        assert tmpl["template_id"] == "tpl-bug-fix"

    def test_get_template_not_found(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        assert mp.get_template(template_id="tpl-nonexistent") is None

    async def test_deploy_template_no_agent_store(self) -> None:
        from app.enterprise.marketplace import DeployedTemplate, Marketplace

        mp = Marketplace()
        result = await mp.deploy(
            template_id="tpl-bug-fix",
            params={"name": "My Bug Fixer"},
            tenant_ctx=T,
        )
        assert isinstance(result, DeployedTemplate)
        assert result.template_id == "tpl-bug-fix"
        assert result.tenant_id == T.tenant_id

    async def test_deploy_nonexistent_template_raises(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        with pytest.raises(ValueError, match="not found"):
            await mp.deploy(template_id="tpl-ghost", params={}, tenant_ctx=T)

    def test_publish_custom_template(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        record = mp.publish(
            template={
                "name": "My Custom Agent",
                "domain": "analytics",
                "description": "Analyzes data",
                "connectors": ["datadog"],
                "autonomy_mode": "supervised",
                "visibility": "community",
            },
            tenant_ctx=T,
        )
        assert record["template_id"].startswith("tpl-custom-")
        assert record["is_community"] is True
        assert record["author"] == T.tenant_id

    def test_publish_private_template(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        record = mp.publish(
            template={"name": "Private", "visibility": "private"},
            tenant_ctx=T,
        )
        assert record["is_community"] is False
        assert record["visibility"] == "private"

    def test_publish_adds_to_browse(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        record = mp.publish(
            template={
                "name": "Unique Agent xyz123",
                "domain": "analytics",
                "description": "analytics xyz123",
            },
            tenant_ctx=T,
        )
        results = mp.browse(query="xyz123", tenant_ctx=T)
        ids = [t["template_id"] for t in results]
        assert record["template_id"] in ids

    async def test_create_bundle_all_valid(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        result = await mp.create_bundle(
            name="DevOps Bundle",
            template_ids=["tpl-devops", "tpl-e2e-testing"],
            tenant_ctx=T,
        )
        assert result["bundle_name"] == "DevOps Bundle"
        assert result["templates_deployed"] == 2
        assert result["errors"] == []
        assert result["status"] == "complete"

    async def test_create_bundle_invalid_template_captured_in_errors(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        result = await mp.create_bundle(
            name="Bad Bundle",
            template_ids=["tpl-bug-fix", "tpl-nonexistent"],
            tenant_ctx=T,
        )
        assert result["templates_deployed"] == 1
        assert len(result["errors"]) == 1
        assert result["status"] == "partial"

    async def test_publish_version_no_db(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        result = await mp.publish_version(
            template_id="tpl-bug-fix",
            version="1.1.0",
            changelog="Fixed edge case",
        )
        assert result["template_id"] == "tpl-bug-fix"
        assert result["version"] == "1.1.0"

    async def test_get_version_history_no_db(self) -> None:
        from app.enterprise.marketplace import Marketplace

        mp = Marketplace()
        history = await mp.get_version_history(template_id="tpl-bug-fix")
        assert history == []
