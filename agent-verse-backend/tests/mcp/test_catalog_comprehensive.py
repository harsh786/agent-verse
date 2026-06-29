"""Comprehensive tests for app/mcp/catalog.py (32 connectors) and app/mcp/a2a.py."""
from __future__ import annotations

import pytest

from app.mcp.catalog import CONNECTOR_CATALOG, ConnectorSpec


# ── CONNECTOR_CATALOG length ──────────────────────────────────────────────────


def test_catalog_has_at_least_32_connectors() -> None:
    assert len(CONNECTOR_CATALOG) >= 32


def test_catalog_has_exactly_32_connectors() -> None:
    """The spec promises 32 connectors — count to catch accidental deletions."""
    assert len(CONNECTOR_CATALOG) == 32


# ── Every entry is a valid ConnectorSpec ─────────────────────────────────────


def test_all_connectors_are_connector_spec_instances() -> None:
    for spec in CONNECTOR_CATALOG:
        assert isinstance(spec, ConnectorSpec), f"{spec} is not a ConnectorSpec"


def test_all_connectors_have_non_empty_name() -> None:
    for spec in CONNECTOR_CATALOG:
        assert spec.name, f"Empty name in spec: {spec}"


def test_all_connectors_have_non_empty_description() -> None:
    for spec in CONNECTOR_CATALOG:
        assert spec.description, f"Empty description for: {spec.name}"


def test_all_connectors_have_non_empty_auth_type() -> None:
    for spec in CONNECTOR_CATALOG:
        assert spec.auth_type, f"Empty auth_type for: {spec.name}"


def test_all_connectors_have_non_empty_default_url() -> None:
    for spec in CONNECTOR_CATALOG:
        assert spec.default_url, f"Empty default_url for: {spec.name}"


def test_all_connector_names_are_unique() -> None:
    names = [s.name for s in CONNECTOR_CATALOG]
    assert len(names) == len(set(names)), "Duplicate connector names found"


def test_all_connectors_auth_types_are_valid() -> None:
    valid_auth_types = {"bearer", "api_key", "oauth_ac", "connection_string", "basic"}
    for spec in CONNECTOR_CATALOG:
        assert spec.auth_type in valid_auth_types, (
            f"Unknown auth_type '{spec.auth_type}' for {spec.name}"
        )


# ── Specific connector checks ─────────────────────────────────────────────────


def _get(name: str) -> ConnectorSpec:
    for s in CONNECTOR_CATALOG:
        if s.name == name:
            return s
    raise KeyError(f"Connector '{name}' not found")


def test_github_connector() -> None:
    spec = _get("github")
    assert spec.auth_type == "bearer"
    assert "github.com" in spec.default_url


def test_jira_connector() -> None:
    spec = _get("jira")
    assert spec.auth_type == "api_key"
    assert "atlassian.net" in spec.default_url


def test_slack_connector() -> None:
    spec = _get("slack")
    assert spec.auth_type == "oauth_ac"
    assert "slack.com" in spec.default_url


def test_salesforce_connector() -> None:
    spec = _get("salesforce")
    assert spec.auth_type == "oauth_ac"


def test_stripe_connector() -> None:
    spec = _get("stripe")
    assert spec.auth_type == "bearer"
    assert "stripe.com" in spec.default_url


def test_postgres_connector_uses_connection_string() -> None:
    spec = _get("postgresql")
    assert spec.auth_type == "connection_string"
    assert spec.default_url.startswith("postgresql://")


def test_mysql_connector_uses_connection_string() -> None:
    spec = _get("mysql")
    assert spec.auth_type == "connection_string"
    assert spec.default_url.startswith("mysql://")


def test_mongodb_connector_uses_connection_string() -> None:
    spec = _get("mongodb")
    assert spec.auth_type == "connection_string"
    assert spec.default_url.startswith("mongodb://")


def test_twilio_connector_uses_basic_auth() -> None:
    spec = _get("twilio")
    assert spec.auth_type == "basic"


def test_datadog_connector() -> None:
    spec = _get("datadog")
    assert spec.auth_type == "api_key"
    assert "datadoghq.com" in spec.default_url


def test_kubernetes_connector() -> None:
    spec = _get("kubernetes")
    assert spec.auth_type == "bearer"


def test_gitlab_connector() -> None:
    spec = _get("gitlab")
    assert spec.auth_type == "bearer"
    assert "gitlab.com" in spec.default_url


