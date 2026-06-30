"""Amazon SQS MCP server — message queue operations via SQS API.

Environment:
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
        "name": "sqs_send_message",
        "description": "Send a message to an SQS queue",
        "parameters": {
            "type": "object",
            "properties": {
                "queue_url": {"type": "string", "description": "URL of the SQS queue"},
                "message_body": {"type": "string"},
                "delay_seconds": {"type": "integer", "default": 0},
                "message_group_id": {
                    "type": "string",
                    "description": "Required for FIFO queues",
                },
            },
            "required": ["queue_url", "message_body"],
        },
    },
    {
        "name": "sqs_receive_messages",
        "description": "Receive up to 10 messages from an SQS queue",
        "parameters": {
            "type": "object",
            "properties": {
                "queue_url": {"type": "string"},
                "max_number_of_messages": {
                    "type": "integer",
                    "default": 1,
                    "description": "1-10",
                },
                "visibility_timeout": {"type": "integer", "default": 30},
                "wait_time_seconds": {
                    "type": "integer",
                    "default": 0,
                    "description": "Long polling duration 0-20s",
                },
            },
            "required": ["queue_url"],
        },
    },
    {
        "name": "sqs_delete_message",
        "description": "Delete a processed message from the queue using its receipt handle",
        "parameters": {
            "type": "object",
            "properties": {
                "queue_url": {"type": "string"},
                "receipt_handle": {
                    "type": "string",
                    "description": "Receipt handle returned by receive_messages",
                },
            },
            "required": ["queue_url", "receipt_handle"],
        },
    },
    {
        "name": "sqs_list_queues",
        "description": "List SQS queue URLs in the AWS account",
        "parameters": {
            "type": "object",
            "properties": {
                "queue_name_prefix": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "sqs_create_queue",
        "description": "Create a new SQS standard or FIFO queue",
        "parameters": {
            "type": "object",
            "properties": {
                "queue_name": {"type": "string"},
                "fifo": {"type": "boolean", "default": False},
                "visibility_timeout": {"type": "integer", "default": 30},
                "message_retention_seconds": {"type": "integer", "default": 345600},
            },
            "required": ["queue_name"],
        },
    },
    {
        "name": "sqs_get_queue_attributes",
        "description": "Get attributes of an SQS queue such as ARN, message count, and settings",
        "parameters": {
            "type": "object",
            "properties": {
                "queue_url": {"type": "string"},
                "attribute_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["All"],
                },
            },
            "required": ["queue_url"],
        },
    },
]


def _client() -> Any:
    import boto3  # type: ignore[import]

    return boto3.client(
        "sqs",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        import boto3  # noqa: F401  # type: ignore[import]
    except ImportError:
        return {"error": "boto3 not installed. Run: pip install boto3"}

    if not os.getenv("AWS_ACCESS_KEY_ID", ""):
        return {"error": "AWS_ACCESS_KEY_ID not configured"}

    def _sync() -> dict[str, Any]:
        try:
            c = _client()

            if tool_name == "sqs_send_message":
                kwargs: dict[str, Any] = {
                    "QueueUrl": arguments["queue_url"],
                    "MessageBody": arguments["message_body"],
                    "DelaySeconds": arguments.get("delay_seconds", 0),
                }
                if arguments.get("message_group_id"):
                    kwargs["MessageGroupId"] = arguments["message_group_id"]
                resp = c.send_message(**kwargs)
                return {
                    "message_id": resp.get("MessageId"),
                    "md5_of_body": resp.get("MD5OfMessageBody"),
                    "sent": True,
                }

            elif tool_name == "sqs_receive_messages":
                resp = c.receive_message(
                    QueueUrl=arguments["queue_url"],
                    MaxNumberOfMessages=arguments.get("max_number_of_messages", 1),
                    VisibilityTimeout=arguments.get("visibility_timeout", 30),
                    WaitTimeSeconds=arguments.get("wait_time_seconds", 0),
                    AttributeNames=["All"],
                    MessageAttributeNames=["All"],
                )
                return {
                    "messages": [
                        {
                            "message_id": m.get("MessageId"),
                            "receipt_handle": m.get("ReceiptHandle"),
                            "body": m.get("Body"),
                            "md5": m.get("MD5OfBody"),
                        }
                        for m in resp.get("Messages", [])
                    ]
                }

            elif tool_name == "sqs_delete_message":
                c.delete_message(
                    QueueUrl=arguments["queue_url"],
                    ReceiptHandle=arguments["receipt_handle"],
                )
                return {"deleted": True, "queue_url": arguments["queue_url"]}

            elif tool_name == "sqs_list_queues":
                kwargs = {"MaxResults": arguments.get("max_results", 50)}
                if arguments.get("queue_name_prefix"):
                    kwargs["QueueNamePrefix"] = arguments["queue_name_prefix"]
                resp = c.list_queues(**kwargs)
                return {"queue_urls": resp.get("QueueUrls", [])}

            elif tool_name == "sqs_create_queue":
                queue_name = arguments["queue_name"]
                attrs: dict[str, str] = {
                    "VisibilityTimeout": str(arguments.get("visibility_timeout", 30)),
                    "MessageRetentionPeriod": str(
                        arguments.get("message_retention_seconds", 345600)
                    ),
                }
                if arguments.get("fifo"):
                    if not queue_name.endswith(".fifo"):
                        queue_name += ".fifo"
                    attrs["FifoQueue"] = "true"
                resp = c.create_queue(QueueName=queue_name, Attributes=attrs)
                return {"queue_url": resp.get("QueueUrl"), "created": True}

            elif tool_name == "sqs_get_queue_attributes":
                resp = c.get_queue_attributes(
                    QueueUrl=arguments["queue_url"],
                    AttributeNames=arguments.get("attribute_names", ["All"]),
                )
                return {"attributes": resp.get("Attributes", {})}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as exc:
            logger.exception("sqs_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}

    return await asyncio.get_running_loop().run_in_executor(None, _sync)
