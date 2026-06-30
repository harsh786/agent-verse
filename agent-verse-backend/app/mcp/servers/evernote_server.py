"""Evernote MCP server — note-taking, notebooks, and search via Evernote API.

Environment:
  EVERNOTE_ACCESS_TOKEN: Evernote OAuth access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Evernote uses a Thrift-based API; the REST-compatible endpoint proxies are used here
EVERNOTE_BASE = "https://www.evernote.com/edam/note"
EVERNOTE_USER_STORE = "https://www.evernote.com/edam/user"

TOOL_DEFINITIONS = [
    {
        "name": "evernote_list_notebooks",
        "description": "List all notebooks in the Evernote account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "evernote_create_note",
        "description": "Create a new note in Evernote",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string", "description": "Note body as plain text or ENML"},
                "notebook_guid": {"type": "string", "description": "Target notebook GUID"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tag names to apply",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "evernote_list_notes",
        "description": "List notes in a notebook or across all notebooks",
        "parameters": {
            "type": "object",
            "properties": {
                "notebook_guid": {"type": "string"},
                "max_notes": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "evernote_search_notes",
        "description": "Search notes using Evernote Query Language (EQL)",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Evernote search query e.g. 'tag:important notebook:Work'",
                },
                "max_notes": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "evernote_update_note",
        "description": "Update the title or content of an existing note",
        "parameters": {
            "type": "object",
            "properties": {
                "note_guid": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["note_guid"],
        },
    },
    {
        "name": "evernote_list_tags",
        "description": "List all tags in the Evernote account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _enml_wrap(content: str) -> str:
    """Wrap plain text in minimal ENML."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
        f"<en-note>{content}</en-note>"
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("EVERNOTE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "EVERNOTE_ACCESS_TOKEN not configured"}

    # Evernote's modern OAuth endpoints
    headers = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "evernote_list_notebooks":
                r = await c.get(
                    f"https://api.evernote.com/edam/notestore/notebooks",
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "notebooks": [
                        {
                            "guid": nb.get("guid"),
                            "name": nb.get("name"),
                            "default_notebook": nb.get("defaultNotebook", False),
                        }
                        for nb in (data if isinstance(data, list) else data.get("notebooks", []))
                    ]
                }

            elif tool_name == "evernote_create_note":
                content = arguments["content"]
                if not content.startswith("<?xml"):
                    content = _enml_wrap(content)
                body: dict[str, Any] = {
                    "title": arguments["title"],
                    "content": content,
                }
                if arguments.get("notebook_guid"):
                    body["notebookGuid"] = arguments["notebook_guid"]
                if arguments.get("tags"):
                    body["tagNames"] = arguments["tags"]
                r = await c.post(
                    "https://api.evernote.com/edam/notestore/notes",
                    headers=headers,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "guid": data.get("guid"),
                    "title": data.get("title"),
                    "created": True,
                }

            elif tool_name == "evernote_list_notes":
                params: dict[str, Any] = {
                    "maxNotes": arguments.get("max_notes", 20),
                    "offset": arguments.get("offset", 0),
                }
                if arguments.get("notebook_guid"):
                    params["notebookGuid"] = arguments["notebook_guid"]
                r = await c.get(
                    "https://api.evernote.com/edam/notestore/notes",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                notes = data.get("notes", []) if isinstance(data, dict) else data
                return {
                    "notes": [
                        {
                            "guid": n.get("guid"),
                            "title": n.get("title"),
                            "created": n.get("created"),
                            "updated": n.get("updated"),
                        }
                        for n in notes
                    ],
                    "total": data.get("totalNotes", len(notes))
                    if isinstance(data, dict)
                    else len(notes),
                }

            elif tool_name == "evernote_search_notes":
                r = await c.get(
                    "https://api.evernote.com/edam/notestore/notes",
                    headers=headers,
                    params={
                        "words": arguments["query"],
                        "maxNotes": arguments.get("max_notes", 20),
                    },
                )
                r.raise_for_status()
                data = r.json()
                notes = data.get("notes", []) if isinstance(data, dict) else data
                return {
                    "notes": [
                        {
                            "guid": n.get("guid"),
                            "title": n.get("title"),
                            "notebook_guid": n.get("notebookGuid"),
                        }
                        for n in notes
                    ],
                    "total": data.get("totalNotes", len(notes))
                    if isinstance(data, dict)
                    else len(notes),
                }

            elif tool_name == "evernote_update_note":
                body = {"guid": arguments["note_guid"]}
                if arguments.get("title"):
                    body["title"] = arguments["title"]
                if arguments.get("content"):
                    content = arguments["content"]
                    if not content.startswith("<?xml"):
                        content = _enml_wrap(content)
                    body["content"] = content
                r = await c.put(
                    "https://api.evernote.com/edam/notestore/notes",
                    headers=headers,
                    json=body,
                )
                r.raise_for_status()
                return {"guid": arguments["note_guid"], "updated": True}

            elif tool_name == "evernote_list_tags":
                r = await c.get(
                    "https://api.evernote.com/edam/notestore/tags",
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                tags = data if isinstance(data, list) else data.get("tags", [])
                return {
                    "tags": [
                        {"guid": t.get("guid"), "name": t.get("name")} for t in tags
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("evernote_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
