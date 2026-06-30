"""Yotpo MCP server — Yotpo reviews, loyalty points, and marketing campaigns.

Environment:
  YOTPO_APP_KEY: Yotpo application key (uToken)
  YOTPO_SECRET: Yotpo API secret for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.yotpo.com/core/v3"

TOOL_DEFINITIONS = [
    {
        "name": "yotpo_list_reviews",
        "description": "List reviews across the Yotpo account with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by review status: published, pending, spam, deleted"},
                "score": {"type": "integer", "description": "Filter by star rating (1-5)"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "count": {"type": "integer", "description": "Number of reviews per page (max 150)", "default": 20},
            },
        },
    },
    {
        "name": "yotpo_get_product_reviews",
        "description": "Get reviews for a specific product by product ID",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "The product ID as stored in Yotpo"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "count": {"type": "integer", "description": "Number of reviews per page (max 150)", "default": 20},
                "star": {"type": "integer", "description": "Filter by star rating (1-5)"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "yotpo_create_review_request",
        "description": "Send a review request email to a customer for a specific order",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_email": {"type": "string", "description": "Customer email address"},
                "customer_name": {"type": "string", "description": "Customer display name"},
                "order_id": {"type": "string", "description": "Order ID to request a review for"},
                "product_id": {"type": "string", "description": "Product ID to review"},
                "product_title": {"type": "string", "description": "Product title"},
            },
            "required": ["customer_email", "customer_name", "order_id", "product_id", "product_title"],
        },
    },
    {
        "name": "yotpo_get_loyalty_points",
        "description": "Get loyalty points balance and history for a customer",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_email": {"type": "string", "description": "Customer email address"},
            },
            "required": ["customer_email"],
        },
    },
    {
        "name": "yotpo_list_campaigns",
        "description": "List loyalty and referral campaigns in the Yotpo account",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Campaign type filter: earning_rule, redemption_option"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "count": {"type": "integer", "description": "Number of campaigns per page", "default": 20},
            },
        },
    },
    {
        "name": "yotpo_get_site_reviews",
        "description": "Get aggregated site-wide reviews and ratings summary",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "count": {"type": "integer", "description": "Number of reviews per page (max 150)", "default": 20},
                "status": {"type": "string", "description": "Filter by status: published, pending"},
            },
        },
    },
]


async def _get_utoken(client: httpx.AsyncClient, app_key: str, secret: str) -> str:
    """Exchange app_key+secret for a short-lived uToken."""
    r = await client.post(
        "https://api.yotpo.com/oauth/token",
        json={"client_id": app_key, "client_secret": secret, "grant_type": "client_credentials"},
    )
    r.raise_for_status()
    return r.json().get("access_token", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    app_key = os.getenv("YOTPO_APP_KEY", "")
    secret = os.getenv("YOTPO_SECRET", "")
    if not app_key:
        return {"error": "YOTPO_APP_KEY not configured"}
    if not secret:
        return {"error": "YOTPO_SECRET not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            utoken = await _get_utoken(client, app_key, secret)
            headers = {
                "X-Yotpo-Token": utoken,
                "Content-Type": "application/json",
            }

            if tool_name == "yotpo_list_reviews":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "count": arguments.get("count", 20),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                if "score" in arguments:
                    params["score"] = arguments["score"]
                r = await client.get(
                    f"https://api.yotpo.com/v1/apps/{app_key}/reviews",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "reviews": data.get("reviews", []),
                    "pagination": data.get("pagination", {}),
                }

            elif tool_name == "yotpo_get_product_reviews":
                params = {
                    "page": arguments.get("page", 1),
                    "count": arguments.get("count", 20),
                }
                if "star" in arguments:
                    params["star"] = arguments["star"]
                r = await client.get(
                    f"https://api.yotpo.com/v1/widget/{app_key}/products/{arguments['product_id']}/reviews.json",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                response_data = data.get("response", {})
                return {
                    "reviews": response_data.get("reviews", []),
                    "product": response_data.get("products", [{}])[0] if response_data.get("products") else {},
                    "pagination": response_data.get("pagination", {}),
                }

            elif tool_name == "yotpo_create_review_request":
                payload = {
                    "email": arguments["customer_email"],
                    "customer_name": arguments["customer_name"],
                    "order_id": arguments["order_id"],
                    "order_date": "",
                    "products": [
                        {
                            "product_id": arguments["product_id"],
                            "product_title": arguments["product_title"],
                        }
                    ],
                }
                r = await client.post(
                    f"https://api.yotpo.com/v1/apps/{app_key}/purchases",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return {"created": True, "customer_email": arguments["customer_email"]}

            elif tool_name == "yotpo_get_loyalty_points":
                r = await client.get(
                    f"{BASE}/loyalty/customers",
                    headers=headers,
                    params={"customer_email": arguments["customer_email"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "yotpo_list_campaigns":
                params = {
                    "page": arguments.get("page", 1),
                    "count": arguments.get("count", 20),
                }
                if "type" in arguments:
                    params["type"] = arguments["type"]
                r = await client.get(f"{BASE}/loyalty/campaigns", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "yotpo_get_site_reviews":
                params = {
                    "page": arguments.get("page", 1),
                    "count": arguments.get("count", 20),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(
                    f"https://api.yotpo.com/v1/apps/{app_key}/reviews",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "reviews": data.get("reviews", []),
                    "pagination": data.get("pagination", {}),
                    "stats": data.get("bottomline", {}),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("yotpo_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