def test_pagerduty_connector() -> None:
    spec = _get("pagerduty")
    assert spec.auth_type == "api_key"


def test_confluence_connector() -> None:
    spec = _get("confluence")
    assert spec.auth_type == "api_key"
    assert "atlassian.net" in spec.default_url


def test_connector_spec_is_frozen() -> None:
    """ConnectorSpec is frozen — mutation must raise."""
    spec = _get("github")
    with pytest.raises((AttributeError, TypeError)):
        spec.name = "modified"  # type: ignore[misc]


# ── Auth type distribution ────────────────────────────────────────────────────


def test_oauth_ac_connectors_present() -> None:
    oauth_names = [s.name for s in CONNECTOR_CATALOG if s.auth_type == "oauth_ac"]
    assert len(oauth_names) >= 4  # slack, salesforce, gcp, google_sheets, etc.


def test_bearer_connectors_present() -> None:
    bearer_names = [s.name for s in CONNECTOR_CATALOG if s.auth_type == "bearer"]
    assert len(bearer_names) >= 6  # github, notion, sentry, stripe, discord, etc.


def test_api_key_connectors_present() -> None:
    api_key_names = [s.name for s in CONNECTOR_CATALOG if s.auth_type == "api_key"]
    assert len(api_key_names) >= 6


def test_connection_string_connectors_present() -> None:
    conn_names = [s.name for s in CONNECTOR_CATALOG if s.auth_type == "connection_string"]
    assert len(conn_names) >= 3  # postgresql, mysql, mongodb


# ── A2A protocol models ───────────────────────────────────────────────────────


class TestAgentCard:
    def test_agent_card_creation(self) -> None:
        from app.mcp.a2a import AgentCard

        card = AgentCard(
            name="TestAgent",
            version="1.0.0",
            endpoint="https://agent.example.com",
            capabilities=["summarize", "search"],
            description="A test agent",
            tenant_id="t1",
            auth_required=True,
        )
        assert card.name == "TestAgent"
        assert card.version == "1.0.0"
        assert "summarize" in card.capabilities
        assert card.auth_required is True

    def test_agent_card_defaults(self) -> None:
        from app.mcp.a2a import AgentCard

        card = AgentCard(name="Minimal", version="0.1", endpoint="http://x.com")
        assert card.agent_id == ""
        assert card.capabilities == []
        assert card.description == ""
        assert card.auth_required is False

    def test_agent_card_is_pydantic_model(self) -> None:
        from pydantic import BaseModel

        from app.mcp.a2a import AgentCard

        assert issubclass(AgentCard, BaseModel)

    def test_agent_card_serialization(self) -> None:
        from app.mcp.a2a import AgentCard

        card = AgentCard(
            name="Agent", version="1.0", endpoint="https://ep.com",
            capabilities=["tools"], tenant_id="t1"
        )
        d = card.model_dump()
        assert d["name"] == "Agent"
        assert "capabilities" in d


class TestA2ATask:
    def test_a2a_task_creation(self) -> None:
        from app.mcp.a2a import A2ATask

        task = A2ATask(
            task_id="task-1",
            goal="Summarize the repo",
            sender_endpoint="https://sender.example.com",
            context={"repo": "my-repo"},
            callback_url="https://callback.example.com",
        )
        assert task.task_id == "task-1"
        assert task.goal == "Summarize the repo"
        assert task.status == "pending"
        assert task.context["repo"] == "my-repo"

    def test_a2a_task_defaults(self) -> None:
        from app.mcp.a2a import A2ATask

        task = A2ATask(task_id="t", goal="do something")
        assert task.sender_endpoint == ""
        assert task.context == {}
        assert task.callback_url is None
        assert task.status == "pending"


class TestA2ATaskResult:
    def test_a2a_result_success(self) -> None:
        from app.mcp.a2a import A2ATaskResult

        result = A2ATaskResult(
            task_id="task-1",
            status="completed",
            result={"output": "done"},
        )
        assert result.task_id == "task-1"
        assert result.status == "completed"
        assert result.result == {"output": "done"}
        assert result.error == ""

    def test_a2a_result_failure(self) -> None:
        from app.mcp.a2a import A2ATaskResult

        result = A2ATaskResult(
            task_id="task-2",
            status="failed",
            error="Connection refused",
        )
        assert result.status == "failed"
        assert result.result is None
        assert result.error == "Connection refused"
