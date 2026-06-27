"""Redis MCP server — interact with an external Redis instance as a data store.

NOTE: This server treats Redis as an external tool/data store.
      It is NOT AgentVerse's internal Redis cache.

Environment:
  REDIS_MCP_URL: Redis connection URL (e.g. redis://[:password@]host:6379/0)
"""
from __future__ import annotations

import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "redis_get",
        "description": "Get the value of a Redis key",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Redis key to retrieve"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "redis_set",
        "description": "Set a Redis key to a value with optional expiry",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "ex": {"type": "integer", "description": "Expiry in seconds"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "redis_delete",
        "description": "Delete one or more Redis keys",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to delete"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "redis_list_keys",
        "description": "Scan for Redis keys matching a pattern (uses SCAN, not KEYS)",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "default": "*", "description": "Glob pattern"},
                "count": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "redis_hget",
        "description": "Get the value of a field in a Redis hash",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Hash key"},
                "field": {"type": "string", "description": "Field name"},
            },
            "required": ["key", "field"],
        },
    },
    {
        "name": "redis_hset",
        "description": "Set the value of a field in a Redis hash",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "field": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "field", "value"],
        },
    },
    {
        "name": "redis_hgetall",
        "description": "Get all fields and values of a Redis hash",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "redis_lpush",
        "description": "Prepend values to a Redis list",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string", "description": "Value to push"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "redis_lrange",
        "description": "Get a range of elements from a Redis list",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "start": {"type": "integer", "default": 0},
                "stop": {"type": "integer", "default": -1},
            },
            "required": ["key"],
        },
    },
    {
        "name": "redis_publish",
        "description": "Publish a message to a Redis Pub/Sub channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["channel", "message"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("REDIS_MCP_URL", "")
    if not url:
        return {"error": "REDIS_MCP_URL not configured"}

    try:
        import redis.asyncio as aioredis  # type: ignore[import]

        client: Any = aioredis.from_url(url, decode_responses=True)
        try:
            if tool_name == "redis_get":
                value = await client.get(arguments["key"])
                return {"key": arguments["key"], "value": value, "exists": value is not None}

            elif tool_name == "redis_set":
                kwargs: dict[str, Any] = {}
                if ex := arguments.get("ex"):
                    kwargs["ex"] = ex
                result = await client.set(arguments["key"], arguments["value"], **kwargs)
                return {"ok": bool(result), "key": arguments["key"]}

            elif tool_name == "redis_delete":
                deleted = await client.delete(arguments["key"])
                return {"deleted": deleted, "key": arguments["key"]}

            elif tool_name == "redis_list_keys":
                pattern = arguments.get("pattern", "*")
                count = arguments.get("count", 100)
                keys: list[str] = []
                async for key in client.scan_iter(match=pattern, count=count):
                    keys.append(key)
                    if len(keys) >= count:
                        break
                return {"keys": keys, "count": len(keys)}

            elif tool_name == "redis_hget":
                value = await client.hget(arguments["key"], arguments["field"])
                return {"value": value, "exists": value is not None}

            elif tool_name == "redis_hset":
                await client.hset(arguments["key"], arguments["field"], arguments["value"])
                return {"ok": True, "key": arguments["key"], "field": arguments["field"]}

            elif tool_name == "redis_hgetall":
                data = await client.hgetall(arguments["key"])
                return {"data": data, "key": arguments["key"]}

            elif tool_name == "redis_lpush":
                length = await client.lpush(arguments["key"], arguments["value"])
                return {"list_length": length, "key": arguments["key"]}

            elif tool_name == "redis_lrange":
                items = await client.lrange(
                    arguments["key"],
                    arguments.get("start", 0),
                    arguments.get("stop", -1),
                )
                return {"items": items, "count": len(items)}

            elif tool_name == "redis_publish":
                receivers = await client.publish(arguments["channel"], arguments["message"])
                return {"receivers": receivers, "channel": arguments["channel"]}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        finally:
            await client.aclose()

    except ImportError:
        return {"error": "redis not installed: pip install redis[asyncio]"}
    except Exception as exc:
        logger.exception("redis_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
