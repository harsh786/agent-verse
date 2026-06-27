"""Perplexity MCP server — AI-powered search and research.

Environment:
  PERPLEXITY_API_KEY: Perplexity API key (pplx-...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

PERPLEXITY_BASE = "https://api.perplexity.ai"

TOOL_DEFINITIONS = [
    {
        "name": "perplexity_chat",
        "description": "Chat with Perplexity AI models with web-grounded responses",
        "parameters": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of {role, content} messages",
                },
                "model": {
                    "type": "string",
                    "default": "llama-3.1-sonar-large-128k-online",
                    "description": "Model: sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro",
                },
                "max_tokens": {"type": "integer", "default": 1024},
                "temperature": {"type": "number", "default": 0.2},
                "system": {
                    "type": "string",
                    "description": "System prompt (prepended as system message)",
                },
            },
            "required": ["messages"],
        },
    },
    {
        "name": "perplexity_search",
        "description": "Perform a web-grounded search query with Perplexity Sonar",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query or question"},
                "model": {
                    "type": "string",
                    "default": "llama-3.1-sonar-large-128k-online",
                    "description": "Online Sonar model for real-time web search",
                },
                "max_tokens": {"type": "integer", "default": 1024},
                "return_citations": {"type": "boolean", "default": True},
                "search_recency_filter": {
                    "type": "string",
                    "enum": ["month", "week", "day", "hour"],
                    "description": "Filter results by recency",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "perplexity_reasoning",
        "description": "Use Perplexity reasoning model for complex, multi-step analysis with web access",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context": {"type": "string", "description": "Additional context to include"},
                "model": {
                    "type": "string",
                    "default": "llama-3.1-sonar-huge-128k-online",
                },
                "max_tokens": {"type": "integer", "default": 2048},
            },
            "required": ["question"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("PERPLEXITY_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("PERPLEXITY_API_KEY", "")
    if not key:
        return {"error": "PERPLEXITY_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=PERPLEXITY_BASE, headers=_headers(), timeout=60.0) as c:
            if tool_name == "perplexity_chat":
                messages = list(arguments.get("messages", []))
                if sys := arguments.get("system"):
                    messages = [{"role": "system", "content": sys}] + messages
                payload: dict[str, Any] = {
                    "model": arguments.get("model", "llama-3.1-sonar-large-128k-online"),
                    "messages": messages,
                    "max_tokens": arguments.get("max_tokens", 1024),
                    "temperature": arguments.get("temperature", 0.2),
                }
                r = await c.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "model": data.get("model"),
                    "usage": data.get("usage", {}),
                    "citations": data.get("citations", []),
                }

            elif tool_name == "perplexity_search":
                payload = {
                    "model": arguments.get("model", "llama-3.1-sonar-large-128k-online"),
                    "messages": [
                        {"role": "user", "content": arguments["query"]}
                    ],
                    "max_tokens": arguments.get("max_tokens", 1024),
                    "return_citations": arguments.get("return_citations", True),
                }
                if recency := arguments.get("search_recency_filter"):
                    payload["search_recency_filter"] = recency
                r = await c.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "answer": data["choices"][0]["message"]["content"],
                    "citations": data.get("citations", []),
                    "model": data.get("model"),
                }

            elif tool_name == "perplexity_reasoning":
                content = arguments["question"]
                if ctx := arguments.get("context"):
                    content = f"Context: {ctx}\n\nQuestion: {content}"
                payload = {
                    "model": arguments.get("model", "llama-3.1-sonar-huge-128k-online"),
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": arguments.get("max_tokens", 2048),
                }
                r = await c.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "answer": data["choices"][0]["message"]["content"],
                    "citations": data.get("citations", []),
                    "usage": data.get("usage", {}),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("perplexity_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
