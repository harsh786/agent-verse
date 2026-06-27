"""New Relic MCP server — query metrics, alerts, and applications via NerdGraph + REST.

Environment:
  NEW_RELIC_API_KEY:    New Relic User API key (NRAK-...)
  NEW_RELIC_ACCOUNT_ID: Account ID (numeric)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

NERDGRAPH_URL = "https://api.newrelic.com/graphql"
NEWRELIC_API_V2 = "https://api.newrelic.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "newrelic_nrql_query",
        "description": "Run a NRQL query against New Relic via NerdGraph",
        "parameters": {
            "type": "object",
            "properties": {
                "nrql": {
                    "type": "string",
                    "description": "NRQL query (e.g. SELECT count(*) FROM Transaction SINCE 1 hour ago)",
                },
                "account_id": {
                    "type": "integer",
                    "description": "Account ID override (uses NEW_RELIC_ACCOUNT_ID env if omitted)",
                },
            },
            "required": ["nrql"],
        },
    },
    {
        "name": "newrelic_list_alerts",
        "description": "List New Relic alert policies",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "newrelic_list_applications",
        "description": "List APM applications monitored by New Relic",
        "parameters": {
            "type": "object",
            "properties": {
                "filter_name": {"type": "string", "description": "Filter by app name"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "newrelic_get_metrics",
        "description": "Get metric timeslice data for a New Relic APM application",
        "parameters": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metric names (e.g. ['HttpDispatcher', 'CPU/User Time'])",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metric value fields (e.g. ['requests_per_minute', 'average_response_time'])",
                },
                "from_": {"type": "string", "description": "ISO8601 start time"},
                "to": {"type": "string", "description": "ISO8601 end time"},
                "period": {"type": "integer", "description": "Period in seconds"},
                "summarize": {"type": "boolean", "default": False},
            },
            "required": ["application_id", "names"],
        },
    },
    {
        "name": "newrelic_get_entity",
        "description": "Search for New Relic entities (services, hosts, dashboards) via NerdGraph",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name to search"},
                "entity_type": {
                    "type": "string",
                    "description": "Entity type (e.g. APM_APPLICATION_ENTITY, BROWSER_APPLICATION_ENTITY)",
                },
                "tags": {"type": "object", "description": "Key-value tag filters"},
            },
        },
    },
]


def _api_headers(api_key: str) -> dict[str, str]:
    return {
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("NEW_RELIC_API_KEY", "")
    account_id_str = os.getenv("NEW_RELIC_ACCOUNT_ID", "")

    if not api_key:
        return {"error": "NEW_RELIC_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            hdrs = _api_headers(api_key)

            if tool_name == "newrelic_nrql_query":
                acct_id = arguments.get("account_id") or (int(account_id_str) if account_id_str else None)
                if not acct_id:
                    return {"error": "NEW_RELIC_ACCOUNT_ID not configured"}
                query = """
                query NrqlQuery($accountId: Int!, $nrql: Nrql!) {
                  actor {
                    account(id: $accountId) {
                      nrql(query: $nrql) {
                        results
                        totalResult
                        performanceStats {
                          inspectedCount
                          responseTime
                        }
                      }
                    }
                  }
                }
                """
                resp = await client.post(
                    NERDGRAPH_URL,
                    json={
                        "query": query,
                        "variables": {"accountId": acct_id, "nrql": arguments["nrql"]},
                    },
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                if "errors" in data:
                    return {"error": data["errors"]}
                nrql_data = (
                    data.get("data", {})
                    .get("actor", {})
                    .get("account", {})
                    .get("nrql", {})
                )
                return {
                    "results": nrql_data.get("results", []),
                    "performance": nrql_data.get("performanceStats"),
                }

            elif tool_name == "newrelic_list_alerts":
                resp = await client.get(
                    f"{NEWRELIC_API_V2}/alerts_policies.json",
                    params={"page": arguments.get("page", 1)},
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "policies": [
                        {"id": p["id"], "name": p["name"], "incident_preference": p.get("incident_preference")}
                        for p in data.get("policies", [])
                    ]
                }

            elif tool_name == "newrelic_list_applications":
                params: dict[str, Any] = {"page": arguments.get("page", 1)}
                if name := arguments.get("filter_name"):
                    params["filter[name]"] = name
                resp = await client.get(
                    f"{NEWRELIC_API_V2}/applications.json",
                    params=params,
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "applications": [
                        {
                            "id": a["id"],
                            "name": a["name"],
                            "language": a.get("language"),
                            "health_status": a.get("health_status"),
                            "reporting": a.get("reporting"),
                        }
                        for a in data.get("applications", [])
                    ]
                }

            elif tool_name == "newrelic_get_metrics":
                app_id = arguments["application_id"]
                params = {
                    "names[]": arguments["names"],
                    "summarize": str(arguments.get("summarize", False)).lower(),
                }
                if values := arguments.get("values"):
                    params["values[]"] = values
                if from_ := arguments.get("from_"):
                    params["from"] = from_
                if to := arguments.get("to"):
                    params["to"] = to
                if period := arguments.get("period"):
                    params["period"] = period
                resp = await client.get(
                    f"{NEWRELIC_API_V2}/applications/{app_id}/metrics/data.json",
                    params=params,
                    headers=hdrs,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "newrelic_get_entity":
                query = """
                query SearchEntities($name: String, $entityType: EntitySearchQueryBuilderType, $tags: [TaggingTagInput]) {
                  actor {
                    entitySearch(queryBuilder: {name: $name, type: $entityType, tags: $tags}) {
                      results {
                        entities {
                          guid
                          name
                          entityType
                          accountId
                          ... on AlertableEntity {
                            alertSeverity
                          }
                        }
                      }
                      count
                    }
                  }
                }
                """
                variables: dict[str, Any] = {}
                if name := arguments.get("name"):
                    variables["name"] = name
                if et := arguments.get("entity_type"):
                    variables["entityType"] = et
                if tags := arguments.get("tags"):
                    variables["tags"] = [{"key": k, "value": v} for k, v in tags.items()]
                resp = await client.post(
                    NERDGRAPH_URL,
                    json={"query": query, "variables": variables},
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                if "errors" in data:
                    return {"error": data["errors"]}
                search = (
                    data.get("data", {})
                    .get("actor", {})
                    .get("entitySearch", {})
                )
                return {
                    "entities": search.get("results", {}).get("entities", []),
                    "count": search.get("count", 0),
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("newrelic_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
