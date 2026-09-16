"""Tests for the plugin base classes and registry —
app/org/plugins/__init__.py and app/org/plugins/registry.py"""
from __future__ import annotations

import pytest

from app.org.plugins import (
    EvaluatorPlugin,
    KnowledgePlugin,
    MemoryPlugin,
    ModelPlugin,
    PluginType,
    PolicyPlugin,
    ToolPlugin,
)
from app.org.plugins.registry import PluginRegistry, plugin_registry


# ── AgentVersePlugin base behaviour ───────────────────────────────────────────


class _EchoToolPlugin(ToolPlugin):
    name = "echo"
    version = "1.2.3"
    description = "Echoes inputs"

    async def execute(self, inputs: dict, context: dict) -> dict:
        return {"echoed": inputs}


def test_plugin_metadata_reports_expected_fields():
    plugin = _EchoToolPlugin()
    meta = plugin.metadata()
    assert meta["name"] == "echo"
    assert meta["version"] == "1.2.3"
    assert meta["type"] == "tool"
    assert meta["healthy"] is True
    assert meta["sandbox"] == "restricted"


def test_plugin_health_check_defaults_true():
    plugin = _EchoToolPlugin()
    assert plugin.health_check() is True


@pytest.mark.asyncio
async def test_tool_plugin_execute_overridden():
    plugin = _EchoToolPlugin()
    result = await plugin.execute({"a": 1}, {})
    assert result == {"echoed": {"a": 1}}


@pytest.mark.asyncio
async def test_model_plugin_base_methods_raise_not_implemented():
    class _Model(ModelPlugin):
        name = "custom-model"

    plugin = _Model()
    with pytest.raises(NotImplementedError):
        await plugin.complete([], {})
    with pytest.raises(NotImplementedError):
        await plugin.embed("hi")
    with pytest.raises(NotImplementedError):
        plugin.cost_estimate(10, 20)


@pytest.mark.asyncio
async def test_memory_plugin_base_methods_raise_not_implemented():
    class _Memory(MemoryPlugin):
        name = "custom-memory"

    plugin = _Memory()
    with pytest.raises(NotImplementedError):
        await plugin.store({})
    with pytest.raises(NotImplementedError):
        await plugin.retrieve("q", "scope")


@pytest.mark.asyncio
async def test_knowledge_plugin_base_methods_raise_not_implemented():
    class _Knowledge(KnowledgePlugin):
        name = "custom-knowledge"

    plugin = _Knowledge()
    with pytest.raises(NotImplementedError):
        await plugin.index({})
    with pytest.raises(NotImplementedError):
        await plugin.search("q")


@pytest.mark.asyncio
async def test_evaluator_plugin_base_method_raises_not_implemented():
    class _Evaluator(EvaluatorPlugin):
        name = "custom-evaluator"

    plugin = _Evaluator()
    with pytest.raises(NotImplementedError):
        await plugin.evaluate("output", {})


@pytest.mark.asyncio
async def test_policy_plugin_base_method_raises_not_implemented():
    class _Policy(PolicyPlugin):
        name = "custom-policy"

    plugin = _Policy()
    with pytest.raises(NotImplementedError):
        await plugin.check({}, {})


@pytest.mark.asyncio
async def test_tool_plugin_base_execute_raises_not_implemented():
    class _BareTool(ToolPlugin):
        name = "bare"

    plugin = _BareTool()
    with pytest.raises(NotImplementedError):
        await plugin.execute({}, {})


def test_plugin_type_values():
    assert PluginType.MODEL == "model"
    assert PluginType.TOOL == "tool"
    assert PluginType.MEMORY == "memory"
    assert PluginType.KNOWLEDGE == "knowledge"
    assert PluginType.EVALUATOR == "evaluator"
    assert PluginType.POLICY == "policy"


# ── PluginRegistry ─────────────────────────────────────────────────────────────


def test_registry_register_and_get():
    registry = PluginRegistry()
    plugin = _EchoToolPlugin()
    registry.register(plugin)
    fetched = registry.get(PluginType.TOOL, "echo")
    assert fetched is plugin


def test_registry_get_unknown_returns_none():
    registry = PluginRegistry()
    assert registry.get(PluginType.TOOL, "nope") is None


def test_registry_list_by_type_filters():
    registry = PluginRegistry()
    registry.register(_EchoToolPlugin())

    class _OtherModel(ModelPlugin):
        name = "other-model"

    registry.register(_OtherModel())
    tool_plugins = registry.list_by_type(PluginType.TOOL)
    assert len(tool_plugins) == 1
    assert tool_plugins[0].name == "echo"


def test_registry_list_all_returns_metadata_dicts():
    registry = PluginRegistry()
    registry.register(_EchoToolPlugin())
    all_meta = registry.list_all()
    assert len(all_meta) == 1
    assert all_meta[0]["name"] == "echo"


def test_registry_health_check_reports_per_plugin_status():
    registry = PluginRegistry()
    registry.register(_EchoToolPlugin())
    statuses = registry.health_check()
    assert statuses["tool:echo"] is True


def test_global_plugin_registry_singleton():
    assert isinstance(plugin_registry, PluginRegistry)
