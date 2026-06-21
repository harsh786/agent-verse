"""Tests for the meta-agent — one NL command creates a complete agent config.

The meta-agent decomposes a command like:
  "Create an agent that onboards new engineers"
into:
  - goal_template: "Onboard new engineer {name} by creating accounts, filing tickets, messaging welcome"
  - connectors: ["github", "jira", "slack"]
  - trigger: TriggerSpec(EVENT, event_channel="hr.new_hire")
  - autonomy_mode: "bounded-autonomous"
  - policy_suggestions: ["require approval for account creation"]

Tests use FakeProvider for deterministic LLM responses.
"""

from __future__ import annotations

import json

import pytest

from app.intelligence.meta_agent import MetaAgentConfig, MetaAgentPlanner
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")


def test_meta_agent_config_model() -> None:
    config = MetaAgentConfig(
        name="onboarding-agent",
        goal_template="Onboard new engineer {name}",
        connectors=["jira", "slack"],
        trigger_type="event",
        autonomy_mode="bounded-autonomous",
    )
    assert config.name == "onboarding-agent"
    assert "jira" in config.connectors


async def test_meta_agent_planner_creates_config() -> None:
    llm_response = json.dumps({
        "name": "onboarding-agent",
        "goal_template": "Onboard new engineer",
        "connectors": ["jira", "slack", "github"],
        "trigger_type": "event",
        "event_channel": "hr.new_hire",
        "autonomy_mode": "bounded-autonomous",
        "policy_suggestions": ["require approval for account creation"],
    })
    provider = FakeProvider(responses=[llm_response])
    planner = MetaAgentPlanner(provider=provider)

    config = await planner.plan(
        command="Create an agent that onboards new engineers",
        tenant_ctx=_CTX,
    )
    assert config.name == "onboarding-agent"
    assert "jira" in config.connectors
    assert config.autonomy_mode == "bounded-autonomous"


async def test_meta_agent_infers_connectors() -> None:
    llm_response = json.dumps({
        "name": "bug-fix-agent",
        "goal_template": "Fix bug {issue_id}",
        "connectors": ["github", "jira", "sentry"],
        "trigger_type": "webhook",
        "autonomy_mode": "supervised",
        "policy_suggestions": [],
    })
    provider = FakeProvider(responses=[llm_response])
    planner = MetaAgentPlanner(provider=provider)

    config = await planner.plan(
        command="Fix JIRA bugs labeled prod-down and open a PR",
        tenant_ctx=_CTX,
    )
    assert "github" in config.connectors or "jira" in config.connectors
