"""Kubernetes MCP server — interact with Kubernetes clusters via REST API.

Environment variables:
  KUBE_API_SERVER: Kubernetes API server URL (e.g. https://k8s.example.com:6443)
  KUBE_TOKEN:      Service account bearer token
  KUBE_NAMESPACE:  Default namespace (default: default)
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

KUBE_API = os.getenv("KUBE_API_SERVER", "").rstrip("/")
_DEFAULT_NS = os.getenv("KUBE_NAMESPACE", "default")

TOOL_DEFINITIONS = [
    {
        "name": "k8s_list_pods",
        "description": "List pods in a Kubernetes namespace",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Namespace (defaults to KUBE_NAMESPACE env)"},
                "label_selector": {"type": "string", "description": "Label selector filter"},
                "field_selector": {"type": "string"},
            },
        },
    },
    {
        "name": "k8s_get_pod",
        "description": "Get details of a specific Kubernetes pod",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "k8s_delete_pod",
        "description": "Delete a Kubernetes pod (triggers restart if managed by a controller)",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
                "grace_period_seconds": {"type": "integer", "default": 30},
            },
            "required": ["name"],
        },
    },
    {
        "name": "k8s_list_deployments",
        "description": "List Deployments in a Kubernetes namespace",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "label_selector": {"type": "string"},
            },
        },
    },
    {
        "name": "k8s_scale_deployment",
        "description": "Scale a Kubernetes Deployment to the specified number of replicas",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "replicas": {"type": "integer", "minimum": 0},
                "namespace": {"type": "string"},
            },
            "required": ["name", "replicas"],
        },
    },
    {
        "name": "k8s_restart_deployment",
        "description": "Perform a rolling restart of a Kubernetes Deployment",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "k8s_get_logs",
        "description": "Get logs from a Kubernetes pod or specific container",
        "parameters": {
            "type": "object",
            "properties": {
                "pod_name": {"type": "string"},
                "namespace": {"type": "string"},
                "container": {"type": "string"},
                "tail_lines": {"type": "integer", "default": 100},
                "previous": {"type": "boolean", "default": False, "description": "Get logs from previous container instance"},
            },
            "required": ["pod_name"],
        },
    },
    {
        "name": "k8s_list_services",
        "description": "List Services in a Kubernetes namespace",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "label_selector": {"type": "string"},
            },
        },
    },
    {
        "name": "k8s_list_namespaces",
        "description": "List all Kubernetes namespaces in the cluster",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "k8s_apply_manifest",
        "description": "Create or update a Kubernetes resource from a manifest dict",
        "parameters": {
            "type": "object",
            "properties": {
                "manifest": {
                    "type": "object",
                    "description": "Kubernetes resource manifest (apiVersion, kind, metadata, spec)",
                },
                "namespace": {"type": "string"},
            },
            "required": ["manifest"],
        },
    },
]


def _kube_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('KUBE_TOKEN', '')}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _ns(arguments: dict[str, Any]) -> str:
    return arguments.get("namespace") or os.getenv("KUBE_NAMESPACE", "default")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api = KUBE_API or os.getenv("KUBE_API_SERVER", "").rstrip("/")
    if not api:
        return {"error": "KUBE_API_SERVER not configured"}

    # Most clusters use self-signed certs; allow env-override for CA
    verify: bool | str = os.getenv("KUBE_CA_CERT", True)
    if verify is True and os.getenv("KUBE_INSECURE_SKIP_VERIFY", "").lower() == "true":
        verify = False

    async with httpx.AsyncClient(
        base_url=api, headers=_kube_headers(), timeout=30.0, verify=verify
    ) as client:
        ns = _ns(arguments)

        if tool_name == "k8s_list_pods":
            params: dict[str, Any] = {}
            if arguments.get("label_selector"):
                params["labelSelector"] = arguments["label_selector"]
            if arguments.get("field_selector"):
                params["fieldSelector"] = arguments["field_selector"]
            resp = await client.get(f"/api/v1/namespaces/{ns}/pods", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "pods": [
                    {
                        "name": p["metadata"]["name"],
                        "namespace": p["metadata"]["namespace"],
                        "phase": p.get("status", {}).get("phase"),
                        "node": p.get("spec", {}).get("nodeName"),
                        "ip": p.get("status", {}).get("podIP"),
                        "ready": all(
                            c.get("ready", False)
                            for c in p.get("status", {}).get("containerStatuses", [])
                        ),
                        "restart_count": sum(
                            c.get("restartCount", 0)
                            for c in p.get("status", {}).get("containerStatuses", [])
                        ),
                    }
                    for p in data.get("items", [])
                ]
            }

        elif tool_name == "k8s_get_pod":
            name = arguments["name"]
            resp = await client.get(f"/api/v1/namespaces/{ns}/pods/{name}")
            resp.raise_for_status()
            data = resp.json()
            return {
                "name": data["metadata"]["name"],
                "namespace": data["metadata"]["namespace"],
                "phase": data.get("status", {}).get("phase"),
                "conditions": data.get("status", {}).get("conditions", []),
                "containers": [
                    {
                        "name": c["name"],
                        "image": c.get("image"),
                        "ready": c.get("ready"),
                        "restart_count": c.get("restartCount"),
                    }
                    for c in data.get("status", {}).get("containerStatuses", [])
                ],
                "node": data.get("spec", {}).get("nodeName"),
                "start_time": data.get("status", {}).get("startTime"),
            }

        elif tool_name == "k8s_delete_pod":
            name = arguments["name"]
            grace = arguments.get("grace_period_seconds", 30)
            resp = await client.delete(
                f"/api/v1/namespaces/{ns}/pods/{name}",
                params={"gracePeriodSeconds": grace},
            )
            resp.raise_for_status()
            return {"deleted": True, "pod": name, "namespace": ns}

        elif tool_name == "k8s_list_deployments":
            params = {}
            if arguments.get("label_selector"):
                params["labelSelector"] = arguments["label_selector"]
            resp = await client.get(f"/apis/apps/v1/namespaces/{ns}/deployments", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "deployments": [
                    {
                        "name": d["metadata"]["name"],
                        "namespace": d["metadata"]["namespace"],
                        "replicas": d.get("spec", {}).get("replicas"),
                        "ready_replicas": d.get("status", {}).get("readyReplicas", 0),
                        "available_replicas": d.get("status", {}).get("availableReplicas", 0),
                        "updated_replicas": d.get("status", {}).get("updatedReplicas", 0),
                        "image": d.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image"),
                    }
                    for d in data.get("items", [])
                ]
            }

        elif tool_name == "k8s_scale_deployment":
            name = arguments["name"]
            replicas = arguments["replicas"]
            patch = {"spec": {"replicas": replicas}}
            resp = await client.patch(
                f"/apis/apps/v1/namespaces/{ns}/deployments/{name}/scale",
                content=json.dumps(patch),
                headers={**_kube_headers(), "Content-Type": "application/merge-patch+json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "name": name,
                "replicas": data.get("spec", {}).get("replicas"),
                "namespace": ns,
            }

        elif tool_name == "k8s_restart_deployment":
            import datetime
            name = arguments["name"]
            # Patch restartedAt annotation to trigger a rolling restart
            now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": now,
                            }
                        }
                    }
                }
            }
            resp = await client.patch(
                f"/apis/apps/v1/namespaces/{ns}/deployments/{name}",
                content=json.dumps(patch),
                headers={**_kube_headers(), "Content-Type": "application/merge-patch+json"},
            )
            resp.raise_for_status()
            return {"restarted": True, "name": name, "namespace": ns, "timestamp": now}

        elif tool_name == "k8s_get_logs":
            pod_name = arguments["pod_name"]
            params = {
                "tailLines": arguments.get("tail_lines", 100),
            }
            if arguments.get("container"):
                params["container"] = arguments["container"]
            if arguments.get("previous"):
                params["previous"] = "true"
            resp = await client.get(
                f"/api/v1/namespaces/{ns}/pods/{pod_name}/log",
                params=params,
            )
            resp.raise_for_status()
            return {"pod": pod_name, "namespace": ns, "logs": resp.text}

        elif tool_name == "k8s_list_services":
            params = {}
            if arguments.get("label_selector"):
                params["labelSelector"] = arguments["label_selector"]
            resp = await client.get(f"/api/v1/namespaces/{ns}/services", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "services": [
                    {
                        "name": s["metadata"]["name"],
                        "namespace": s["metadata"]["namespace"],
                        "type": s.get("spec", {}).get("type"),
                        "cluster_ip": s.get("spec", {}).get("clusterIP"),
                        "external_ips": s.get("spec", {}).get("externalIPs", []),
                        "ports": [
                            {
                                "name": p.get("name"),
                                "port": p.get("port"),
                                "target_port": p.get("targetPort"),
                                "protocol": p.get("protocol"),
                            }
                            for p in s.get("spec", {}).get("ports", [])
                        ],
                    }
                    for s in data.get("items", [])
                ]
            }

        elif tool_name == "k8s_list_namespaces":
            resp = await client.get("/api/v1/namespaces")
            resp.raise_for_status()
            data = resp.json()
            return {
                "namespaces": [
                    {
                        "name": n["metadata"]["name"],
                        "status": n.get("status", {}).get("phase"),
                        "labels": n.get("metadata", {}).get("labels", {}),
                    }
                    for n in data.get("items", [])
                ]
            }

        elif tool_name == "k8s_apply_manifest":
            manifest = arguments["manifest"]
            api_version = manifest.get("apiVersion", "v1")
            kind = manifest.get("kind", "")
            name = manifest.get("metadata", {}).get("name", "")
            mf_ns = arguments.get("namespace") or manifest.get("metadata", {}).get("namespace", ns)

            # Build the resource URL
            if "/" in api_version:
                group, version = api_version.split("/", 1)
                group_path = f"/apis/{group}/{version}"
            else:
                group_path = f"/api/{api_version}"

            kind_lower = kind.lower() + "s"
            if mf_ns:
                resource_url = f"{group_path}/namespaces/{mf_ns}/{kind_lower}"
            else:
                resource_url = f"{group_path}/{kind_lower}"

            # Try PATCH first (update), fall back to POST (create)
            if name:
                patch_url = f"{resource_url}/{name}"
                patch_resp = await client.patch(
                    patch_url,
                    content=json.dumps(manifest),
                    headers={**_kube_headers(), "Content-Type": "application/merge-patch+json"},
                )
                if patch_resp.status_code in (200, 201):
                    data = patch_resp.json()
                    return {
                        "action": "updated",
                        "name": data.get("metadata", {}).get("name"),
                        "kind": kind,
                        "namespace": mf_ns,
                    }

            resp = await client.post(
                resource_url,
                content=json.dumps(manifest),
                headers={**_kube_headers(), "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "action": "created",
                "name": data.get("metadata", {}).get("name"),
                "kind": kind,
                "namespace": mf_ns,
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
