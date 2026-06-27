"""OpenAI MCP server — comprehensive OpenAI API integration.

Environment:
  OPENAI_API_KEY: OpenAI API key (sk-...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

OPENAI_BASE = "https://api.openai.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "openai_chat_completion",
        "description": "Generate a chat completion using OpenAI models (GPT-4o, GPT-4, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of messages in {role, content} format",
                },
                "model": {
                    "type": "string",
                    "default": "gpt-4o",
                    "description": "Model ID, e.g. 'gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo'",
                },
                "temperature": {"type": "number", "default": 0.7},
                "max_tokens": {"type": "integer", "default": 2000},
                "stream": {"type": "boolean", "default": False},
                "response_format": {
                    "type": "object",
                    "description": "e.g. {'type': 'json_object'} for JSON mode",
                },
                "system": {
                    "type": "string",
                    "description": "System prompt (shorthand — prepended as system message)",
                },
            },
            "required": ["messages"],
        },
    },
    {
        "name": "openai_create_embedding",
        "description": "Create vector embeddings for text using OpenAI embedding models",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {
                    "description": "Text string or array of strings to embed",
                },
                "model": {
                    "type": "string",
                    "default": "text-embedding-3-small",
                    "description": "Embedding model: text-embedding-3-small, text-embedding-3-large, text-embedding-ada-002",
                },
                "encoding_format": {
                    "type": "string",
                    "enum": ["float", "base64"],
                    "default": "float",
                },
                "dimensions": {
                    "type": "integer",
                    "description": "Number of dimensions (text-embedding-3 models only)",
                },
            },
            "required": ["input"],
        },
    },
    {
        "name": "openai_generate_image",
        "description": "Generate images using DALL-E 3 or DALL-E 2",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Image description"},
                "model": {
                    "type": "string",
                    "enum": ["dall-e-3", "dall-e-2"],
                    "default": "dall-e-3",
                },
                "n": {"type": "integer", "default": 1, "description": "Number of images"},
                "size": {
                    "type": "string",
                    "enum": ["256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"],
                    "default": "1024x1024",
                },
                "quality": {
                    "type": "string",
                    "enum": ["standard", "hd"],
                    "default": "standard",
                },
                "style": {
                    "type": "string",
                    "enum": ["vivid", "natural"],
                    "default": "vivid",
                },
                "response_format": {
                    "type": "string",
                    "enum": ["url", "b64_json"],
                    "default": "url",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "openai_edit_image",
        "description": "Edit an image using DALL-E 2 with a mask and prompt",
        "parameters": {
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "URL or base64 PNG image"},
                "prompt": {"type": "string"},
                "mask_url": {"type": "string", "description": "Optional mask PNG"},
                "n": {"type": "integer", "default": 1},
                "size": {
                    "type": "string",
                    "enum": ["256x256", "512x512", "1024x1024"],
                    "default": "1024x1024",
                },
            },
            "required": ["image_url", "prompt"],
        },
    },
    {
        "name": "openai_text_to_speech",
        "description": "Convert text to speech using OpenAI TTS models",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "Text to convert (max 4096 chars)"},
                "model": {
                    "type": "string",
                    "enum": ["tts-1", "tts-1-hd"],
                    "default": "tts-1",
                },
                "voice": {
                    "type": "string",
                    "enum": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
                    "default": "alloy",
                },
                "response_format": {
                    "type": "string",
                    "enum": ["mp3", "opus", "aac", "flac", "wav", "pcm"],
                    "default": "mp3",
                },
                "speed": {"type": "number", "description": "Speech speed 0.25–4.0", "default": 1.0},
            },
            "required": ["input"],
        },
    },
    {
        "name": "openai_speech_to_text",
        "description": "Transcribe audio to text using OpenAI Whisper",
        "parameters": {
            "type": "object",
            "properties": {
                "file_url": {"type": "string", "description": "URL to audio file"},
                "model": {"type": "string", "default": "whisper-1"},
                "language": {
                    "type": "string",
                    "description": "ISO-639-1 language code (optional, auto-detected if omitted)",
                },
                "prompt": {"type": "string", "description": "Optional context prompt"},
                "response_format": {
                    "type": "string",
                    "enum": ["json", "text", "srt", "verbose_json", "vtt"],
                    "default": "json",
                },
            },
            "required": ["file_url"],
        },
    },
    {
        "name": "openai_list_models",
        "description": "List all available OpenAI models",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "openai_create_file",
        "description": "Upload a file to OpenAI for use with fine-tuning or assistants",
        "parameters": {
            "type": "object",
            "properties": {
                "file_content": {"type": "string", "description": "JSONL file content as string"},
                "filename": {"type": "string", "description": "Name for the uploaded file"},
                "purpose": {
                    "type": "string",
                    "enum": ["fine-tune", "assistants", "batch", "vision"],
                    "default": "fine-tune",
                },
            },
            "required": ["file_content", "filename"],
        },
    },
    {
        "name": "openai_fine_tune",
        "description": "Create a fine-tuning job to train a custom model",
        "parameters": {
            "type": "object",
            "properties": {
                "training_file": {"type": "string", "description": "File ID of training data"},
                "model": {
                    "type": "string",
                    "default": "gpt-3.5-turbo",
                    "description": "Base model to fine-tune",
                },
                "validation_file": {"type": "string"},
                "n_epochs": {"type": "integer", "description": "Number of training epochs"},
                "suffix": {"type": "string", "description": "Custom suffix for model name"},
            },
            "required": ["training_file"],
        },
    },
    {
        "name": "openai_list_assistants",
        "description": "List OpenAI Assistants",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "after": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "openai_create_assistant",
        "description": "Create a new OpenAI Assistant",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "instructions": {"type": "string", "description": "System prompt / instructions"},
                "model": {"type": "string", "default": "gpt-4o"},
                "tools": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Tools available to the assistant, e.g. [{'type': 'code_interpreter'}]",
                },
                "file_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File IDs to attach",
                },
            },
            "required": ["name", "instructions", "model"],
        },
    },
    {
        "name": "openai_create_thread",
        "description": "Create a new OpenAI Assistants thread",
        "parameters": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Initial messages to seed the thread",
                },
                "metadata": {"type": "object"},
            },
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("OPENAI_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "assistants=v2",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return {"error": "OPENAI_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=OPENAI_BASE, headers=_headers(), timeout=60.0
        ) as c:
            if tool_name == "openai_chat_completion":
                messages = list(arguments.get("messages", []))
                if sys := arguments.get("system"):
                    messages = [{"role": "system", "content": sys}] + messages
                payload: dict[str, Any] = {
                    "model": arguments.get("model", "gpt-4o"),
                    "messages": messages,
                    "temperature": arguments.get("temperature", 0.7),
                    "max_tokens": arguments.get("max_tokens", 2000),
                }
                if rf := arguments.get("response_format"):
                    payload["response_format"] = rf
                r = await c.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "usage": data.get("usage", {}),
                    "model": data.get("model"),
                    "finish_reason": data["choices"][0].get("finish_reason"),
                }

            elif tool_name == "openai_create_embedding":
                payload = {
                    "model": arguments.get("model", "text-embedding-3-small"),
                    "input": arguments["input"],
                    "encoding_format": arguments.get("encoding_format", "float"),
                }
                if dims := arguments.get("dimensions"):
                    payload["dimensions"] = dims
                r = await c.post("/embeddings", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "openai_generate_image":
                payload = {
                    "model": arguments.get("model", "dall-e-3"),
                    "prompt": arguments["prompt"],
                    "n": arguments.get("n", 1),
                    "size": arguments.get("size", "1024x1024"),
                    "quality": arguments.get("quality", "standard"),
                    "style": arguments.get("style", "vivid"),
                    "response_format": arguments.get("response_format", "url"),
                }
                r = await c.post("/images/generations", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "openai_edit_image":
                return {
                    "info": "Image edit requires multipart/form-data upload. "
                    "Use the OpenAI SDK directly for this operation.",
                    "prompt": arguments.get("prompt"),
                }

            elif tool_name == "openai_text_to_speech":
                payload = {
                    "model": arguments.get("model", "tts-1"),
                    "input": arguments["input"],
                    "voice": arguments.get("voice", "alloy"),
                    "response_format": arguments.get("response_format", "mp3"),
                    "speed": arguments.get("speed", 1.0),
                }
                r = await c.post("/audio/speech", json=payload)
                r.raise_for_status()
                return {
                    "content_type": r.headers.get("content-type", "audio/mpeg"),
                    "size_bytes": len(r.content),
                    "note": "Audio binary returned. Use the OpenAI SDK for direct playback.",
                }

            elif tool_name == "openai_speech_to_text":
                return {
                    "info": "Transcription requires multipart/form-data upload. "
                    "Use the OpenAI SDK or pass the file URL via SDK.",
                    "file_url": arguments.get("file_url"),
                    "model": arguments.get("model", "whisper-1"),
                }

            elif tool_name == "openai_list_models":
                r = await c.get("/models")
                r.raise_for_status()
                data = r.json()
                return {
                    "models": [
                        {"id": m["id"], "owned_by": m.get("owned_by"), "created": m.get("created")}
                        for m in data.get("data", [])
                    ]
                }

            elif tool_name == "openai_create_file":
                content = arguments["file_content"].encode("utf-8")
                files = {
                    "file": (arguments["filename"], content, "application/octet-stream"),
                    "purpose": (None, arguments.get("purpose", "fine-tune")),
                }
                headers = {"Authorization": f"Bearer {key}"}
                async with httpx.AsyncClient(timeout=60.0) as fc:
                    r = await fc.post(f"{OPENAI_BASE}/files", headers=headers, files=files)
                r.raise_for_status()
                return r.json()

            elif tool_name == "openai_fine_tune":
                payload = {
                    "training_file": arguments["training_file"],
                    "model": arguments.get("model", "gpt-3.5-turbo"),
                }
                if vf := arguments.get("validation_file"):
                    payload["validation_file"] = vf
                if n := arguments.get("n_epochs"):
                    payload["hyperparameters"] = {"n_epochs": n}
                if suffix := arguments.get("suffix"):
                    payload["suffix"] = suffix
                r = await c.post("/fine_tuning/jobs", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "openai_list_assistants":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "order": arguments.get("order", "desc"),
                }
                if after := arguments.get("after"):
                    params["after"] = after
                r = await c.get("/assistants", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "openai_create_assistant":
                payload = {
                    "name": arguments["name"],
                    "instructions": arguments["instructions"],
                    "model": arguments.get("model", "gpt-4o"),
                }
                if tools := arguments.get("tools"):
                    payload["tools"] = tools
                if file_ids := arguments.get("file_ids"):
                    payload["file_ids"] = file_ids
                r = await c.post("/assistants", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "openai_create_thread":
                payload = {}
                if messages := arguments.get("messages"):
                    payload["messages"] = messages
                if metadata := arguments.get("metadata"):
                    payload["metadata"] = metadata
                r = await c.post("/threads", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("openai_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
