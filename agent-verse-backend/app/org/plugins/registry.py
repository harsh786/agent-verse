"""Plugin registry — manages installed plugins at runtime."""

from __future__ import annotations

from typing import Any

import structlog

from app.org.plugins import AgentVersePlugin, PluginType

_log = structlog.get_logger(__name__)


class PluginRegistry:
    """
    Central registry for all installed AgentVerse plugins.
    Plugins are registered at startup or dynamically via API.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, AgentVersePlugin] = {}

    def register(self, plugin: AgentVersePlugin) -> None:
        key = f"{plugin.plugin_type.value}:{plugin.name}"
        self._plugins[key] = plugin
        _log.info("plugin.registered", key=key, version=plugin.version)

    def get(self, plugin_type: PluginType, name: str) -> AgentVersePlugin | None:
        return self._plugins.get(f"{plugin_type.value}:{name}")

    def list_by_type(self, plugin_type: PluginType) -> list[AgentVersePlugin]:
        return [p for k, p in self._plugins.items() if k.startswith(f"{plugin_type.value}:")]

    def list_all(self) -> list[dict[str, Any]]:
        return [p.metadata() for p in self._plugins.values()]

    def health_check(self) -> dict[str, bool]:
        return {k: p.health_check() for k, p in self._plugins.items()}


# Global singleton
plugin_registry = PluginRegistry()
