"""
PART 17 — Org-level Tool Registrations.

New tools available to agents operating in an org context:
  org_memory_read       — read org/dept memory
  org_memory_write      — write to org/dept memory (audited)
  cross_dept_message    — send a message to another department
  task_delegate         — delegate a task to another agent/dept
  approval_request      — request human approval for an action
  org_knowledge_search  — search org knowledge base
  budget_check          — check remaining mission/dept budget
  compliance_check      — validate an action against org policy
  performance_record    — record agent performance
  artifact_store        — store and version an artifact

Each tool has: risk_level, requires_approval, audit flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


@dataclass
class OrgToolSpec:
    """Specification for an org-level tool."""

    name: str
    description: str
    risk: str  # low | medium | high | critical
    approval: bool | str  # False | True | "always" | "L3+"
    audit: bool = False
    rate_limit_per_hour: int = 100
    requires_scope: str = "orgs:read"


# ── New org-level tool definitions (PART 17) ──────────────────────────────────

ORG_TOOL_DEFINITIONS: dict[str, OrgToolSpec] = {
    "org_memory_read": OrgToolSpec(
        name="org_memory_read",
        description="Read from org or department memory. Returns relevant memory items.",
        risk="low",
        approval=False,
        audit=False,
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    "org_memory_write": OrgToolSpec(
        name="org_memory_write",
        description="Write a new lesson or fact to org/dept memory tier.",
        risk="medium",
        approval=False,
        audit=True,
        rate_limit_per_hour=50,
        requires_scope="missions:write",
    ),
    "cross_dept_message": OrgToolSpec(
        name="cross_dept_message",
        description="Send a message or work artifact to another department.",
        risk="low",
        approval=False,
        audit=True,
        rate_limit_per_hour=30,
        requires_scope="missions:write",
    ),
    "task_delegate": OrgToolSpec(
        name="task_delegate",
        description="Delegate a sub-task to another agent or department.",
        risk="medium",
        approval=False,
        rate_limit_per_hour=20,
        requires_scope="missions:write",
    ),
    "approval_request": OrgToolSpec(
        name="approval_request",
        description="Request human approval before executing a high-risk action.",
        risk="high",
        approval="always",
        audit=True,
        rate_limit_per_hour=10,
        requires_scope="approve",
    ),
    "org_knowledge_search": OrgToolSpec(
        name="org_knowledge_search",
        description="Search the org's knowledge base for relevant information.",
        risk="low",
        approval=False,
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    "budget_check": OrgToolSpec(
        name="budget_check",
        description="Check remaining budget for this mission or department.",
        risk="low",
        approval=False,
        rate_limit_per_hour=100,
        requires_scope="orgs:read",
    ),
    "compliance_check": OrgToolSpec(
        name="compliance_check",
        description="Validate a proposed action against org policies and GDPR/SOC2.",
        risk="low",
        approval=False,
        audit=True,
        rate_limit_per_hour=100,
        requires_scope="orgs:read",
    ),
    "performance_record": OrgToolSpec(
        name="performance_record",
        description="Record agent task performance score for reputation tracking.",
        risk="low",
        approval=False,
        audit=True,
        rate_limit_per_hour=50,
        requires_scope="missions:write",
    ),
    "artifact_store": OrgToolSpec(
        name="artifact_store",
        description="Store and version a mission artifact (document, code, report).",
        risk="medium",
        approval=False,
        audit=True,
        rate_limit_per_hour=30,
        requires_scope="missions:write",
    ),
}


def get_tool(name: str) -> OrgToolSpec | None:
    return ORG_TOOL_DEFINITIONS.get(name)


def list_tools_for_risk(max_risk: str = "medium") -> list[str]:
    """Return tool names with risk level <= max_risk."""
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    threshold = order.get(max_risk, 1)
    return [
        name for name, spec in ORG_TOOL_DEFINITIONS.items() if order.get(spec.risk, 99) <= threshold
    ]


def tool_allowed_for_autonomy(tool_name: str, autonomy_level: int) -> bool:
    """
    Check if a tool is allowed at a given autonomy level (L0-L5).
    L2+: low-risk tools
    L3+: medium-risk tools
    L4+: high-risk tools (with approval)
    Never: critical without human
    """
    spec = ORG_TOOL_DEFINITIONS.get(tool_name)
    if not spec:
        return False
    risk_to_min_level = {"low": 2, "medium": 3, "high": 4, "critical": 99}
    return autonomy_level >= risk_to_min_level.get(spec.risk, 99)


# ── MCP tool schema generator ─────────────────────────────────────────────────


def to_mcp_tool_schema(spec: OrgToolSpec) -> dict[str, Any]:
    """Convert OrgToolSpec to MCP tool definition format."""
    return {
        "name": spec.name,
        "description": spec.description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string", "description": "Organization ID"},
                "query": {"type": "string", "description": "Query or content"},
                "dept_id": {"type": "string", "description": "Department ID (optional)"},
                "mission_id": {"type": "string", "description": "Mission ID (optional)"},
            },
            "required": ["org_id"],
        },
    }
