"""Jenkins MCP server — interact with Jenkins CI/CD via REST API.

Environment variables:
  JENKINS_URL:       Jenkins instance base URL (e.g. https://jenkins.example.com)
  JENKINS_USER:      Jenkins username
  JENKINS_API_TOKEN: Jenkins API token (not password)
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

JENKINS_URL = os.getenv("JENKINS_URL", "").rstrip("/")

TOOL_DEFINITIONS = [
    {
        "name": "jenkins_list_jobs",
        "description": "List all Jenkins jobs with their name, URL, and status color",
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Optional folder/view path"},
            },
        },
    },
    {
        "name": "jenkins_get_job",
        "description": "Get detailed information about a Jenkins job",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Job name (use folder/job for nested jobs)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "jenkins_trigger_build",
        "description": "Trigger a new build for a Jenkins job (no parameters)",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Job name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "jenkins_trigger_build_params",
        "description": "Trigger a parameterized Jenkins build",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "params": {
                    "type": "object",
                    "description": "Key-value build parameters",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["name", "params"],
        },
    },
    {
        "name": "jenkins_get_build_status",
        "description": "Get status and details of a specific Jenkins build",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "number": {"type": "integer", "description": "Build number; use -1 for lastBuild"},
            },
            "required": ["name", "number"],
        },
    },
    {
        "name": "jenkins_get_build_log",
        "description": "Get console output log for a Jenkins build",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "number": {"type": "integer"},
                "max_chars": {"type": "integer", "default": 5000, "description": "Max characters to return"},
            },
            "required": ["name", "number"],
        },
    },
    {
        "name": "jenkins_list_builds",
        "description": "List recent builds for a Jenkins job",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
]


def _auth() -> dict[str, str]:
    user = os.getenv("JENKINS_USER", "")
    token = os.getenv("JENKINS_API_TOKEN", "")
    creds = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _job_path(name: str) -> str:
    """Convert 'folder/job' style names to /job/folder/job/ URL segments."""
    parts = name.strip("/").split("/")
    return "/job/" + "/job/".join(parts)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base = JENKINS_URL or os.getenv("JENKINS_URL", "").rstrip("/")
    if not base:
        return {"error": "JENKINS_URL not configured"}

    headers = _auth()

    async with httpx.AsyncClient(timeout=30.0) as client:
        if tool_name == "jenkins_list_jobs":
            folder = arguments.get("folder", "")
            if folder:
                url = f"{base}{_job_path(folder)}/api/json"
            else:
                url = f"{base}/api/json"
            resp = await client.get(
                url,
                params={"tree": "jobs[name,url,color,buildable]"},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "jobs": [
                    {"name": j["name"], "url": j.get("url"), "color": j.get("color")}
                    for j in data.get("jobs", [])
                ]
            }

        elif tool_name == "jenkins_get_job":
            name = arguments["name"]
            url = f"{base}{_job_path(name)}/api/json"
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return {
                "name": data.get("name"),
                "description": data.get("description"),
                "buildable": data.get("buildable"),
                "color": data.get("color"),
                "last_build": data.get("lastBuild"),
                "last_successful_build": data.get("lastSuccessfulBuild"),
                "last_failed_build": data.get("lastFailedBuild"),
            }

        elif tool_name == "jenkins_trigger_build":
            name = arguments["name"]
            url = f"{base}{_job_path(name)}/build"
            resp = await client.post(url, headers=headers)
            if resp.status_code in (201, 200, 302):
                location = resp.headers.get("Location", "")
                return {"triggered": True, "queue_url": location}
            resp.raise_for_status()
            return {"triggered": True}

        elif tool_name == "jenkins_trigger_build_params":
            name = arguments["name"]
            params = arguments.get("params", {})
            url = f"{base}{_job_path(name)}/buildWithParameters"
            resp = await client.post(url, params=params, headers=headers)
            if resp.status_code in (201, 200, 302):
                location = resp.headers.get("Location", "")
                return {"triggered": True, "queue_url": location, "params": params}
            resp.raise_for_status()
            return {"triggered": True, "params": params}

        elif tool_name == "jenkins_get_build_status":
            name = arguments["name"]
            number = arguments["number"]
            build_ref = "lastBuild" if number == -1 else str(number)
            url = f"{base}{_job_path(name)}/{build_ref}/api/json"
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return {
                "number": data.get("number"),
                "result": data.get("result"),
                "building": data.get("building"),
                "duration": data.get("duration"),
                "timestamp": data.get("timestamp"),
                "url": data.get("url"),
                "description": data.get("description"),
            }

        elif tool_name == "jenkins_get_build_log":
            name = arguments["name"]
            number = arguments["number"]
            max_chars = arguments.get("max_chars", 5000)
            build_ref = "lastBuild" if number == -1 else str(number)
            url = f"{base}{_job_path(name)}/{build_ref}/consoleText"
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            log_text = resp.text
            return {
                "log": log_text[-max_chars:] if len(log_text) > max_chars else log_text,
                "truncated": len(log_text) > max_chars,
                "total_chars": len(log_text),
            }

        elif tool_name == "jenkins_list_builds":
            name = arguments["name"]
            url = f"{base}{_job_path(name)}/api/json"
            resp = await client.get(
                url,
                params={"tree": "builds[number,status,result,timestamp,duration,url]"},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "builds": [
                    {
                        "number": b.get("number"),
                        "result": b.get("result"),
                        "timestamp": b.get("timestamp"),
                        "duration": b.get("duration"),
                        "url": b.get("url"),
                    }
                    for b in data.get("builds", [])
                ]
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
