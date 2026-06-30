"""Amazon SES MCP server — email delivery via SES API.

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
        "name": "ses_send_email",
        "description": "Send a plain-text or HTML email via Amazon SES",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Sender email address"},
                "to_addresses": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "body_text": {"type": "string", "description": "Plain-text body"},
                "body_html": {"type": "string", "description": "HTML body"},
                "cc_addresses": {"type": "array", "items": {"type": "string"}},
                "reply_to_addresses": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["source", "to_addresses", "subject"],
        },
    },
    {
        "name": "ses_list_identities",
        "description": "List verified email addresses and domains in SES",
        "parameters": {
            "type": "object",
            "properties": {
                "identity_type": {
                    "type": "string",
                    "enum": ["EmailAddress", "Domain"],
                    "default": "EmailAddress",
                },
                "max_items": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "ses_verify_email_identity",
        "description": "Send a verification email to confirm ownership of an address",
        "parameters": {
            "type": "object",
            "properties": {
                "email_address": {"type": "string"},
            },
            "required": ["email_address"],
        },
    },
    {
        "name": "ses_get_send_statistics",
        "description": "Get sending statistics (bounces, complaints, delivery attempts) for the past two weeks",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "ses_list_templates",
        "description": "List all email templates stored in SES",
        "parameters": {
            "type": "object",
            "properties": {
                "max_items": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "ses_send_templated_email",
        "description": "Send an email using a pre-defined SES template",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "to_addresses": {"type": "array", "items": {"type": "string"}},
                "template": {"type": "string", "description": "Template name"},
                "template_data": {
                    "type": "object",
                    "description": "Key-value pairs for template variable substitution",
                },
            },
            "required": ["source", "to_addresses", "template", "template_data"],
        },
    },
]


def _client() -> Any:
    import boto3  # type: ignore[import]

    return boto3.client(
        "ses",
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

            if tool_name == "ses_send_email":
                body: dict[str, Any] = {}
                if arguments.get("body_text"):
                    body["Text"] = {"Data": arguments["body_text"], "Charset": "UTF-8"}
                if arguments.get("body_html"):
                    body["Html"] = {"Data": arguments["body_html"], "Charset": "UTF-8"}
                if not body:
                    body["Text"] = {"Data": "", "Charset": "UTF-8"}
                kwargs: dict[str, Any] = {
                    "Source": arguments["source"],
                    "Destination": {"ToAddresses": arguments["to_addresses"]},
                    "Message": {
                        "Subject": {"Data": arguments["subject"], "Charset": "UTF-8"},
                        "Body": body,
                    },
                }
                if arguments.get("cc_addresses"):
                    kwargs["Destination"]["CcAddresses"] = arguments["cc_addresses"]
                if arguments.get("reply_to_addresses"):
                    kwargs["ReplyToAddresses"] = arguments["reply_to_addresses"]
                resp = c.send_email(**kwargs)
                return {"message_id": resp.get("MessageId"), "sent": True}

            elif tool_name == "ses_list_identities":
                resp = c.list_identities(
                    IdentityType=arguments.get("identity_type", "EmailAddress"),
                    MaxItems=arguments.get("max_items", 50),
                )
                return {"identities": resp.get("Identities", [])}

            elif tool_name == "ses_verify_email_identity":
                c.verify_email_identity(EmailAddress=arguments["email_address"])
                return {
                    "email_address": arguments["email_address"],
                    "verification_sent": True,
                }

            elif tool_name == "ses_get_send_statistics":
                resp = c.get_send_statistics()
                points = resp.get("SendDataPoints", [])
                return {
                    "data_points": [
                        {
                            "timestamp": dp.get("Timestamp").isoformat()
                            if dp.get("Timestamp")
                            else None,
                            "delivery_attempts": dp.get("DeliveryAttempts", 0),
                            "bounces": dp.get("Bounces", 0),
                            "complaints": dp.get("Complaints", 0),
                            "rejects": dp.get("Rejects", 0),
                        }
                        for dp in points
                    ],
                    "total_data_points": len(points),
                }

            elif tool_name == "ses_list_templates":
                resp = c.list_templates(MaxItems=arguments.get("max_items", 50))
                return {
                    "templates": [
                        {
                            "name": t.get("Name"),
                            "created_timestamp": t.get("CreatedTimestamp").isoformat()
                            if t.get("CreatedTimestamp")
                            else None,
                        }
                        for t in resp.get("TemplatesMetadata", [])
                    ]
                }

            elif tool_name == "ses_send_templated_email":
                import json as _json

                resp = c.send_templated_email(
                    Source=arguments["source"],
                    Destination={"ToAddresses": arguments["to_addresses"]},
                    Template=arguments["template"],
                    TemplateData=_json.dumps(arguments["template_data"]),
                )
                return {"message_id": resp.get("MessageId"), "sent": True}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as exc:
            logger.exception("ses_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}

    return await asyncio.get_running_loop().run_in_executor(None, _sync)
