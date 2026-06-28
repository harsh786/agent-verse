"""AWS Lambda MCP server — manage Lambda functions via boto3.

Environment variables:
  AWS_ACCESS_KEY_ID:     AWS access key
  AWS_SECRET_ACCESS_KEY: AWS secret key
  AWS_REGION:            AWS region (default: us-east-1)
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "lambda_list_functions",
        "description": "List AWS Lambda functions in the configured region",
        "parameters": {
            "type": "object",
            "properties": {
                "max_items": {"type": "integer", "default": 50},
                "function_version": {"type": "string", "default": "ALL"},
            },
        },
    },
    {
        "name": "lambda_invoke_function",
        "description": "Invoke an AWS Lambda function synchronously or asynchronously",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string"},
                "payload": {"type": "object", "description": "JSON payload to pass to the function"},
                "invocation_type": {
                    "type": "string",
                    "enum": ["RequestResponse", "Event", "DryRun"],
                    "default": "RequestResponse",
                },
            },
            "required": ["function_name"],
        },
    },
    {
        "name": "lambda_get_function",
        "description": "Get configuration and metadata for an AWS Lambda function",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string"},
                "qualifier": {"type": "string", "description": "Version or alias"},
            },
            "required": ["function_name"],
        },
    },
    {
        "name": "lambda_update_function_code",
        "description": "Update the code of an AWS Lambda function from an S3 object",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string"},
                "s3_bucket": {"type": "string"},
                "s3_key": {"type": "string"},
                "s3_object_version": {"type": "string"},
                "publish": {"type": "boolean", "default": False},
            },
            "required": ["function_name", "s3_bucket", "s3_key"],
        },
    },
    {
        "name": "lambda_list_aliases",
        "description": "List aliases for an AWS Lambda function",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string"},
                "function_version": {"type": "string"},
            },
            "required": ["function_name"],
        },
    },
    {
        "name": "lambda_get_logs",
        "description": "Get recent CloudWatch log events for a Lambda function",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "start_time_ms": {"type": "integer", "description": "Unix timestamp in ms"},
            },
            "required": ["function_name"],
        },
    },
]


def get_tools() -> list[dict[str, Any]]:
    try:
        import boto3  # noqa: F401  # type: ignore[import]
    except ImportError:
        return [
            {
                "name": "unavailable",
                "description": "boto3 not installed. Run: pip install boto3",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
    return TOOL_DEFINITIONS


def _client(service: str) -> Any:
    import boto3  # type: ignore[import]

    return boto3.client(
        service,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        import boto3  # noqa: F401  # type: ignore[import]
    except ImportError:
        return {
            "error": "boto3 not installed. Run: pip install boto3",
            "tool": tool_name,
            "status": "dependency_missing",
        }

    def _sync() -> dict[str, Any]:
        try:
            if tool_name == "lambda_list_functions":
                c = _client("lambda")
                result = c.list_functions(
                    MaxItems=arguments.get("max_items", 50),
                    FunctionVersion=arguments.get("function_version", "ALL"),
                )
                return {
                    "functions": [
                        {
                            "name": f["FunctionName"],
                            "arn": f.get("FunctionArn"),
                            "runtime": f.get("Runtime"),
                            "state": f.get("State"),
                            "last_modified": f.get("LastModified"),
                            "description": f.get("Description", ""),
                            "memory_size": f.get("MemorySize"),
                            "timeout": f.get("Timeout"),
                        }
                        for f in result.get("Functions", [])
                    ]
                }

            elif tool_name == "lambda_invoke_function":
                c = _client("lambda")
                resp = c.invoke(
                    FunctionName=arguments["function_name"],
                    InvocationType=arguments.get("invocation_type", "RequestResponse"),
                    Payload=json.dumps(arguments.get("payload", {})),
                )
                payload_bytes = resp["Payload"].read()
                try:
                    parsed_payload = json.loads(payload_bytes)
                except Exception:
                    parsed_payload = payload_bytes.decode("utf-8", errors="replace")
                return {
                    "status_code": resp["StatusCode"],
                    "executed_version": resp.get("ExecutedVersion"),
                    "function_error": resp.get("FunctionError"),
                    "result": parsed_payload,
                }

            elif tool_name == "lambda_get_function":
                c = _client("lambda")
                kwargs: dict[str, Any] = {"FunctionName": arguments["function_name"]}
                if arguments.get("qualifier"):
                    kwargs["Qualifier"] = arguments["qualifier"]
                resp = c.get_function(**kwargs)
                cfg = resp.get("Configuration", {})
                code = resp.get("Code", {})
                return {
                    "function_name": cfg.get("FunctionName"),
                    "runtime": cfg.get("Runtime"),
                    "role": cfg.get("Role"),
                    "handler": cfg.get("Handler"),
                    "code_size": cfg.get("CodeSize"),
                    "description": cfg.get("Description"),
                    "timeout": cfg.get("Timeout"),
                    "memory_size": cfg.get("MemorySize"),
                    "last_modified": cfg.get("LastModified"),
                    "version": cfg.get("Version"),
                    "state": cfg.get("State"),
                    "code_location": code.get("Location"),
                }

            elif tool_name == "lambda_update_function_code":
                c = _client("lambda")
                kwargs = {
                    "FunctionName": arguments["function_name"],
                    "S3Bucket": arguments["s3_bucket"],
                    "S3Key": arguments["s3_key"],
                    "Publish": arguments.get("publish", False),
                }
                if arguments.get("s3_object_version"):
                    kwargs["S3ObjectVersion"] = arguments["s3_object_version"]
                resp = c.update_function_code(**kwargs)
                return {
                    "function_name": resp.get("FunctionName"),
                    "code_size": resp.get("CodeSize"),
                    "last_modified": resp.get("LastModified"),
                    "state": resp.get("State"),
                    "version": resp.get("Version"),
                }

            elif tool_name == "lambda_list_aliases":
                c = _client("lambda")
                kwargs = {"FunctionName": arguments["function_name"]}
                if arguments.get("function_version"):
                    kwargs["FunctionVersion"] = arguments["function_version"]
                resp = c.list_aliases(**kwargs)
                return {
                    "aliases": [
                        {
                            "name": a["Name"],
                            "function_version": a.get("FunctionVersion"),
                            "description": a.get("Description", ""),
                            "arn": a.get("AliasArn"),
                        }
                        for a in resp.get("Aliases", [])
                    ]
                }

            elif tool_name == "lambda_get_logs":
                c = _client("logs")
                fn_name = arguments["function_name"]
                log_group = f"/aws/lambda/{fn_name}"
                limit = arguments.get("limit", 50)

                # Get the most recent log stream
                streams_resp = c.describe_log_streams(
                    logGroupName=log_group,
                    orderBy="LastEventTime",
                    descending=True,
                    limit=1,
                )
                streams = streams_resp.get("logStreams", [])
                if not streams:
                    return {"logs": [], "message": f"No log streams found for {log_group}"}

                stream_name = streams[0]["logStreamName"]
                kwargs = {
                    "logGroupName": log_group,
                    "logStreamName": stream_name,
                    "limit": limit,
                    "startFromHead": False,
                }
                if arguments.get("start_time_ms"):
                    kwargs["startTime"] = arguments["start_time_ms"]

                events_resp = c.get_log_events(**kwargs)
                return {
                    "log_group": log_group,
                    "log_stream": stream_name,
                    "events": [
                        {
                            "timestamp": e.get("timestamp"),
                            "message": e.get("message", "").rstrip(),
                        }
                        for e in events_resp.get("events", [])
                    ],
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as exc:
            return {"error": str(exc)}

    return await asyncio.get_running_loop().run_in_executor(None, _sync)
