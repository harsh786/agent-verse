"""X (Twitter) MCP server — tweets, users, and search via Twitter API v2.

Environment:
  TWITTER_BEARER_TOKEN:        OAuth 2.0 Bearer token (app-only, for reads)
  TWITTER_API_KEY:             API key (consumer key)
  TWITTER_API_SECRET:          API secret (consumer secret)
  TWITTER_ACCESS_TOKEN:        OAuth 1.0a access token (for write operations)
  TWITTER_ACCESS_TOKEN_SECRET: OAuth 1.0a access token secret (for write operations)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
import uuid
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TWITTER_BASE = "https://api.twitter.com/2"

TOOL_DEFINITIONS = [
    {
        "name": "twitter_search_tweets",
        "description": "Search recent tweets (last 7 days) using Twitter API v2",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Twitter search query, e.g. '#AI lang:en -is:retweet'",
                },
                "max_results": {"type": "integer", "default": 10, "description": "10–100"},
                "tweet_fields": {
                    "type": "string",
                    "default": "created_at,author_id,public_metrics,entities",
                },
                "expansions": {
                    "type": "string",
                    "description": "e.g. 'author_id' to include user info",
                },
                "next_token": {"type": "string", "description": "Pagination token"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "twitter_get_tweet",
        "description": "Get a tweet by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string"},
                "tweet_fields": {
                    "type": "string",
                    "default": "created_at,author_id,public_metrics,entities",
                },
            },
            "required": ["tweet_id"],
        },
    },
    {
        "name": "twitter_create_tweet",
        "description": "Post a new tweet (requires OAuth 1.0a credentials)",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Tweet text (max 280 chars)"},
                "reply_to_tweet_id": {"type": "string", "description": "Tweet ID to reply to"},
                "quote_tweet_id": {"type": "string"},
                "media_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text"],
        },
    },
    {
        "name": "twitter_delete_tweet",
        "description": "Delete a tweet by ID (requires OAuth 1.0a credentials)",
        "parameters": {
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string"},
            },
            "required": ["tweet_id"],
        },
    },
    {
        "name": "twitter_get_user_tweets",
        "description": "Get recent tweets by a user's ID",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
                "tweet_fields": {
                    "type": "string",
                    "default": "created_at,public_metrics",
                },
                "exclude": {
                    "type": "string",
                    "description": "Comma-separated exclusions: 'retweets,replies'",
                },
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "twitter_lookup_user",
        "description": "Look up a Twitter user by username",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Twitter username without @",
                },
                "user_fields": {
                    "type": "string",
                    "default": "name,username,description,public_metrics,profile_image_url",
                },
            },
            "required": ["username"],
        },
    },
    {
        "name": "twitter_follow_user",
        "description": "Follow a Twitter user (requires OAuth 1.0a credentials and authenticated user ID)",
        "parameters": {
            "type": "object",
            "properties": {
                "my_user_id": {"type": "string", "description": "Authenticated user's ID"},
                "target_user_id": {"type": "string", "description": "User ID to follow"},
            },
            "required": ["my_user_id", "target_user_id"],
        },
    },
]


def _bearer_headers() -> dict[str, str]:
    token = os.getenv("TWITTER_BEARER_TOKEN", "")
    return {"Authorization": f"Bearer {token}"}


def _oauth1_header(method: str, url: str, params: dict | None = None) -> dict[str, str]:
    """Generate OAuth 1.0a Authorization header for write operations."""
    api_key = os.getenv("TWITTER_API_KEY", "")
    api_secret = os.getenv("TWITTER_API_SECRET", "")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN", "")
    access_token_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }

    all_params = {**oauth_params, **(params or {})}
    sorted_params = sorted(all_params.items())
    param_string = "&".join(
        f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted_params
    )
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(param_string, safe=""),
    ])
    signing_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(access_token_secret, safe='')}"
    signature = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    import base64
    oauth_params["oauth_signature"] = base64.b64encode(signature).decode()

    auth_header = "OAuth " + ", ".join(
        f'{urllib.parse.quote(str(k), safe="")}="{urllib.parse.quote(str(v), safe="")}"'
        for k, v in sorted(oauth_params.items())
    )
    return {"Authorization": auth_header, "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    bearer = os.getenv("TWITTER_BEARER_TOKEN", "")
    if not bearer:
        return {"error": "TWITTER_BEARER_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "twitter_search_tweets":
                params: dict[str, Any] = {
                    "query": arguments["query"],
                    "max_results": min(arguments.get("max_results", 10), 100),
                    "tweet.fields": arguments.get("tweet_fields", "created_at,author_id,public_metrics"),
                }
                if exp := arguments.get("expansions"):
                    params["expansions"] = exp
                if nt := arguments.get("next_token"):
                    params["next_token"] = nt
                r = await c.get(
                    f"{TWITTER_BASE}/tweets/search/recent",
                    params=params,
                    headers=_bearer_headers(),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "twitter_get_tweet":
                params = {"tweet.fields": arguments.get("tweet_fields", "created_at,author_id,public_metrics")}
                r = await c.get(
                    f"{TWITTER_BASE}/tweets/{arguments['tweet_id']}",
                    params=params,
                    headers=_bearer_headers(),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "twitter_create_tweet":
                url = f"{TWITTER_BASE}/tweets"
                payload: dict[str, Any] = {"text": arguments["text"]}
                if reply_to := arguments.get("reply_to_tweet_id"):
                    payload["reply"] = {"in_reply_to_tweet_id": reply_to}
                if quote := arguments.get("quote_tweet_id"):
                    payload["quote_tweet_id"] = quote
                if media := arguments.get("media_ids"):
                    payload["media"] = {"media_ids": media}
                headers = _oauth1_header("POST", url)
                r = await c.post(url, json=payload, headers=headers)
                r.raise_for_status()
                return r.json()

            elif tool_name == "twitter_delete_tweet":
                tid = arguments["tweet_id"]
                url = f"{TWITTER_BASE}/tweets/{tid}"
                headers = _oauth1_header("DELETE", url)
                r = await c.delete(url, headers=headers)
                r.raise_for_status()
                return r.json()

            elif tool_name == "twitter_get_user_tweets":
                uid = arguments["user_id"]
                params = {
                    "max_results": arguments.get("max_results", 10),
                    "tweet.fields": arguments.get("tweet_fields", "created_at,public_metrics"),
                }
                if excl := arguments.get("exclude"):
                    params["exclude"] = excl
                r = await c.get(
                    f"{TWITTER_BASE}/users/{uid}/tweets",
                    params=params,
                    headers=_bearer_headers(),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "twitter_lookup_user":
                r = await c.get(
                    f"{TWITTER_BASE}/users/by/username/{arguments['username']}",
                    params={"user.fields": arguments.get("user_fields", "name,username,description,public_metrics")},
                    headers=_bearer_headers(),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "twitter_follow_user":
                uid = arguments["my_user_id"]
                url = f"{TWITTER_BASE}/users/{uid}/following"
                payload = {"target_user_id": arguments["target_user_id"]}
                headers = _oauth1_header("POST", url)
                r = await c.post(url, json=payload, headers=headers)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("twitter_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
