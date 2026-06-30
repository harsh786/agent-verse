"""ElevenLabs MCP server — AI text-to-speech and voice cloning.

Environment:
  ELEVENLABS_API_KEY: ElevenLabs API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.elevenlabs.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "elevenlabs_text_to_speech",
        "description": "Convert text to speech audio using a specified ElevenLabs voice",
        "parameters": {
            "type": "object",
            "properties": {
                "voice_id": {"type": "string", "description": "ID of the voice to use for synthesis"},
                "text": {"type": "string", "description": "Text content to convert to speech"},
                "model_id": {"type": "string", "description": "Model ID (e.g. eleven_monolingual_v1)"},
                "stability": {"type": "number", "description": "Voice stability (0.0-1.0)"},
                "similarity_boost": {"type": "number", "description": "Voice similarity boost (0.0-1.0)"},
                "style": {"type": "number", "description": "Style exaggeration (0.0-1.0)"},
            },
            "required": ["voice_id", "text"],
        },
    },
    {
        "name": "elevenlabs_list_voices",
        "description": "List all available voices including pre-made and cloned voices",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "elevenlabs_clone_voice",
        "description": "Create a cloned voice from audio samples",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the new cloned voice"},
                "description": {"type": "string", "description": "Description of the voice"},
                "labels": {"type": "object", "description": "Key-value labels for the voice"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "elevenlabs_get_voice",
        "description": "Get details of a specific voice by its ID including settings and samples",
        "parameters": {
            "type": "object",
            "properties": {
                "voice_id": {"type": "string", "description": "ID of the voice to retrieve"},
            },
            "required": ["voice_id"],
        },
    },
    {
        "name": "elevenlabs_list_models",
        "description": "List all available TTS models with their capabilities and supported languages",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "elevenlabs_get_usage_stats",
        "description": "Get character usage statistics for the current billing period",
        "parameters": {
            "type": "object",
            "properties": {
                "start_unix": {"type": "integer", "description": "Start of period as Unix timestamp"},
                "end_unix": {"type": "integer", "description": "End of period as Unix timestamp"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        return {"error": "ELEVENLABS_API_KEY not configured"}

    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            if tool_name == "elevenlabs_text_to_speech":
                voice_id = arguments["voice_id"]
                payload: dict[str, Any] = {
                    "text": arguments["text"],
                    "model_id": arguments.get("model_id", "eleven_monolingual_v1"),
                }
                voice_settings: dict[str, Any] = {}
                if "stability" in arguments:
                    voice_settings["stability"] = arguments["stability"]
                if "similarity_boost" in arguments:
                    voice_settings["similarity_boost"] = arguments["similarity_boost"]
                if "style" in arguments:
                    voice_settings["style"] = arguments["style"]
                if voice_settings:
                    payload["voice_settings"] = voice_settings
                r = await client.post(
                    f"{BASE_URL}/text-to-speech/{voice_id}",
                    headers={**headers, "Accept": "audio/mpeg"},
                    json=payload,
                )
                r.raise_for_status()
                return {"audio_content_length": len(r.content), "content_type": r.headers.get("content-type")}

            if tool_name == "elevenlabs_list_voices":
                r = await client.get(f"{BASE_URL}/voices", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "elevenlabs_clone_voice":
                r = await client.post(
                    f"{BASE_URL}/voices/add",
                    headers={"xi-api-key": api_key},
                    data={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "elevenlabs_get_voice":
                voice_id = arguments["voice_id"]
                r = await client.get(f"{BASE_URL}/voices/{voice_id}", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "elevenlabs_list_models":
                r = await client.get(f"{BASE_URL}/models", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "elevenlabs_get_usage_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/user/subscription", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
