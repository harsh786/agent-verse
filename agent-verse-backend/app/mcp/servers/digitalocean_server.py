"""DigitalOcean MCP server — manage DigitalOcean resources via v2 API.

Environment variables:
  DIGITALOCEAN_TOKEN: DigitalOcean personal access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.digitalocean.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "digitalocean_list_droplets",
        "description": "List all DigitalOcean Droplets in the account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
                "tag_name": {"type": "string", "description": "Filter by tag"},
            },
        },
    },
    {
        "name": "digitalocean_create_droplet",
        "description": "Create a new DigitalOcean Droplet",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "region": {"type": "string", "description": "Region slug (e.g. nyc3, fra1)"},
                "size": {"type": "string", "description": "Droplet size slug (e.g. s-1vcpu-1gb)"},
                "image": {"type": "string", "description": "Image slug or ID (e.g. ubuntu-22-04-x64)"},
                "ssh_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SSH key IDs or fingerprints",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "backups": {"type": "boolean", "default": False},
                "monitoring": {"type": "boolean", "default": True},
            },
            "required": ["name", "region", "size", "image"],
        },
    },
    {
        "name": "digitalocean_delete_droplet",
        "description": "Delete a DigitalOcean Droplet",
        "parameters": {
            "type": "object",
            "properties": {
                "droplet_id": {"type": "integer"},
            },
            "required": ["droplet_id"],
        },
    },
    {
        "name": "digitalocean_list_databases",
        "description": "List all managed database clusters",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "digitalocean_list_apps",
        "description": "List all DigitalOcean App Platform apps",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "digitalocean_get_app",
        "description": "Get details of a DigitalOcean App Platform app",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {"type": "string"},
            },
            "required": ["app_id"],
        },
    },
    {
        "name": "digitalocean_list_domains",
        "description": "List all domains managed by DigitalOcean",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "digitalocean_create_domain_record",
        "description": "Create a DNS record for a DigitalOcean domain",
        "parameters": {
            "type": "object",
            "properties": {
                "domain_name": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["A", "AAAA", "CAA", "CNAME", "MX", "NS", "SOA", "SRV", "TXT"],
                },
                "name": {"type": "string", "description": "Subdomain or @ for apex"},
                "data": {"type": "string", "description": "Record data (IP, hostname, etc.)"},
                "priority": {"type": "integer"},
                "port": {"type": "integer"},
                "ttl": {"type": "integer", "default": 1800},
                "weight": {"type": "integer"},
            },
            "required": ["domain_name", "type", "name", "data"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("DIGITALOCEAN_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _call_tool_inner(tool_name, arguments)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {"error": f"HTTP {exc.response.status_code}: {error_body or exc.response.reason_phrase}", "status_code": exc.response.status_code}
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _call_tool_inner(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_API_BASE, headers=_headers(), timeout=30.0) as client:
        if tool_name == "digitalocean_list_droplets":
            params: dict[str, Any] = {
                "page": arguments.get("page", 1),
                "per_page": arguments.get("per_page", 20),
            }
            if arguments.get("tag_name"):
                params["tag_name"] = arguments["tag_name"]
            resp = await client.get("/droplets", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "droplets": [
                    {
                        "id": d["id"],
                        "name": d["name"],
                        "status": d.get("status"),
                        "region": d.get("region", {}).get("slug"),
                        "size": d.get("size_slug"),
                        "vcpus": d.get("vcpus"),
                        "memory": d.get("memory"),
                        "disk": d.get("disk"),
                        "ip_address": next(
                            (n["ip_address"] for n in d.get("networks", {}).get("v4", []) if n.get("type") == "public"),
                            None,
                        ),
                        "tags": d.get("tags", []),
                        "created_at": d.get("created_at"),
                    }
                    for d in data.get("droplets", [])
                ],
                "total": data.get("meta", {}).get("total"),
            }

        elif tool_name == "digitalocean_create_droplet":
            payload: dict[str, Any] = {
                "name": arguments["name"],
                "region": arguments["region"],
                "size": arguments["size"],
                "image": arguments["image"],
                "backups": arguments.get("backups", False),
                "monitoring": arguments.get("monitoring", True),
            }
            if arguments.get("ssh_keys"):
                payload["ssh_keys"] = arguments["ssh_keys"]
            if arguments.get("tags"):
                payload["tags"] = arguments["tags"]
            resp = await client.post("/droplets", json=payload)
            resp.raise_for_status()
            data = resp.json()
            droplet = data.get("droplet", {})
            return {
                "id": droplet.get("id"),
                "name": droplet.get("name"),
                "status": droplet.get("status"),
                "region": droplet.get("region", {}).get("slug"),
            }

        elif tool_name == "digitalocean_delete_droplet":
            droplet_id = arguments["droplet_id"]
            resp = await client.delete(f"/droplets/{droplet_id}")
            if resp.status_code == 204:
                return {"deleted": True, "droplet_id": droplet_id}
            resp.raise_for_status()
            return {"deleted": True}

        elif tool_name == "digitalocean_list_databases":
            resp = await client.get("/databases")
            resp.raise_for_status()
            data = resp.json()
            return {
                "databases": [
                    {
                        "id": db["id"],
                        "name": db["name"],
                        "engine": db.get("engine"),
                        "version": db.get("version"),
                        "status": db.get("status"),
                        "region": db.get("region"),
                        "num_nodes": db.get("num_nodes"),
                        "size": db.get("size"),
                    }
                    for db in data.get("databases", [])
                ]
            }

        elif tool_name == "digitalocean_list_apps":
            params = {
                "page": arguments.get("page", 1),
                "per_page": arguments.get("per_page", 20),
            }
            resp = await client.get("/apps", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "apps": [
                    {
                        "id": a["id"],
                        "spec_name": a.get("spec", {}).get("name"),
                        "default_ingress": a.get("default_ingress"),
                        "phase": a.get("phase"),
                        "updated_at": a.get("updated_at"),
                        "active_deployment_id": a.get("active_deployment", {}).get("id"),
                    }
                    for a in data.get("apps", [])
                ]
            }

        elif tool_name == "digitalocean_get_app":
            app_id = arguments["app_id"]
            resp = await client.get(f"/apps/{app_id}")
            resp.raise_for_status()
            data = resp.json().get("app", {})
            return {
                "id": data["id"],
                "spec": data.get("spec", {}).get("name"),
                "default_ingress": data.get("default_ingress"),
                "phase": data.get("phase"),
                "tier_slug": data.get("tier_slug"),
                "created_at": data.get("created_at"),
                "active_deployment": data.get("active_deployment", {}).get("id"),
            }

        elif tool_name == "digitalocean_list_domains":
            params = {
                "page": arguments.get("page", 1),
                "per_page": arguments.get("per_page", 20),
            }
            resp = await client.get("/domains", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "domains": [
                    {
                        "name": d["name"],
                        "ttl": d.get("ttl"),
                        "zone_file": (d.get("zone_file") or "")[:200],
                    }
                    for d in data.get("domains", [])
                ]
            }

        elif tool_name == "digitalocean_create_domain_record":
            domain_name = arguments["domain_name"]
            payload = {
                "type": arguments["type"],
                "name": arguments["name"],
                "data": arguments["data"],
                "ttl": arguments.get("ttl", 1800),
            }
            for opt in ("priority", "port", "weight"):
                if opt in arguments:
                    payload[opt] = arguments[opt]
            resp = await client.post(f"/domains/{domain_name}/records", json=payload)
            resp.raise_for_status()
            data = resp.json().get("domain_record", {})
            return {
                "id": data.get("id"),
                "type": data.get("type"),
                "name": data.get("name"),
                "data": data.get("data"),
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
