"""Docker MCP server — interact with Docker Engine and Docker Hub.

Environment variables:
  DOCKER_HOST:         Docker daemon socket or TCP URL (default: unix:///var/run/docker.sock)
  DOCKER_HUB_USERNAME: Docker Hub username (for Hub operations)
  DOCKER_HUB_PASSWORD: Docker Hub password or access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_HUB_API = "https://hub.docker.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "docker_list_containers",
        "description": "List Docker containers on the local daemon",
        "parameters": {
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "default": False, "description": "Include stopped containers"},
                "filters": {"type": "object", "description": "Docker filters (e.g. {status: running})"},
            },
        },
    },
    {
        "name": "docker_inspect_container",
        "description": "Inspect a Docker container",
        "parameters": {
            "type": "object",
            "properties": {
                "container_id": {"type": "string"},
            },
            "required": ["container_id"],
        },
    },
    {
        "name": "docker_container_logs",
        "description": "Get logs from a Docker container",
        "parameters": {
            "type": "object",
            "properties": {
                "container_id": {"type": "string"},
                "tail": {"type": "string", "default": "100", "description": "Number of lines (or 'all')"},
                "timestamps": {"type": "boolean", "default": False},
            },
            "required": ["container_id"],
        },
    },
    {
        "name": "docker_list_images",
        "description": "List Docker images on the local daemon",
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {"type": "object"},
            },
        },
    },
    {
        "name": "docker_pull_image",
        "description": "Pull a Docker image from a registry",
        "parameters": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Image name with optional tag (e.g. nginx:latest)"},
            },
            "required": ["image"],
        },
    },
    {
        "name": "docker_hub_search",
        "description": "Search Docker Hub for images",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "page_size": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "docker_hub_list_tags",
        "description": "List available tags for a Docker Hub image",
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "Image repository (e.g. library/nginx or org/repo)"},
                "page_size": {"type": "integer", "default": 20},
            },
            "required": ["repository"],
        },
    },
    {
        "name": "docker_list_volumes",
        "description": "List Docker volumes on the local daemon",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "docker_list_networks",
        "description": "List Docker networks on the local daemon",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _docker_unix_socket() -> str:
    """Return the Docker host socket path."""
    return os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")


async def _docker_request(
    method: str,
    path: str,
    params: dict | None = None,
    json: dict | None = None,
) -> Any:
    """Make a request to the Docker Engine API via unix socket or TCP."""
    docker_host = _docker_unix_socket()

    if docker_host.startswith("unix://"):
        socket_path = docker_host[len("unix://"):]
        transport = httpx.AsyncHTTPTransport(uds=socket_path)
        base = "http://localhost"
    else:
        transport = httpx.AsyncHTTPTransport()
        base = docker_host

    async with httpx.AsyncClient(transport=transport, base_url=base, timeout=30.0) as client:
        resp = await client.request(method, f"/v1.44{path}", params=params, json=json)
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "docker_list_containers":
        params: dict[str, Any] = {}
        if arguments.get("all"):
            params["all"] = "true"
        if arguments.get("filters"):
            import json as _json
            params["filters"] = _json.dumps(arguments["filters"])
        try:
            data = await _docker_request("GET", "/containers/json", params=params)
            return {
                "containers": [
                    {
                        "id": c["Id"][:12],
                        "names": c.get("Names", []),
                        "image": c.get("Image"),
                        "status": c.get("Status"),
                        "state": c.get("State"),
                        "ports": c.get("Ports", []),
                        "created": c.get("Created"),
                    }
                    for c in data
                ]
            }
        except Exception as exc:
            return {"error": str(exc)}

    elif tool_name == "docker_inspect_container":
        try:
            cid = arguments["container_id"]
            data = await _docker_request("GET", f"/containers/{cid}/json")
            return {
                "id": data["Id"][:12],
                "name": data.get("Name", "").lstrip("/"),
                "image": data.get("Config", {}).get("Image"),
                "state": data.get("State"),
                "network": data.get("NetworkSettings", {}).get("Networks"),
                "mounts": data.get("Mounts", []),
                "env": data.get("Config", {}).get("Env", []),
            }
        except Exception as exc:
            return {"error": str(exc)}

    elif tool_name == "docker_container_logs":
        try:
            cid = arguments["container_id"]
            params = {
                "tail": arguments.get("tail", "100"),
                "stdout": "true",
                "stderr": "true",
                "timestamps": "true" if arguments.get("timestamps") else "false",
            }
            data = await _docker_request("GET", f"/containers/{cid}/logs", params=params)
            # Strip Docker multiplexing stream headers (8-byte header per chunk)
            if isinstance(data, str):
                cleaned = []
                i = 0
                raw = data.encode("latin-1")
                while i < len(raw):
                    if i + 8 <= len(raw):
                        length = int.from_bytes(raw[i + 4:i + 8], "big")
                        cleaned.append(raw[i + 8:i + 8 + length].decode("utf-8", errors="replace"))
                        i += 8 + length
                    else:
                        break
                return {"logs": "".join(cleaned)}
            return {"logs": str(data)}
        except Exception as exc:
            return {"error": str(exc)}

    elif tool_name == "docker_list_images":
        try:
            params = {}
            if arguments.get("filters"):
                import json as _json
                params["filters"] = _json.dumps(arguments["filters"])
            data = await _docker_request("GET", "/images/json", params=params)
            return {
                "images": [
                    {
                        "id": img["Id"][:19],
                        "repo_tags": img.get("RepoTags", []),
                        "size": img.get("Size"),
                        "created": img.get("Created"),
                    }
                    for img in data
                ]
            }
        except Exception as exc:
            return {"error": str(exc)}

    elif tool_name == "docker_pull_image":
        try:
            image = arguments["image"]
            # fromImage and tag params
            if ":" in image:
                from_image, tag = image.rsplit(":", 1)
            else:
                from_image, tag = image, "latest"
            data = await _docker_request(
                "POST", "/images/create",
                params={"fromImage": from_image, "tag": tag},
            )
            return {"pulled": True, "image": image, "output": str(data)[:500]}
        except Exception as exc:
            return {"error": str(exc)}

    elif tool_name == "docker_hub_search":
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_HUB_API}/search/repositories/",
                params={"query": arguments["query"], "page_size": arguments.get("page_size", 10)},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "results": [
                    {
                        "name": r.get("repo_name"),
                        "description": (r.get("short_description") or "")[:200],
                        "stars": r.get("star_count"),
                        "official": r.get("is_official"),
                        "pull_count": r.get("pull_count"),
                    }
                    for r in data.get("results", [])
                ]
            }

    elif tool_name == "docker_hub_list_tags":
        repository = arguments["repository"]
        if "/" not in repository:
            repository = f"library/{repository}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_HUB_API}/repositories/{repository}/tags",
                params={"page_size": arguments.get("page_size", 20)},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "tags": [
                    {
                        "name": t.get("name"),
                        "full_size": t.get("full_size"),
                        "last_updated": t.get("last_updated"),
                        "digest": t.get("digest"),
                    }
                    for t in data.get("results", [])
                ]
            }

    elif tool_name == "docker_list_volumes":
        try:
            data = await _docker_request("GET", "/volumes")
            volumes = data.get("Volumes") or []
            return {
                "volumes": [
                    {
                        "name": v.get("Name"),
                        "driver": v.get("Driver"),
                        "mountpoint": v.get("Mountpoint"),
                        "created_at": v.get("CreatedAt"),
                    }
                    for v in volumes
                ]
            }
        except Exception as exc:
            return {"error": str(exc)}

    elif tool_name == "docker_list_networks":
        try:
            data = await _docker_request("GET", "/networks")
            return {
                "networks": [
                    {
                        "id": n["Id"][:12],
                        "name": n.get("Name"),
                        "driver": n.get("Driver"),
                        "scope": n.get("Scope"),
                        "created": n.get("Created"),
                    }
                    for n in data
                ]
            }
        except Exception as exc:
            return {"error": str(exc)}

    else:
        return {"error": f"Unknown tool: {tool_name}"}
