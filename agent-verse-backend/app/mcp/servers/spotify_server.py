"""Spotify MCP server — Spotify music track search, playlists, and artist info.

Environment:
  SPOTIFY_ACCESS_TOKEN: Spotify OAuth2 access token (requires appropriate scopes)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.spotify.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "spotify_search_tracks",
        "description": "Search for tracks, albums, artists, or playlists on Spotify",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query string"},
                "type": {
                    "type": "string",
                    "description": "Comma-separated types to search: track, album, artist, playlist",
                    "default": "track",
                },
                "limit": {"type": "integer", "description": "Number of results (max 50)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "market": {"type": "string", "description": "ISO 3166-1 alpha-2 market code e.g. US"},
            },
            "required": ["q"],
        },
    },
    {
        "name": "spotify_get_track",
        "description": "Get full details of a Spotify track by track ID",
        "parameters": {
            "type": "object",
            "properties": {
                "track_id": {"type": "string", "description": "Spotify track ID"},
                "market": {"type": "string", "description": "ISO 3166-1 alpha-2 market code"},
            },
            "required": ["track_id"],
        },
    },
    {
        "name": "spotify_list_playlists",
        "description": "List playlists for the current authenticated Spotify user",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of playlists to return (max 50)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "spotify_create_playlist",
        "description": "Create a new playlist for the authenticated Spotify user",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Playlist name"},
                "description": {"type": "string", "description": "Playlist description"},
                "public": {"type": "boolean", "description": "Whether the playlist is public", "default": True},
                "collaborative": {"type": "boolean", "description": "Whether others can add tracks", "default": False},
            },
            "required": ["name"],
        },
    },
    {
        "name": "spotify_add_to_playlist",
        "description": "Add one or more tracks to a Spotify playlist",
        "parameters": {
            "type": "object",
            "properties": {
                "playlist_id": {"type": "string", "description": "Spotify playlist ID"},
                "uris": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Spotify track URIs e.g. spotify:track:XXXX (max 100)",
                },
                "position": {"type": "integer", "description": "Position to insert tracks (0-indexed, appends if omitted)"},
            },
            "required": ["playlist_id", "uris"],
        },
    },
    {
        "name": "spotify_get_artist",
        "description": "Get details and top tracks for a Spotify artist",
        "parameters": {
            "type": "object",
            "properties": {
                "artist_id": {"type": "string", "description": "Spotify artist ID"},
                "market": {"type": "string", "description": "ISO 3166-1 alpha-2 market code for top tracks", "default": "US"},
            },
            "required": ["artist_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("SPOTIFY_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "SPOTIFY_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "spotify_search_tracks":
                params: dict[str, Any] = {
                    "q": arguments["q"],
                    "type": arguments.get("type", "track"),
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "market" in arguments:
                    params["market"] = arguments["market"]
                r = await client.get(f"{BASE}/search", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "spotify_get_track":
                params = {}
                if "market" in arguments:
                    params["market"] = arguments["market"]
                r = await client.get(
                    f"{BASE}/tracks/{arguments['track_id']}",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "duration_ms": data.get("duration_ms"),
                    "popularity": data.get("popularity"),
                    "artists": [a.get("name") for a in data.get("artists", [])],
                    "album": data.get("album", {}).get("name"),
                    "preview_url": data.get("preview_url"),
                    "external_urls": data.get("external_urls"),
                }

            elif tool_name == "spotify_list_playlists":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                r = await client.get(f"{BASE}/me/playlists", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "playlists": [
                        {
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "tracks": p.get("tracks", {}).get("total"),
                            "public": p.get("public"),
                        }
                        for p in data.get("items", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "spotify_create_playlist":
                # First get current user ID
                me_r = await client.get(f"{BASE}/me", headers=headers)
                me_r.raise_for_status()
                user_id = me_r.json().get("id")
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "public": arguments.get("public", True),
                    "collaborative": arguments.get("collaborative", False),
                }
                if "description" in arguments:
                    payload["description"] = arguments["description"]
                r = await client.post(
                    f"{BASE}/users/{user_id}/playlists",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "name": data.get("name"), "external_urls": data.get("external_urls")}

            elif tool_name == "spotify_add_to_playlist":
                payload = {"uris": arguments["uris"][:100]}
                if "position" in arguments:
                    payload["position"] = arguments["position"]
                r = await client.post(
                    f"{BASE}/playlists/{arguments['playlist_id']}/tracks",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return {"snapshot_id": r.json().get("snapshot_id"), "added": len(arguments["uris"])}

            elif tool_name == "spotify_get_artist":
                artist_r = await client.get(f"{BASE}/artists/{arguments['artist_id']}", headers=headers)
                artist_r.raise_for_status()
                artist_data = artist_r.json()
                top_r = await client.get(
                    f"{BASE}/artists/{arguments['artist_id']}/top-tracks",
                    headers=headers,
                    params={"market": arguments.get("market", "US")},
                )
                top_r.raise_for_status()
                top_data = top_r.json()
                return {
                    "id": artist_data.get("id"),
                    "name": artist_data.get("name"),
                    "genres": artist_data.get("genres"),
                    "popularity": artist_data.get("popularity"),
                    "followers": artist_data.get("followers", {}).get("total"),
                    "top_tracks": [
                        {"id": t.get("id"), "name": t.get("name"), "popularity": t.get("popularity")}
                        for t in top_data.get("tracks", [])[:5]
                    ],
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("spotify_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
