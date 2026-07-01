"""PagerDuty MCP server — incidents, services, on-call schedules, and escalation policies.

Environment variables:
  PAGERDUTY_API_KEY: PagerDuty REST API v2 key
  PAGERDUTY_FROM_EMAIL: Email address of the PagerDuty user making the request
                        (required by the API for incident create/update operations)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

PAGERDUTY_BASE_URL = "https://api.pagerduty.com"

TOOL_DEFINITIONS = [
    {
        "name": "pagerduty_list_incidents",
        "description": "List PagerDuty incidents. Defaults to triggered and acknowledged.",
        "parameters": {
            "type": "object",
            "properties": {
                "statuses": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["triggered", "acknowledged", "resolved"],
                    },
                    "default": ["triggered", "acknowledged"],
                    "description": "Filter by incident status",
                },
                "service_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by service IDs",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["high", "low"],
                    "description": "Filter by urgency",
                },
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
                "sort_by": {
                    "type": "string",
                    "default": "created_at:desc",
                    "description": "Sort field and direction, e.g. 'created_at:desc'",
                },
            },
        },
    },
    {
        "name": "pagerduty_get_incident",
        "description": "Get full details of a PagerDuty incident by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "pagerduty_create_incident",
        "description": "Create a new PagerDuty incident. Requires PAGERDUTY_FROM_EMAIL.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Incident title"},
                "service_id": {
                    "type": "string",
                    "description": "PagerDuty service ID to associate the incident with",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["high", "low"],
                    "default": "high",
                },
                "body": {
                    "type": "string",
                    "description": "Detailed incident description",
                },
                "escalation_policy_id": {
                    "type": "string",
                    "description": "Override the escalation policy for this incident",
                },
                "priority_id": {
                    "type": "string",
                    "description": "Priority ID to assign (P1, P2 etc.)",
                },
            },
            "required": ["title", "service_id"],
        },
    },
    {
        "name": "pagerduty_acknowledge_incident",
        "description": "Acknowledge a PagerDuty incident. Requires PAGERDUTY_FROM_EMAIL.",
        "parameters": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "pagerduty_resolve_incident",
        "description": "Resolve a PagerDuty incident. Requires PAGERDUTY_FROM_EMAIL.",
        "parameters": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "pagerduty_add_note",
        "description": "Add a note (comment) to an existing PagerDuty incident.",
        "parameters": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "content": {"type": "string", "description": "Note content"},
            },
            "required": ["incident_id", "content"],
        },
    },
    {
        "name": "pagerduty_list_services",
        "description": "List PagerDuty services, optionally filtered by team or name.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Filter services by name",
                },
                "team_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by team IDs",
                },
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "pagerduty_list_on_calls",
        "description": "List who is currently on-call across all escalation policies.",
        "parameters": {
            "type": "object",
            "properties": {
                "schedule_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by schedule IDs",
                },
                "escalation_policy_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by escalation policy IDs",
                },
                "user_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by user IDs",
                },
            },
        },
    },
    {
        "name": "pagerduty_list_escalation_policies",
        "description": "List PagerDuty escalation policies.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Filter escalation policies by name",
                },
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
]


def _headers(include_from: bool = False) -> dict[str, str]:
    api_key = os.getenv("PAGERDUTY_API_KEY", "")
    h: dict[str, str] = {
        "Authorization": f"Token token={api_key}",
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json",
    }
    if include_from:
        from_email = os.getenv("PAGERDUTY_FROM_EMAIL", "")
        if from_email:
            h["From"] = from_email
    return h


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("PAGERDUTY_API_KEY", "")
    if not api_key:
        return {"error": "PAGERDUTY_API_KEY not configured"}

    _mutating = {
        "pagerduty_create_incident",
        "pagerduty_acknowledge_incident",
        "pagerduty_resolve_incident",
        "pagerduty_add_note",
    }
    needs_from = tool_name in _mutating

    try:
        async with httpx.AsyncClient(
            base_url=PAGERDUTY_BASE_URL,
            headers=_headers(include_from=needs_from),
            timeout=30.0,
        ) as client:
            if tool_name == "pagerduty_list_incidents":
                statuses = arguments.get("statuses", ["triggered", "acknowledged"])
                params: dict[str, Any] = {
                    "statuses[]": statuses,
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                    "sort_by": arguments.get("sort_by", "created_at:desc"),
                }
                if arguments.get("service_ids"):
                    params["service_ids[]"] = arguments["service_ids"]
                if arguments.get("urgency"):
                    params["urgencies[]"] = [arguments["urgency"]]
                resp = await client.get("/incidents", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "total": data.get("total", 0),
                    "incidents": [
                        {
                            "id": inc["id"],
                            "incident_number": inc.get("incident_number"),
                            "title": inc.get("title", ""),
                            "status": inc.get("status", ""),
                            "urgency": inc.get("urgency", ""),
                            "service": (inc.get("service") or {}).get("summary", ""),
                            "created_at": inc.get("created_at", ""),
                            "html_url": inc.get("html_url", ""),
                        }
                        for inc in data.get("incidents", [])
                    ],
                }

            elif tool_name == "pagerduty_get_incident":
                resp = await client.get(f"/incidents/{arguments['incident_id']}")
                resp.raise_for_status()
                inc = resp.json().get("incident", {})
                return {
                    "id": inc.get("id", ""),
                    "incident_number": inc.get("incident_number"),
                    "title": inc.get("title", ""),
                    "status": inc.get("status", ""),
                    "urgency": inc.get("urgency", ""),
                    "description": (inc.get("body") or {}).get("details", ""),
                    "service": (inc.get("service") or {}).get("summary", ""),
                    "assigned_to": [
                        (a.get("assignee") or {}).get("summary", "")
                        for a in inc.get("assignments", [])
                    ],
                    "created_at": inc.get("created_at", ""),
                    "updated_at": inc.get("updated_at", ""),
                    "resolved_at": inc.get("resolved_at", ""),
                    "html_url": inc.get("html_url", ""),
                }

            elif tool_name == "pagerduty_create_incident":
                from_email = os.getenv("PAGERDUTY_FROM_EMAIL", "")
                if not from_email:
                    return {"error": "PAGERDUTY_FROM_EMAIL not configured (required for incident creation)"}
                incident: dict[str, Any] = {
                    "type": "incident",
                    "title": arguments["title"],
                    "service": {"id": arguments["service_id"], "type": "service_reference"},
                    "urgency": arguments.get("urgency", "high"),
                }
                if arguments.get("body"):
                    incident["body"] = {
                        "type": "incident_body",
                        "details": arguments["body"],
                    }
                if arguments.get("escalation_policy_id"):
                    incident["escalation_policy"] = {
                        "id": arguments["escalation_policy_id"],
                        "type": "escalation_policy_reference",
                    }
                if arguments.get("priority_id"):
                    incident["priority"] = {
                        "id": arguments["priority_id"],
                        "type": "priority_reference",
                    }
                resp = await client.post("/incidents", json={"incident": incident})
                resp.raise_for_status()
                inc = resp.json().get("incident", {})
                return {
                    "id": inc.get("id", ""),
                    "incident_number": inc.get("incident_number"),
                    "title": inc.get("title", ""),
                    "status": inc.get("status", ""),
                    "html_url": inc.get("html_url", ""),
                }

            elif tool_name == "pagerduty_acknowledge_incident":
                incident_id = arguments["incident_id"]
                payload = {
                    "incident": {
                        "type": "incident_reference",
                        "status": "acknowledged",
                    }
                }
                resp = await client.put(f"/incidents/{incident_id}", json=payload)
                resp.raise_for_status()
                inc = resp.json().get("incident", {})
                return {
                    "id": inc.get("id", incident_id),
                    "status": inc.get("status", "acknowledged"),
                    "acknowledged": True,
                }

            elif tool_name == "pagerduty_resolve_incident":
                incident_id = arguments["incident_id"]
                payload = {
                    "incident": {
                        "type": "incident_reference",
                        "status": "resolved",
                    }
                }
                resp = await client.put(f"/incidents/{incident_id}", json=payload)
                resp.raise_for_status()
                inc = resp.json().get("incident", {})
                return {
                    "id": inc.get("id", incident_id),
                    "status": inc.get("status", "resolved"),
                    "resolved": True,
                }

            elif tool_name == "pagerduty_add_note":
                incident_id = arguments["incident_id"]
                payload = {"note": {"content": arguments["content"]}}
                resp = await client.post(
                    f"/incidents/{incident_id}/notes", json=payload
                )
                resp.raise_for_status()
                note = resp.json().get("note", {})
                return {
                    "id": note.get("id", ""),
                    "content": note.get("content", ""),
                    "created_at": note.get("created_at", ""),
                }

            elif tool_name == "pagerduty_list_services":
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if arguments.get("query"):
                    params["query"] = arguments["query"]
                if arguments.get("team_ids"):
                    params["team_ids[]"] = arguments["team_ids"]
                resp = await client.get("/services", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "total": data.get("total", 0),
                    "services": [
                        {
                            "id": svc["id"],
                            "name": svc.get("name", ""),
                            "description": svc.get("description", ""),
                            "status": svc.get("status", ""),
                            "escalation_policy": (svc.get("escalation_policy") or {}).get("summary", ""),
                            "html_url": svc.get("html_url", ""),
                        }
                        for svc in data.get("services", [])
                    ],
                }

            elif tool_name == "pagerduty_list_on_calls":
                params = {}
                if arguments.get("schedule_ids"):
                    params["schedule_ids[]"] = arguments["schedule_ids"]
                if arguments.get("escalation_policy_ids"):
                    params["escalation_policy_ids[]"] = arguments["escalation_policy_ids"]
                if arguments.get("user_ids"):
                    params["user_ids[]"] = arguments["user_ids"]
                resp = await client.get("/oncalls", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "oncalls": [
                        {
                            "escalation_level": oc.get("escalation_level"),
                            "user": (oc.get("user") or {}).get("summary", ""),
                            "user_id": (oc.get("user") or {}).get("id", ""),
                            "schedule": (oc.get("schedule") or {}).get("summary", ""),
                            "escalation_policy": (oc.get("escalation_policy") or {}).get("summary", ""),
                            "start": oc.get("start", ""),
                            "end": oc.get("end", ""),
                        }
                        for oc in data.get("oncalls", [])
                    ]
                }

            elif tool_name == "pagerduty_list_escalation_policies":
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if arguments.get("query"):
                    params["query"] = arguments["query"]
                resp = await client.get("/escalation_policies", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "total": data.get("total", 0),
                    "escalation_policies": [
                        {
                            "id": ep["id"],
                            "name": ep.get("name", ""),
                            "description": ep.get("description", ""),
                            "num_loops": ep.get("num_loops", 0),
                            "services": [
                                s.get("summary", "") for s in ep.get("services", [])
                            ],
                            "html_url": ep.get("html_url", ""),
                        }
                        for ep in data.get("escalation_policies", [])
                    ],
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {
            "error": f"HTTP {exc.response.status_code}: {error_body}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}
