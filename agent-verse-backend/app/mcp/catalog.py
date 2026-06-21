"""Pre-built connector catalog — 9 popular services ready to connect.

Each ConnectorSpec is a template: the user supplies their credentials and
the registry instantiates an MCPServerConfig from the spec + their auth config.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    description: str
    auth_type: str
    default_url: str
    icon: str = ""


CONNECTOR_CATALOG: list[ConnectorSpec] = [
    ConnectorSpec(
        name="github",
        description="GitHub — code repositories, PRs, issues, Actions",
        auth_type="bearer",
        default_url="https://api.github.com",
        icon="github",
    ),
    ConnectorSpec(
        name="jira",
        description="JIRA — project management, issue tracking, sprints",
        auth_type="api_key",
        default_url="https://your-domain.atlassian.net",
        icon="jira",
    ),
    ConnectorSpec(
        name="slack",
        description="Slack — messaging, channels, workflows, notifications",
        auth_type="oauth_ac",
        default_url="https://slack.com/api",
        icon="slack",
    ),
    ConnectorSpec(
        name="salesforce",
        description="Salesforce — CRM, leads, opportunities, accounts",
        auth_type="oauth_ac",
        default_url="https://login.salesforce.com",
        icon="salesforce",
    ),
    ConnectorSpec(
        name="linear",
        description="Linear — engineering issue tracking, cycles, roadmaps",
        auth_type="api_key",
        default_url="https://api.linear.app",
        icon="linear",
    ),
    ConnectorSpec(
        name="notion",
        description="Notion — wiki, docs, databases, tasks",
        auth_type="bearer",
        default_url="https://api.notion.com",
        icon="notion",
    ),
    ConnectorSpec(
        name="sentry",
        description="Sentry — error tracking, performance monitoring",
        auth_type="bearer",
        default_url="https://sentry.io/api/0",
        icon="sentry",
    ),
    ConnectorSpec(
        name="datadog",
        description="Datadog — metrics, APM, logs, monitors, alerts",
        auth_type="api_key",
        default_url="https://api.datadoghq.com",
        icon="datadog",
    ),
    ConnectorSpec(
        name="stripe",
        description="Stripe — payments, subscriptions, invoices, customers",
        auth_type="bearer",
        default_url="https://api.stripe.com",
        icon="stripe",
    ),
]
