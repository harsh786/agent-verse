"""Pre-built connector catalog — 32 popular services ready to connect.

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
    # ── Original 9 ───────────────────────────────────────────────────────────
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
    # ── CRM / Support ────────────────────────────────────────────────────────
    ConnectorSpec(
        name="hubspot",
        description="HubSpot — CRM, contacts, deals, marketing automation",
        auth_type="oauth_ac",
        default_url="https://api.hubapi.com",
        icon="hubspot",
    ),
    ConnectorSpec(
        name="zendesk",
        description="Zendesk — customer support, tickets, agents, macros",
        auth_type="api_key",
        default_url="https://your-domain.zendesk.com/api/v2",
        icon="zendesk",
    ),
    ConnectorSpec(
        name="intercom",
        description="Intercom — customer messaging, conversations, contacts",
        auth_type="bearer",
        default_url="https://api.intercom.io",
        icon="intercom",
    ),
    # ── Cloud Providers ──────────────────────────────────────────────────────
    ConnectorSpec(
        name="aws",
        description="AWS — boto3-backed tools for S3, EC2, Lambda, IAM, CloudWatch",
        auth_type="api_key",
        default_url="https://aws.amazon.com",
        icon="aws",
    ),
    ConnectorSpec(
        name="gcp",
        description="GCP — Google Cloud Storage, BigQuery, Pub/Sub, Cloud Run",
        auth_type="oauth_ac",
        default_url="https://www.googleapis.com",
        icon="gcp",
    ),
    # ── Databases ────────────────────────────────────────────────────────────
    ConnectorSpec(
        name="postgresql",
        description="PostgreSQL — execute queries, inspect schema, manage tables",
        auth_type="connection_string",
        default_url="postgresql://localhost:5432/db",
        icon="postgresql",
    ),
    ConnectorSpec(
        name="mysql",
        description="MySQL — query execution, schema introspection, DML operations",
        auth_type="connection_string",
        default_url="mysql://localhost:3306/db",
        icon="mysql",
    ),
    ConnectorSpec(
        name="mongodb",
        description="MongoDB — documents CRUD, aggregations, index management",
        auth_type="connection_string",
        default_url="mongodb://localhost:27017",
        icon="mongodb",
    ),
    ConnectorSpec(
        name="snowflake",
        description="Snowflake — data warehouse queries, warehouses, schemas",
        auth_type="api_key",
        default_url="https://account.snowflakecomputing.com",
        icon="snowflake",
    ),
    # ── Productivity ─────────────────────────────────────────────────────────
    ConnectorSpec(
        name="google_sheets",
        description="Google Sheets — read/write spreadsheets, ranges, formulas",
        auth_type="oauth_ac",
        default_url="https://sheets.googleapis.com/v4",
        icon="google_sheets",
    ),
    ConnectorSpec(
        name="asana",
        description="Asana — tasks, projects, portfolios, team workspaces",
        auth_type="bearer",
        default_url="https://app.asana.com/api/1.0",
        icon="asana",
    ),
    ConnectorSpec(
        name="monday",
        description="Monday.com — boards, items, columns, automations",
        auth_type="bearer",
        default_url="https://api.monday.com/v2",
        icon="monday",
    ),
    # ── Communication ────────────────────────────────────────────────────────
    ConnectorSpec(
        name="teams",
        description="Microsoft Teams — messaging, channels, meetings, notifications",
        auth_type="oauth_ac",
        default_url="https://graph.microsoft.com/v1.0",
        icon="teams",
    ),
    ConnectorSpec(
        name="discord",
        description="Discord — community messaging, channels, webhooks",
        auth_type="bearer",
        default_url="https://discord.com/api/v10",
        icon="discord",
    ),
    ConnectorSpec(
        name="twilio",
        description="Twilio — SMS, voice, WhatsApp, email notifications",
        auth_type="basic",
        default_url="https://api.twilio.com/2010-04-01",
        icon="twilio",
    ),
    # ── Finance ──────────────────────────────────────────────────────────────
    ConnectorSpec(
        name="quickbooks",
        description="QuickBooks — accounting, invoicing, expense tracking",
        auth_type="oauth_ac",
        default_url="https://quickbooks.api.intuit.com/v3",
        icon="quickbooks",
    ),
    # ── Identity ─────────────────────────────────────────────────────────────
    ConnectorSpec(
        name="okta",
        description="Okta — identity, SSO, user provisioning, MFA",
        auth_type="api_key",
        default_url="https://your-domain.okta.com/api/v1",
        icon="okta",
    ),
    # ── DevOps / Infrastructure ──────────────────────────────────────────────
    ConnectorSpec(
        name="circleci",
        description="CircleCI — CI/CD pipelines, jobs, workflows, artifacts",
        auth_type="bearer",
        default_url="https://circleci.com/api/v2",
        icon="circleci",
    ),
    ConnectorSpec(
        name="terraform",
        description="Terraform Cloud — IaC workspaces, runs, state management",
        auth_type="bearer",
        default_url="https://app.terraform.io/api/v2",
        icon="terraform",
    ),
    ConnectorSpec(
        name="kubernetes",
        description="Kubernetes — pods, deployments, services, namespaces",
        auth_type="bearer",
        default_url="https://kubernetes.default.svc",
        icon="kubernetes",
    ),
    # ── Additional DevOps / Documentation ────────────────────────────────────
    ConnectorSpec(
        name="gitlab",
        description="GitLab — repositories, CI/CD pipelines, merge requests, issues",
        auth_type="bearer",
        default_url="https://gitlab.com/api/v4",
        icon="gitlab",
    ),
    ConnectorSpec(
        name="pagerduty",
        description="PagerDuty — incident management, on-call schedules, alerts",
        auth_type="api_key",
        default_url="https://api.pagerduty.com",
        icon="pagerduty",
    ),
    ConnectorSpec(
        name="confluence",
        description="Confluence — wiki pages, spaces, team knowledge base",
        auth_type="api_key",
        default_url="https://your-domain.atlassian.net/wiki/rest/api",
        icon="confluence",
    ),
]
