"""AWS MCP server — unified AWS operations across S3, Lambda, EC2, CloudWatch, IAM.

Environment variables:
  AWS_ACCESS_KEY_ID: AWS access key
  AWS_SECRET_ACCESS_KEY: AWS secret key
  AWS_DEFAULT_REGION: AWS region (default: us-east-1)

This server provides a unified interface to common AWS services.
For service-specific features, register aws_s3, aws_lambda, etc. separately.
"""
from __future__ import annotations

import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    # S3
    {
        "name": "aws_s3_list_buckets",
        "description": "List all S3 buckets",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "aws_s3_list_objects",
        "description": "List objects in an S3 bucket",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "prefix": {"type": "string", "default": ""},
            },
            "required": ["bucket"],
        },
    },
    {
        "name": "aws_s3_get_object",
        "description": "Get an object from S3 (returns content preview)",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
            },
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "aws_s3_put_object",
        "description": "Upload text content to S3",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["bucket", "key", "content"],
        },
    },
    # Lambda
    {
        "name": "aws_lambda_list_functions",
        "description": "List Lambda functions",
        "parameters": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "default": ""},
            },
        },
    },
    {
        "name": "aws_lambda_invoke",
        "description": "Invoke a Lambda function",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["function_name"],
        },
    },
    # CloudWatch
    {
        "name": "aws_cloudwatch_get_metrics",
        "description": "Get CloudWatch metric statistics",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "metric_name": {"type": "string"},
                "period": {"type": "integer", "default": 300},
            },
            "required": ["namespace", "metric_name"],
        },
    },
    {
        "name": "aws_cloudwatch_list_alarms",
        "description": "List CloudWatch alarms",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["OK", "ALARM", "INSUFFICIENT_DATA"],
                },
            },
        },
    },
    # EC2
    {
        "name": "aws_ec2_list_instances",
        "description": "List EC2 instances",
        "parameters": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "default": ""},
                "state": {"type": "string", "default": "running"},
            },
        },
    },
    # IAM
    {
        "name": "aws_iam_list_users",
        "description": "List IAM users",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "aws_iam_list_roles",
        "description": "List IAM roles",
        "parameters": {"type": "object", "properties": {}},
    },
]


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    region = arguments.get("region") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    if not access_key or not secret_key:
        return {"error": "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set"}

    try:
        import boto3  # type: ignore[import-untyped]

        if tool_name == "aws_s3_list_buckets":
            s3 = boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            resp = s3.list_buckets()
            return {
                "buckets": [
                    {"name": b["Name"], "created": str(b["CreationDate"])}
                    for b in resp.get("Buckets", [])
                ]
            }

        elif tool_name == "aws_s3_list_objects":
            s3 = boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            resp = s3.list_objects_v2(
                Bucket=arguments["bucket"],
                Prefix=arguments.get("prefix", ""),
                MaxKeys=100,
            )
            return {
                "objects": [
                    {
                        "key": o["Key"],
                        "size": o["Size"],
                        "modified": str(o["LastModified"]),
                    }
                    for o in resp.get("Contents", [])
                ],
                "count": resp.get("KeyCount", 0),
            }

        elif tool_name == "aws_s3_get_object":
            s3 = boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            resp = s3.get_object(Bucket=arguments["bucket"], Key=arguments["key"])
            content = resp["Body"].read(4096).decode("utf-8", errors="replace")
            return {
                "content": content,
                "content_type": resp.get("ContentType", ""),
                "size": resp["ContentLength"],
            }

        elif tool_name == "aws_s3_put_object":
            s3 = boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            s3.put_object(
                Bucket=arguments["bucket"],
                Key=arguments["key"],
                Body=arguments["content"].encode(),
            )
            return {
                "success": True,
                "bucket": arguments["bucket"],
                "key": arguments["key"],
            }

        elif tool_name == "aws_lambda_list_functions":
            lam = boto3.client(
                "lambda",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            resp = lam.list_functions(MaxItems=50)
            return {
                "functions": [
                    {
                        "name": f["FunctionName"],
                        "runtime": f.get("Runtime", ""),
                        "state": f.get("State", ""),
                    }
                    for f in resp.get("Functions", [])
                ]
            }

        elif tool_name == "aws_lambda_invoke":
            import json as _json

            lam = boto3.client(
                "lambda",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            payload = _json.dumps(arguments.get("payload", {})).encode()
            resp = lam.invoke(FunctionName=arguments["function_name"], Payload=payload)
            result = resp["Payload"].read().decode()
            return {
                "status_code": resp.get("StatusCode"),
                "result": result[:2000],
                "function_error": resp.get("FunctionError"),
            }

        elif tool_name == "aws_cloudwatch_get_metrics":
            import datetime

            cw = boto3.client(
                "cloudwatch",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            end = datetime.datetime.utcnow()
            start = end - datetime.timedelta(hours=1)
            resp = cw.get_metric_statistics(
                Namespace=arguments["namespace"],
                MetricName=arguments["metric_name"],
                StartTime=start,
                EndTime=end,
                Period=arguments.get("period", 300),
                Statistics=["Average", "Sum", "Maximum"],
            )
            return {
                "datapoints": [
                    {
                        "time": str(d["Timestamp"]),
                        "average": d.get("Average"),
                        "sum": d.get("Sum"),
                    }
                    for d in resp.get("Datapoints", [])
                ]
            }

        elif tool_name == "aws_cloudwatch_list_alarms":
            cw = boto3.client(
                "cloudwatch",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            kwargs: dict[str, Any] = {}
            if arguments.get("state"):
                kwargs["StateValue"] = arguments["state"]
            resp = cw.describe_alarms(**kwargs)
            return {
                "alarms": [
                    {
                        "name": a["AlarmName"],
                        "state": a["StateValue"],
                        "metric": a.get("MetricName", ""),
                    }
                    for a in resp.get("MetricAlarms", [])
                ]
            }

        elif tool_name == "aws_ec2_list_instances":
            ec2 = boto3.client(
                "ec2",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            filters = [
                {
                    "Name": "instance-state-name",
                    "Values": [arguments.get("state", "running")],
                }
            ]
            resp = ec2.describe_instances(Filters=filters)
            instances = []
            for r in resp.get("Reservations", []):
                for i in r.get("Instances", []):
                    name = next(
                        (t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"),
                        "",
                    )
                    instances.append(
                        {
                            "id": i["InstanceId"],
                            "name": name,
                            "type": i["InstanceType"],
                            "state": i["State"]["Name"],
                        }
                    )
            return {"instances": instances}

        elif tool_name == "aws_iam_list_users":
            iam = boto3.client(
                "iam",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            resp = iam.list_users(MaxItems=100)
            return {
                "users": [
                    {
                        "name": u["UserName"],
                        "id": u["UserId"],
                        "created": str(u["CreateDate"]),
                    }
                    for u in resp.get("Users", [])
                ]
            }

        elif tool_name == "aws_iam_list_roles":
            iam = boto3.client(
                "iam",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            resp = iam.list_roles(MaxItems=100)
            return {
                "roles": [
                    {"name": r["RoleName"], "arn": r["Arn"]}
                    for r in resp.get("Roles", [])
                ]
            }

        return {"error": f"Unknown tool: {tool_name}"}

    except ImportError:
        return {"error": "boto3 not installed. Run: uv add boto3"}
    except Exception as exc:
        logger.error("aws_call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}
