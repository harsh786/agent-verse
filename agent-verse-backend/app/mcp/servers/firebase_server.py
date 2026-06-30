"""Firebase / Firestore MCP server — document database and push notifications.

Environment:
  FIREBASE_PROJECT_ID:   Firebase project ID
  FIREBASE_ACCESS_TOKEN: OAuth2 bearer token for Firestore/FCM APIs
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "firestore_get_document",
        "description": "Get a single Firestore document by path",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Collection name"},
                "document_id": {"type": "string"},
            },
            "required": ["collection", "document_id"],
        },
    },
    {
        "name": "firestore_create_document",
        "description": "Create a new Firestore document (auto-generates ID)",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "data": {"type": "object", "description": "Document field values"},
            },
            "required": ["collection", "data"],
        },
    },
    {
        "name": "firestore_update_document",
        "description": "Update fields in an existing Firestore document",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "document_id": {"type": "string"},
                "data": {"type": "object", "description": "Fields to update"},
            },
            "required": ["collection", "document_id", "data"],
        },
    },
    {
        "name": "firestore_delete_document",
        "description": "Delete a Firestore document",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "document_id": {"type": "string"},
            },
            "required": ["collection", "document_id"],
        },
    },
    {
        "name": "firestore_query_collection",
        "description": "Query documents in a Firestore collection with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "page_size": {"type": "integer", "default": 20},
                "order_by": {"type": "string", "description": "Field to order by"},
            },
            "required": ["collection"],
        },
    },
    {
        "name": "firebase_send_notification",
        "description": "Send a push notification via Firebase Cloud Messaging (FCM)",
        "parameters": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Device registration token"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "data": {"type": "object", "description": "Custom data payload"},
                "topic": {"type": "string", "description": "FCM topic (alternative to token)"},
            },
        },
    },
]


def _fs_base(project_id: str) -> str:
    return f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _to_firestore_value(value: Any) -> dict[str, Any]:
    """Convert a Python value to Firestore REST API typed value."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: _to_firestore_value(v) for k, v in value.items()}}}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_to_firestore_value(v) for v in value]}}
    return {"stringValue": str(value)}


def _from_firestore_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Firestore REST document into plain Python dict."""
    fields = doc.get("fields", {})
    result = {}
    for k, v in fields.items():
        if "stringValue" in v:
            result[k] = v["stringValue"]
        elif "integerValue" in v:
            result[k] = int(v["integerValue"])
        elif "doubleValue" in v:
            result[k] = v["doubleValue"]
        elif "booleanValue" in v:
            result[k] = v["booleanValue"]
        elif "nullValue" in v:
            result[k] = None
        else:
            result[k] = v
    return result


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("FIREBASE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "FIREBASE_ACCESS_TOKEN not configured"}

    project_id = os.getenv("FIREBASE_PROJECT_ID", "")
    hdrs = _headers(token)
    fs_base = _fs_base(project_id)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "firestore_get_document":
                collection = arguments["collection"]
                doc_id = arguments["document_id"]
                r = await c.get(f"{fs_base}/{collection}/{doc_id}", headers=hdrs)
                r.raise_for_status()
                doc = r.json()
                return {
                    "id": doc_id,
                    "data": _from_firestore_doc(doc),
                    "create_time": doc.get("createTime"),
                    "update_time": doc.get("updateTime"),
                }

            elif tool_name == "firestore_create_document":
                collection = arguments["collection"]
                fields = {
                    k: _to_firestore_value(v) for k, v in arguments["data"].items()
                }
                r = await c.post(
                    f"{fs_base}/{collection}",
                    headers=hdrs,
                    json={"fields": fields},
                )
                r.raise_for_status()
                doc = r.json()
                name = doc.get("name", "")
                doc_id = name.split("/")[-1] if name else ""
                return {"id": doc_id, "create_time": doc.get("createTime"), "created": True}

            elif tool_name == "firestore_update_document":
                collection = arguments["collection"]
                doc_id = arguments["document_id"]
                fields = {
                    k: _to_firestore_value(v) for k, v in arguments["data"].items()
                }
                update_mask = "&".join(
                    f"updateMask.fieldPaths={k}" for k in arguments["data"]
                )
                r = await c.patch(
                    f"{fs_base}/{collection}/{doc_id}?{update_mask}",
                    headers=hdrs,
                    json={"fields": fields},
                )
                r.raise_for_status()
                doc = r.json()
                return {"id": doc_id, "update_time": doc.get("updateTime"), "updated": True}

            elif tool_name == "firestore_delete_document":
                collection = arguments["collection"]
                doc_id = arguments["document_id"]
                r = await c.delete(f"{fs_base}/{collection}/{doc_id}", headers=hdrs)
                r.raise_for_status()
                return {"deleted": True, "id": doc_id}

            elif tool_name == "firestore_query_collection":
                collection = arguments["collection"]
                body: dict[str, Any] = {
                    "structuredQuery": {
                        "from": [{"collectionId": collection}],
                        "limit": arguments.get("page_size", 20),
                    }
                }
                if arguments.get("order_by"):
                    body["structuredQuery"]["orderBy"] = [
                        {"field": {"fieldPath": arguments["order_by"]}, "direction": "ASCENDING"}
                    ]
                r = await c.post(
                    f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents:runQuery",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                results = r.json()
                documents = []
                for item in results:
                    doc = item.get("document")
                    if doc:
                        name = doc.get("name", "")
                        documents.append(
                            {
                                "id": name.split("/")[-1],
                                "data": _from_firestore_doc(doc),
                            }
                        )
                return {"documents": documents, "count": len(documents)}

            elif tool_name == "firebase_send_notification":
                fcm_url = "https://fcm.googleapis.com/fcm/send"
                message: dict[str, Any] = {
                    "notification": {
                        "title": arguments.get("title", ""),
                        "body": arguments.get("body", ""),
                    }
                }
                if arguments.get("data"):
                    message["data"] = {k: str(v) for k, v in arguments["data"].items()}
                if arguments.get("token"):
                    message["to"] = arguments["token"]
                elif arguments.get("topic"):
                    message["to"] = f"/topics/{arguments['topic']}"
                else:
                    return {"error": "Either token or topic is required"}
                r = await c.post(fcm_url, headers=hdrs, json=message)
                r.raise_for_status()
                return r.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("firebase_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
