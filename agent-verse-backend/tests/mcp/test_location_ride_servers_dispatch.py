"""Dispatch-level tests for location & ride MCP servers.

Exercises call_tool() branches and tool listing by mocking httpx.AsyncClient.
Targets: google_maps, uber.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    """Return a mock AsyncClient context manager."""
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# Google Maps
# ---------------------------------------------------------------------------

_MAPS = {"GOOGLE_MAPS_API_KEY": "maps-key"}


def test_maps_tool_listing():
    from app.mcp.servers.google_maps_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {
        "maps_geocode",
        "maps_directions",
        "maps_place_search",
        "maps_distance_matrix",
    }
    for t in TOOL_DEFINITIONS:
        assert t["description"]
        assert t["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_maps_geocode():
    from app.mcp.servers.google_maps_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={
                "status": "OK",
                "results": [
                    {
                        "formatted_address": "1600 Amphitheatre Pkwy, Mountain View, CA",
                        "geometry": {"location": {"lat": 37.4224, "lng": -122.0842}},
                        "place_id": "PID1",
                    }
                ],
            }
        )
    )
    with patch.dict("os.environ", _MAPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("maps_geocode", {"address": "1600 Amphitheatre Pkwy"})
    assert "error" not in result
    assert result["lat"] == 37.4224
    assert result["lng"] == -122.0842


@pytest.mark.asyncio
async def test_maps_directions():
    from app.mcp.servers.google_maps_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={
                "status": "OK",
                "routes": [
                    {
                        "summary": "US-101 N",
                        "legs": [
                            {
                                "distance": {"text": "5 km"},
                                "duration": {"text": "10 mins"},
                                "start_address": "A",
                                "end_address": "B",
                                "steps": [
                                    {
                                        "html_instructions": "Head north",
                                        "distance": {"text": "1 km"},
                                        "duration": {"text": "2 mins"},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    )
    with patch.dict("os.environ", _MAPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "maps_directions", {"origin": "A", "destination": "B", "mode": "driving"}
        )
    assert "error" not in result
    assert result["distance"] == "5 km"
    assert len(result["steps"]) == 1


@pytest.mark.asyncio
async def test_maps_place_search():
    from app.mcp.servers.google_maps_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={
                "status": "OK",
                "results": [
                    {
                        "name": "Blue Bottle Coffee",
                        "formatted_address": "66 Mint St",
                        "geometry": {"location": {"lat": 37.78, "lng": -122.40}},
                        "rating": 4.5,
                        "place_id": "PID2",
                    }
                ],
            }
        )
    )
    with patch.dict("os.environ", _MAPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "maps_place_search", {"query": "coffee", "near": "37.78,-122.40"}
        )
    assert "error" not in result
    assert result["places"][0]["name"] == "Blue Bottle Coffee"


@pytest.mark.asyncio
async def test_maps_distance_matrix():
    from app.mcp.servers.google_maps_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={
                "status": "OK",
                "origin_addresses": ["A"],
                "destination_addresses": ["B"],
                "rows": [
                    {
                        "elements": [
                            {
                                "status": "OK",
                                "distance": {"text": "5 km"},
                                "duration": {"text": "10 mins"},
                            }
                        ]
                    }
                ],
            }
        )
    )
    with patch.dict("os.environ", _MAPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "maps_distance_matrix", {"origins": ["A"], "destinations": ["B"]}
        )
    assert "error" not in result
    assert result["rows"][0]["elements"][0]["distance"] == "5 km"


@pytest.mark.asyncio
async def test_maps_missing_env():
    from app.mcp.servers.google_maps_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": ""}):
        os.environ.pop("GOOGLE_MAPS_API_KEY", None)
        result = await call_tool("maps_geocode", {"address": "x"})
    assert "error" in result


@pytest.mark.asyncio
async def test_maps_unknown_tool():
    from app.mcp.servers.google_maps_server import call_tool

    with patch.dict("os.environ", _MAPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mk_client()
        result = await call_tool("maps_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Uber
# ---------------------------------------------------------------------------

_UBER = {"UBER_ACCESS_TOKEN": "uber-tok"}


def test_uber_tool_listing():
    from app.mcp.servers.uber_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {"uber_estimate_ride", "uber_request_ride", "uber_ride_status"}
    for t in TOOL_DEFINITIONS:
        assert t["description"]
        assert t["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_uber_estimate_ride():
    from app.mcp.servers.uber_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={
                "prices": [
                    {
                        "display_name": "uberX",
                        "product_id": "PROD1",
                        "estimate": "$12-15",
                        "low_estimate": 12,
                        "high_estimate": 15,
                        "currency_code": "USD",
                        "distance": 3.2,
                        "duration": 600,
                    }
                ],
                "times": [{"product_id": "PROD1", "estimate": 180}],
            }
        )
    )
    with patch.dict("os.environ", _UBER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "uber_estimate_ride",
            {"pickup": {"lat": 37.77, "lng": -122.41}, "dropoff": {"lat": 37.80, "lng": -122.42}},
        )
    assert "error" not in result
    assert result["estimates"][0]["product"] == "uberX"
    assert result["estimates"][0]["pickup_eta_seconds"] == 180


@pytest.mark.asyncio
async def test_uber_request_ride():
    from app.mcp.servers.uber_server import call_tool

    mc = mk_client(
        post=make_resp(
            data={
                "request_id": "REQ1",
                "status": "processing",
                "product_id": "PROD1",
                "eta": 5,
                "surge_multiplier": 1.0,
            }
        )
    )
    with patch.dict("os.environ", _UBER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "uber_request_ride",
            {
                "pickup": {"lat": 37.77, "lng": -122.41},
                "dropoff": {"lat": 37.80, "lng": -122.42},
                "product_id": "PROD1",
            },
        )
    assert "error" not in result
    assert result["ride_id"] == "REQ1"
    assert result["status"] == "processing"


@pytest.mark.asyncio
async def test_uber_ride_status():
    from app.mcp.servers.uber_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={
                "request_id": "REQ1",
                "status": "accepted",
                "eta": 3,
                "driver": {"name": "Pat"},
                "vehicle": {"make": "Toyota"},
            }
        )
    )
    with patch.dict("os.environ", _UBER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("uber_ride_status", {"ride_id": "REQ1"})
    assert "error" not in result
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_uber_missing_env():
    from app.mcp.servers.uber_server import call_tool

    with patch.dict("os.environ", {"UBER_ACCESS_TOKEN": ""}):
        os.environ.pop("UBER_ACCESS_TOKEN", None)
        result = await call_tool("uber_ride_status", {"ride_id": "x"})
    assert "error" in result


@pytest.mark.asyncio
async def test_uber_unknown_tool():
    from app.mcp.servers.uber_server import call_tool

    with patch.dict("os.environ", _UBER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mk_client()
        result = await call_tool("uber_nonexistent", {})
    assert "error" in result
