"""Agent-to-Agent (A2A) protocol types.

The AgentCard is served at /.well-known/agent.json and describes this agent's
capabilities to other agents that may want to delegate sub-tasks.
"""

from __future__ import annotations

from pydantic import BaseModel


class AgentCard(BaseModel):
    """Publicly discoverable capability declaration for this agent."""

    name: str
    version: str
    endpoint: str
    capabilities: list[str] = []
    description: str = ""
    tenant_id: str = ""


class A2ATask(BaseModel):
    """A task sent from one agent to another."""

    task_id: str
    sender_endpoint: str
    goal: str
    context: dict[str, object] = {}


class A2ATaskResult(BaseModel):
    """Result returned from an A2A task execution."""

    task_id: str
    status: str
    result: str = ""
    error: str = ""
