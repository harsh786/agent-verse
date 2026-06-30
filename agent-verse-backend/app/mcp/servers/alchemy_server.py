"""Alchemy MCP server — blockchain data, NFTs, and Web3 development.

Environment:
  ALCHEMY_API_KEY: Alchemy API key
  ALCHEMY_NETWORK: Alchemy network identifier (e.g. eth-mainnet, polygon-mainnet)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    network = os.getenv("ALCHEMY_NETWORK", "eth-mainnet")
    api_key = os.getenv("ALCHEMY_API_KEY", "")
    return f"https://{network}.g.alchemy.com/v2/{api_key}"


TOOL_DEFINITIONS = [
    {
        "name": "alchemy_get_balance",
        "description": "Get the native token balance (ETH, MATIC, etc.) for a wallet address",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Wallet address (0x...)"},
                "block": {"type": "string", "description": "Block number or 'latest', 'earliest'"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "alchemy_get_token_balances",
        "description": "Get ERC-20 token balances for a wallet address",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Wallet address"},
                "token_addresses": {
                    "type": "array",
                    "description": "Optional list of specific token contract addresses to query",
                    "items": {"type": "string"},
                },
            },
            "required": ["address"],
        },
    },
    {
        "name": "alchemy_get_nfts",
        "description": "Get NFTs owned by a wallet address across all contracts",
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Wallet address of the NFT owner"},
                "contract_addresses": {
                    "type": "array",
                    "description": "Filter by specific NFT contract addresses",
                    "items": {"type": "string"},
                },
                "page_key": {"type": "string", "description": "Pagination key from previous response"},
                "page_size": {"type": "integer", "description": "NFTs per page (max 100)"},
            },
            "required": ["owner"],
        },
    },
    {
        "name": "alchemy_transfer_nft",
        "description": "Get transfer history for an NFT contract or specific token",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_address": {"type": "string", "description": "NFT contract address"},
                "token_id": {"type": "string", "description": "Token ID in hex format"},
                "from_block": {"type": "string", "description": "Start block for transfer history"},
            },
            "required": ["contract_address"],
        },
    },
    {
        "name": "alchemy_get_transaction",
        "description": "Get transaction details by transaction hash",
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_hash": {"type": "string", "description": "Transaction hash (0x...)"},
            },
            "required": ["transaction_hash"],
        },
    },
    {
        "name": "alchemy_get_gas_price",
        "description": "Get the current gas price and EIP-1559 fee estimates",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("ALCHEMY_API_KEY", "")
    network = os.getenv("ALCHEMY_NETWORK", "eth-mainnet")
    if not api_key:
        return {"error": "ALCHEMY_API_KEY not configured"}

    base_url = _base_url()
    nft_base = f"https://{network}.g.alchemy.com/nft/v3/{api_key}"
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "alchemy_get_balance":
                r = await client.post(
                    base_url,
                    headers=headers,
                    json={
                        "id": 1,
                        "jsonrpc": "2.0",
                        "method": "eth_getBalance",
                        "params": [arguments["address"], arguments.get("block", "latest")],
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "alchemy_get_token_balances":
                params_list = [arguments["address"]]
                if "token_addresses" in arguments:
                    params_list.append(arguments["token_addresses"])
                else:
                    params_list.append("erc20")
                r = await client.post(
                    base_url,
                    headers=headers,
                    json={
                        "id": 1,
                        "jsonrpc": "2.0",
                        "method": "alchemy_getTokenBalances",
                        "params": params_list,
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "alchemy_get_nfts":
                params: dict[str, Any] = {"owner": arguments["owner"]}
                if "contract_addresses" in arguments:
                    params["contractAddresses"] = arguments["contract_addresses"]
                if "page_key" in arguments:
                    params["pageKey"] = arguments["page_key"]
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                r = await client.get(f"{nft_base}/getNFTsForOwner", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "alchemy_transfer_nft":
                params = {"contractAddress": arguments["contract_address"]}
                if "token_id" in arguments:
                    params["tokenId"] = arguments["token_id"]
                if "from_block" in arguments:
                    params["fromBlock"] = arguments["from_block"]
                r = await client.get(f"{nft_base}/getTransfersForContract", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "alchemy_get_transaction":
                r = await client.post(
                    base_url,
                    headers=headers,
                    json={
                        "id": 1,
                        "jsonrpc": "2.0",
                        "method": "eth_getTransactionByHash",
                        "params": [arguments["transaction_hash"]],
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "alchemy_get_gas_price":
                r = await client.post(
                    base_url,
                    headers=headers,
                    json={"id": 1, "jsonrpc": "2.0", "method": "eth_gasPrice", "params": []},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
