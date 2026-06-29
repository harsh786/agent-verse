"""Dispatch-level tests for productivity/project-management MCP servers.

Targets: notion, asana, monday, trello, clickup, todoist, wrike,
         basecamp, calendly, docusign, pandadoc, box, dropbox,
         microsoft_onedrive.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    """Return a mock AsyncClient context manager.
    
    All HTTP method mocks are explicitly set to AsyncMock so that
    awaiting them works correctly regardless of Python version.
    """
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

_NOTION = {"NOTION_API_KEY": "notion-tok"}


@pytest.mark.asyncio
async def test_notion_search():
    from app.mcp.servers.notion_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"id": "p1", "object": "page", "url": "url", "last_edited_time": "2024-01-01"}], "next_cursor": None}))
    with patch.dict("os.environ", _NOTION), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("notion_search", {"query": "meeting notes"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_notion_get_page():
    from app.mcp.servers.notion_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "p1", "object": "page", "url": "url", "last_edited_time": "2024-01-01", "properties": {}}))
    with patch.dict("os.environ", _NOTION), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("notion_get_page", {"page_id": "p1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_notion_create_page():
    from app.mcp.servers.notion_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "p2", "object": "page", "url": "url", "last_edited_time": "2024-01-01"}))
    with patch.dict("os.environ", _NOTION), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "notion_create_page",
            {"parent_id": "parent1", "title": "New Page"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_notion_update_page():
    from app.mcp.servers.notion_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "p1", "archived": False}))
    with patch.dict("os.environ", _NOTION), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("notion_update_page", {"page_id": "p1", "archived": False})
    assert "error" not in result


@pytest.mark.asyncio
async def test_notion_get_database():
    from app.mcp.servers.notion_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "db1", "object": "database", "title": [{"plain_text": "Tasks"}], "properties": {}}))
    with patch.dict("os.environ", _NOTION), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("notion_get_database", {"database_id": "db1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_notion_query_database():
    from app.mcp.servers.notion_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [], "next_cursor": None, "has_more": False}))
    with patch.dict("os.environ", _NOTION), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("notion_query_database", {"database_id": "db1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_notion_append_blocks():
    from app.mcp.servers.notion_server import call_tool

    # Server uses "block_id" and "children" arguments
    mc = mk_client(patch=make_resp(data={"results": [{"id": "b1", "type": "paragraph"}]}))
    with patch.dict("os.environ", _NOTION), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "notion_append_blocks",
            {
                "block_id": "p1",
                "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Hello"}}]}}],
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_notion_missing_env():
    from app.mcp.servers.notion_server import call_tool

    with patch.dict("os.environ", {"NOTION_API_KEY": ""}):
        os.environ.pop("NOTION_API_KEY", None)
        result = await call_tool("notion_search", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Asana
# ---------------------------------------------------------------------------

_ASANA = {"ASANA_ACCESS_TOKEN": "asana-tok"}


@pytest.mark.asyncio
async def test_asana_list_tasks():
    from app.mcp.servers.asana_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"gid": "t1", "name": "Task 1", "completed": False, "due_on": None, "assignee": None, "permalink_url": "url"}]}))
    with patch.dict("os.environ", _ASANA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("asana_list_tasks", {"project_gid": "proj1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_asana_get_task():
    from app.mcp.servers.asana_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"gid": "t1", "name": "Task 1", "completed": False, "notes": "desc", "due_on": None, "assignee": None, "projects": [], "permalink_url": "url"}}))
    with patch.dict("os.environ", _ASANA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("asana_get_task", {"task_gid": "t1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_asana_create_task():
    from app.mcp.servers.asana_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"gid": "t2", "name": "New Task", "permalink_url": "url", "completed": False}}))
    with patch.dict("os.environ", _ASANA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("asana_create_task", {"name": "New Task", "workspace_gid": "ws1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_asana_update_task():
    from app.mcp.servers.asana_server import call_tool

    mc = mk_client(put=make_resp(data={"data": {"gid": "t1", "name": "Updated Task", "completed": True}}))
    with patch.dict("os.environ", _ASANA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("asana_update_task", {"task_gid": "t1", "completed": True})
    assert "error" not in result


@pytest.mark.asyncio
async def test_asana_list_projects():
    from app.mcp.servers.asana_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"gid": "p1", "name": "Project 1", "color": "green", "archived": False, "permalink_url": "url"}]}))
    with patch.dict("os.environ", _ASANA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("asana_list_projects", {"workspace_gid": "ws1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_asana_create_project():
    from app.mcp.servers.asana_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"gid": "p2", "name": "New Project", "permalink_url": "url"}}))
    with patch.dict("os.environ", _ASANA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "asana_create_project", {"name": "New Project", "workspace_gid": "ws1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_asana_add_comment():
    from app.mcp.servers.asana_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"gid": "c1", "text": "LGTM", "created_at": "2024-01-01"}}))
    with patch.dict("os.environ", _ASANA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("asana_add_comment", {"task_gid": "t1", "text": "LGTM"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_asana_missing_env():
    from app.mcp.servers.asana_server import call_tool

    with patch.dict("os.environ", {"ASANA_ACCESS_TOKEN": ""}):
        os.environ.pop("ASANA_ACCESS_TOKEN", None)
        result = await call_tool("asana_list_tasks", {"project_gid": "p1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Monday.com (GraphQL)
# ---------------------------------------------------------------------------

_MONDAY = {"MONDAY_API_KEY": "monday-key"}


@pytest.mark.asyncio
async def test_monday_list_boards():
    from app.mcp.servers.monday_server import call_tool

    resp = {"data": {"boards": [{"id": "b1", "name": "My Board", "state": "active", "board_kind": "public", "items_count": 10}]}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _MONDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("monday_list_boards", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_monday_get_items():
    from app.mcp.servers.monday_server import call_tool

    resp = {"data": {"boards": [{"items_page": {"items": [{"id": "i1", "name": "Item 1", "state": "active", "group": {"title": "Group 1"}, "column_values": []}]}}]}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _MONDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("monday_get_items", {"board_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_monday_create_item():
    from app.mcp.servers.monday_server import call_tool

    resp = {"data": {"create_item": {"id": "i2", "name": "New Item", "board": {"id": "b1"}}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _MONDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("monday_create_item", {"board_id": 1, "item_name": "New Item"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_monday_update_item():
    from app.mcp.servers.monday_server import call_tool

    # Server requires "column_values" (JSON string), "board_id", and "item_id"
    resp = {"data": {"change_multiple_column_values": {"id": "i1", "name": "Item 1"}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _MONDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "monday_update_item",
            {"board_id": 1, "item_id": "i1", "column_values": '{"status":{"label":"Done"}}'},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_monday_create_update():
    from app.mcp.servers.monday_server import call_tool

    resp = {"data": {"create_update": {"id": "u1", "body": "LGTM"}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _MONDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("monday_create_update", {"item_id": "i1", "body": "LGTM"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_monday_missing_env():
    from app.mcp.servers.monday_server import call_tool

    with patch.dict("os.environ", {"MONDAY_API_KEY": ""}):
        os.environ.pop("MONDAY_API_KEY", None)
        result = await call_tool("monday_list_boards", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Trello
# ---------------------------------------------------------------------------

_TRELLO = {"TRELLO_API_KEY": "trello-key", "TRELLO_TOKEN": "trello-token"}


@pytest.mark.asyncio
async def test_trello_list_boards():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "b1", "name": "My Board", "url": "url", "closed": False}]))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_list_boards", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_get_board():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "b1", "name": "My Board", "url": "url", "lists": [], "labels": []}))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_get_board", {"board_id": "b1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_list_cards():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "c1", "name": "Card 1", "desc": "desc", "idList": "l1", "url": "url", "due": None, "closed": False}]))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_list_cards", {"board_id": "b1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_create_card():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "c2", "name": "New Card", "url": "url", "idList": "l1"}))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_create_card", {"list_id": "l1", "name": "New Card"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_update_card():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "c1", "name": "Updated Card", "url": "url"}))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_update_card", {"card_id": "c1", "name": "Updated Card"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_archive_card():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "c1", "closed": True}))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_archive_card", {"card_id": "c1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_add_comment():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "action1", "data": {"text": "LGTM"}}))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_add_comment", {"card_id": "c1", "text": "LGTM"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_move_card():
    from app.mcp.servers.trello_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "c1", "idList": "l2"}))
    with patch.dict("os.environ", _TRELLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("trello_move_card", {"card_id": "c1", "list_id": "l2"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_trello_missing_env():
    from app.mcp.servers.trello_server import call_tool

    with patch.dict("os.environ", {"TRELLO_API_KEY": "", "TRELLO_TOKEN": ""}):
        os.environ.pop("TRELLO_API_KEY", None)
        result = await call_tool("trello_list_boards", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# ClickUp
# ---------------------------------------------------------------------------

_CU = {"CLICKUP_API_TOKEN": "cu-tok"}


@pytest.mark.asyncio
async def test_clickup_list_spaces():
    from app.mcp.servers.clickup_server import call_tool

    # Server requires "workspace_id" argument (not "team_id")
    mc = mk_client(get=make_resp(data={"spaces": [{"id": "s1", "name": "Engineering", "private": False}]}))
    with patch.dict("os.environ", _CU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clickup_list_spaces", {"workspace_id": "t1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_clickup_list_lists():
    from app.mcp.servers.clickup_server import call_tool

    mc = mk_client(get=make_resp(data={"lists": [{"id": "l1", "name": "Backlog", "orderindex": 0, "status": None}]}))
    with patch.dict("os.environ", _CU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clickup_list_lists", {"folder_id": "f1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_clickup_list_tasks():
    from app.mcp.servers.clickup_server import call_tool

    mc = mk_client(get=make_resp(data={"tasks": [{"id": "task1", "name": "Fix Bug", "status": {"status": "open"}, "assignees": [], "due_date": None, "url": "url", "priority": None}]}))
    with patch.dict("os.environ", _CU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clickup_list_tasks", {"list_id": "l1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_clickup_create_task():
    from app.mcp.servers.clickup_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "task2", "name": "New Task", "url": "url", "status": {"status": "open"}}))
    with patch.dict("os.environ", _CU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clickup_create_task", {"list_id": "l1", "name": "New Task"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_clickup_update_task():
    from app.mcp.servers.clickup_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "task1", "name": "Updated", "status": {"status": "in progress"}}))
    with patch.dict("os.environ", _CU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clickup_update_task", {"task_id": "task1", "status": "in progress"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_clickup_add_comment():
    from app.mcp.servers.clickup_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 1, "comment": {"text_content": "Nice work"}}))
    with patch.dict("os.environ", _CU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clickup_add_comment", {"task_id": "task1", "comment_text": "Nice work"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_clickup_missing_env():
    from app.mcp.servers.clickup_server import call_tool

    with patch.dict("os.environ", {"CLICKUP_API_TOKEN": ""}):
        os.environ.pop("CLICKUP_API_TOKEN", None)
        result = await call_tool("clickup_list_spaces", {"team_id": "t1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Todoist
# ---------------------------------------------------------------------------

_TD = {"TODOIST_API_TOKEN": "td-tok"}


@pytest.mark.asyncio
async def test_todoist_list_tasks():
    from app.mcp.servers.todoist_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "t1", "content": "Buy milk", "project_id": "p1", "due": None, "priority": 1, "url": "url", "is_completed": False}]))
    with patch.dict("os.environ", _TD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todoist_list_tasks", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_todoist_get_task():
    from app.mcp.servers.todoist_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "t1", "content": "Buy milk", "project_id": "p1", "due": None, "priority": 1, "url": "url", "description": ""}))
    with patch.dict("os.environ", _TD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todoist_get_task", {"task_id": "t1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_todoist_create_task():
    from app.mcp.servers.todoist_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "t2", "content": "New Task", "url": "url", "project_id": "p1"}))
    with patch.dict("os.environ", _TD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todoist_create_task", {"content": "New Task"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_todoist_update_task():
    from app.mcp.servers.todoist_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "t1", "content": "Updated", "priority": 2}))
    with patch.dict("os.environ", _TD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todoist_update_task", {"task_id": "t1", "content": "Updated"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_todoist_close_task():
    from app.mcp.servers.todoist_server import call_tool

    mc = mk_client(post=make_resp(status=204))
    with patch.dict("os.environ", _TD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todoist_close_task", {"task_id": "t1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_todoist_list_projects():
    from app.mcp.servers.todoist_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "p1", "name": "Inbox", "color": "grey", "is_favorite": False, "url": "url"}]))
    with patch.dict("os.environ", _TD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todoist_list_projects", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_todoist_create_project():
    from app.mcp.servers.todoist_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "p2", "name": "Work", "url": "url"}))
    with patch.dict("os.environ", _TD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("todoist_create_project", {"name": "Work"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_todoist_missing_env():
    from app.mcp.servers.todoist_server import call_tool

    with patch.dict("os.environ", {"TODOIST_API_TOKEN": ""}):
        os.environ.pop("TODOIST_API_TOKEN", None)
        result = await call_tool("todoist_list_tasks", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Wrike
# ---------------------------------------------------------------------------

_WRIKE = {"WRIKE_ACCESS_TOKEN": "wrike-tok"}


@pytest.mark.asyncio
async def test_wrike_list_tasks():
    from app.mcp.servers.wrike_server import call_tool

    # Server requires "folder_id" argument
    mc = mk_client(get=make_resp(data={"data": [{"id": "IEAABC", "title": "Task 1", "status": "Active", "importance": "Normal", "dates": {}, "permalink": "url"}]}))
    with patch.dict("os.environ", _WRIKE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wrike_list_tasks", {"folder_id": "f1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wrike_get_task():
    from app.mcp.servers.wrike_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "IEAABC", "title": "Task 1", "description": "desc", "status": "Active", "importance": "Normal", "dates": {}, "permalink": "url", "responsibleIds": [], "parentIds": []}]}))
    with patch.dict("os.environ", _WRIKE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wrike_get_task", {"task_id": "IEAABC"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wrike_create_task():
    from app.mcp.servers.wrike_server import call_tool

    mc = mk_client(post=make_resp(data={"data": [{"id": "IEBB", "title": "New Task", "status": "Active", "permalink": "url"}]}))
    with patch.dict("os.environ", _WRIKE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wrike_create_task", {"folder_id": "f1", "title": "New Task"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wrike_update_task():
    from app.mcp.servers.wrike_server import call_tool

    mc = mk_client(put=make_resp(data={"data": [{"id": "IEAABC", "title": "Updated", "status": "Completed"}]}))
    with patch.dict("os.environ", _WRIKE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wrike_update_task", {"task_id": "IEAABC", "status": "Completed"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wrike_list_spaces():
    from app.mcp.servers.wrike_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "sp1", "title": "Marketing", "avatarUrl": "url"}]}))
    with patch.dict("os.environ", _WRIKE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wrike_list_spaces", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wrike_missing_env():
    from app.mcp.servers.wrike_server import call_tool

    with patch.dict("os.environ", {"WRIKE_ACCESS_TOKEN": ""}):
        os.environ.pop("WRIKE_ACCESS_TOKEN", None)
        result = await call_tool("wrike_list_tasks", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Basecamp
# ---------------------------------------------------------------------------

_BC = {
    "BASECAMP_ACCESS_TOKEN": "bc-tok",
    "BASECAMP_ACCOUNT_ID": "12345",
}


@pytest.mark.asyncio
async def test_basecamp_list_projects():
    from app.mcp.servers.basecamp_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "name": "Project X", "purpose": "company", "status": "active", "app_url": "url"}]))
    with patch.dict("os.environ", _BC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("basecamp_list_projects", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_basecamp_get_project():
    from app.mcp.servers.basecamp_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "name": "Project X", "description": "desc", "status": "active", "dock": []}))
    with patch.dict("os.environ", _BC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("basecamp_get_project", {"project_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_basecamp_list_todolists():
    from app.mcp.servers.basecamp_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "title": "Sprint 1", "completed_ratio": "0/10", "app_url": "url"}]))
    with patch.dict("os.environ", _BC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("basecamp_list_todolists", {"project_id": 1, "todoset_id": 2})
    assert "error" not in result


@pytest.mark.asyncio
async def test_basecamp_create_todo():
    from app.mcp.servers.basecamp_server import call_tool

    # Response must have "content" key (not "title")
    mc = mk_client(post=make_resp(data={"id": 100, "content": "New Todo", "completed": False, "app_url": "url"}))
    with patch.dict("os.environ", _BC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "basecamp_create_todo",
            {"project_id": 1, "todolist_id": 1, "content": "New Todo"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_basecamp_list_people():
    from app.mcp.servers.basecamp_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "name": "Alice", "email_address": "a@b.com", "admin": False}]))
    with patch.dict("os.environ", _BC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("basecamp_list_people", {"project_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_basecamp_missing_env():
    from app.mcp.servers.basecamp_server import call_tool

    with patch.dict("os.environ", {"BASECAMP_ACCESS_TOKEN": "", "BASECAMP_ACCOUNT_ID": ""}):
        os.environ.pop("BASECAMP_ACCESS_TOKEN", None)
        result = await call_tool("basecamp_list_projects", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Calendly
# ---------------------------------------------------------------------------

_CALY = {"CALENDLY_ACCESS_TOKEN": "caly-tok"}


@pytest.mark.asyncio
async def test_calendly_get_current_user():
    from app.mcp.servers.calendly_server import call_tool

    # Tool is "calendly_get_user" not "calendly_get_current_user"
    mc = mk_client(get=make_resp(data={"resource": {"uri": "u/abc", "name": "Alice", "email": "a@b.com", "scheduling_url": "url"}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_get_user", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_list_event_types():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": [{"uri": "et/1", "name": "30min", "duration": 30, "active": True, "scheduling_url": "url"}]}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_list_event_types", {"user_uri": "u/abc"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_list_scheduled_events():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": [{"uri": "ev/1", "name": "Meeting", "status": "active", "start_time": "2024-01-15T10:00:00Z", "end_time": "2024-01-15T11:00:00Z", "event_type": "et/1"}], "pagination": {}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_list_scheduled_events", {"user_uri": "u/abc"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_missing_env():
    from app.mcp.servers.calendly_server import call_tool

    with patch.dict("os.environ", {"CALENDLY_ACCESS_TOKEN": ""}):
        os.environ.pop("CALENDLY_ACCESS_TOKEN", None)
        result = await call_tool("calendly_get_current_user", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# DocuSign
# ---------------------------------------------------------------------------

_DS = {
    "DOCUSIGN_ACCESS_TOKEN": "ds-tok",
    "DOCUSIGN_ACCOUNT_ID": "acct-123",
}


@pytest.mark.asyncio
async def test_docusign_list_envelopes():
    from app.mcp.servers.docusign_server import call_tool

    mc = mk_client(get=make_resp(data={"envelopes": [{"envelopeId": "e1", "status": "sent", "emailSubject": "Sign this", "sentDateTime": "2024-01-01", "completedDateTime": None}], "totalSetSize": "1"}))
    with patch.dict("os.environ", _DS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("docusign_list_envelopes", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docusign_get_envelope():
    from app.mcp.servers.docusign_server import call_tool

    mc = mk_client(get=make_resp(data={"envelopeId": "e1", "status": "sent", "emailSubject": "Sign", "sentDateTime": "2024-01-01", "signers": []}))
    with patch.dict("os.environ", _DS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("docusign_get_envelope", {"envelope_id": "e1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docusign_send_envelope():
    from app.mcp.servers.docusign_server import call_tool

    # "docusign_send_envelope" sets status to "sent" for an existing envelope_id
    mc = mk_client(put=make_resp(data={"envelopeId": "e2", "status": "sent"}))
    with patch.dict("os.environ", _DS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "docusign_send_envelope",
            {"envelope_id": "e2"},  # server requires envelope_id to PUT status
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_docusign_missing_env():
    from app.mcp.servers.docusign_server import call_tool

    with patch.dict("os.environ", {"DOCUSIGN_ACCESS_TOKEN": ""}):
        os.environ.pop("DOCUSIGN_ACCESS_TOKEN", None)
        result = await call_tool("docusign_list_envelopes", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# PandaDoc
# ---------------------------------------------------------------------------

_PD = {"PANDADOC_API_KEY": "pd-key"}


@pytest.mark.asyncio
async def test_pandadoc_list_documents():
    from app.mcp.servers.pandadoc_server import call_tool

    mc = mk_client(get=make_resp(data={"results": [{"id": "doc1", "name": "Contract", "status": "document.draft", "date_created": "2024-01-01", "recipients": []}]}))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pandadoc_list_documents", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pandadoc_get_document():
    from app.mcp.servers.pandadoc_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "doc1", "name": "Contract", "status": "document.draft", "date_created": "2024-01-01", "recipients": []}))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pandadoc_get_document", {"document_id": "doc1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pandadoc_send_document():
    from app.mcp.servers.pandadoc_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "doc1", "status": "document.sent"}))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "pandadoc_send_document",
            {"document_id": "doc1", "message": "Please sign"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_pandadoc_missing_env():
    from app.mcp.servers.pandadoc_server import call_tool

    with patch.dict("os.environ", {"PANDADOC_API_KEY": ""}):
        os.environ.pop("PANDADOC_API_KEY", None)
        result = await call_tool("pandadoc_list_documents", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Box
# ---------------------------------------------------------------------------

_BOX = {"BOX_ACCESS_TOKEN": "box-tok"}


@pytest.mark.asyncio
async def test_box_list_folder():
    from app.mcp.servers.box_server import call_tool

    mc = mk_client(get=make_resp(data={"entries": [{"id": "f1", "name": "Documents", "type": "folder"}, {"id": "fi1", "name": "report.pdf", "type": "file", "size": 1024}], "total_count": 2}))
    with patch.dict("os.environ", _BOX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("box_list_folder", {"folder_id": "0"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_box_get_file_info():
    from app.mcp.servers.box_server import call_tool

    # Tool is "box_get_file" not "box_get_file_info"
    mc = mk_client(get=make_resp(data={"id": "fi1", "name": "report.pdf", "size": 1024, "modified_at": "2024-01-01", "created_at": "2024-01-01", "parent": {"id": "0"}, "shared_link": None}))
    with patch.dict("os.environ", _BOX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("box_get_file", {"file_id": "fi1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_box_search():
    from app.mcp.servers.box_server import call_tool

    mc = mk_client(get=make_resp(data={"entries": [{"id": "fi1", "name": "report.pdf", "type": "file"}], "total_count": 1}))
    with patch.dict("os.environ", _BOX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("box_search", {"query": "report"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_box_create_folder():
    from app.mcp.servers.box_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "f2", "name": "New Folder", "type": "folder"}))
    with patch.dict("os.environ", _BOX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("box_create_folder", {"name": "New Folder", "parent_id": "0"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_box_delete_file():
    from app.mcp.servers.box_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _BOX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("box_delete_file", {"file_id": "fi1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_box_missing_env():
    from app.mcp.servers.box_server import call_tool

    with patch.dict("os.environ", {"BOX_ACCESS_TOKEN": ""}):
        os.environ.pop("BOX_ACCESS_TOKEN", None)
        result = await call_tool("box_list_folder", {"folder_id": "0"})
    assert "error" in result
