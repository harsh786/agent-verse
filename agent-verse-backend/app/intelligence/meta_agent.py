"""Meta-agent — decomposes one NL command into a complete agent configuration.

This is the "one command, any domain" surface described in the platform spec.
The meta-agent's LLM call returns a structured AgentConfig:
  - name: agent name slug
  - goal_template: parameterized goal (e.g. "Onboard {name}")
  - connectors: list of MCP connector names to auto-provision
  - trigger_type + trigger details
  - autonomy_mode: supervised | bounded-autonomous | fully-autonomous
  - policy_suggestions: governance rules inferred from the command

The result is used by POST /agents/create to bootstrap a live agent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.providers.base import CompletionRequest, LLMProvider, Message
from app.tenancy.context import TenantContext

_META_AGENT_SYSTEM = (
    "You are an expert agent architect. Given a natural language command, "
    "design a complete autonomous agent configuration.\n\n"
    "Respond with JSON:\n"
    '{"name": "slug", "goal_template": "...", "connectors": [...], '
    '"trigger_type": "cron|interval|webhook|event|rest|once", '
    '"event_channel": "", "cron_expression": "", "interval_seconds": 0, '
    '"autonomy_mode": "supervised|bounded-autonomous|fully-autonomous", '
    '"policy_suggestions": [...]}\n\n'
    "No markdown, no explanation — only the JSON object."
)


@dataclass
class MetaAgentConfig:
    name: str
    goal_template: str
    connectors: list[str]
    trigger_type: str = "rest"
    event_channel: str = ""
    cron_expression: str = ""
    interval_seconds: int = 0
    autonomy_mode: str = "bounded-autonomous"
    policy_suggestions: list[str] = field(default_factory=list)


class MetaAgentPlanner:
    """Converts one NL command into a MetaAgentConfig via an LLM provider."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def plan(self, *, command: str, tenant_ctx: TenantContext) -> MetaAgentConfig:
        req = CompletionRequest(
            messages=[
                Message(role="system", content=_META_AGENT_SYSTEM),
                Message(role="user", content=command),
            ],
            model="claude-opus-4-8",
        )
        resp = await self._provider.complete(req)
        text = re.sub(r"```(?:json)?\n?", "", resp.content).strip()

        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return MetaAgentConfig(
                name="unnamed-agent",
                goal_template=command,
                connectors=[],
            )

        return MetaAgentConfig(
            name=str(obj.get("name", "unnamed-agent")),
            goal_template=str(obj.get("goal_template", command)),
            connectors=[str(c) for c in obj.get("connectors", [])],
            trigger_type=str(obj.get("trigger_type", "rest")),
            event_channel=str(obj.get("event_channel", "")),
            cron_expression=str(obj.get("cron_expression", "")),
            interval_seconds=int(str(obj.get("interval_seconds", 0))),
            autonomy_mode=str(obj.get("autonomy_mode", "bounded-autonomous")),
            policy_suggestions=[str(p) for p in obj.get("policy_suggestions", [])],
        )
