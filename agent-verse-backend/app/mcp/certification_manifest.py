from __future__ import annotations

from typing import Literal, TypedDict

AuthMode = Literal["basic", "bearer", "connection_string", "custom_header", "oauth_ac"]
ExpectedArtifactKind = Literal["cards", "json", "table", "text"]


class ConnectorTarget(TypedDict):
    display_name: str
    category: str
    auth_modes: list[AuthMode]
    required_secrets: list[str]
    read_tool: str
    read_arguments: dict[str, object]
    expected_artifact_kind: ExpectedArtifactKind
    live_env: list[str]

CONNECTOR_CERTIFICATION_TARGETS: dict[str, ConnectorTarget] = {
    "jira": {
        "display_name": "Jira",
        "category": "project_management",
        "auth_modes": ["basic", "custom_header", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "jira_search_issues",
        "read_arguments": {
            "jql": "assignee = currentUser() AND created >= -26w ORDER BY created DESC",
            "max_results": 10,
        },
        "expected_artifact_kind": "table",
        "live_env": ["JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"],
    },
    "github": {
        "display_name": "GitHub",
        "category": "devtools",
        "auth_modes": ["bearer", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "github_search_issues",
        "read_arguments": {"query": "is:issue is:open"},
        "expected_artifact_kind": "table",
        "live_env": ["GITHUB_TOKEN"],
    },
    "slack": {
        "display_name": "Slack",
        "category": "communication",
        "auth_modes": ["bearer", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "slack_search_messages",
        "read_arguments": {"query": "from:me"},
        "expected_artifact_kind": "cards",
        "live_env": ["SLACK_BOT_TOKEN"],
    },
    "google_workspace": {
        "display_name": "Google Workspace",
        "category": "productivity",
        "auth_modes": ["oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "google_drive_search",
        "read_arguments": {"query": "modifiedTime > '2026-01-01'"},
        "expected_artifact_kind": "table",
        "live_env": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
    },
    "hubspot": {
        "display_name": "HubSpot",
        "category": "crm",
        "auth_modes": ["bearer", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "hubspot_search_contacts",
        "read_arguments": {"limit": 10},
        "expected_artifact_kind": "table",
        "live_env": ["HUBSPOT_ACCESS_TOKEN"],
    },
    "stripe": {
        "display_name": "Stripe",
        "category": "finance",
        "auth_modes": ["bearer"],
        "required_secrets": ["Authorization"],
        "read_tool": "stripe_list_customers",
        "read_arguments": {"limit": 10},
        "expected_artifact_kind": "table",
        "live_env": ["STRIPE_API_KEY"],
    },
    "datadog": {
        "display_name": "Datadog",
        "category": "observability",
        "auth_modes": ["custom_header"],
        "required_secrets": ["DD-API-KEY", "DD-APPLICATION-KEY"],
        "read_tool": "datadog_list_monitors",
        "read_arguments": {},
        "expected_artifact_kind": "table",
        "live_env": ["DATADOG_API_KEY", "DATADOG_APP_KEY"],
    },
    "sentry": {
        "display_name": "Sentry",
        "category": "observability",
        "auth_modes": ["bearer"],
        "required_secrets": ["Authorization"],
        "read_tool": "sentry_list_issues",
        "read_arguments": {"limit": 10},
        "expected_artifact_kind": "table",
        "live_env": ["SENTRY_AUTH_TOKEN", "SENTRY_ORG"],
    },
    "aws": {
        "display_name": "AWS",
        "category": "cloud",
        "auth_modes": ["custom_header"],
        "required_secrets": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        "read_tool": "aws_list_buckets",
        "read_arguments": {},
        "expected_artifact_kind": "table",
        "live_env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    },
    "postgres": {
        "display_name": "Postgres",
        "category": "database",
        "auth_modes": ["connection_string"],
        "required_secrets": ["DATABASE_URL"],
        "read_tool": "postgres_list_tables",
        "read_arguments": {},
        "expected_artifact_kind": "table",
        "live_env": ["POSTGRES_MCP_URL"],
    },
}
