"""Alpaca MCP server — commission-free trading, market data, and portfolio management.

Environment:
  ALPACA_API_KEY: Alpaca API key ID
  ALPACA_SECRET_KEY: Alpaca secret key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"

TOOL_DEFINITIONS = [
    {
        "name": "alpaca_get_account",
        "description": "Get the Alpaca brokerage account details including buying power and equity",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "alpaca_list_positions",
        "description": "List all open positions in the Alpaca portfolio",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "alpaca_place_order",
        "description": "Place a market, limit, or stop order to buy or sell a security",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol (e.g. AAPL, TSLA)"},
                "qty": {"type": "number", "description": "Number of shares to buy/sell"},
                "notional": {"type": "number", "description": "Dollar amount for fractional trading"},
                "side": {"type": "string", "description": "Order side: buy or sell"},
                "type": {"type": "string", "description": "Order type: market, limit, stop"},
                "time_in_force": {"type": "string", "description": "Time in force: day, gtc, ioc, fok"},
                "limit_price": {"type": "number", "description": "Limit price for limit orders"},
                "stop_price": {"type": "number", "description": "Stop price for stop orders"},
            },
            "required": ["symbol", "side", "type", "time_in_force"],
        },
    },
    {
        "name": "alpaca_cancel_order",
        "description": "Cancel a pending or open order by order ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "UUID of the order to cancel"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "alpaca_get_market_data",
        "description": "Get latest quotes, trades, and bars for a stock symbol",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol"},
                "data_type": {"type": "string", "description": "Data type: quotes, trades, bars"},
                "timeframe": {"type": "string", "description": "Bar timeframe: 1Min, 5Min, 1Hour, 1Day"},
                "start": {"type": "string", "description": "Start datetime in RFC-3339 format"},
                "end": {"type": "string", "description": "End datetime in RFC-3339 format"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "alpaca_list_assets",
        "description": "List tradeable assets available on Alpaca with optional class filter",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Asset status: active or inactive"},
                "asset_class": {"type": "string", "description": "Asset class: us_equity or crypto"},
                "exchange": {"type": "string", "description": "Exchange filter (e.g. NYSE, NASDAQ)"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        return {"error": "ALPACA_API_KEY and ALPACA_SECRET_KEY not configured"}

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "alpaca_get_account":
                r = await client.get(f"{BASE_URL}/account", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "alpaca_list_positions":
                r = await client.get(f"{BASE_URL}/positions", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "alpaca_place_order":
                payload: dict[str, Any] = {
                    "symbol": arguments["symbol"],
                    "side": arguments["side"],
                    "type": arguments["type"],
                    "time_in_force": arguments["time_in_force"],
                }
                for k in ("qty", "notional", "limit_price", "stop_price"):
                    if k in arguments:
                        payload[k] = arguments[k]
                r = await client.post(f"{BASE_URL}/orders", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "alpaca_cancel_order":
                r = await client.delete(
                    f"{BASE_URL}/orders/{arguments['order_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return {"cancelled": True, "order_id": arguments["order_id"]}

            if tool_name == "alpaca_get_market_data":
                symbol = arguments["symbol"]
                data_type = arguments.get("data_type", "bars")
                params: dict[str, Any] = {}
                if "timeframe" in arguments:
                    params["timeframe"] = arguments["timeframe"]
                if "start" in arguments:
                    params["start"] = arguments["start"]
                if "end" in arguments:
                    params["end"] = arguments["end"]
                r = await client.get(
                    f"{DATA_URL}/stocks/{symbol}/{data_type}",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "alpaca_list_assets":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/assets", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
