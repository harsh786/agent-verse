"""Amadeus MCP server — travel search, flight booking, and hotel discovery.

Environment:
  AMADEUS_CLIENT_ID: Amadeus API client ID
  AMADEUS_CLIENT_SECRET: Amadeus API client secret
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.amadeus.com/v2"
AUTH_URL = "https://api.amadeus.com/v1/security/oauth2/token"

TOOL_DEFINITIONS = [
    {
        "name": "amadeus_search_flights",
        "description": "Search for available flight offers between origin and destination",
        "parameters": {
            "type": "object",
            "properties": {
                "origin_location_code": {"type": "string", "description": "IATA code of departure airport (e.g. JFK)"},
                "destination_location_code": {"type": "string", "description": "IATA code of arrival airport (e.g. LAX)"},
                "departure_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                "return_date": {"type": "string", "description": "Return date for round trips (YYYY-MM-DD)"},
                "adults": {"type": "integer", "description": "Number of adult travelers"},
                "max_results": {"type": "integer", "description": "Maximum flight offers to return"},
                "currency_code": {"type": "string", "description": "Currency for pricing (e.g. USD)"},
            },
            "required": ["origin_location_code", "destination_location_code", "departure_date"],
        },
    },
    {
        "name": "amadeus_search_hotels",
        "description": "Search for hotels in a city with availability and rates",
        "parameters": {
            "type": "object",
            "properties": {
                "city_code": {"type": "string", "description": "IATA city code (e.g. PAR for Paris)"},
                "check_in_date": {"type": "string", "description": "Check-in date in YYYY-MM-DD"},
                "check_out_date": {"type": "string", "description": "Check-out date in YYYY-MM-DD"},
                "adults": {"type": "integer", "description": "Number of adult guests"},
                "ratings": {
                    "type": "array",
                    "description": "Hotel star ratings to filter by (1-5)",
                    "items": {"type": "integer"},
                },
            },
            "required": ["city_code"],
        },
    },
    {
        "name": "amadeus_get_flight_prices",
        "description": "Get historical and trending flight price analysis for a route",
        "parameters": {
            "type": "object",
            "properties": {
                "origin_iata_code": {"type": "string", "description": "Origin airport IATA code"},
                "destination_iata_code": {"type": "string", "description": "Destination airport IATA code"},
                "departure_date": {"type": "string", "description": "Departure date YYYY-MM-DD"},
                "one_way": {"type": "boolean", "description": "True for one-way, false for round-trip"},
            },
            "required": ["origin_iata_code", "destination_iata_code", "departure_date"],
        },
    },
    {
        "name": "amadeus_book_flight",
        "description": "Create a flight order from a selected flight offer",
        "parameters": {
            "type": "object",
            "properties": {
                "flight_offer": {"type": "object", "description": "Flight offer object from search results"},
                "traveler_info": {
                    "type": "array",
                    "description": "Traveler details (name, DOB, passport, contact)",
                    "items": {"type": "object"},
                },
            },
            "required": ["flight_offer", "traveler_info"],
        },
    },
    {
        "name": "amadeus_get_airport_info",
        "description": "Get information about an airport by IATA code",
        "parameters": {
            "type": "object",
            "properties": {
                "iata_code": {"type": "string", "description": "Airport IATA code (e.g. JFK)"},
            },
            "required": ["iata_code"],
        },
    },
    {
        "name": "amadeus_search_activities",
        "description": "Search for tours, activities, and experiences near a location",
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude of the search center"},
                "longitude": {"type": "number", "description": "Longitude of the search center"},
                "radius": {"type": "integer", "description": "Search radius in kilometers"},
            },
            "required": ["latitude", "longitude"],
        },
    },
]


async def _get_token(client: httpx.AsyncClient) -> str:
    client_id = os.getenv("AMADEUS_CLIENT_ID", "")
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET", "")
    r = await client.post(
        AUTH_URL,
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    client_id = os.getenv("AMADEUS_CLIENT_ID", "")
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {"error": "AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            token = await _get_token(client)
            headers = {"Authorization": f"Bearer {token}"}

            if tool_name == "amadeus_search_flights":
                params: dict[str, Any] = {
                    "originLocationCode": arguments["origin_location_code"],
                    "destinationLocationCode": arguments["destination_location_code"],
                    "departureDate": arguments["departure_date"],
                    "adults": arguments.get("adults", 1),
                }
                if "return_date" in arguments:
                    params["returnDate"] = arguments["return_date"]
                if "max_results" in arguments:
                    params["max"] = arguments["max_results"]
                if "currency_code" in arguments:
                    params["currencyCode"] = arguments["currency_code"]
                r = await client.get(f"{BASE_URL}/shopping/flight-offers", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "amadeus_search_hotels":
                params = {"cityCode": arguments["city_code"]}
                if "check_in_date" in arguments:
                    params["checkInDate"] = arguments["check_in_date"]
                if "check_out_date" in arguments:
                    params["checkOutDate"] = arguments["check_out_date"]
                if "adults" in arguments:
                    params["adults"] = arguments["adults"]
                if "ratings" in arguments:
                    params["ratings"] = ",".join(str(r) for r in arguments["ratings"])
                r = await client.get(f"{BASE_URL}/shopping/hotel-offers", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "amadeus_get_flight_prices":
                params = {
                    "originIataCode": arguments["origin_iata_code"],
                    "destinationIataCode": arguments["destination_iata_code"],
                    "departureDate": arguments["departure_date"],
                    "oneWay": str(arguments.get("one_way", True)).lower(),
                }
                r = await client.get(
                    "https://api.amadeus.com/v1/analytics/itinerary-price-metrics",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "amadeus_book_flight":
                payload = {
                    "data": {
                        "type": "flight-order",
                        "flightOffers": [arguments["flight_offer"]],
                        "travelers": arguments["traveler_info"],
                    }
                }
                r = await client.post(
                    f"{BASE_URL}/booking/flight-orders",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "amadeus_get_airport_info":
                r = await client.get(
                    "https://api.amadeus.com/v1/reference-data/locations",
                    headers=headers,
                    params={"keyword": arguments["iata_code"], "subType": "AIRPORT"},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "amadeus_search_activities":
                params = {
                    "latitude": arguments["latitude"],
                    "longitude": arguments["longitude"],
                    "radius": arguments.get("radius", 1),
                }
                r = await client.get(
                    "https://api.amadeus.com/v1/shopping/activities",
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
