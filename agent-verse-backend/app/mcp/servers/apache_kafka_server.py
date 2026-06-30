"""Apache Kafka MCP server — topic and consumer-group management via Confluent Cloud REST Proxy.

Environment:
  KAFKA_API_KEY:         Confluent Cloud API key
  KAFKA_API_SECRET:      Confluent Cloud API secret
  KAFKA_REST_ENDPOINT:   Confluent REST Proxy base URL (e.g. https://pkc-xxx.region.confluent.cloud)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "kafka_list_topics",
        "description": "List all topics on the Kafka cluster",
        "parameters": {
            "type": "object",
            "properties": {
                "cluster_id": {"type": "string", "description": "Confluent cluster ID"},
            },
            "required": ["cluster_id"],
        },
    },
    {
        "name": "kafka_create_topic",
        "description": "Create a new Kafka topic with configurable partitions and replication",
        "parameters": {
            "type": "object",
            "properties": {
                "cluster_id": {"type": "string"},
                "topic_name": {"type": "string"},
                "partitions_count": {"type": "integer", "default": 3},
                "replication_factor": {"type": "integer", "default": 3},
                "configs": {
                    "type": "object",
                    "description": "Topic configuration overrides (e.g. retention.ms)",
                },
            },
            "required": ["cluster_id", "topic_name"],
        },
    },
    {
        "name": "kafka_delete_topic",
        "description": "Delete a Kafka topic permanently",
        "parameters": {
            "type": "object",
            "properties": {
                "cluster_id": {"type": "string"},
                "topic_name": {"type": "string"},
            },
            "required": ["cluster_id", "topic_name"],
        },
    },
    {
        "name": "kafka_list_consumer_groups",
        "description": "List consumer groups registered on the cluster",
        "parameters": {
            "type": "object",
            "properties": {
                "cluster_id": {"type": "string"},
            },
            "required": ["cluster_id"],
        },
    },
    {
        "name": "kafka_get_topic_config",
        "description": "Get the configuration settings for a specific Kafka topic",
        "parameters": {
            "type": "object",
            "properties": {
                "cluster_id": {"type": "string"},
                "topic_name": {"type": "string"},
            },
            "required": ["cluster_id", "topic_name"],
        },
    },
    {
        "name": "kafka_describe_cluster",
        "description": "Describe the Kafka cluster metadata including broker details",
        "parameters": {
            "type": "object",
            "properties": {
                "cluster_id": {"type": "string"},
            },
            "required": ["cluster_id"],
        },
    },
]


def _base() -> str:
    return os.getenv("KAFKA_REST_ENDPOINT", "https://pkc-placeholder.region.confluent.cloud")


def _auth() -> tuple[str, str]:
    return (os.getenv("KAFKA_API_KEY", ""), os.getenv("KAFKA_API_SECRET", ""))


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("KAFKA_API_KEY", "")
    if not api_key:
        return {"error": "KAFKA_API_KEY not configured"}

    base = _base()
    auth = _auth()
    cluster_id = arguments.get("cluster_id", "")

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "kafka_list_topics":
                r = await c.get(
                    f"{base}/kafka/v3/clusters/{cluster_id}/topics",
                    auth=auth,
                )
                r.raise_for_status()
                data = r.json()
                return {"topics": [t.get("topic_name") for t in data.get("data", [])]}

            elif tool_name == "kafka_create_topic":
                body: dict[str, Any] = {
                    "topic_name": arguments["topic_name"],
                    "partitions_count": arguments.get("partitions_count", 3),
                    "replication_factor": arguments.get("replication_factor", 3),
                }
                if arguments.get("configs"):
                    body["configs"] = [
                        {"name": k, "value": str(v)}
                        for k, v in arguments["configs"].items()
                    ]
                r = await c.post(
                    f"{base}/kafka/v3/clusters/{cluster_id}/topics",
                    auth=auth,
                    json=body,
                )
                r.raise_for_status()
                return {"created": True, "topic_name": arguments["topic_name"]}

            elif tool_name == "kafka_delete_topic":
                r = await c.delete(
                    f"{base}/kafka/v3/clusters/{cluster_id}/topics/{arguments['topic_name']}",
                    auth=auth,
                )
                r.raise_for_status()
                return {"deleted": True, "topic_name": arguments["topic_name"]}

            elif tool_name == "kafka_list_consumer_groups":
                r = await c.get(
                    f"{base}/kafka/v3/clusters/{cluster_id}/consumer-groups",
                    auth=auth,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "consumer_groups": [
                        g.get("consumer_group_id") for g in data.get("data", [])
                    ]
                }

            elif tool_name == "kafka_get_topic_config":
                r = await c.get(
                    f"{base}/kafka/v3/clusters/{cluster_id}/topics/{arguments['topic_name']}/configs",
                    auth=auth,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "configs": {
                        cfg.get("name"): cfg.get("value")
                        for cfg in data.get("data", [])
                    }
                }

            elif tool_name == "kafka_describe_cluster":
                r = await c.get(
                    f"{base}/kafka/v3/clusters/{cluster_id}",
                    auth=auth,
                )
                r.raise_for_status()
                return r.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("kafka_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
