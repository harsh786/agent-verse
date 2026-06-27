"""AWS CloudWatch MCP server — query metrics, alarms, and logs via boto3.

Environment variables:
  AWS_ACCESS_KEY_ID:     AWS access key
  AWS_SECRET_ACCESS_KEY: AWS secret key
  AWS_REGION:            AWS region (default: us-east-1)
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "cloudwatch_get_metric_data",
        "description": "Retrieve CloudWatch metric data for a given metric and time range",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "CloudWatch namespace (e.g. AWS/EC2)"},
                "metric_name": {"type": "string"},
                "dimensions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "Name": {"type": "string"},
                            "Value": {"type": "string"},
                        },
                    },
                },
                "start_time": {"type": "string", "description": "ISO 8601 start time"},
                "end_time": {"type": "string", "description": "ISO 8601 end time"},
                "period": {"type": "integer", "default": 300, "description": "Aggregation period in seconds"},
                "stat": {
                    "type": "string",
                    "enum": ["Average", "Sum", "Minimum", "Maximum", "SampleCount"],
                    "default": "Average",
                },
            },
            "required": ["namespace", "metric_name", "start_time", "end_time"],
        },
    },
    {
        "name": "cloudwatch_list_alarms",
        "description": "List CloudWatch alarms, optionally filtered by state or prefix",
        "parameters": {
            "type": "object",
            "properties": {
                "state_value": {
                    "type": "string",
                    "enum": ["OK", "ALARM", "INSUFFICIENT_DATA"],
                },
                "alarm_name_prefix": {"type": "string"},
                "max_records": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "cloudwatch_put_metric_alarm",
        "description": "Create or update a CloudWatch metric alarm",
        "parameters": {
            "type": "object",
            "properties": {
                "alarm_name": {"type": "string"},
                "alarm_description": {"type": "string", "default": ""},
                "namespace": {"type": "string"},
                "metric_name": {"type": "string"},
                "dimensions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "Name": {"type": "string"},
                            "Value": {"type": "string"},
                        },
                    },
                },
                "threshold": {"type": "number"},
                "comparison_operator": {
                    "type": "string",
                    "enum": [
                        "GreaterThanOrEqualToThreshold",
                        "GreaterThanThreshold",
                        "LessThanThreshold",
                        "LessThanOrEqualToThreshold",
                    ],
                },
                "evaluation_periods": {"type": "integer", "default": 1},
                "period": {"type": "integer", "default": 300},
                "statistic": {"type": "string", "default": "Average"},
                "alarm_actions": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "alarm_name",
                "namespace",
                "metric_name",
                "threshold",
                "comparison_operator",
            ],
        },
    },
    {
        "name": "cloudwatch_get_log_events",
        "description": "Get log events from a CloudWatch Logs stream",
        "parameters": {
            "type": "object",
            "properties": {
                "log_group_name": {"type": "string"},
                "log_stream_name": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "start_time_ms": {"type": "integer"},
                "end_time_ms": {"type": "integer"},
                "start_from_head": {"type": "boolean", "default": False},
            },
            "required": ["log_group_name", "log_stream_name"],
        },
    },
    {
        "name": "cloudwatch_filter_log_events",
        "description": "Filter log events across streams in a CloudWatch log group",
        "parameters": {
            "type": "object",
            "properties": {
                "log_group_name": {"type": "string"},
                "filter_pattern": {"type": "string", "description": "CloudWatch filter pattern"},
                "start_time_ms": {"type": "integer"},
                "end_time_ms": {"type": "integer"},
                "limit": {"type": "integer", "default": 50},
                "log_stream_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Limit search to specific log streams",
                },
            },
            "required": ["log_group_name"],
        },
    },
]


def _cw_client() -> Any:
    import boto3  # type: ignore[import]

    return boto3.client(
        "cloudwatch",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


def _logs_client() -> Any:
    import boto3  # type: ignore[import]

    return boto3.client(
        "logs",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    def _sync() -> dict[str, Any]:
        from datetime import datetime

        try:
            if tool_name == "cloudwatch_get_metric_data":
                c = _cw_client()
                resp = c.get_metric_statistics(
                    Namespace=arguments["namespace"],
                    MetricName=arguments["metric_name"],
                    Dimensions=arguments.get("dimensions", []),
                    StartTime=datetime.fromisoformat(arguments["start_time"].replace("Z", "+00:00")),
                    EndTime=datetime.fromisoformat(arguments["end_time"].replace("Z", "+00:00")),
                    Period=arguments.get("period", 300),
                    Statistics=[arguments.get("stat", "Average")],
                )
                datapoints = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
                return {
                    "namespace": arguments["namespace"],
                    "metric_name": arguments["metric_name"],
                    "datapoints": [
                        {
                            "timestamp": dp["Timestamp"].isoformat(),
                            "value": dp.get(arguments.get("stat", "Average")),
                            "unit": dp.get("Unit"),
                        }
                        for dp in datapoints
                    ],
                }

            elif tool_name == "cloudwatch_list_alarms":
                c = _cw_client()
                kwargs: dict[str, Any] = {"MaxRecords": arguments.get("max_records", 50)}
                if arguments.get("state_value"):
                    kwargs["StateValue"] = arguments["state_value"]
                if arguments.get("alarm_name_prefix"):
                    kwargs["AlarmNamePrefix"] = arguments["alarm_name_prefix"]
                resp = c.describe_alarms(**kwargs)
                alarms = resp.get("MetricAlarms", []) + resp.get("CompositeAlarms", [])
                return {
                    "alarms": [
                        {
                            "name": a["AlarmName"],
                            "state": a.get("StateValue"),
                            "description": a.get("AlarmDescription", ""),
                            "metric": a.get("MetricName"),
                            "namespace": a.get("Namespace"),
                            "threshold": a.get("Threshold"),
                            "comparison_operator": a.get("ComparisonOperator"),
                            "state_updated": a.get("StateUpdatedTimestamp").isoformat() if a.get("StateUpdatedTimestamp") else None,
                        }
                        for a in alarms
                    ]
                }

            elif tool_name == "cloudwatch_put_metric_alarm":
                c = _cw_client()
                kwargs = {
                    "AlarmName": arguments["alarm_name"],
                    "AlarmDescription": arguments.get("alarm_description", ""),
                    "Namespace": arguments["namespace"],
                    "MetricName": arguments["metric_name"],
                    "Dimensions": arguments.get("dimensions", []),
                    "Threshold": arguments["threshold"],
                    "ComparisonOperator": arguments["comparison_operator"],
                    "EvaluationPeriods": arguments.get("evaluation_periods", 1),
                    "Period": arguments.get("period", 300),
                    "Statistic": arguments.get("statistic", "Average"),
                    "ActionsEnabled": True,
                }
                if arguments.get("alarm_actions"):
                    kwargs["AlarmActions"] = arguments["alarm_actions"]
                c.put_metric_alarm(**kwargs)
                return {"created": True, "alarm_name": arguments["alarm_name"]}

            elif tool_name == "cloudwatch_get_log_events":
                c = _logs_client()
                kwargs = {
                    "logGroupName": arguments["log_group_name"],
                    "logStreamName": arguments["log_stream_name"],
                    "limit": arguments.get("limit", 50),
                    "startFromHead": arguments.get("start_from_head", False),
                }
                if arguments.get("start_time_ms"):
                    kwargs["startTime"] = arguments["start_time_ms"]
                if arguments.get("end_time_ms"):
                    kwargs["endTime"] = arguments["end_time_ms"]
                resp = c.get_log_events(**kwargs)
                return {
                    "log_group": arguments["log_group_name"],
                    "log_stream": arguments["log_stream_name"],
                    "events": [
                        {
                            "timestamp": e.get("timestamp"),
                            "message": e.get("message", "").rstrip(),
                        }
                        for e in resp.get("events", [])
                    ],
                }

            elif tool_name == "cloudwatch_filter_log_events":
                c = _logs_client()
                kwargs = {
                    "logGroupName": arguments["log_group_name"],
                    "limit": arguments.get("limit", 50),
                }
                if arguments.get("filter_pattern"):
                    kwargs["filterPattern"] = arguments["filter_pattern"]
                if arguments.get("start_time_ms"):
                    kwargs["startTime"] = arguments["start_time_ms"]
                if arguments.get("end_time_ms"):
                    kwargs["endTime"] = arguments["end_time_ms"]
                if arguments.get("log_stream_names"):
                    kwargs["logStreamNames"] = arguments["log_stream_names"]
                resp = c.filter_log_events(**kwargs)
                return {
                    "log_group": arguments["log_group_name"],
                    "events": [
                        {
                            "log_stream": e.get("logStreamName"),
                            "timestamp": e.get("timestamp"),
                            "message": e.get("message", "").rstrip(),
                            "event_id": e.get("eventId"),
                        }
                        for e in resp.get("events", [])
                    ],
                    "searched_log_streams": resp.get("searchedLogStreams", []),
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as exc:
            return {"error": str(exc)}

    return await asyncio.get_running_loop().run_in_executor(None, _sync)
