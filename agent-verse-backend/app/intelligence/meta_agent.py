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

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

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


def _connector_id_from_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("server_id", "id", "connector_id", "type", "name"):
            raw = value.get(key)
            if raw is not None and str(raw).strip():
                return str(raw).strip()
        return ""
    return str(value).strip()


def _normalize_connectors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    connectors: list[str] = []
    seen: set[str] = set()
    for item in value:
        connector_id = _connector_id_from_value(item)
        if not connector_id or connector_id in seen:
            continue
        connectors.append(connector_id)
        seen.add(connector_id)
    return connectors


class MetaAgentPlanner:
    """Converts one NL command into a MetaAgentConfig via an LLM provider."""

    def __init__(self, provider: LLMProvider, *, timeout_seconds: float = 15.0) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def plan(self, *, command: str, tenant_ctx: TenantContext) -> MetaAgentConfig:
        req = CompletionRequest(
            messages=[
                Message(role="system", content=_META_AGENT_SYSTEM),
                Message(role="user", content=command),
            ],
            model="",
        )
        try:
            resp = await asyncio.wait_for(
                self._provider.complete(req),
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("meta_agent_provider_failed: %s", exc)
            return MetaAgentConfig(
                name="unnamed-agent",
                goal_template=command,
                connectors=[],
            )
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
            connectors=_normalize_connectors(obj.get("connectors", [])),
            trigger_type=str(obj.get("trigger_type", "rest")),
            event_channel=str(obj.get("event_channel", "")),
            cron_expression=str(obj.get("cron_expression", "")),
            interval_seconds=int(str(obj.get("interval_seconds", 0))),
            autonomy_mode=str(obj.get("autonomy_mode", "bounded-autonomous")),
            policy_suggestions=[str(p) for p in obj.get("policy_suggestions", [])],
        )
