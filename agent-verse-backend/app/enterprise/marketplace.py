"""Marketplace — agent template gallery for browse/deploy/publish.

V1 (this module): in-memory template gallery used by existing API endpoints.
V2: see app.enterprise.marketplace_v2 for DB-backed service with atomic install,
    security review pipeline, and full-text search.

The existing API (browse/deploy/publish) is preserved unchanged so no existing
tests break. New marketplace v2 endpoints in enterprise.py use marketplace_v2.
"""

from __future__ import annotations

import uuid
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.enterprise.marketplace_v2 import bundle_report as _bundle_report
from app.enterprise.marketplace_v2 import install_autonomy
from app.tenancy.context import TenantContext

warnings.warn(
    "app.enterprise.marketplace is deprecated. Use app.enterprise.marketplace_v2 instead. "
    "This module will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

# Built-in template gallery (pre-built templates matching the 6 reference domains)
_BUILTIN_TEMPLATES = [
    {
        "template_id": "tpl-bug-fix",
        "name": "Bug Fix Agent",
        "domain": "software",
        "description": "Fix JIRA bugs labeled prod-down and open a PR",
        "connectors": ["github", "jira", "sentry"],
        "required_connectors": ["github", "jira", "sentry"],
        "trigger_type": "webhook",
        "autonomy_mode": "bounded-autonomous",
        "author": "AgentVerse",
        "goal_template": "Fix all open bugs labeled {label} in {repo} and open PRs",
        "version": "1.0.0",
    },
    {
        "template_id": "tpl-devops",
        "name": "DevOps Watchdog",
        "domain": "devops",
        "description": "Roll back last deploy if error rate > 2% for 5 min",
        "connectors": ["datadog", "github"],
        "required_connectors": ["datadog", "github"],
        "trigger_type": "event",
        "autonomy_mode": "supervised",
        "author": "AgentVerse",
        "goal_template": "Monitor {service} and roll back if error rate exceeds {threshold}%",
        "version": "1.0.0",
    },
    {
        "template_id": "tpl-e2e-testing",
        "name": "E2E Test Generator",
        "domain": "testing",
        "description": "Generate and run E2E tests for the checkout flow nightly",
        "connectors": ["github"],
        "required_connectors": ["github"],
        "trigger_type": "cron",
        "autonomy_mode": "fully-autonomous",
        "author": "AgentVerse",
        "goal_template": ("Generate and run E2E tests for {feature} and commit results to {repo}"),
        "version": "1.0.0",
    },
    {
        "template_id": "tpl-hr-onboarding",
        "name": "HR Onboarding Agent",
        "domain": "hr",
        "description": "Onboard new engineers end-to-end",
        "connectors": ["slack", "jira"],
        "required_connectors": ["slack", "jira"],
        "trigger_type": "event",
        "autonomy_mode": "supervised",
        "author": "AgentVerse",
        "goal_template": (
            "Onboard {employee_name}: create accounts, file IT tickets, assign first-week tasks"
        ),
        "version": "1.0.0",
    },
    {
        "template_id": "tpl-sales-followup",
        "name": "Sales Follow-up Agent",
        "domain": "sales",
        "description": "Follow up with leads idle 7+ days",
        "connectors": ["salesforce"],
        "required_connectors": ["salesforce"],
        "trigger_type": "interval",
        "autonomy_mode": "bounded-autonomous",
        "author": "AgentVerse",
        "goal_template": ("Follow up with all leads idle more than {idle_days} days in {pipeline}"),
        "version": "1.0.0",
    },
    {
        "template_id": "tpl-support-triage",
        "name": "Support Triage Agent",
        "domain": "support",
        "description": "Triage new tickets, draft replies, escalate P1s",
        "connectors": ["slack", "jira"],
        "required_connectors": ["slack", "jira"],
        "trigger_type": "webhook",
        "autonomy_mode": "bounded-autonomous",
        "author": "AgentVerse",
        "goal_template": (
            "Triage new support tickets in {queue}: draft replies and "
            "escalate P1s to {escalation_channel}"
        ),
        "version": "1.0.0",
    },
    {
        "template_id": "tpl-code-review",
        "name": "Code Review Agent",
        "domain": "software",
        "description": "Automatically review open PRs, post inline comments, and request changes",
        "connectors": ["github", "jira"],
        "required_connectors": ["github", "jira"],
        "trigger_type": "webhook",
        "autonomy_mode": "bounded-autonomous",
        "author": "AgentVerse",
        "goal_template": (
            "Review all open PRs in {repo} for code quality, security issues, and style "
            "compliance; post comments and request changes where needed"
        ),
        "version": "1.0.0",
    },
    {
        "template_id": "tpl-incident-response",
        "name": "Incident Response Agent",
        "domain": "devops",
        "description": "Detect production incidents, page on-call, open Jira tickets, and post status updates",  # noqa: E501
        "connectors": ["datadog", "slack", "jira"],
        "required_connectors": ["datadog", "slack", "jira"],
        "trigger_type": "event",
        "autonomy_mode": "supervised",
        "author": "AgentVerse",
        "goal_template": (
            "When {service} error rate exceeds {threshold}%, page on-call via {channel}, "
            "open a P1 Jira incident, and post hourly status updates until resolved"
        ),
        "version": "1.0.0",
    },
]


@dataclass
class DeployedTemplate:
    deployment_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    template_id: str = ""
    agent_id: str = ""
    tenant_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    template_version: str = "1.0.0"


class Marketplace:
    """Template gallery + deploy functionality."""

    def __init__(self, agent_store: Any = None) -> None:
        self._custom_templates: dict[str, dict[str, Any]] = {}
        self._community_templates: dict[str, Any] = {}
        self._deployments: dict[str, DeployedTemplate] = {}
        self._agent_store: Any = agent_store

    def browse(
        self, *, query: str = "", domain: str = "", tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        templates = (
            list(_BUILTIN_TEMPLATES)
            + list(self._custom_templates.values())
            + list(self._community_templates.values())
        )
        if domain:
            templates = [t for t in templates if t.get("domain") == domain]
        if query:
            q = query.lower()
            templates = [
                t
                for t in templates
                if q in t.get("name", "").lower() or q in t.get("description", "").lower()
            ]
        return templates

    def get_template(self, *, template_id: str) -> dict[str, Any] | None:
        for t in _BUILTIN_TEMPLATES:
            if t["template_id"] == template_id:
                return t
        return self._custom_templates.get(template_id)

    async def deploy(
        self,
        *,
        template_id: str,
        params: dict[str, Any],
        tenant_ctx: TenantContext,
        registry: Any = None,
    ) -> Any:
        """Deploy a template as a live agent for the tenant.

        If *registry* is provided, validates that all required connectors are
        registered for the tenant before proceeding. Returns a failure dict
        (not DeployedTemplate) when required connectors are missing.
        """
        template = self.get_template(template_id=template_id)
        if template is None:
            raise ValueError(f"Template {template_id} not found")

        # Validate required connectors when a registry is available
        missing_connectors: list[str] = []
        if registry is not None:
            required = template.get("required_connectors", [])
            for required_connector in required:
                try:
                    servers = await registry.list_servers(tenant_ctx=tenant_ctx)
                    server_names = {getattr(s, "name", "").lower() for s in servers}
                    if required_connector.lower() not in server_names:
                        missing_connectors.append(required_connector)
                except Exception:
                    pass

        if missing_connectors:
            return {
                "status": "failed",
                "reason": f"Required connectors not registered: {missing_connectors}",
                "required_connectors": missing_connectors,
                "next_step": (f"Register these connectors first: {', '.join(missing_connectors)}"),
            }

        # No fallback id: this used to substitute ``uuid4().hex`` when there was
        # no agent store or ``create`` failed, reporting an agent that was never
        # created. A deploy either creates the agent or raises.
        if self._agent_store is None:
            raise RuntimeError("Cannot deploy: no agent store is configured")
        agent_config = {
            "name": params.get("name", template["name"]),
            "goal_template": template.get(
                "goal_template",
                f"Execute tasks: {template['description'][:200]}",
            ),
            # MEM-22: an install never yields a fully-autonomous agent (the
            # rollout gate is applied on PUT /agents/{id}).
            "autonomy_mode": install_autonomy(template.get("autonomy_mode"))[0],
            "connector_ids": [],
            "description": template.get("description", ""),
        }
        agent_id = await self._agent_store.create(agent_config, tenant_ctx=tenant_ctx)
        if not agent_id:
            raise RuntimeError(f"Agent store did not create an agent for {template_id}")

        deployment = DeployedTemplate(
            template_id=template_id,
            agent_id=agent_id,
            tenant_id=tenant_ctx.tenant_id,
            params=params,
        )
        self._deployments[deployment.deployment_id] = deployment
        return deployment

    def publish(self, *, template: dict[str, Any], tenant_ctx: TenantContext) -> dict[str, Any]:
        """Publish a custom agent template to the marketplace."""
        import uuid as _uuid
        from datetime import UTC
        from datetime import datetime as _dt

        template_id = f"tpl-custom-{_uuid.uuid4().hex[:8]}"
        visibility = template.get("visibility", "private")
        record: dict[str, Any] = {
            "template_id": template_id,
            "name": template.get("name", "Untitled"),
            "domain": template.get("domain", "custom"),
            "description": template.get("description", ""),
            "connectors": template.get("connectors", []),
            "autonomy_mode": template.get("autonomy_mode", "bounded-autonomous"),
            "author": tenant_ctx.tenant_id,
            "published_at": _dt.now(UTC).isoformat(),
            "published_by": tenant_ctx.tenant_id,
            "is_community": visibility == "community",
            "visibility": visibility,  # private | team | community
        }
        self._community_templates[template_id] = record
        return record

    async def create_bundle(
        self, *, name: str, template_ids: list[str], tenant_ctx: Any
    ) -> dict[str, Any]:
        """Deploy multiple templates as a group (a 'bundle').

        Every item reports its own ``status`` (``deployed`` with the real agent
        id, or ``failed`` with the error); the bundle is ``complete``,
        ``partial`` or ``failed``. A failed item never carries an agent id.
        """
        items: list[dict[str, Any]] = []
        for template_id in template_ids:
            try:
                result = await self.deploy(
                    template_id=template_id, params={}, tenant_ctx=tenant_ctx
                )
            except Exception as exc:
                items.append({"template_id": template_id, "status": "failed", "error": str(exc)})
                continue
            if isinstance(result, dict):
                items.append(
                    {
                        "template_id": template_id,
                        "status": "failed",
                        "error": result.get("reason", "deploy failed"),
                    }
                )
            else:
                items.append(
                    {
                        "template_id": template_id,
                        "status": "deployed",
                        "deployment_id": result.deployment_id,
                        "agent_id": result.agent_id,
                    }
                )
        return _bundle_report(name, items)

    async def publish_version(
        self,
        *,
        template_id: str,
        version: str,
        changelog: str,
        db: Any = None,
        template: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save a version snapshot of a template to DB (``template``: the resolved record)."""
        if template is None:
            template = self.get_template(template_id=template_id) or {}
        if db is not None:
            try:
                import json
                import uuid

                from sqlalchemy import text

                async with db() as session, session.begin():
                    await session.execute(
                        text("""
                            INSERT INTO template_versions
                                (id, template_id, version, changelog, template_data, published_at)
                            VALUES (:id, :tid, :ver, :log, CAST(:data AS jsonb), NOW())
                        """),
                        {
                            "id": uuid.uuid4().hex,
                            "tid": template_id,
                            "ver": version,
                            "log": changelog,
                            "data": json.dumps(dict(template), default=str),
                        },
                    )
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("version_publish_failed: %s", exc)

        return {"template_id": template_id, "version": version, "changelog": changelog}

    async def get_version_history(
        self, *, template_id: str, db: Any = None
    ) -> list[dict[str, Any]]:
        """Get version history for a template."""
        if db is None:
            return []
        try:
            from sqlalchemy import text

            async with db() as session:
                rows = (
                    await session.execute(
                        text("""
                            SELECT version, changelog, published_by, published_at
                            FROM template_versions
                            WHERE template_id = :tid
                            ORDER BY published_at DESC LIMIT 20
                        """),
                        {"tid": template_id},
                    )
                ).fetchall()
            return [
                {
                    "version": r[0],
                    "changelog": r[1],
                    "published_by": r[2],
                    "published_at": r[3].isoformat() if r[3] else "",
                }
                for r in rows
            ]
        except Exception:
            return []

