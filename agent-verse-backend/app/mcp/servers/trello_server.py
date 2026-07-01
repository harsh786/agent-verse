"""Trello MCP server — Trello REST API v1 integration.

Environment variables:
  TRELLO_API_KEY: Trello Power-Up API key
  TRELLO_TOKEN: Trello user token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TRELLO_BASE = "https://api.trello.com/1"

TOOL_DEFINITIONS = [
    {
        "name": "trello_list_boards",
        "description": "List all Trello boards for the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["all", "open", "closed", "starred"],
                    "default": "open",
                },
                "fields": {
                    "type": "string",
                    "default": "name,url,shortUrl,closed,starred",
                },
            },
        },
    },
    {
        "name": "trello_get_board",
        "description": "Get detailed information about a Trello board including lists and cards",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "lists": {
                    "type": "string",
                    "enum": ["all", "open", "closed", "none"],
                    "default": "open",
                },
                "cards": {
                    "type": "string",
                    "enum": ["all", "open", "closed", "none"],
                    "default": "open",
                },
            },
            "required": ["board_id"],
        },
    },
    {
        "name": "trello_list_cards",
        "description": "List cards on a Trello board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "filter": {
                    "type": "string",
                    "enum": ["all", "open", "closed"],
                    "default": "open",
                },
                "fields": {
                    "type": "string",
                    "default": "name,desc,due,dueComplete,idList,labels,url,shortUrl",
                },
            },
            "required": ["board_id"],
        },
    },
    {
        "name": "trello_create_card",
        "description": "Create a new Trello card in a list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "ID of the list to add the card to"},
                "name": {"type": "string", "description": "Card title"},
                "desc": {"type": "string", "default": "", "description": "Card description"},
                "due": {"type": "string", "description": "Due date in ISO 8601 format"},
                "id_members": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Member IDs to assign",
                },
                "id_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Label IDs to apply",
                },
                "pos": {
                    "type": "string",
                    "enum": ["top", "bottom"],
                    "default": "bottom",
                },
            },
            "required": ["list_id", "name"],
        },
    },
    {
        "name": "trello_update_card",
        "description": "Update an existing Trello card",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {"type": "string"},
                "name": {"type": "string"},
                "desc": {"type": "string"},
                "due": {"type": "string"},
                "due_complete": {"type": "boolean"},
                "id_list": {"type": "string", "description": "Move card to this list ID"},
                "pos": {"type": "string"},
            },
            "required": ["card_id"],
        },
    },
    {
        "name": "trello_archive_card",
        "description": "Archive (close) a Trello card",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {"type": "string"},
            },
            "required": ["card_id"],
        },
    },
    {
        "name": "trello_add_comment",
        "description": "Add a comment to a Trello card",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["card_id", "text"],
        },
    },
    {
        "name": "trello_create_checklist",
        "description": "Create a checklist on a Trello card",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {"type": "string"},
                "name": {"type": "string", "default": "Checklist"},
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of checklist item names",
                },
            },
            "required": ["card_id"],
        },
    },
    {
        "name": "trello_move_card",
        "description": "Move a Trello card to a different list",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {"type": "string"},
                "list_id": {"type": "string", "description": "Target list ID"},
                "pos": {
                    "type": "string",
                    "enum": ["top", "bottom"],
                    "default": "bottom",
                },
            },
            "required": ["card_id", "list_id"],
        },
    },
]


def _trello_params() -> dict[str, str]:
    return {
        "key": os.getenv("TRELLO_API_KEY", ""),
        "token": os.getenv("TRELLO_TOKEN", ""),
    }


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
    api_key = os.getenv("TRELLO_API_KEY", "")
    token = os.getenv("TRELLO_TOKEN", "")
    if not api_key or not token:
        return {"error": "TRELLO_API_KEY and TRELLO_TOKEN must be configured"}

    auth = {"key": api_key, "token": token}

    async with httpx.AsyncClient(base_url=TRELLO_BASE, timeout=30.0) as client:
        if tool_name == "trello_list_boards":
            params = {
                **auth,
                "filter": arguments.get("filter", "open"),
                "fields": arguments.get("fields", "name,url,shortUrl,closed,starred"),
            }
            resp = await client.get("/members/me/boards", params=params)
            resp.raise_for_status()
            return {"boards": resp.json()}

        elif tool_name == "trello_get_board":
            board_id = arguments["board_id"]
            params = {
                **auth,
                "lists": arguments.get("lists", "open"),
                "cards": arguments.get("cards", "open"),
            }
            resp = await client.get(f"/boards/{board_id}", params=params)
            resp.raise_for_status()
            return {"board": resp.json()}

        elif tool_name == "trello_list_cards":
            board_id = arguments["board_id"]
            params = {
                **auth,
                "filter": arguments.get("filter", "open"),
                "fields": arguments.get(
                    "fields",
                    "name,desc,due,dueComplete,idList,labels,url,shortUrl",
                ),
            }
            resp = await client.get(f"/boards/{board_id}/cards", params=params)
            resp.raise_for_status()
            return {"cards": resp.json()}

        elif tool_name == "trello_create_card":
            payload: dict[str, Any] = {
                **auth,
                "idList": arguments["list_id"],
                "name": arguments["name"],
                "desc": arguments.get("desc", ""),
                "pos": arguments.get("pos", "bottom"),
            }
            if arguments.get("due"):
                payload["due"] = arguments["due"]
            if arguments.get("id_members"):
                payload["idMembers"] = ",".join(arguments["id_members"])
            if arguments.get("id_labels"):
                payload["idLabels"] = ",".join(arguments["id_labels"])

            resp = await client.post("/cards", params=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "card_id": data.get("id", ""),
                "name": data.get("name", ""),
                "url": data.get("url", ""),
                "short_url": data.get("shortUrl", ""),
            }

        elif tool_name == "trello_update_card":
            card_id = arguments["card_id"]
            params: dict[str, Any] = {**auth}
            field_map = {
                "name": "name",
                "desc": "desc",
                "due": "due",
                "due_complete": "dueComplete",
                "id_list": "idList",
                "pos": "pos",
            }
            for arg_key, param_key in field_map.items():
                if arg_key in arguments:
                    params[param_key] = arguments[arg_key]

            resp = await client.put(f"/cards/{card_id}", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {"card_id": data.get("id", ""), "name": data.get("name", ""), "updated": True}

        elif tool_name == "trello_archive_card":
            card_id = arguments["card_id"]
            params = {**auth, "closed": "true"}
            resp = await client.put(f"/cards/{card_id}", params=params)
            resp.raise_for_status()
            return {"card_id": card_id, "archived": True}

        elif tool_name == "trello_add_comment":
            card_id = arguments["card_id"]
            params = {**auth, "text": arguments["text"]}
            resp = await client.post(f"/cards/{card_id}/actions/comments", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {"action_id": data.get("id", ""), "created": True}

        elif tool_name == "trello_create_checklist":
            card_id = arguments["card_id"]
            checklist_params = {**auth, "idCard": card_id, "name": arguments.get("name", "Checklist")}
            resp = await client.post("/checklists", params=checklist_params)
            resp.raise_for_status()
            checklist = resp.json()
            checklist_id = checklist.get("id", "")

            added_items = []
            for item_name in arguments.get("items", []):
                item_params = {**auth, "name": item_name, "checked": "false"}
                item_resp = await client.post(
                    f"/checklists/{checklist_id}/checkItems", params=item_params
                )
                item_resp.raise_for_status()
                added_items.append(item_resp.json().get("name", item_name))

            return {
                "checklist_id": checklist_id,
                "name": checklist.get("name", ""),
                "items_added": added_items,
            }

        elif tool_name == "trello_move_card":
            card_id = arguments["card_id"]
            params: dict[str, Any] = {
                **auth,
                "idList": arguments["list_id"],
                "pos": arguments.get("pos", "bottom"),
            }
            resp = await client.put(f"/cards/{card_id}/idList", params=params)
            resp.raise_for_status()
            return {"card_id": card_id, "moved_to_list": arguments["list_id"]}

        else:
            return {"error": f"Unknown tool: {tool_name}"}
