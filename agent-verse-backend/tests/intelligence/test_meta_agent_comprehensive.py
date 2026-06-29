"""Comprehensive tests for app/intelligence/meta_agent.py — targeting 100% coverage."""
from __future__ import annotations

import json

import pytest

from app.intelligence.meta_agent import MetaAgentConfig, MetaAgentPlanner
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="meta-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


class TestMetaAgentConfig:
    def test_defaults(self):
        cfg = MetaAgentConfig(
            name="test-agent",
            goal_template="Do something",
            connectors=[],
        )
        assert cfg.trigger_type == "rest"
        assert cfg.event_channel == ""
        assert cfg.cron_expression == ""
        assert cfg.interval_seconds == 0
        assert cfg.autonomy_mode == "bounded-autonomous"
        assert cfg.policy_suggestions == []

    def test_explicit_values(self):
        cfg = MetaAgentConfig(
            name="jira-agent",
            goal_template="Manage Jira issues for {project}",
            connectors=["jira", "slack"],
            trigger_type="cron",
            cron_expression="0 9 * * 1",
            autonomy_mode="supervised",
            policy_suggestions=["require_approval_for_close"],
        )
        assert cfg.name == "jira-agent"
        assert "jira" in cfg.connectors
        assert cfg.trigger_type == "cron"
        assert cfg.cron_expression == "0 9 * * 1"
        assert cfg.autonomy_mode == "supervised"


class TestMetaAgentPlannerFallback:
    """Tests for JSON decode failure fallback path (previously 0% covered)."""

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_to_unnamed_agent(self):
        """When LLM returns garbage, fall back to unnamed-agent with original command."""
        provider = FakeProvider(responses=["not valid JSON at all!!!"])
        planner = MetaAgentPlanner(provider=provider)

        config = await planner.plan(
            command="Create an agent for data pipeline monitoring",
            tenant_ctx=_CTX,
        )
        assert config.name == "unnamed-agent"
        assert config.goal_template == "Create an agent for data pipeline monitoring"
        assert config.connectors == []

    @pytest.mark.asyncio
    async def test_partial_json_falls_back(self):
        """Partial/truncated JSON should also trigger fallback."""
        provider = FakeProvider(responses=['{"name": "my-agent", "goal_temp'])  # truncated
        planner = MetaAgentPlanner(provider=provider)

        config = await planner.plan(command="Partial JSON test command", tenant_ctx=_CTX)
        assert config.name == "unnamed-agent"

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json_is_parsed(self):
        """LLM sometimes wraps JSON in markdown code fences — must be stripped."""
        llm_response = "```json\n" + json.dumps({
            "name": "pr-agent",
            "goal_template": "Review PR {id}",
            "connectors": ["github"],
            "trigger_type": "webhook",
            "autonomy_mode": "supervised",
            "policy_suggestions": [],
        }) + "\n```"
        provider = FakeProvider(responses=[llm_response])
        planner = MetaAgentPlanner(provider=provider)

        config = await planner.plan(command="Review pull requests", tenant_ctx=_CTX)
        assert config.name == "pr-agent"
        assert "github" in config.connectors

    @pytest.mark.asyncio
    async def test_missing_fields_use_defaults(self):
        """JSON missing optional fields should use MetaAgentConfig defaults."""
        llm_response = json.dumps({"name": "minimal-agent"})
        provider = FakeProvider(responses=[llm_response])
        planner = MetaAgentPlanner(provider=provider)

        config = await planner.plan(command="Minimal agent command", tenant_ctx=_CTX)
        assert config.name == "minimal-agent"
        assert config.goal_template == "Minimal agent command"  # fallback to command
        assert config.connectors == []
        assert config.trigger_type == "rest"
        assert config.autonomy_mode == "bounded-autonomous"

    @pytest.mark.asyncio
    async def test_full_config_all_fields(self):
        """Full JSON response maps to MetaAgentConfig correctly."""
        llm_response = json.dumps({
            "name": "full-agent",
            "goal_template": "Complete task {name}",
            "connectors": ["jira", "github", "slack"],
            "trigger_type": "interval",
            "event_channel": "",
            "cron_expression": "",
            "interval_seconds": 3600,
            "autonomy_mode": "fully-autonomous",
            "policy_suggestions": ["no-delete", "log-all"],
        })
        provider = FakeProvider(responses=[llm_response])
        planner = MetaAgentPlanner(provider=provider)

        config = await planner.plan(command="Run hourly sync", tenant_ctx=_CTX)
        assert config.name == "full-agent"
        assert config.interval_seconds == 3600
        assert config.autonomy_mode == "fully-autonomous"
        assert len(config.policy_suggestions) == 2

    @pytest.mark.asyncio
    async def test_interval_seconds_coerced_to_int(self):
        """interval_seconds value should be coerced to int."""
        llm_response = json.dumps({
            "name": "interval-agent",
            "goal_template": "Run every 30 min",
            "connectors": [],
            "interval_seconds": "1800",  # string, should be coerced
        })
        provider = FakeProvider(responses=[llm_response])
        planner = MetaAgentPlanner(provider=provider)

        config = await planner.plan(command="Interval test", tenant_ctx=_CTX)
        assert isinstance(config.interval_seconds, int)
        assert config.interval_seconds == 1800

    @pytest.mark.asyncio
    async def test_provider_is_called_with_correct_messages(self):
        """The planner must pass system + user messages to the provider."""
        provider = FakeProvider(responses=[json.dumps({
            "name": "verify-agent",
            "goal_template": "Verify",
            "connectors": [],
        })])
        planner = MetaAgentPlanner(provider=provider)

        await planner.plan(command="My test command", tenant_ctx=_CTX)

        assert len(provider.call_history) == 1
        req = provider.call_history[0]
        roles = [m.role for m in req.messages]
        assert "system" in roles
        assert "user" in roles
        user_msg = next(m for m in req.messages if m.role == "user")
        assert "My test command" in str(user_msg.content)

    @pytest.mark.asyncio
    async def test_empty_json_object_uses_defaults(self):
        """Empty JSON {} should produce an agent with all defaults."""
        provider = FakeProvider(responses=["{}"])
        planner = MetaAgentPlanner(provider=provider)

        config = await planner.plan(command="Empty JSON test", tenant_ctx=_CTX)
        assert config.name == "unnamed-agent"
        assert config.connectors == []
