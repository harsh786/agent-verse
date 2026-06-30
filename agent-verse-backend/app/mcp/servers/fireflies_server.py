"""Fireflies.ai MCP server — meeting transcription and conversation intelligence.

Environment:
  FIREFLIES_API_KEY: Fireflies.ai API key for GraphQL authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.fireflies.ai/graphql"

TOOL_DEFINITIONS = [
    {
        "name": "fireflies_list_transcripts",
        "description": "List recent meeting transcripts from Fireflies.ai with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum number of transcripts to return"},
                "skip": {"type": "integer", "description": "Number of transcripts to skip for pagination"},
                "user_id": {"type": "string", "description": "Filter by participant user ID"},
            },
        },
    },
    {
        "name": "fireflies_get_transcript",
        "description": "Get the full transcript, summary, and action items for a specific meeting",
        "parameters": {
            "type": "object",
            "properties": {
                "transcript_id": {"type": "string", "description": "Fireflies transcript ID"},
            },
            "required": ["transcript_id"],
        },
    },
    {
        "name": "fireflies_search_transcripts",
        "description": "Search through all transcripts using a keyword or phrase",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to find in transcripts"},
                "limit": {"type": "integer", "description": "Maximum results to return"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fireflies_list_meetings",
        "description": "List upcoming and past meetings synced to Fireflies",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum meetings to return"},
                "skip": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "fireflies_get_summary",
        "description": "Get the AI-generated summary and key talking points for a transcript",
        "parameters": {
            "type": "object",
            "properties": {
                "transcript_id": {"type": "string", "description": "ID of the transcript to summarize"},
            },
            "required": ["transcript_id"],
        },
    },
    {
        "name": "fireflies_add_to_meeting",
        "description": "Add Fireflies bot to an active or upcoming meeting to record and transcribe",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_link": {"type": "string", "description": "URL of the meeting to join (Zoom, Meet, Teams)"},
                "title": {"type": "string", "description": "Title or name for the meeting recording"},
                "attendees": {
                    "type": "array",
                    "description": "List of attendee email addresses",
                    "items": {"type": "string"},
                },
            },
            "required": ["meeting_link"],
        },
    },
]


async def _gql(client: httpx.AsyncClient, api_key: str, query: str, variables: dict[str, Any] | None = None) -> Any:
    r = await client.post(
        BASE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        return {"error": data["errors"][0].get("message", "GraphQL error")}
    return data.get("data", data)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("FIREFLIES_API_KEY", "")
    if not api_key:
        return {"error": "FIREFLIES_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "fireflies_list_transcripts":
                query = """
                query ListTranscripts($limit: Int, $skip: Int) {
                  transcripts(limit: $limit, skip: $skip) {
                    id title date duration
                    organizer_email participants
                  }
                }"""
                return await _gql(client, api_key, query, {
                    "limit": arguments.get("limit", 10),
                    "skip": arguments.get("skip", 0),
                })

            if tool_name == "fireflies_get_transcript":
                query = """
                query GetTranscript($id: String!) {
                  transcript(id: $id) {
                    id title date duration
                    organizer_email participants
                    transcript_url
                    sentences { raw_text speaker_name start_time }
                    action_items { text }
                  }
                }"""
                return await _gql(client, api_key, query, {"id": arguments["transcript_id"]})

            if tool_name == "fireflies_search_transcripts":
                query = """
                query SearchTranscripts($query: String!, $limit: Int) {
                  transcripts(title: $query, limit: $limit) {
                    id title date duration organizer_email
                  }
                }"""
                return await _gql(client, api_key, query, {
                    "query": arguments["query"],
                    "limit": arguments.get("limit", 10),
                })

            if tool_name == "fireflies_list_meetings":
                query = """
                query ListMeetings($limit: Int, $skip: Int) {
                  meetings(limit: $limit, skip: $skip) {
                    id title date participants status
                  }
                }"""
                return await _gql(client, api_key, query, {
                    "limit": arguments.get("limit", 10),
                    "skip": arguments.get("skip", 0),
                })

            if tool_name == "fireflies_get_summary":
                query = """
                query GetSummary($id: String!) {
                  transcript(id: $id) {
                    id title
                    summary {
                      keywords action_items outline
                      shorthand_bullet short_summary overview
                    }
                  }
                }"""
                return await _gql(client, api_key, query, {"id": arguments["transcript_id"]})

            if tool_name == "fireflies_add_to_meeting":
                mutation = """
                mutation AddToMeeting($url: String!, $title: String, $attendees: [String]) {
                  addToMeeting(
                    meeting_url: $url
                    title: $title
                    attendees: $attendees
                  ) { success message }
                }"""
                return await _gql(client, api_key, mutation, {
                    "url": arguments["meeting_link"],
                    "title": arguments.get("title"),
                    "attendees": arguments.get("attendees"),
                })

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
