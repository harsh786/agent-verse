"""Monday.com MCP server — monday.com GraphQL API v2 integration.

Environment variables:
  MONDAY_API_KEY: monday.com personal API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

MONDAY_GQL = "https://api.monday.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "monday_list_boards",
        "description": "List monday.com boards accessible to the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "page": {"type": "integer", "default": 1},
                "board_kind": {
                    "type": "string",
                    "enum": ["public", "private", "share"],
                    "description": "Filter by board kind",
                },
                "state": {
                    "type": "string",
                    "enum": ["active", "archived", "deleted", "all"],
                    "default": "active",
                },
            },
        },
    },
    {
        "name": "monday_get_items",
        "description": "Get items (rows) from a monday.com board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "integer", "description": "Board ID"},
                "limit": {"type": "integer", "default": 50},
                "page": {"type": "integer", "default": 1},
                "group_id": {"type": "string", "description": "Filter by group ID"},
            },
            "required": ["board_id"],
        },
    },
    {
        "name": "monday_create_item",
        "description": "Create a new item on a monday.com board",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "integer"},
                "item_name": {"type": "string"},
                "group_id": {"type": "string", "description": "Group to add item to"},
                "column_values": {
                    "type": "string",
                    "description": "JSON string of column values, e.g. '{\"status\":{\"label\":\"Done\"}}'",
                },
            },
            "required": ["board_id", "item_name"],
        },
    },
    {
        "name": "monday_update_item",
        "description": "Update multiple column values for a monday.com item",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": "integer"},
                "item_id": {"type": "integer"},
                "column_values": {
                    "type": "string",
                    "description": "JSON string of column values to update",
                },
            },
            "required": ["board_id", "item_id", "column_values"],
        },
    },
    {
        "name": "monday_create_update",
        "description": "Create an update (comment) on a monday.com item",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "body": {"type": "string", "description": "Update body text"},
            },
            "required": ["item_id", "body"],
        },
    },
    {
        "name": "monday_move_item_to_group",
        "description": "Move a monday.com item to a different group on the same board",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "group_id": {"type": "string", "description": "Target group ID"},
            },
            "required": ["item_id", "group_id"],
        },
    },
]


async def _monday_gql(query: str, variables: dict, token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            MONDAY_GQL,
            json={"query": query, "variables": variables},
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "API-Version": "2024-01",
            },
        )
        resp.raise_for_status()
        return resp.json()


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
    token = os.getenv("MONDAY_API_KEY", "")
    if not token:
        return {"error": "MONDAY_API_KEY not configured"}

    if tool_name == "monday_list_boards":
        query = """
        query ListBoards($limit: Int, $page: Int, $state: State) {
          boards(limit: $limit, page: $page, state: $state) {
            id
            name
            description
            state
            board_kind
            updated_at
          }
        }
        """
        variables: dict[str, Any] = {
            "limit": arguments.get("limit", 50),
            "page": arguments.get("page", 1),
            "state": arguments.get("state", "active"),
        }
        result = await _monday_gql(query, variables, token)
        return {"boards": result.get("data", {}).get("boards", [])}

    elif tool_name == "monday_get_items":
        query = """
        query GetItems($boardId: ID!, $limit: Int, $page: Int) {
          boards(ids: [$boardId]) {
            items_page(limit: $limit, cursor: null) {
              cursor
              items {
                id
                name
                state
                created_at
                updated_at
                group { id title }
                column_values { id text value }
              }
            }
          }
        }
        """
        variables = {
            "boardId": str(arguments["board_id"]),
            "limit": arguments.get("limit", 50),
        }
        result = await _monday_gql(query, variables, token)
        boards = result.get("data", {}).get("boards", [])
        items = []
        if boards:
            items_page = boards[0].get("items_page", {})
            items = items_page.get("items", [])
        return {"items": items}

    elif tool_name == "monday_create_item":
        query = """
        mutation CreateItem($boardId: ID!, $groupId: String, $itemName: String!, $columnValues: JSON) {
          create_item(board_id: $boardId, group_id: $groupId, item_name: $itemName, column_values: $columnValues) {
            id
            name
            created_at
          }
        }
        """
        variables = {
            "boardId": str(arguments["board_id"]),
            "itemName": arguments["item_name"],
        }
        if arguments.get("group_id"):
            variables["groupId"] = arguments["group_id"]
        if arguments.get("column_values"):
            variables["columnValues"] = arguments["column_values"]

        result = await _monday_gql(query, variables, token)
        item = result.get("data", {}).get("create_item", {})
        return {"item_id": item.get("id", ""), "name": item.get("name", ""), "created": True}

    elif tool_name == "monday_update_item":
        query = """
        mutation UpdateItem($boardId: ID!, $itemId: ID!, $columnValues: JSON!) {
          change_multiple_column_values(board_id: $boardId, item_id: $itemId, column_values: $columnValues) {
            id
            name
          }
        }
        """
        variables = {
            "boardId": str(arguments["board_id"]),
            "itemId": str(arguments["item_id"]),
            "columnValues": arguments["column_values"],
        }
        result = await _monday_gql(query, variables, token)
        item = result.get("data", {}).get("change_multiple_column_values", {})
        return {"item_id": item.get("id", ""), "name": item.get("name", ""), "updated": True}

    elif tool_name == "monday_create_update":
        query = """
        mutation CreateUpdate($itemId: ID!, $body: String!) {
          create_update(item_id: $itemId, body: $body) {
            id
            body
            created_at
          }
        }
        """
        variables = {
            "itemId": str(arguments["item_id"]),
            "body": arguments["body"],
        }
        result = await _monday_gql(query, variables, token)
        update = result.get("data", {}).get("create_update", {})
        return {
            "update_id": update.get("id", ""),
            "body": update.get("body", ""),
            "created_at": update.get("created_at", ""),
        }

    elif tool_name == "monday_move_item_to_group":
        query = """
        mutation MoveItem($itemId: ID!, $groupId: String!) {
          move_item_to_group(item_id: $itemId, group_id: $groupId) {
            id
            name
            group { id title }
          }
        }
        """
        variables = {
            "itemId": str(arguments["item_id"]),
            "groupId": arguments["group_id"],
        }
        result = await _monday_gql(query, variables, token)
        item = result.get("data", {}).get("move_item_to_group", {})
        return {
            "item_id": item.get("id", ""),
            "name": item.get("name", ""),
            "group": item.get("group", {}),
            "moved": True,
        }

    else:
        return {"error": f"Unknown tool: {tool_name}"}
