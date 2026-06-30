"""Moralis Web3 MCP server — blockchain data, NFT collections, and wallet analytics.

Environment:
  MORALIS_API_KEY: Moralis API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://deep-index.moralis.io/api/v2.2"

TOOL_DEFINITIONS = [
    {
        "name": "moralis_get_native_balance",
        "description": "Get the native token balance for a wallet address on a blockchain",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Wallet address (0x...)"},
                "chain": {"type": "string", "description": "Chain identifier: eth, polygon, bsc, etc."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "moralis_get_token_price",
        "description": "Get the current price and market data for an ERC-20 token",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token contract address"},
                "chain": {"type": "string", "description": "Chain identifier"},
                "exchange": {"type": "string", "description": "DEX to query (e.g. uniswapv3)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "moralis_get_nft_collections",
        "description": "Get all NFT collections owned by a wallet address",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Wallet address"},
                "chain": {"type": "string", "description": "Chain identifier"},
                "limit": {"type": "integer", "description": "Maximum results"},
                "cursor": {"type": "string", "description": "Pagination cursor"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "moralis_get_wallet_history",
        "description": "Get complete transaction history for a wallet address",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Wallet address"},
                "chain": {"type": "string", "description": "Chain identifier"},
                "from_date": {"type": "string", "description": "Start date in YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date in YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Maximum transactions"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "moralis_get_contract_events",
        "description": "Get events emitted by a smart contract",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Contract address"},
                "topic": {"type": "string", "description": "Event topic hash (keccak256 of event signature)"},
                "chain": {"type": "string", "description": "Chain identifier"},
                "from_block": {"type": "integer", "description": "Start block number"},
                "to_block": {"type": "integer", "description": "End block number"},
            },
            "required": ["address", "topic"],
        },
    },
    {
        "name": "moralis_search_nfts",
        "description": "Search for NFTs by metadata, name, or description",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for NFT metadata"},
                "chain": {"type": "string", "description": "Chain to search on"},
                "filter": {"type": "string", "description": "Filter field: name, description, global"},
                "limit": {"type": "integer", "description": "Maximum results"},
            },
            "required": ["query"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("MORALIS_API_KEY", "")
    if not api_key:
        return {"error": "MORALIS_API_KEY not configured"}

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            chain = arguments.get("chain", "eth")

            if tool_name == "moralis_get_native_balance":
                r = await client.get(
                    f"{BASE_URL}/{arguments['address']}/balance",
                    headers=headers,
                    params={"chain": chain},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "moralis_get_token_price":
                params: dict[str, Any] = {"chain": chain}
                if "exchange" in arguments:
                    params["exchange"] = arguments["exchange"]
                r = await client.get(
                    f"{BASE_URL}/erc20/{arguments['address']}/price",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "moralis_get_nft_collections":
                params = {"chain": chain}
                if "limit" in arguments:
                    params["limit"] = arguments["limit"]
                if "cursor" in arguments:
                    params["cursor"] = arguments["cursor"]
                r = await client.get(
                    f"{BASE_URL}/{arguments['address']}/nft/collections",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "moralis_get_wallet_history":
                params = {"chain": chain}
                if "from_date" in arguments:
                    params["from_date"] = arguments["from_date"]
                if "to_date" in arguments:
                    params["to_date"] = arguments["to_date"]
                if "limit" in arguments:
                    params["limit"] = arguments["limit"]
                r = await client.get(
                    f"{BASE_URL}/{arguments['address']}",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "moralis_get_contract_events":
                params = {
                    "chain": chain,
                    "topic": arguments["topic"],
                }
                if "from_block" in arguments:
                    params["from_block"] = arguments["from_block"]
                if "to_block" in arguments:
                    params["to_block"] = arguments["to_block"]
                r = await client.get(
                    f"{BASE_URL}/{arguments['address']}/events",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "moralis_search_nfts":
                params = {
                    "q": arguments["query"],
                    "chain": chain,
                    "filter": arguments.get("filter", "global"),
                }
                if "limit" in arguments:
                    params["limit"] = arguments["limit"]
                r = await client.get(
                    f"{BASE_URL}/nft/search",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
