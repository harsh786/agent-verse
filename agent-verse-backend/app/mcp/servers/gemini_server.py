"""Google Gemini MCP server — multimodal AI text generation and embeddings.

Environment:
  GEMINI_API_KEY: Google Gemini API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://generativelanguage.googleapis.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gemini_generate_text",
        "description": "Generate text content using a Gemini model given a text prompt",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The text prompt to generate content from"},
                "model": {"type": "string", "description": "Gemini model ID (default: gemini-pro)"},
                "max_output_tokens": {"type": "integer", "description": "Maximum tokens to generate"},
                "temperature": {"type": "number", "description": "Sampling temperature (0.0-1.0)"},
                "top_p": {"type": "number", "description": "Nucleus sampling parameter"},
                "top_k": {"type": "integer", "description": "Top-k sampling parameter"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "gemini_generate_with_image",
        "description": "Generate content from a text prompt combined with an image (multimodal)",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text prompt to accompany the image"},
                "image_url": {"type": "string", "description": "URL of the image to include"},
                "image_data": {"type": "string", "description": "Base64-encoded image data"},
                "mime_type": {"type": "string", "description": "MIME type of the image (e.g. image/jpeg)"},
                "model": {"type": "string", "description": "Gemini model ID (default: gemini-pro-vision)"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "gemini_list_models",
        "description": "List all available Gemini models with their capabilities and limits",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Number of models to return per page"},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
        },
    },
    {
        "name": "gemini_count_tokens",
        "description": "Count the number of tokens in a text prompt for a specific model",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text to count tokens for"},
                "model": {"type": "string", "description": "Gemini model ID (default: gemini-pro)"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "gemini_embed_text",
        "description": "Generate embeddings for text using the Gemini embedding model",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text content to embed"},
                "model": {"type": "string", "description": "Embedding model (default: embedding-001)"},
                "task_type": {"type": "string", "description": "Task type (RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, etc.)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "gemini_chat_completion",
        "description": "Have a multi-turn conversation with a Gemini model",
        "parameters": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "description": "Conversation history as array of {role, parts} objects",
                    "items": {"type": "object"},
                },
                "model": {"type": "string", "description": "Gemini model ID (default: gemini-pro)"},
                "max_output_tokens": {"type": "integer", "description": "Maximum tokens in the response"},
                "temperature": {"type": "number", "description": "Sampling temperature"},
            },
            "required": ["messages"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            if tool_name == "gemini_generate_text":
                model = arguments.get("model", "gemini-pro")
                payload: dict[str, Any] = {
                    "contents": [{"parts": [{"text": arguments["prompt"]}]}],
                }
                generation_config: dict[str, Any] = {}
                if "max_output_tokens" in arguments:
                    generation_config["maxOutputTokens"] = arguments["max_output_tokens"]
                if "temperature" in arguments:
                    generation_config["temperature"] = arguments["temperature"]
                if "top_p" in arguments:
                    generation_config["topP"] = arguments["top_p"]
                if "top_k" in arguments:
                    generation_config["topK"] = arguments["top_k"]
                if generation_config:
                    payload["generationConfig"] = generation_config
                r = await client.post(
                    f"{BASE_URL}/models/{model}:generateContent",
                    params={"key": api_key},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gemini_generate_with_image":
                model = arguments.get("model", "gemini-pro-vision")
                parts: list[dict[str, Any]] = [{"text": arguments["prompt"]}]
                if "image_data" in arguments:
                    parts.append({
                        "inlineData": {
                            "mimeType": arguments.get("mime_type", "image/jpeg"),
                            "data": arguments["image_data"],
                        }
                    })
                elif "image_url" in arguments:
                    parts.append({"fileData": {"fileUri": arguments["image_url"]}})
                r = await client.post(
                    f"{BASE_URL}/models/{model}:generateContent",
                    params={"key": api_key},
                    json={"contents": [{"parts": parts}]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gemini_list_models":
                params: dict[str, Any] = {"key": api_key}
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                if "page_token" in arguments:
                    params["pageToken"] = arguments["page_token"]
                r = await client.get(f"{BASE_URL}/models", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gemini_count_tokens":
                model = arguments.get("model", "gemini-pro")
                r = await client.post(
                    f"{BASE_URL}/models/{model}:countTokens",
                    params={"key": api_key},
                    json={"contents": [{"parts": [{"text": arguments["prompt"]}]}]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gemini_embed_text":
                model = arguments.get("model", "embedding-001")
                payload = {"content": {"parts": [{"text": arguments["text"]}]}}
                if "task_type" in arguments:
                    payload["taskType"] = arguments["task_type"]
                r = await client.post(
                    f"{BASE_URL}/models/{model}:embedContent",
                    params={"key": api_key},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gemini_chat_completion":
                model = arguments.get("model", "gemini-pro")
                payload = {"contents": arguments["messages"]}
                gen_cfg: dict[str, Any] = {}
                if "max_output_tokens" in arguments:
                    gen_cfg["maxOutputTokens"] = arguments["max_output_tokens"]
                if "temperature" in arguments:
                    gen_cfg["temperature"] = arguments["temperature"]
                if gen_cfg:
                    payload["generationConfig"] = gen_cfg
                r = await client.post(
                    f"{BASE_URL}/models/{model}:generateContent",
                    params={"key": api_key},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
