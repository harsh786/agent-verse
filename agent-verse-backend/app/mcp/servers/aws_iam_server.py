"""AWS IAM MCP server — manage IAM users, roles, and policies via boto3.

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
        "name": "iam_list_users",
        "description": "List IAM users in the AWS account",
        "parameters": {
            "type": "object",
            "properties": {
                "path_prefix": {"type": "string", "default": "/", "description": "Path prefix filter"},
                "max_items": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "iam_list_roles",
        "description": "List IAM roles in the AWS account",
        "parameters": {
            "type": "object",
            "properties": {
                "path_prefix": {"type": "string", "default": "/"},
                "max_items": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "iam_create_user",
        "description": "Create a new IAM user",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "path": {"type": "string", "default": "/"},
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "Key": {"type": "string"},
                            "Value": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["username"],
        },
    },
    {
        "name": "iam_attach_policy",
        "description": "Attach a managed IAM policy to a user, role, or group",
        "parameters": {
            "type": "object",
            "properties": {
                "policy_arn": {"type": "string", "description": "ARN of the managed policy"},
                "user_name": {"type": "string"},
                "role_name": {"type": "string"},
                "group_name": {"type": "string"},
            },
            "required": ["policy_arn"],
        },
    },
    {
        "name": "iam_list_policies",
        "description": "List IAM managed policies",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["All", "AWS", "Local"],
                    "default": "Local",
                    "description": "All=both, AWS=AWS-managed, Local=customer-managed",
                },
                "max_items": {"type": "integer", "default": 50},
                "path_prefix": {"type": "string", "default": "/"},
            },
        },
    },
    {
        "name": "iam_get_user",
        "description": "Get details of a specific IAM user",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
            },
            "required": ["username"],
        },
    },
    {
        "name": "iam_list_attached_user_policies",
        "description": "List managed policies attached to an IAM user",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
            },
            "required": ["username"],
        },
    },
]


def _client() -> Any:
    import boto3  # type: ignore[import]

    return boto3.client(
        "iam",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    def _sync() -> dict[str, Any]:
        try:
            c = _client()

            if tool_name == "iam_list_users":
                kwargs: dict[str, Any] = {
                    "PathPrefix": arguments.get("path_prefix", "/"),
                    "MaxItems": arguments.get("max_items", 50),
                }
                resp = c.list_users(**kwargs)
                return {
                    "users": [
                        {
                            "user_name": u["UserName"],
                            "user_id": u.get("UserId"),
                            "arn": u.get("Arn"),
                            "path": u.get("Path"),
                            "created_date": u.get("CreateDate").isoformat() if u.get("CreateDate") else None,
                            "password_last_used": u.get("PasswordLastUsed").isoformat() if u.get("PasswordLastUsed") else None,
                        }
                        for u in resp.get("Users", [])
                    ],
                    "is_truncated": resp.get("IsTruncated", False),
                }

            elif tool_name == "iam_list_roles":
                kwargs = {
                    "PathPrefix": arguments.get("path_prefix", "/"),
                    "MaxItems": arguments.get("max_items", 50),
                }
                resp = c.list_roles(**kwargs)
                return {
                    "roles": [
                        {
                            "role_name": r["RoleName"],
                            "role_id": r.get("RoleId"),
                            "arn": r.get("Arn"),
                            "path": r.get("Path"),
                            "description": r.get("Description", ""),
                            "created_date": r.get("CreateDate").isoformat() if r.get("CreateDate") else None,
                        }
                        for r in resp.get("Roles", [])
                    ],
                    "is_truncated": resp.get("IsTruncated", False),
                }

            elif tool_name == "iam_create_user":
                kwargs = {
                    "UserName": arguments["username"],
                    "Path": arguments.get("path", "/"),
                }
                if arguments.get("tags"):
                    kwargs["Tags"] = arguments["tags"]
                resp = c.create_user(**kwargs)
                user = resp.get("User", {})
                return {
                    "user_name": user.get("UserName"),
                    "user_id": user.get("UserId"),
                    "arn": user.get("Arn"),
                    "path": user.get("Path"),
                    "created_date": user.get("CreateDate").isoformat() if user.get("CreateDate") else None,
                }

            elif tool_name == "iam_attach_policy":
                policy_arn = arguments["policy_arn"]
                if arguments.get("user_name"):
                    c.attach_user_policy(UserName=arguments["user_name"], PolicyArn=policy_arn)
                    return {"attached": True, "target": arguments["user_name"], "policy_arn": policy_arn}
                elif arguments.get("role_name"):
                    c.attach_role_policy(RoleName=arguments["role_name"], PolicyArn=policy_arn)
                    return {"attached": True, "target": arguments["role_name"], "policy_arn": policy_arn}
                elif arguments.get("group_name"):
                    c.attach_group_policy(GroupName=arguments["group_name"], PolicyArn=policy_arn)
                    return {"attached": True, "target": arguments["group_name"], "policy_arn": policy_arn}
                else:
                    return {"error": "Must provide user_name, role_name, or group_name"}

            elif tool_name == "iam_list_policies":
                kwargs = {
                    "Scope": arguments.get("scope", "Local"),
                    "MaxItems": arguments.get("max_items", 50),
                    "PathPrefix": arguments.get("path_prefix", "/"),
                }
                resp = c.list_policies(**kwargs)
                return {
                    "policies": [
                        {
                            "policy_name": p["PolicyName"],
                            "policy_id": p.get("PolicyId"),
                            "arn": p.get("Arn"),
                            "description": p.get("Description", ""),
                            "default_version_id": p.get("DefaultVersionId"),
                            "attachment_count": p.get("AttachmentCount"),
                            "created_date": p.get("CreateDate").isoformat() if p.get("CreateDate") else None,
                        }
                        for p in resp.get("Policies", [])
                    ],
                    "is_truncated": resp.get("IsTruncated", False),
                }

            elif tool_name == "iam_get_user":
                resp = c.get_user(UserName=arguments["username"])
                user = resp.get("User", {})
                return {
                    "user_name": user.get("UserName"),
                    "user_id": user.get("UserId"),
                    "arn": user.get("Arn"),
                    "path": user.get("Path"),
                    "created_date": user.get("CreateDate").isoformat() if user.get("CreateDate") else None,
                    "password_last_used": user.get("PasswordLastUsed").isoformat() if user.get("PasswordLastUsed") else None,
                    "tags": user.get("Tags", []),
                }

            elif tool_name == "iam_list_attached_user_policies":
                resp = c.list_attached_user_policies(UserName=arguments["username"])
                return {
                    "username": arguments["username"],
                    "policies": [
                        {
                            "policy_name": p["PolicyName"],
                            "policy_arn": p.get("PolicyArn"),
                        }
                        for p in resp.get("AttachedPolicies", [])
                    ],
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as exc:
            return {"error": str(exc)}

    return await asyncio.get_running_loop().run_in_executor(None, _sync)
