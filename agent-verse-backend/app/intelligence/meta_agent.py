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
    '{"name": "Short Title Case name, e.g. Invoice Reconciler", '
    '"goal_template": "...", "connectors": [...], '
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


# Filler words dropped when deriving a name from a free-text command, so the
# derived name is the meaningful part ("Research Agent", not "You Are Research").
_NAME_FILLER = frozenset(
    {
        "a", "an", "the", "you", "are", "is", "be", "your", "my", "me", "i",
        "we", "please", "kindly", "want", "need", "would", "like", "to", "that",
        "this", "it", "will", "shall", "should", "must", "can", "create", "build",
        "make", "design", "setup", "set", "up", "and", "of", "for", "with",
        "task", "tasks", "who", "which", "whose", "do", "does",
    }
)
# Names the LLM sometimes echoes from the schema/example — treat as "no name".
_PLACEHOLDER_NAMES = frozenset(
    {"unnamed-agent", "unnamed", "slug", "agent", "name", "new agent", ""}
)


def _titlecase(text: str) -> str:
    """Title-case words while preserving short acronyms (AI, ML, API, CRM…)."""
    out: list[str] = []
    for word in text.split():
        if word.isupper() and len(word) <= 4:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def _derive_agent_name(command: str) -> str:
    """Derive a readable Title-Case agent name from a free-text command.

    Used whenever the LLM can't supply one (provider error, non-JSON output, or
    an omitted/placeholder name) so an agent is never persisted as the generic
    "unnamed-agent". Prefers an explicit "<role> agent" mention, else the first
    few meaningful words, always ending in "Agent".
    """
    text = re.sub(r"\s+", " ", (command or "")).strip()
    if not text:
        return "New Agent"

    # 1. Explicit "<role> agent" mention → "Role Agent" (keep the role's tail words).
    match = re.search(r"([A-Za-z][A-Za-z0-9 /+-]{1,40}?)\s+agents?\b", text, re.IGNORECASE)
    if match:
        role_words = [w for w in match.group(1).split() if w.lower() not in _NAME_FILLER]
        role = " ".join(role_words[-3:])
        if role.strip():
            return _cap_name(_titlecase(role) + " Agent")

    # 2. First few meaningful words → "Word Word Word Agent".
    words = [w for w in re.split(r"[^A-Za-z0-9+]+", text) if w and w.lower() not in _NAME_FILLER]
    if not words:
        return "New Agent"
    name = _titlecase(" ".join(words[:3]))
    if "agent" not in name.lower() and "bot" not in name.lower():
        name = f"{name} Agent"
    return _cap_name(name)


def _cap_name(name: str) -> str:
    name = name.strip()
    return name[:48].rstrip() if len(name) > 48 else name


def _clean_llm_name(raw: Any, command: str) -> str:
    """Accept the LLM's name only if it's real; otherwise derive one."""
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    if name.lower() in _PLACEHOLDER_NAMES:
        return _derive_agent_name(command)
    return _cap_name(name)


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
                name=_derive_agent_name(command),
                goal_template=command,
                connectors=[],
            )
        text = re.sub(r"```(?:json)?\n?", "", resp.content).strip()

        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return MetaAgentConfig(
                name=_derive_agent_name(command),
                goal_template=command,
                connectors=[],
            )

        return MetaAgentConfig(
            name=_clean_llm_name(obj.get("name"), command),
            goal_template=str(obj.get("goal_template", command)),
            connectors=_normalize_connectors(obj.get("connectors", [])),
            trigger_type=str(obj.get("trigger_type", "rest")),
            event_channel=str(obj.get("event_channel", "")),
            cron_expression=str(obj.get("cron_expression", "")),
            interval_seconds=int(str(obj.get("interval_seconds", 0))),
            autonomy_mode=str(obj.get("autonomy_mode", "bounded-autonomous")),
            policy_suggestions=[str(p) for p in obj.get("policy_suggestions", [])],
        )
