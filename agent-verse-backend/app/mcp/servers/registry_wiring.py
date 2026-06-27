"""Wire all built-in MCP server wrappers into the MCP catalog."""
from __future__ import annotations

from typing import Any


def get_builtin_server_configs() -> list[dict]:
    """Return configurations for all built-in MCP server wrappers."""
    from app.mcp.servers import (
        amplitude_server,
        elasticsearch_server,
        github_server,
        loggly_server,
        mixpanel_server,
        mongodb_server,
        mysql_server,
        new_relic_server,
        pinecone_server,
        postgres_server,
        prometheus_server,
        redis_server,
        sentry_server,
        slack_server,
        snowflake_server,
        splunk_server,
        supabase_server,
        # ── DevOps & Source Control ──────────────────────────────────────────
        gitlab_server,
        bitbucket_server,
        jenkins_server,
        # ── Hosting & Deployment ─────────────────────────────────────────────
        vercel_server,
        netlify_server,
        heroku_server,
        digitalocean_server,
        # ── Container & Orchestration ────────────────────────────────────────
        kubernetes_server,
        docker_server,
        # ── AWS ──────────────────────────────────────────────────────────────
        aws_lambda_server,
        aws_s3_server,
        aws_cloudwatch_server,
        aws_iam_server,
        # ── Azure DevOps ─────────────────────────────────────────────────────
        azure_devops_server,
        # ── Communication & Messaging ────────────────────────────────────────
        discord_server,
        telegram_server,
        microsoft_teams_server,
        whatsapp_server,
        mattermost_server,
        intercom_server,
        # ── Email & Marketing ────────────────────────────────────────────────
        sendgrid_server,
        mailchimp_server,
        klaviyo_server,
        brevo_server,
        mailerlite_server,
        convertkit_server,
        customerio_server,
        twilio_server,
        mandrill_server,
    )

    return [
        # ── Communication & Collaboration ───────────────────────────────────
        {
            "server_id": "builtin-github",
            "name": "GitHub",
            "description": "GitHub repositories, issues, PRs, and code search",
            "tool_definitions": github_server.TOOL_DEFINITIONS,
            "handler": github_server.call_tool,
            "requires_env": ["GITHUB_TOKEN"],
        },
        {
            "server_id": "builtin-slack",
            "name": "Slack",
            "description": "Send messages and search Slack",
            "tool_definitions": slack_server.TOOL_DEFINITIONS,
            "handler": slack_server.call_tool,
            "requires_env": ["SLACK_BOT_TOKEN"],
        },
        # ── Databases ───────────────────────────────────────────────────────
        {
            "server_id": "builtin-postgres",
            "name": "PostgreSQL",
            "description": "Query PostgreSQL databases",
            "tool_definitions": postgres_server.TOOL_DEFINITIONS,
            "handler": postgres_server.call_tool,
            "requires_env": ["POSTGRES_MCP_URL"],
        },
        {
            "server_id": "builtin-mysql",
            "name": "MySQL",
            "description": "Query and manage MySQL databases",
            "tool_definitions": mysql_server.TOOL_DEFINITIONS,
            "handler": mysql_server.call_tool,
            "requires_env": ["MYSQL_MCP_URL"],
        },
        {
            "server_id": "builtin-mongodb",
            "name": "MongoDB",
            "description": "Query and manage MongoDB collections",
            "tool_definitions": mongodb_server.TOOL_DEFINITIONS,
            "handler": mongodb_server.call_tool,
            "requires_env": ["MONGODB_MCP_URL"],
        },
        {
            "server_id": "builtin-redis",
            "name": "Redis",
            "description": "Interact with an external Redis data store",
            "tool_definitions": redis_server.TOOL_DEFINITIONS,
            "handler": redis_server.call_tool,
            "requires_env": ["REDIS_MCP_URL"],
        },
        {
            "server_id": "builtin-snowflake",
            "name": "Snowflake",
            "description": "Query Snowflake cloud data warehouse",
            "tool_definitions": snowflake_server.TOOL_DEFINITIONS,
            "handler": snowflake_server.call_tool,
            "requires_env": ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"],
        },
        # ── Search & Analytics ──────────────────────────────────────────────
        {
            "server_id": "builtin-elasticsearch",
            "name": "Elasticsearch",
            "description": "Search and manage Elasticsearch indices",
            "tool_definitions": elasticsearch_server.TOOL_DEFINITIONS,
            "handler": elasticsearch_server.call_tool,
            "requires_env": ["ELASTICSEARCH_URL"],
        },
        {
            "server_id": "builtin-supabase",
            "name": "Supabase",
            "description": "Query and manage Supabase tables and auth",
            "tool_definitions": supabase_server.TOOL_DEFINITIONS,
            "handler": supabase_server.call_tool,
            "requires_env": ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"],
        },
        # ── Vector Databases ────────────────────────────────────────────────
        {
            "server_id": "builtin-pinecone",
            "name": "Pinecone",
            "description": "Vector search and upsert operations on Pinecone indexes",
            "tool_definitions": pinecone_server.TOOL_DEFINITIONS,
            "handler": pinecone_server.call_tool,
            "requires_env": ["PINECONE_API_KEY"],
        },
        # ── Error Tracking & APM ────────────────────────────────────────────
        {
            "server_id": "builtin-sentry",
            "name": "Sentry",
            "description": "Manage Sentry issues, events, and releases",
            "tool_definitions": sentry_server.TOOL_DEFINITIONS,
            "handler": sentry_server.call_tool,
            "requires_env": ["SENTRY_AUTH_TOKEN", "SENTRY_ORG_SLUG"],
        },
        {
            "server_id": "builtin-new-relic",
            "name": "New Relic",
            "description": "Query metrics, alerts, and APM data from New Relic",
            "tool_definitions": new_relic_server.TOOL_DEFINITIONS,
            "handler": new_relic_server.call_tool,
            "requires_env": ["NEW_RELIC_API_KEY"],
        },
        # ── Product Analytics ───────────────────────────────────────────────
        {
            "server_id": "builtin-mixpanel",
            "name": "Mixpanel",
            "description": "Query Mixpanel event data, funnels, retention, and user profiles",
            "tool_definitions": mixpanel_server.TOOL_DEFINITIONS,
            "handler": mixpanel_server.call_tool,
            "requires_env": [
                "MIXPANEL_SERVICE_ACCOUNT_USERNAME",
                "MIXPANEL_SERVICE_ACCOUNT_SECRET",
                "MIXPANEL_PROJECT_ID",
            ],
        },
        {
            "server_id": "builtin-amplitude",
            "name": "Amplitude",
            "description": "Query Amplitude events, cohorts, and user profiles",
            "tool_definitions": amplitude_server.TOOL_DEFINITIONS,
            "handler": amplitude_server.call_tool,
            "requires_env": ["AMPLITUDE_API_KEY", "AMPLITUDE_SECRET_KEY"],
        },
        # ── Metrics & Observability ─────────────────────────────────────────
        {
            "server_id": "builtin-prometheus",
            "name": "Prometheus",
            "description": "Query Prometheus metrics, alerts, and targets via PromQL",
            "tool_definitions": prometheus_server.TOOL_DEFINITIONS,
            "handler": prometheus_server.call_tool,
            "requires_env": ["PROMETHEUS_URL"],
        },
        {
            "server_id": "builtin-splunk",
            "name": "Splunk",
            "description": "Search and analyze Splunk log events via SPL",
            "tool_definitions": splunk_server.TOOL_DEFINITIONS,
            "handler": splunk_server.call_tool,
            "requires_env": ["SPLUNK_URL"],
        },
        {
            "server_id": "builtin-smartsuite",
            "name": "SmartSuite",
            "description": "SmartSuite solutions, tables, and record management",
            "tool_definitions": smartsuite_server.TOOL_DEFINITIONS,
            "handler": smartsuite_server.call_tool,
            "requires_env": ["SMARTSUITE_API_KEY", "SMARTSUITE_ACCOUNT_ID"],
        },
        # ── DevOps & Source Control ──────────────────────────────────────────
        {
            "server_id": "builtin-gitlab",
            "name": "GitLab",
            "description": "GitLab — projects, issues, merge requests, file contents, and CI/CD pipelines",
            "tool_definitions": gitlab_server.TOOL_DEFINITIONS,
            "handler": gitlab_server.call_tool,
            "requires_env": ["GITLAB_TOKEN"],
        },
        {
            "server_id": "builtin-bitbucket",
            "name": "Bitbucket",
            "description": "Bitbucket Cloud — repositories, issues, pull requests, and pipelines",
            "tool_definitions": bitbucket_server.TOOL_DEFINITIONS,
            "handler": bitbucket_server.call_tool,
            "requires_env": ["BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD"],
        },
        {
            "server_id": "builtin-jenkins",
            "name": "Jenkins",
            "description": "Jenkins CI/CD — jobs, builds, triggers, logs, and parameterized builds",
            "tool_definitions": jenkins_server.TOOL_DEFINITIONS,
            "handler": jenkins_server.call_tool,
            "requires_env": ["JENKINS_URL", "JENKINS_USER", "JENKINS_API_TOKEN"],
        },
        # ── Hosting & Deployment ─────────────────────────────────────────────
        {
            "server_id": "builtin-vercel",
            "name": "Vercel",
            "description": "Vercel — projects, deployments, domains, and environment variables",
            "tool_definitions": vercel_server.TOOL_DEFINITIONS,
            "handler": vercel_server.call_tool,
            "requires_env": ["VERCEL_TOKEN"],
        },
        {
            "server_id": "builtin-netlify",
            "name": "Netlify",
            "description": "Netlify — sites, deploys, locking, and publishing",
            "tool_definitions": netlify_server.TOOL_DEFINITIONS,
            "handler": netlify_server.call_tool,
            "requires_env": ["NETLIFY_ACCESS_TOKEN"],
        },
        {
            "server_id": "builtin-heroku",
            "name": "Heroku",
            "description": "Heroku Platform — apps, dynos, releases, config vars, and add-ons",
            "tool_definitions": heroku_server.TOOL_DEFINITIONS,
            "handler": heroku_server.call_tool,
            "requires_env": ["HEROKU_API_KEY"],
        },
        {
            "server_id": "builtin-digitalocean",
            "name": "DigitalOcean",
            "description": "DigitalOcean — Droplets, databases, App Platform apps, and domains",
            "tool_definitions": digitalocean_server.TOOL_DEFINITIONS,
            "handler": digitalocean_server.call_tool,
            "requires_env": ["DIGITALOCEAN_TOKEN"],
        },
        # ── Container & Orchestration ────────────────────────────────────────
        {
            "server_id": "builtin-kubernetes",
            "name": "Kubernetes",
            "description": "Kubernetes — pods, deployments, scaling, logs, services, and manifests",
            "tool_definitions": kubernetes_server.TOOL_DEFINITIONS,
            "handler": kubernetes_server.call_tool,
            "requires_env": ["KUBE_API_SERVER", "KUBE_TOKEN"],
        },
        {
            "server_id": "builtin-docker",
            "name": "Docker",
            "description": "Docker Engine & Docker Hub — containers, images, volumes, networks, and image search",
            "tool_definitions": docker_server.TOOL_DEFINITIONS,
            "handler": docker_server.call_tool,
            "requires_env": [],
        },
        # ── AWS ──────────────────────────────────────────────────────────────
        {
            "server_id": "builtin-aws-lambda",
            "name": "AWS Lambda",
            "description": "AWS Lambda — list, invoke, inspect, update, and get logs for Lambda functions",
            "tool_definitions": aws_lambda_server.TOOL_DEFINITIONS,
            "handler": aws_lambda_server.call_tool,
            "requires_env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        },
        {
            "server_id": "builtin-aws-s3",
            "name": "AWS S3",
            "description": "AWS S3 — buckets, objects, presigned URLs, and bucket management",
            "tool_definitions": aws_s3_server.TOOL_DEFINITIONS,
            "handler": aws_s3_server.call_tool,
            "requires_env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        },
        {
            "server_id": "builtin-aws-cloudwatch",
            "name": "AWS CloudWatch",
            "description": "AWS CloudWatch — metrics, alarms, log groups, and log filtering",
            "tool_definitions": aws_cloudwatch_server.TOOL_DEFINITIONS,
            "handler": aws_cloudwatch_server.call_tool,
            "requires_env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        },
        {
            "server_id": "builtin-aws-iam",
            "name": "AWS IAM",
            "description": "AWS IAM — users, roles, managed policies, and policy attachments",
            "tool_definitions": aws_iam_server.TOOL_DEFINITIONS,
            "handler": aws_iam_server.call_tool,
            "requires_env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        },
        # ── Azure DevOps ─────────────────────────────────────────────────────
        {
            "server_id": "builtin-azure-devops",
            "name": "Azure DevOps",
            "description": "Azure DevOps — repos, pull requests, work items, and pipelines",
            "tool_definitions": azure_devops_server.TOOL_DEFINITIONS,
            "handler": azure_devops_server.call_tool,
            "requires_env": ["AZURE_DEVOPS_TOKEN", "AZURE_DEVOPS_ORG"],
        },
        # ── Communication & Messaging ────────────────────────────────────────
        {
            "server_id": "builtin-discord",
            "name": "Discord",
            "description": "Discord — send messages, list guilds/channels, create threads, manage reactions",
            "tool_definitions": discord_server.TOOL_DEFINITIONS,
            "handler": discord_server.call_tool,
            "requires_env": ["DISCORD_BOT_TOKEN"],
        },
        {
            "server_id": "builtin-telegram",
            "name": "Telegram",
            "description": "Telegram Bot API — send messages, photos, documents, get updates and chat info",
            "tool_definitions": telegram_server.TOOL_DEFINITIONS,
            "handler": telegram_server.call_tool,
            "requires_env": ["TELEGRAM_BOT_TOKEN"],
        },
        {
            "server_id": "builtin-microsoft-teams",
            "name": "Microsoft Teams",
            "description": "Microsoft Teams via Graph API — teams, channels, messages",
            "tool_definitions": microsoft_teams_server.TOOL_DEFINITIONS,
            "handler": microsoft_teams_server.call_tool,
            "requires_env": ["TEAMS_ACCESS_TOKEN"],
        },
        {
            "server_id": "builtin-whatsapp",
            "name": "WhatsApp Business",
            "description": "WhatsApp Business Cloud API — send text, templates, and media messages",
            "tool_definitions": whatsapp_server.TOOL_DEFINITIONS,
            "handler": whatsapp_server.call_tool,
            "requires_env": ["WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN"],
        },
        {
            "server_id": "builtin-mattermost",
            "name": "Mattermost",
            "description": "Mattermost — teams, channels, posts, and search",
            "tool_definitions": mattermost_server.TOOL_DEFINITIONS,
            "handler": mattermost_server.call_tool,
            "requires_env": ["MATTERMOST_URL", "MATTERMOST_TOKEN"],
        },
        {
            "server_id": "builtin-intercom",
            "name": "Intercom",
            "description": "Intercom — conversations, users/contacts, notes, and tags",
            "tool_definitions": intercom_server.TOOL_DEFINITIONS,
            "handler": intercom_server.call_tool,
            "requires_env": ["INTERCOM_ACCESS_TOKEN"],
        },
        # ── Email & Marketing ────────────────────────────────────────────────
        {
            "server_id": "builtin-sendgrid",
            "name": "SendGrid",
            "description": "SendGrid — transactional email, templates, marketing contacts and lists",
            "tool_definitions": sendgrid_server.TOOL_DEFINITIONS,
            "handler": sendgrid_server.call_tool,
            "requires_env": ["SENDGRID_API_KEY"],
        },
        {
            "server_id": "builtin-mailchimp",
            "name": "Mailchimp",
            "description": "Mailchimp — audiences, subscribers, and email campaigns",
            "tool_definitions": mailchimp_server.TOOL_DEFINITIONS,
            "handler": mailchimp_server.call_tool,
            "requires_env": ["MAILCHIMP_API_KEY"],
        },
        {
            "server_id": "builtin-klaviyo",
            "name": "Klaviyo",
            "description": "Klaviyo — profiles, lists, events, and campaigns",
            "tool_definitions": klaviyo_server.TOOL_DEFINITIONS,
            "handler": klaviyo_server.call_tool,
            "requires_env": ["KLAVIYO_API_KEY"],
        },
        {
            "server_id": "builtin-brevo",
            "name": "Brevo",
            "description": "Brevo (Sendinblue) — transactional email, contacts, and campaigns",
            "tool_definitions": brevo_server.TOOL_DEFINITIONS,
            "handler": brevo_server.call_tool,
            "requires_env": ["BREVO_API_KEY"],
        },
        {
            "server_id": "builtin-mailerlite",
            "name": "MailerLite",
            "description": "MailerLite — subscribers, groups, and email campaigns",
            "tool_definitions": mailerlite_server.TOOL_DEFINITIONS,
            "handler": mailerlite_server.call_tool,
            "requires_env": ["MAILERLITE_API_KEY"],
        },
        {
            "server_id": "builtin-convertkit",
            "name": "ConvertKit",
            "description": "ConvertKit — subscribers, forms, sequences, and tags",
            "tool_definitions": convertkit_server.TOOL_DEFINITIONS,
            "handler": convertkit_server.call_tool,
            "requires_env": ["CONVERTKIT_API_KEY"],
        },
        {
            "server_id": "builtin-customerio",
            "name": "Customer.io",
            "description": "Customer.io — customer profiles, event tracking, and transactional email",
            "tool_definitions": customerio_server.TOOL_DEFINITIONS,
            "handler": customerio_server.call_tool,
            "requires_env": ["CUSTOMERIO_SITE_ID", "CUSTOMERIO_API_KEY"],
        },
        {
            "server_id": "builtin-twilio",
            "name": "Twilio",
            "description": "Twilio — SMS, WhatsApp messages, voice calls, and phone number lookup",
            "tool_definitions": twilio_server.TOOL_DEFINITIONS,
            "handler": twilio_server.call_tool,
            "requires_env": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
        },
        {
            "server_id": "builtin-mandrill",
            "name": "Mandrill",
            "description": "Mandrill (Mailchimp Transactional) — send, template, and search transactional email",
            "tool_definitions": mandrill_server.TOOL_DEFINITIONS,
            "handler": mandrill_server.call_tool,
            "requires_env": ["MANDRILL_API_KEY"],
        },
    ]


async def register_builtin_servers(registry: Any, tenant_ctx: Any) -> int:
    """Register all built-in MCP servers that have their required env vars set.

    Returns the number of servers successfully registered.
    """
    import logging
    import os

    from app.mcp.registry import MCPRegistry, MCPServerConfig

    count = 0
    for cfg in get_builtin_server_configs():
        # Only register if all required env vars are present and non-empty
        if all(os.getenv(env, "") for env in cfg.get("requires_env", [])):
            try:
                server_config = MCPServerConfig(
                    server_id=cfg["server_id"],
                    name=cfg["name"],
                    description=cfg.get("description", ""),
                    base_url="builtin://",
                    enabled=True,
                    tool_definitions=cfg["tool_definitions"],
                    builtin_handler=cfg["handler"],
                )
                await registry.register(server_config, tenant_ctx=tenant_ctx)
                # Also register handler in the process-local registry so it
                # survives Redis serialization round-trips (builtin_handler is
                # excluded from JSON).
                MCPRegistry.register_builtin_handler(cfg["server_id"], cfg["handler"])
                count += 1
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "builtin_server_register_failed: %s %s", cfg["name"], exc
                )
    return count
