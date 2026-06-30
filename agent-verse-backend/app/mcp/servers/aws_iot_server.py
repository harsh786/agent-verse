"""AWS IoT Core MCP server — device management, shadows, and message publishing.

Environment:
  AWS_ACCESS_KEY_ID: AWS access key ID
  AWS_SECRET_ACCESS_KEY: AWS secret access key
  AWS_REGION: AWS region (e.g. us-east-1)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _endpoint() -> str:
    region = os.getenv("AWS_REGION", "us-east-1")
    return f"https://iot.{region}.amazonaws.com"


TOOL_DEFINITIONS = [
    {
        "name": "aws_iot_list_things",
        "description": "List IoT Things registered in AWS IoT Core",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Maximum number of Things to return"},
                "next_token": {"type": "string", "description": "Pagination token"},
                "thing_type_name": {"type": "string", "description": "Filter by Thing type name"},
            },
        },
    },
    {
        "name": "aws_iot_describe_thing",
        "description": "Get details of a specific IoT Thing including attributes and type",
        "parameters": {
            "type": "object",
            "properties": {
                "thing_name": {"type": "string", "description": "Name of the IoT Thing"},
            },
            "required": ["thing_name"],
        },
    },
    {
        "name": "aws_iot_update_thing_shadow",
        "description": "Update the device shadow (desired state) for an IoT Thing",
        "parameters": {
            "type": "object",
            "properties": {
                "thing_name": {"type": "string", "description": "Name of the IoT Thing"},
                "desired_state": {"type": "object", "description": "Desired state key-value pairs"},
                "shadow_name": {"type": "string", "description": "Named shadow (optional, omit for classic shadow)"},
            },
            "required": ["thing_name", "desired_state"],
        },
    },
    {
        "name": "aws_iot_list_topic_rules",
        "description": "List IoT topic rules that route messages to AWS services",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Maximum rules to return"},
                "next_token": {"type": "string", "description": "Pagination token"},
                "rule_disabled": {"type": "boolean", "description": "Filter disabled rules"},
            },
        },
    },
    {
        "name": "aws_iot_publish_message",
        "description": "Publish an MQTT message to an IoT topic using the data endpoint",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "MQTT topic to publish to"},
                "payload": {"type": "object", "description": "Message payload as JSON object"},
                "qos": {"type": "integer", "description": "QoS level: 0 or 1"},
            },
            "required": ["topic", "payload"],
        },
    },
    {
        "name": "aws_iot_get_device_stats",
        "description": "Get statistics and fleet metrics for IoT devices and connections",
        "parameters": {
            "type": "object",
            "properties": {
                "query_string": {"type": "string", "description": "Fleet indexing query string"},
                "aggregation_field": {"type": "string", "description": "Field to aggregate on"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    import json
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    region = os.getenv("AWS_REGION", "us-east-1")
    if not access_key or not secret_key:
        return {"error": "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY not configured"}

    try:
        import boto3
    except ImportError:
        return {"error": "boto3 not installed"}

    try:
        iot_client = boto3.client(
            "iot",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        iot_data_client = boto3.client(
            "iot-data",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

        if tool_name == "aws_iot_list_things":
            kwargs: dict[str, Any] = {}
            if "max_results" in arguments:
                kwargs["maxResults"] = arguments["max_results"]
            if "next_token" in arguments:
                kwargs["nextToken"] = arguments["next_token"]
            if "thing_type_name" in arguments:
                kwargs["thingTypeName"] = arguments["thing_type_name"]
            return iot_client.list_things(**kwargs)

        if tool_name == "aws_iot_describe_thing":
            return iot_client.describe_thing(thingName=arguments["thing_name"])

        if tool_name == "aws_iot_update_thing_shadow":
            payload = json.dumps({"state": {"desired": arguments["desired_state"]}})
            kwargs = {
                "thingName": arguments["thing_name"],
                "payload": payload.encode(),
            }
            if "shadow_name" in arguments:
                kwargs["shadowName"] = arguments["shadow_name"]
            result = iot_data_client.update_thing_shadow(**kwargs)
            return {"updated": True, "thing_name": arguments["thing_name"]}

        if tool_name == "aws_iot_list_topic_rules":
            kwargs = {}
            if "max_results" in arguments:
                kwargs["maxResults"] = arguments["max_results"]
            if "next_token" in arguments:
                kwargs["nextToken"] = arguments["next_token"]
            if "rule_disabled" in arguments:
                kwargs["ruleDisabled"] = arguments["rule_disabled"]
            return iot_client.list_topic_rules(**kwargs)

        if tool_name == "aws_iot_publish_message":
            result = iot_data_client.publish(
                topic=arguments["topic"],
                qos=arguments.get("qos", 0),
                payload=json.dumps(arguments["payload"]).encode(),
            )
            return {"published": True, "topic": arguments["topic"]}

        if tool_name == "aws_iot_get_device_stats":
            kwargs = {}
            if "query_string" in arguments:
                kwargs["queryString"] = arguments["query_string"]
            if "aggregation_field" in arguments:
                kwargs["aggregationField"] = arguments["aggregation_field"]
            return iot_client.get_statistics(**kwargs)

        return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"error": str(e)}
