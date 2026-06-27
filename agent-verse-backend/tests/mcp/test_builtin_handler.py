"""Tests for HIGH-3: builtin_handler process-local registry survives Redis round-trip."""
from __future__ import annotations


def test_builtin_handler_survives_registry_round_trip():
    """Built-in handler must be recoverable after Redis serialization."""
    from app.mcp.registry import MCPRegistry, _BUILTIN_HANDLER_REGISTRY

    def my_handler(tool, args):
        return {"result": "ok"}

    MCPRegistry.register_builtin_handler("test-srv-1", my_handler)
    assert MCPRegistry.get_builtin_handler("test-srv-1") is my_handler
    assert MCPRegistry.get_builtin_handler("unknown-srv") is None


def test_builtin_handler_registry_stores_multiple_servers():
    """Multiple built-in handlers can be registered independently."""
    from app.mcp.registry import MCPRegistry

    def handler_a(tool, args):
        return "a"

    def handler_b(tool, args):
        return "b"

    MCPRegistry.register_builtin_handler("srv-a", handler_a)
    MCPRegistry.register_builtin_handler("srv-b", handler_b)

    assert MCPRegistry.get_builtin_handler("srv-a") is handler_a
    assert MCPRegistry.get_builtin_handler("srv-b") is handler_b


def test_builtin_handler_registry_overwrite():
    """Re-registering a server_id replaces the handler."""
    from app.mcp.registry import MCPRegistry

    def old_handler(tool, args):
        return "old"

    def new_handler(tool, args):
        return "new"

    MCPRegistry.register_builtin_handler("srv-overwrite", old_handler)
    assert MCPRegistry.get_builtin_handler("srv-overwrite") is old_handler

    MCPRegistry.register_builtin_handler("srv-overwrite", new_handler)
    assert MCPRegistry.get_builtin_handler("srv-overwrite") is new_handler


def test_get_reattaches_handler_after_json_roundtrip():
    """get() re-attaches the process-local handler to the deserialized MCPServerConfig."""
    import asyncio

    from app.mcp.registry import MCPRegistry, MCPServerConfig
    from app.tenancy.context import PlanTier, TenantContext

    tenant_ctx = TenantContext(
        tenant_id="t-handler-test",
        plan=PlanTier.FREE,
        api_key_id="k",
    )

    def builtin_fn(tool, args):
        return {"done": True}

    # Create a fake in-memory Redis for this test
    class _MemRedis:
        def __init__(self):
            self._d = {}
            self._s = {}

        async def get(self, key):
            return self._d.get(key)

        async def set(self, key, val, **kw):
            self._d[key] = val

        async def sadd(self, key, member):
            self._s.setdefault(key, set()).add(member)

        async def smembers(self, key):
            return self._s.get(key, set())

    mem_redis = _MemRedis()
    registry = MCPRegistry(redis=mem_redis)

    server_config = MCPServerConfig(
        server_id="builtin-test-srv",
        name="Test Server",
        base_url="builtin://",
        builtin_handler=builtin_fn,
    )

    # Register the handler in the process-local registry
    MCPRegistry.register_builtin_handler("builtin-test-srv", builtin_fn)

    async def _run():
        await registry.register(server_config, tenant_ctx=tenant_ctx)
        # Retrieve — simulates Redis round-trip (handler stripped by exclude=True)
        cfg = await registry.get("builtin-test-srv", tenant_ctx=tenant_ctx)
        assert cfg is not None
        # Handler must have been re-attached from the process-local registry
        assert cfg.builtin_handler is builtin_fn

    asyncio.run(_run())
