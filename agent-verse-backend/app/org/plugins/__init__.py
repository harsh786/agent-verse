"""Plugin system — SUPPLEMENT D of spec.

Defines the 6 plugin types and registration mechanism:
  ModelPlugin    — add a new LLM provider
  ToolPlugin     — add a new tool/action
  MemoryPlugin   — add a new memory backend
  KnowledgePlugin — add a new knowledge source connector
  EvaluatorPlugin — add a custom evaluator
  PolicyPlugin   — add a custom governance policy

Usage:
  from app.org.plugins.registry import plugin_registry
  plugin_registry.register(MyToolPlugin())
"""

from __future__ import annotations

from enum import Enum, StrEnum
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


class PluginType(StrEnum):
    MODEL = "model"
    TOOL = "tool"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    EVALUATOR = "evaluator"
    POLICY = "policy"


class AgentVersePlugin:
    """Base class for all AgentVerse plugins."""

    plugin_type: PluginType
    name: str
    version: str = "1.0.0"
    description: str = ""
    permissions: list[str] = []
    audit: bool = True
    sandbox: str = "restricted"  # restricted | isolated | trusted

    def health_check(self) -> bool:
        return True

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "type": self.plugin_type.value,
            "description": self.description,
            "permissions": self.permissions,
            "audit": self.audit,
            "sandbox": self.sandbox,
            "healthy": self.health_check(),
        }


class ModelPlugin(AgentVersePlugin):
    """Add a new LLM provider."""

    plugin_type = PluginType.MODEL

    async def complete(self, messages: list[dict], config: dict) -> str:
        raise NotImplementedError

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def cost_estimate(self, tokens_in: int, tokens_out: int) -> float:
        raise NotImplementedError


class ToolPlugin(AgentVersePlugin):
    """Add a new tool/action."""

    plugin_type = PluginType.TOOL

    schema: dict[str, Any] = {}
    risk_level: str = "medium"

    async def execute(self, inputs: dict, context: dict) -> dict[str, Any]:
        raise NotImplementedError


class MemoryPlugin(AgentVersePlugin):
    """Add a new memory backend."""

    plugin_type = PluginType.MEMORY

    async def store(self, entry: dict) -> str:
        raise NotImplementedError

    async def retrieve(self, query: str, scope: str, top_k: int = 5) -> list[dict]:
        raise NotImplementedError


class KnowledgePlugin(AgentVersePlugin):
    """Add a new knowledge source connector."""

    plugin_type = PluginType.KNOWLEDGE

    async def index(self, source: dict) -> dict[str, Any]:
        raise NotImplementedError

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        raise NotImplementedError


class EvaluatorPlugin(AgentVersePlugin):
    """Add a custom evaluator."""

    plugin_type = PluginType.EVALUATOR

    async def evaluate(self, output: str, context: dict) -> dict[str, Any]:
        raise NotImplementedError


class PolicyPlugin(AgentVersePlugin):
    """Add a custom governance policy."""

    plugin_type = PluginType.POLICY

    async def check(self, action: dict, context: dict) -> dict[str, Any]:
        raise NotImplementedError


__all__ = [
    "AgentVersePlugin",
    "EvaluatorPlugin",
    "KnowledgePlugin",
    "MemoryPlugin",
    "ModelPlugin",
    "PluginType",
    "PolicyPlugin",
    "ToolPlugin",
]
