"""AWS S3 MCP server — manage S3 buckets and objects via boto3.

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
        "name": "s3_list_buckets",
        "description": "List all S3 buckets in the AWS account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "s3_list_objects",
        "description": "List objects in an S3 bucket, optionally filtered by prefix",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "prefix": {"type": "string", "default": ""},
                "max_keys": {"type": "integer", "default": 50},
                "delimiter": {"type": "string", "default": "/"},
            },
            "required": ["bucket"],
        },
    },
    {
        "name": "s3_get_object",
        "description": "Get the content of an S3 object",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "version_id": {"type": "string"},
                "max_bytes": {"type": "integer", "default": 102400, "description": "Max bytes to return (default 100KB)"},
            },
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "s3_put_object",
        "description": "Upload content to an S3 object",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "content": {"type": "string", "description": "Text content to upload"},
                "content_type": {"type": "string", "default": "text/plain"},
                "metadata": {"type": "object", "description": "Custom metadata key-value pairs"},
            },
            "required": ["bucket", "key", "content"],
        },
    },
    {
        "name": "s3_delete_object",
        "description": "Delete an object from an S3 bucket",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "version_id": {"type": "string"},
            },
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "s3_generate_presigned_url",
        "description": "Generate a pre-signed URL for an S3 object",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": ["get_object", "put_object"],
                    "default": "get_object",
                },
                "expires_in": {"type": "integer", "default": 3600, "description": "Expiry in seconds"},
            },
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "s3_create_bucket",
        "description": "Create a new S3 bucket",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "region": {"type": "string", "description": "AWS region (overrides AWS_REGION env)"},
                "object_ownership": {
                    "type": "string",
                    "enum": ["BucketOwnerEnforced", "BucketOwnerPreferred", "ObjectWriter"],
                    "default": "BucketOwnerEnforced",
                },
            },
            "required": ["bucket"],
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


def _client() -> Any:
    import boto3  # type: ignore[import]

    return boto3.client(
        "s3",
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
            c = _client()

            if tool_name == "s3_list_buckets":
                resp = c.list_buckets()
                return {
                    "buckets": [
                        {
                            "name": b["Name"],
                            "creation_date": b.get("CreationDate").isoformat() if b.get("CreationDate") else None,
                        }
                        for b in resp.get("Buckets", [])
                    ]
                }

            elif tool_name == "s3_list_objects":
                kwargs: dict[str, Any] = {
                    "Bucket": arguments["bucket"],
                    "MaxKeys": arguments.get("max_keys", 50),
                }
                if arguments.get("prefix"):
                    kwargs["Prefix"] = arguments["prefix"]
                if arguments.get("delimiter"):
                    kwargs["Delimiter"] = arguments["delimiter"]
                resp = c.list_objects_v2(**kwargs)
                return {
                    "objects": [
                        {
                            "key": o["Key"],
                            "size": o.get("Size"),
                            "last_modified": o.get("LastModified").isoformat() if o.get("LastModified") else None,
                            "etag": o.get("ETag", "").strip('"'),
                            "storage_class": o.get("StorageClass"),
                        }
                        for o in resp.get("Contents", [])
                    ],
                    "common_prefixes": [p["Prefix"] for p in resp.get("CommonPrefixes", [])],
                    "is_truncated": resp.get("IsTruncated", False),
                    "key_count": resp.get("KeyCount", 0),
                }

            elif tool_name == "s3_get_object":
                kwargs = {"Bucket": arguments["bucket"], "Key": arguments["key"]}
                if arguments.get("version_id"):
                    kwargs["VersionId"] = arguments["version_id"]
                resp = c.get_object(**kwargs)
                max_bytes = arguments.get("max_bytes", 102400)
                body = resp["Body"].read(max_bytes)
                content_type = resp.get("ContentType", "")
                try:
                    content = body.decode("utf-8")
                    encoding = "utf-8"
                except UnicodeDecodeError:
                    import base64 as b64
                    content = b64.b64encode(body).decode()
                    encoding = "base64"
                return {
                    "bucket": arguments["bucket"],
                    "key": arguments["key"],
                    "content": content,
                    "encoding": encoding,
                    "content_type": content_type,
                    "content_length": resp.get("ContentLength"),
                    "last_modified": resp.get("LastModified").isoformat() if resp.get("LastModified") else None,
                    "truncated": resp.get("ContentLength", 0) > max_bytes,
                }

            elif tool_name == "s3_put_object":
                kwargs = {
                    "Bucket": arguments["bucket"],
                    "Key": arguments["key"],
                    "Body": arguments["content"].encode("utf-8"),
                    "ContentType": arguments.get("content_type", "text/plain"),
                }
                if arguments.get("metadata"):
                    kwargs["Metadata"] = {k: str(v) for k, v in arguments["metadata"].items()}
                resp = c.put_object(**kwargs)
                return {
                    "bucket": arguments["bucket"],
                    "key": arguments["key"],
                    "etag": resp.get("ETag", "").strip('"'),
                    "version_id": resp.get("VersionId"),
                }

            elif tool_name == "s3_delete_object":
                kwargs = {"Bucket": arguments["bucket"], "Key": arguments["key"]}
                if arguments.get("version_id"):
                    kwargs["VersionId"] = arguments["version_id"]
                c.delete_object(**kwargs)
                return {
                    "deleted": True,
                    "bucket": arguments["bucket"],
                    "key": arguments["key"],
                }

            elif tool_name == "s3_generate_presigned_url":
                operation = arguments.get("operation", "get_object")
                url = c.generate_presigned_url(
                    ClientMethod=operation,
                    Params={"Bucket": arguments["bucket"], "Key": arguments["key"]},
                    ExpiresIn=arguments.get("expires_in", 3600),
                )
                return {
                    "url": url,
                    "bucket": arguments["bucket"],
                    "key": arguments["key"],
                    "expires_in": arguments.get("expires_in", 3600),
                    "operation": operation,
                }

            elif tool_name == "s3_create_bucket":
                bucket = arguments["bucket"]
                region = arguments.get("region") or os.getenv("AWS_REGION", "us-east-1")
                kwargs = {"Bucket": bucket}
                if region != "us-east-1":
                    kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
                c.create_bucket(**kwargs)
                return {"bucket": bucket, "region": region, "created": True}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as exc:
            return {"error": str(exc)}

    return await asyncio.get_running_loop().run_in_executor(None, _sync)
