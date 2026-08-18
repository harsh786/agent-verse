"""Org-level connector registry — SUPPLEMENT E.

Registry for the 32 built-in enterprise connectors (plus org-registered custom ones).
Each connector exposes:
  - meta(): name, description, category, auth_type
  - connect(credentials): establish connection
  - health_check(): verify connectivity
  - execute(action, inputs): run a connector action
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.observability.logging import get_logger

_log = get_logger(__name__)


class ConnectorCategory(str, Enum):
    COMMUNICATION  = "communication"
    CRM            = "crm"
    DEVELOPMENT    = "development"
    KNOWLEDGE      = "knowledge"
    DATA           = "data"
    HR             = "hr"
    FINANCE        = "finance"
    CLOUD          = "cloud"
    MONITORING     = "monitoring"
    MARKETING      = "marketing"


@dataclass
class ConnectorMeta:
    name: str
    display_name: str
    category: ConnectorCategory
    auth_type: str               # oauth2 | api_key | basic | none
    description: str = ""
    always_audit: bool = False
    high_sensitivity: bool = False
    dept_restricted: list[str] = field(default_factory=list)


class BaseConnector(ABC):
    """Base class for all org-level connectors."""

    meta: ConnectorMeta

    @abstractmethod
    async def connect(self, credentials: dict[str, Any]) -> bool:
        """Establish and verify connection with provided credentials."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the connection is live."""

    @abstractmethod
    async def execute(self, action: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a connector action."""

    async def list_actions(self) -> list[str]:
        """Return supported actions."""
        return []


# ── Stub connector implementations ────────────────────────────────────────────

class LookerConnector(BaseConnector):
    """Looker Business Intelligence connector."""
    meta = ConnectorMeta(
        name="looker",
        display_name="Looker",
        category=ConnectorCategory.DATA,
        auth_type="api_key",
        description="Looker BI — run looks, dashboards, and data exports",
    )

    async def connect(self, credentials: dict) -> bool:
        return bool(credentials.get("looker_api_key"))

    async def health_check(self) -> bool:
        return True

    async def execute(self, action: str, inputs: dict) -> dict:
        return {"action": action, "status": "not_configured", "result": None}

    async def list_actions(self) -> list[str]:
        return ["run_look", "run_dashboard", "query_model", "list_explores"]


class ExpensifyConnector(BaseConnector):
    """Expensify expense management connector."""
    meta = ConnectorMeta(
        name="expensify",
        display_name="Expensify",
        category=ConnectorCategory.FINANCE,
        auth_type="api_key",
        description="Expensify — expense reports, receipts, reimbursements",
        high_sensitivity=True,
        dept_restricted=["finance"],
    )

    async def connect(self, credentials: dict) -> bool:
        return bool(credentials.get("expensify_partner_user_id"))

    async def health_check(self) -> bool:
        return True

    async def execute(self, action: str, inputs: dict) -> dict:
        return {"action": action, "status": "not_configured", "result": None}

    async def list_actions(self) -> list[str]:
        return ["create_expense", "get_reports", "approve_report", "export_to_csv"]


class GrafanaConnector(BaseConnector):
    """Grafana monitoring and observability connector."""
    meta = ConnectorMeta(
        name="grafana",
        display_name="Grafana",
        category=ConnectorCategory.MONITORING,
        auth_type="api_key",
        description="Grafana — dashboards, alerts, metrics, annotations",
        always_audit=True,
    )

    async def connect(self, credentials: dict) -> bool:
        return bool(credentials.get("grafana_api_key") and credentials.get("grafana_url"))

    async def health_check(self) -> bool:
        return True

    async def execute(self, action: str, inputs: dict) -> dict:
        return {"action": action, "status": "not_configured", "result": None}

    async def list_actions(self) -> list[str]:
        return [
            "get_dashboard", "list_dashboards", "query_datasource",
            "create_annotation", "fire_alert", "get_alert_rules",
        ]


# ── Connector registry ────────────────────────────────────────────────────────

class ConnectorRegistry:
    """
    Central registry for all org-level connectors.
    Connectors are registered at startup; custom connectors can be added via API.
    """

    def __init__(self) -> None:
        self._connectors: dict[str, BaseConnector] = {}

    def register(self, connector: BaseConnector) -> None:
        self._connectors[connector.meta.name] = connector
        _log.info("connector.registered", name=connector.meta.name)

    def get(self, name: str) -> BaseConnector | None:
        return self._connectors.get(name)

    def list_all(self) -> list[ConnectorMeta]:
        return [c.meta for c in self._connectors.values()]

    def list_by_category(self, category: ConnectorCategory) -> list[ConnectorMeta]:
        return [c.meta for c in self._connectors.values() if c.meta.category == category]

    async def health_check_all(self) -> dict[str, bool]:
        results = {}
        for name, conn in self._connectors.items():
            try:
                results[name] = await conn.health_check()
            except Exception:
                results[name] = False
        return results


# ── Global instance with all 3 missing connectors registered ──────────────────

org_connector_registry = ConnectorRegistry()
org_connector_registry.register(LookerConnector())
org_connector_registry.register(ExpensifyConnector())
org_connector_registry.register(GrafanaConnector())
