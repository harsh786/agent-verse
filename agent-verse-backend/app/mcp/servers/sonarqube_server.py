"""SonarQube MCP server — code quality and security analysis.

Environment:
  SONARQUBE_TOKEN: SonarQube authentication token
  SONARQUBE_URL:   SonarQube server base URL (e.g. https://sonarcloud.io)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "sq_list_projects",
        "description": "List projects in SonarQube",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 50},
                "organization": {"type": "string", "description": "SonarCloud organization key"},
            },
        },
    },
    {
        "name": "sq_get_project_measures",
        "description": "Get quality metrics/measures for a SonarQube project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string"},
                "metric_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [
                        "coverage",
                        "bugs",
                        "vulnerabilities",
                        "code_smells",
                        "duplicated_lines_density",
                        "ncloc",
                    ],
                },
            },
            "required": ["project_key"],
        },
    },
    {
        "name": "sq_list_issues",
        "description": "List code issues (bugs, vulnerabilities, code smells) for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string"},
                "severities": {
                    "type": "string",
                    "description": "Comma-separated: BLOCKER,CRITICAL,MAJOR,MINOR,INFO",
                },
                "types": {
                    "type": "string",
                    "description": "Comma-separated: BUG,VULNERABILITY,CODE_SMELL",
                },
                "page_size": {"type": "integer", "default": 50},
            },
            "required": ["project_key"],
        },
    },
    {
        "name": "sq_get_hotspots",
        "description": "Get security hotspots requiring manual review for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["TO_REVIEW", "REVIEWED"],
                    "default": "TO_REVIEW",
                },
                "page_size": {"type": "integer", "default": 50},
            },
            "required": ["project_key"],
        },
    },
    {
        "name": "sq_run_analysis",
        "description": "Trigger a background task / scanner analysis for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string"},
            },
            "required": ["project_key"],
        },
    },
    {
        "name": "sq_get_quality_gate",
        "description": "Get the quality gate status for a project (passed/failed)",
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string"},
            },
            "required": ["project_key"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("SONARQUBE_TOKEN", "")
    if not token:
        return {"error": "SONARQUBE_TOKEN not configured"}

    sq_url = os.getenv("SONARQUBE_URL", "https://sonarcloud.io")
    base = f"{sq_url}/api"

    async with httpx.AsyncClient(timeout=30.0, auth=(token, "")) as c:
        try:
            if tool_name == "sq_list_projects":
                params: dict[str, Any] = {
                    "ps": arguments.get("page_size", 50),
                    "p": 1,
                }
                if arguments.get("organization"):
                    params["organization"] = arguments["organization"]
                r = await c.get(f"{base}/projects/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "projects": [
                        {
                            "key": p.get("key"),
                            "name": p.get("name"),
                            "visibility": p.get("visibility"),
                            "last_analysis_date": p.get("lastAnalysisDate"),
                        }
                        for p in data.get("components", [])
                    ],
                    "total": data.get("paging", {}).get("total", 0),
                }

            elif tool_name == "sq_get_project_measures":
                metric_keys = ",".join(
                    arguments.get(
                        "metric_keys",
                        ["coverage", "bugs", "vulnerabilities", "code_smells"],
                    )
                )
                r = await c.get(
                    f"{base}/measures/component",
                    params={
                        "component": arguments["project_key"],
                        "metricKeys": metric_keys,
                    },
                )
                r.raise_for_status()
                data = r.json()
                measures = {
                    m.get("metric"): m.get("value")
                    for m in data.get("component", {}).get("measures", [])
                }
                return {"project_key": arguments["project_key"], "measures": measures}

            elif tool_name == "sq_list_issues":
                params = {
                    "componentKeys": arguments["project_key"],
                    "ps": arguments.get("page_size", 50),
                }
                if arguments.get("severities"):
                    params["severities"] = arguments["severities"]
                if arguments.get("types"):
                    params["types"] = arguments["types"]
                r = await c.get(f"{base}/issues/search", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "issues": [
                        {
                            "key": i.get("key"),
                            "severity": i.get("severity"),
                            "type": i.get("type"),
                            "message": i.get("message"),
                            "component": i.get("component"),
                            "line": i.get("line"),
                            "status": i.get("status"),
                        }
                        for i in data.get("issues", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "sq_get_hotspots":
                r = await c.get(
                    f"{base}/hotspots/search",
                    params={
                        "projectKey": arguments["project_key"],
                        "status": arguments.get("status", "TO_REVIEW"),
                        "ps": arguments.get("page_size", 50),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "hotspots": [
                        {
                            "key": h.get("key"),
                            "message": h.get("message"),
                            "component": h.get("component"),
                            "vulnerability_probability": h.get("vulnerabilityProbability"),
                            "status": h.get("status"),
                        }
                        for h in data.get("hotspots", [])
                    ],
                    "total": data.get("paging", {}).get("total", 0),
                }

            elif tool_name == "sq_run_analysis":
                r = await c.post(
                    f"{base}/ce/submit",
                    params={"projectKey": arguments["project_key"], "type": "REPORT"},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "task_id": data.get("taskId"),
                    "project_key": arguments["project_key"],
                    "submitted": True,
                }

            elif tool_name == "sq_get_quality_gate":
                r = await c.get(
                    f"{base}/qualitygates/project_status",
                    params={"projectKey": arguments["project_key"]},
                )
                r.raise_for_status()
                data = r.json()
                pg = data.get("projectStatus", {})
                return {
                    "project_key": arguments["project_key"],
                    "status": pg.get("status"),
                    "conditions": pg.get("conditions", []),
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("sq_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
