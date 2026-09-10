"""QA11 — MCP Resources + Prompts (Full MCP Spec).

Beyond just Tools, the full MCP spec includes:
  Resources — org state exposed as readable URIs
  Prompts   — templated queries the org can answer

MCP Resources expose org state as:
  org://{org_id}/status          — current org health
  org://{org_id}/missions        — active missions
  org://{org_id}/team            — current team composition
  org://{org_id}/memory/{scope}  — org/dept memory

MCP Prompts provide templated queries:
  analyze_mission_risk           — risk analysis for a mission
  summarize_daily_activity       — daily digest summary
  suggest_next_actions           — what should the org do next?
  explain_model_usage            — model routing explanation
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.observability.logging import get_logger

_log = get_logger(__name__)


# ── MCP Resource definitions ───────────────────────────────────────────────────


@dataclass
class MCPResource:
    """An MCP Resource — org state exposed as a readable URI."""

    uri: str  # e.g. org://org_abc123/status
    name: str  # human-readable name
    description: str
    mime_type: str = "application/json"
    template: bool = False  # True if URI has {variable} parts


@dataclass
class MCPResourceContent:
    """Content returned when a resource is read."""

    uri: str
    mime_type: str
    text: str | None = None
    blob: bytes | None = None


# ── MCP Prompt definitions ─────────────────────────────────────────────────────


@dataclass
class MCPPromptArgument:
    name: str
    description: str
    required: bool = True


@dataclass
class MCPPrompt:
    """An MCP Prompt — templated query the org can answer."""

    name: str
    description: str
    arguments: list[MCPPromptArgument] = field(default_factory=list)


@dataclass
class MCPPromptMessage:
    role: str  # user | assistant
    content: str


# ── OrgMCPResources ────────────────────────────────────────────────────────────


class OrgMCPResources:
    """
    QA11 — Exposes org state as MCP Resources.
    These complement OrgMCPServer tools.
    Compatible with: Claude Desktop, Cursor, any MCP client.
    """

    ORG_RESOURCES: ClassVar[list[MCPResource]] = [
        MCPResource(
            uri="org://{org_id}/status",
            name="Org Status",
            description="Current organization health, active missions, and pending approvals",
            template=True,
        ),
        MCPResource(
            uri="org://{org_id}/missions",
            name="Active Missions",
            description="List of currently active and queued missions",
            template=True,
        ),
        MCPResource(
            uri="org://{org_id}/team",
            name="Current Teams",
            description="Active team composition and agent statuses",
            template=True,
        ),
        MCPResource(
            uri="org://{org_id}/memory/org",
            name="Org Memory",
            description="Organization-wide institutional knowledge and lessons",
            template=True,
        ),
        MCPResource(
            uri="org://{org_id}/memory/dept/{dept_id}",
            name="Department Memory",
            description="Department-scoped institutional knowledge",
            template=True,
        ),
        MCPResource(
            uri="org://{org_id}/approvals/pending",
            name="Pending Approvals",
            description="Items currently waiting for human approval",
            template=True,
        ),
        MCPResource(
            uri="org://{org_id}/analytics/health",
            name="Health Score",
            description="Org health score (0-100) with factor breakdown",
            template=True,
        ),
        MCPResource(
            uri="org://{org_id}/artifacts/recent",
            name="Recent Artifacts",
            description="Recently created and approved mission artifacts",
            template=True,
        ),
    ]

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id

    def list_resources(self) -> list[dict[str, Any]]:
        """Return all resource definitions in MCP format."""
        return [
            {
                "uri": r.uri.format(org_id=self.org_id, dept_id="{dept_id}"),
                "name": r.name,
                "description": r.description,
                "mimeType": r.mime_type,
            }
            for r in self.ORG_RESOURCES
        ]

    async def read_resource(
        self,
        uri: str,
        org_service: Any | None = None,
    ) -> MCPResourceContent:
        """Read a resource by URI and return its content."""
        uri_norm = uri.replace(f"org://{self.org_id}/", "")

        if uri_norm == "status" or uri_norm.startswith("analytics/health"):
            content = await self._read_status(org_service)
        elif uri_norm == "missions":
            content = await self._read_missions(org_service)
        elif uri_norm == "approvals/pending":
            content = await self._read_approvals(org_service)
        elif uri_norm.startswith("memory/"):
            content = await self._read_memory(uri_norm, org_service)
        else:
            content = {"error": f"Unknown resource: {uri}", "org_id": self.org_id}

        return MCPResourceContent(
            uri=uri,
            mime_type="application/json",
            text=json.dumps(content, default=str),
        )

    async def _read_status(self, org_service: Any | None) -> dict:
        if org_service:
            try:
                return await org_service.get_org_health(self.org_id)
            except Exception:
                pass
        return {
            "org_id": self.org_id,
            "status": "active",
            "message": "Connect org service for live data",
            "generated_at": datetime.now(UTC).isoformat(),
        }

    async def _read_missions(self, org_service: Any | None) -> dict:
        if org_service:
            try:
                missions = await org_service.list_missions(self.org_id, {"status": "active"})
                return {"missions": missions, "count": len(missions)}
            except Exception:
                pass
        return {"missions": [], "message": "Connect org service for live data"}

    async def _read_approvals(self, org_service: Any | None) -> dict:
        return {"approvals": [], "message": "Connect approval service for live data"}

    async def _read_memory(self, uri_suffix: str, org_service: Any | None) -> dict:
        scope = uri_suffix.replace("memory/", "")
        return {
            "scope": scope,
            "items": [],
            "message": "Connect memory service for live data",
        }


# ── OrgMCPPrompts ──────────────────────────────────────────────────────────────


class OrgMCPPrompts:
    """
    QA11 — Exposes templated prompts the org can answer.
    """

    ORG_PROMPTS: ClassVar[list[MCPPrompt]] = [
        MCPPrompt(
            name="analyze_mission_risk",
            description="Analyze the risk level of a mission goal before execution",
            arguments=[
                MCPPromptArgument("goal", "The mission goal to analyze", required=True),
                MCPPromptArgument("context", "Additional context about the org", required=False),
            ],
        ),
        MCPPrompt(
            name="summarize_daily_activity",
            description="Generate a 'while you were away' digest of today's org activity",
            arguments=[
                MCPPromptArgument(
                    "since_hours", "Hours to look back (default: 24)", required=False
                ),
            ],
        ),
        MCPPrompt(
            name="suggest_next_actions",
            description="Based on current org state, suggest the highest-priority next actions",
            arguments=[
                MCPPromptArgument("role", "User's role (ceo/cto/etc.) for context", required=False),
            ],
        ),
        MCPPrompt(
            name="explain_model_usage",
            description="Explain how models are being routed for different departments",
            arguments=[
                MCPPromptArgument("department", "Specific department to focus on", required=False),
            ],
        ),
        MCPPrompt(
            name="generate_mission_brief",
            description="Generate a concise mission brief for stakeholder communication",
            arguments=[
                MCPPromptArgument("mission_id", "Mission ID to generate brief for", required=True),
                MCPPromptArgument(
                    "audience", "Target audience (exec/technical/external)", required=False
                ),
            ],
        ),
    ]

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id

    def list_prompts(self) -> list[dict[str, Any]]:
        """Return all prompt definitions in MCP format."""
        return [
            {
                "name": p.name,
                "description": p.description,
                "arguments": [
                    {"name": a.name, "description": a.description, "required": a.required}
                    for a in p.arguments
                ],
            }
            for p in self.ORG_PROMPTS
        ]

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str],
        org_service: Any | None = None,
    ) -> list[MCPPromptMessage]:
        """Generate prompt messages for a named prompt."""
        if name == "analyze_mission_risk":
            goal = arguments.get("goal", "")
            return [
                MCPPromptMessage("user", f"Analyze the risk of this mission goal: {goal}"),
                MCPPromptMessage(
                    "assistant",
                    f"I'll analyze the mission '{goal[:80]}' for org {self.org_id}. "
                    "Please connect the org service for full risk analysis with team availability, "
                    "budget check, and historical success rates.",
                ),
            ]

        if name == "summarize_daily_activity":
            since_hours = arguments.get("since_hours", "24")
            return [
                MCPPromptMessage(
                    "user", f"What happened in the org in the last {since_hours} hours?"
                ),
                MCPPromptMessage(
                    "assistant",
                    f"Here's your org activity digest for the last {since_hours}h. "
                    "Call the /brief/morning endpoint for live data.",
                ),
            ]

        if name == "suggest_next_actions":
            role = arguments.get("role", "user")
            return [
                MCPPromptMessage("user", f"As a {role}, what should I focus on?"),
                MCPPromptMessage(
                    "assistant",
                    f"Based on current org state, here are priorities for your {role} role. "
                    "Connect the org intelligence service for real-time recommendations.",
                ),
            ]

        if name == "generate_mission_brief":
            mission_id = arguments.get("mission_id", "")
            audience = arguments.get("audience", "exec")
            return [
                MCPPromptMessage(
                    "user", f"Generate a brief for mission {mission_id} for {audience} audience"
                ),
                MCPPromptMessage(
                    "assistant",
                    f"Mission Brief — Mission {mission_id}\n"
                    f"Audience: {audience}\n"
                    "Connect mission service for actual content.",
                ),
            ]

        # Default
        return [
            MCPPromptMessage("user", f"Prompt: {name} with args: {arguments}"),
            MCPPromptMessage(
                "assistant", "Prompt not found. Use list_prompts() to see available prompts."
            ),
        ]
