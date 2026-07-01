"""Terraform Cloud MCP server — workspaces, runs, and variables via API v2.

Environment variables:
  TERRAFORM_TOKEN: Terraform Cloud API token
  TERRAFORM_ORG: Default organization name (used when org is not supplied per-call)

Note: Terraform Cloud uses the JSON:API specification. All request bodies must use
Content-Type: application/vnd.api+json and the 'data' wrapper format.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TERRAFORM_BASE_URL = "https://app.terraform.io/api/v2"
_JSONAPI_CT = "application/vnd.api+json"

TOOL_DEFINITIONS = [
    {
        "name": "terraform_list_workspaces",
        "description": "List all workspaces in a Terraform Cloud organization.",
        "parameters": {
            "type": "object",
            "properties": {
                "org": {
                    "type": "string",
                    "description": "Organization name (defaults to TERRAFORM_ORG env var)",
                },
                "search": {
                    "type": "string",
                    "description": "Filter workspaces by name substring",
                },
                "page_number": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "terraform_get_workspace",
        "description": "Get details of a Terraform Cloud workspace by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {
                    "type": "string",
                    "description": "Workspace ID (ws-xxxxxxxxxxxxxxxx)",
                },
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "terraform_create_workspace",
        "description": "Create a new workspace in a Terraform Cloud organization.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workspace name"},
                "org": {
                    "type": "string",
                    "description": "Organization name (defaults to TERRAFORM_ORG)",
                },
                "description": {"type": "string"},
                "terraform_version": {
                    "type": "string",
                    "description": "Terraform version, e.g. '1.6.0'",
                },
                "auto_apply": {
                    "type": "boolean",
                    "default": False,
                    "description": "Automatically apply successful plans",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Relative path within the repository to use as root",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "terraform_list_runs",
        "description": "List runs for a Terraform Cloud workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "page_number": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 20},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "terraform_get_run",
        "description": "Get details of a specific Terraform Cloud run.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID (run-xxxxxxxxxxxxxxxx)",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "terraform_create_run",
        "description": "Trigger a new plan/apply run in a Terraform Cloud workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "message": {
                    "type": "string",
                    "description": "Human-readable message for this run",
                },
                "is_destroy": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, this is a destroy run",
                },
                "auto_apply": {
                    "type": "boolean",
                    "description": "Override auto-apply setting for this run",
                },
                "plan_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, produce a speculative plan without applying",
                },
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "terraform_apply_run",
        "description": "Confirm and apply a Terraform Cloud run that is waiting for approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "comment": {"type": "string", "description": "Optional approval comment"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "terraform_discard_run",
        "description": "Discard a Terraform Cloud run that is waiting for approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "comment": {"type": "string", "description": "Optional discard comment"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "terraform_list_variables",
        "description": "List variables (Terraform and environment) for a workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "terraform_set_variable",
        "description": "Create or update a workspace variable in Terraform Cloud.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "key": {"type": "string", "description": "Variable key"},
                "value": {"type": "string", "description": "Variable value"},
                "category": {
                    "type": "string",
                    "enum": ["terraform", "env"],
                    "default": "terraform",
                    "description": "'terraform' for .tfvars-style, 'env' for environment variables",
                },
                "sensitive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Mark the value as sensitive (write-only after creation)",
                },
                "description": {"type": "string"},
                "hcl": {
                    "type": "boolean",
                    "default": False,
                    "description": "Parse the value as HCL",
                },
            },
            "required": ["workspace_id", "key", "value"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("TERRAFORM_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": _JSONAPI_CT,
        "Accept": _JSONAPI_CT,
    }


def _org(arguments: dict[str, Any]) -> str:
    return arguments.get("org") or os.getenv("TERRAFORM_ORG", "")


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    token = os.getenv("TERRAFORM_TOKEN", "")
    if not token:
        return {"error": "TERRAFORM_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=TERRAFORM_BASE_URL, headers=_headers(), timeout=30.0
        ) as client:
            if tool_name == "terraform_list_workspaces":
                org = _org(arguments)
                if not org:
                    return {"error": "Organization not specified and TERRAFORM_ORG not configured"}
                params: dict[str, Any] = {
                    "page[number]": arguments.get("page_number", 1),
                    "page[size]": arguments.get("page_size", 20),
                }
                if arguments.get("search"):
                    params["search[name]"] = arguments["search"]
                resp = await client.get(
                    f"/organizations/{org}/workspaces", params=params
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "total": (data.get("meta") or {}).get("pagination", {}).get("total-count", 0),
                    "workspaces": [
                        {
                            "id": ws["id"],
                            "name": (ws.get("attributes") or {}).get("name", ""),
                            "status": (ws.get("attributes") or {}).get("latest-run", {}).get("status", ""),
                            "terraform_version": (ws.get("attributes") or {}).get("terraform-version", ""),
                            "locked": (ws.get("attributes") or {}).get("locked", False),
                            "created_at": (ws.get("attributes") or {}).get("created-at", ""),
                        }
                        for ws in data.get("data", [])
                    ],
                }

            elif tool_name == "terraform_get_workspace":
                resp = await client.get(f"/workspaces/{arguments['workspace_id']}")
                resp.raise_for_status()
                ws = resp.json().get("data", {})
                attrs = ws.get("attributes") or {}
                return {
                    "id": ws.get("id", ""),
                    "name": attrs.get("name", ""),
                    "description": attrs.get("description", ""),
                    "terraform_version": attrs.get("terraform-version", ""),
                    "locked": attrs.get("locked", False),
                    "auto_apply": attrs.get("auto-apply", False),
                    "working_directory": attrs.get("working-directory", ""),
                    "execution_mode": attrs.get("execution-mode", ""),
                    "created_at": attrs.get("created-at", ""),
                    "updated_at": attrs.get("updated-at", ""),
                }

            elif tool_name == "terraform_create_workspace":
                org = _org(arguments)
                if not org:
                    return {"error": "Organization not specified and TERRAFORM_ORG not configured"}
                attrs: dict[str, Any] = {"name": arguments["name"]}
                if arguments.get("description"):
                    attrs["description"] = arguments["description"]
                if arguments.get("terraform_version"):
                    attrs["terraform-version"] = arguments["terraform_version"]
                if "auto_apply" in arguments:
                    attrs["auto-apply"] = arguments["auto_apply"]
                if arguments.get("working_directory"):
                    attrs["working-directory"] = arguments["working_directory"]
                payload = {"data": {"type": "workspaces", "attributes": attrs}}
                resp = await client.post(
                    f"/organizations/{org}/workspaces", json=payload
                )
                resp.raise_for_status()
                ws = resp.json().get("data", {})
                ws_attrs = ws.get("attributes") or {}
                return {
                    "id": ws.get("id", ""),
                    "name": ws_attrs.get("name", ""),
                    "created_at": ws_attrs.get("created-at", ""),
                }

            elif tool_name == "terraform_list_runs":
                workspace_id = arguments["workspace_id"]
                params = {
                    "page[number]": arguments.get("page_number", 1),
                    "page[size]": arguments.get("page_size", 20),
                }
                resp = await client.get(
                    f"/workspaces/{workspace_id}/runs", params=params
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "runs": [
                        {
                            "id": r["id"],
                            "status": (r.get("attributes") or {}).get("status", ""),
                            "is_destroy": (r.get("attributes") or {}).get("is-destroy", False),
                            "message": (r.get("attributes") or {}).get("message", ""),
                            "created_at": (r.get("attributes") or {}).get("created-at", ""),
                        }
                        for r in data.get("data", [])
                    ]
                }

            elif tool_name == "terraform_get_run":
                resp = await client.get(f"/runs/{arguments['run_id']}")
                resp.raise_for_status()
                r = resp.json().get("data", {})
                attrs = r.get("attributes") or {}
                return {
                    "id": r.get("id", ""),
                    "status": attrs.get("status", ""),
                    "is_destroy": attrs.get("is-destroy", False),
                    "message": attrs.get("message", ""),
                    "auto_apply": attrs.get("auto-apply", False),
                    "plan_only": attrs.get("plan-only", False),
                    "created_at": attrs.get("created-at", ""),
                    "status_timestamps": attrs.get("status-timestamps", {}),
                }

            elif tool_name == "terraform_create_run":
                workspace_id = arguments["workspace_id"]
                attrs = {}
                if arguments.get("message"):
                    attrs["message"] = arguments["message"]
                if "is_destroy" in arguments:
                    attrs["is-destroy"] = arguments["is_destroy"]
                if "auto_apply" in arguments:
                    attrs["auto-apply"] = arguments["auto_apply"]
                if "plan_only" in arguments:
                    attrs["plan-only"] = arguments["plan_only"]
                payload = {
                    "data": {
                        "type": "runs",
                        "attributes": attrs,
                        "relationships": {
                            "workspace": {
                                "data": {"type": "workspaces", "id": workspace_id}
                            }
                        },
                    }
                }
                resp = await client.post("/runs", json=payload)
                resp.raise_for_status()
                r = resp.json().get("data", {})
                r_attrs = r.get("attributes") or {}
                return {
                    "id": r.get("id", ""),
                    "status": r_attrs.get("status", ""),
                    "created_at": r_attrs.get("created-at", ""),
                }

            elif tool_name == "terraform_apply_run":
                run_id = arguments["run_id"]
                payload: dict[str, Any] = {}
                if arguments.get("comment"):
                    payload["comment"] = arguments["comment"]
                resp = await client.post(f"/runs/{run_id}/actions/apply", json=payload)
                resp.raise_for_status()
                return {"applied": True, "run_id": run_id}

            elif tool_name == "terraform_discard_run":
                run_id = arguments["run_id"]
                payload = {}
                if arguments.get("comment"):
                    payload["comment"] = arguments["comment"]
                resp = await client.post(f"/runs/{run_id}/actions/discard", json=payload)
                resp.raise_for_status()
                return {"discarded": True, "run_id": run_id}

            elif tool_name == "terraform_list_variables":
                workspace_id = arguments["workspace_id"]
                resp = await client.get(f"/workspaces/{workspace_id}/vars")
                resp.raise_for_status()
                data = resp.json()
                return {
                    "variables": [
                        {
                            "id": v["id"],
                            "key": (v.get("attributes") or {}).get("key", ""),
                            "value": (v.get("attributes") or {}).get("value", ""),
                            "category": (v.get("attributes") or {}).get("category", ""),
                            "sensitive": (v.get("attributes") or {}).get("sensitive", False),
                            "hcl": (v.get("attributes") or {}).get("hcl", False),
                            "description": (v.get("attributes") or {}).get("description", ""),
                        }
                        for v in data.get("data", [])
                    ]
                }

            elif tool_name == "terraform_set_variable":
                workspace_id = arguments["workspace_id"]
                attrs = {
                    "key": arguments["key"],
                    "value": arguments["value"],
                    "category": arguments.get("category", "terraform"),
                    "sensitive": arguments.get("sensitive", False),
                    "hcl": arguments.get("hcl", False),
                }
                if arguments.get("description"):
                    attrs["description"] = arguments["description"]
                payload = {
                    "data": {
                        "type": "vars",
                        "attributes": attrs,
                    }
                }
                resp = await client.post(
                    f"/workspaces/{workspace_id}/vars", json=payload
                )
                resp.raise_for_status()
                v = resp.json().get("data", {})
                v_attrs = v.get("attributes") or {}
                return {
                    "id": v.get("id", ""),
                    "key": v_attrs.get("key", ""),
                    "category": v_attrs.get("category", ""),
                    "sensitive": v_attrs.get("sensitive", False),
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
