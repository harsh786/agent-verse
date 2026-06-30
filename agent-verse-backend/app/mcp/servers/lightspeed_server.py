"""Lightspeed MCP server — Lightspeed Retail POS products, sales, customers, and inventory.

Environment:
  LIGHTSPEED_ACCESS_TOKEN: Lightspeed OAuth2 access token
  LIGHTSPEED_ACCOUNT_ID: Lightspeed account ID (numeric)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "lightspeed_list_products",
        "description": "List products/items in the Lightspeed Retail account",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Search by description or system SKU"},
                "category_id": {"type": "integer", "description": "Filter by category ID"},
                "limit": {"type": "integer", "description": "Number of items to return (max 100)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "lightspeed_create_sale",
        "description": "Create a new sale/transaction in Lightspeed Retail",
        "parameters": {
            "type": "object",
            "properties": {
                "register_id": {"type": "integer", "description": "Register ID to assign the sale to"},
                "employee_id": {"type": "integer", "description": "Employee ID creating the sale"},
                "customer_id": {"type": "integer", "description": "Customer ID to associate with the sale"},
                "sale_lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id": {"type": "integer"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                        },
                    },
                    "description": "Line items in the sale",
                },
            },
            "required": ["register_id"],
        },
    },
    {
        "name": "lightspeed_list_sales",
        "description": "List sales/transactions from the Lightspeed Retail account",
        "parameters": {
            "type": "object",
            "properties": {
                "completed": {"type": "boolean", "description": "Filter by completed status"},
                "date_from": {"type": "string", "description": "Start date filter in ISO 8601 format"},
                "date_to": {"type": "string", "description": "End date filter in ISO 8601 format"},
                "limit": {"type": "integer", "description": "Number of sales to return (max 100)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "lightspeed_list_customers",
        "description": "List customers in the Lightspeed Retail account",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "description": "Filter by first name"},
                "last_name": {"type": "string", "description": "Filter by last name"},
                "email": {"type": "string", "description": "Filter by email address"},
                "limit": {"type": "integer", "description": "Number of customers to return (max 100)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "lightspeed_create_customer",
        "description": "Create a new customer in Lightspeed Retail",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "description": "Customer first name"},
                "last_name": {"type": "string", "description": "Customer last name"},
                "email": {"type": "string", "description": "Customer email address"},
                "phone": {"type": "string", "description": "Customer phone number"},
                "company": {"type": "string", "description": "Company name"},
            },
            "required": ["first_name", "last_name"],
        },
    },
    {
        "name": "lightspeed_get_inventory",
        "description": "Get current inventory levels for items in Lightspeed Retail",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "Specific item ID to check inventory"},
                "shop_id": {"type": "integer", "description": "Shop/location ID to filter inventory"},
                "limit": {"type": "integer", "description": "Number of inventory records to return", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("LIGHTSPEED_ACCESS_TOKEN", "")
    account_id = os.getenv("LIGHTSPEED_ACCOUNT_ID", "")
    if not access_token:
        return {"error": "LIGHTSPEED_ACCESS_TOKEN not configured"}
    if not account_id:
        return {"error": "LIGHTSPEED_ACCOUNT_ID not configured"}

    base = f"https://api.lightspeedapp.com/API/V3/Account/{account_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "lightspeed_list_products":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                    "load_relations": '["Category","ItemShops"]',
                }
                if "search" in arguments:
                    params["description"] = f"~,{arguments['search']}"
                if "category_id" in arguments:
                    params["categoryID"] = arguments["category_id"]
                r = await client.get(f"{base}/Item.json", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                items = data.get("Item", [])
                if isinstance(items, dict):
                    items = [items]
                return {
                    "items": [
                        {
                            "item_id": i.get("itemID"),
                            "description": i.get("description"),
                            "sku": i.get("systemSku"),
                            "price": i.get("Prices", {}).get("ItemPrice", [{}])[0].get("amount") if i.get("Prices") else None,
                        }
                        for i in items
                    ],
                    "total": int(data.get("@attributes", {}).get("count", 0)),
                }

            elif tool_name == "lightspeed_create_sale":
                payload: dict[str, Any] = {
                    "registerID": arguments["register_id"],
                    "completed": False,
                }
                if "employee_id" in arguments:
                    payload["employeeID"] = arguments["employee_id"]
                if "customer_id" in arguments:
                    payload["customerID"] = arguments["customer_id"]
                r = await client.post(f"{base}/Sale.json", headers=headers, json={"Sale": payload})
                r.raise_for_status()
                data = r.json()
                sale = data.get("Sale", {})
                sale_id = sale.get("saleID")
                if "sale_lines" in arguments and sale_id:
                    for line in arguments["sale_lines"]:
                        line_payload = {
                            "saleID": sale_id,
                            "itemID": line.get("item_id"),
                            "unitQuantity": line.get("quantity", 1),
                            "unitPrice": line.get("unit_price"),
                        }
                        await client.post(f"{base}/SaleLine.json", headers=headers, json={"SaleLine": line_payload})
                return {"sale_id": sale_id, "completed": sale.get("completed")}

            elif tool_name == "lightspeed_list_sales":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "completed" in arguments:
                    params["completed"] = "true" if arguments["completed"] else "false"
                if "date_from" in arguments:
                    params["timeStamp"] = f">,{arguments['date_from']}"
                r = await client.get(f"{base}/Sale.json", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                sales = data.get("Sale", [])
                if isinstance(sales, dict):
                    sales = [sales]
                return {
                    "sales": [
                        {
                            "sale_id": s.get("saleID"),
                            "total": s.get("calcTotal"),
                            "completed": s.get("completed"),
                            "time_stamp": s.get("timeStamp"),
                        }
                        for s in sales
                    ],
                    "total": int(data.get("@attributes", {}).get("count", 0)),
                }

            elif tool_name == "lightspeed_list_customers":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "first_name" in arguments:
                    params["firstName"] = f"~,{arguments['first_name']}"
                if "last_name" in arguments:
                    params["lastName"] = f"~,{arguments['last_name']}"
                if "email" in arguments:
                    params["Contact.email"] = arguments["email"]
                r = await client.get(f"{base}/Customer.json", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                customers = data.get("Customer", [])
                if isinstance(customers, dict):
                    customers = [customers]
                return {
                    "customers": [
                        {
                            "customer_id": c.get("customerID"),
                            "first_name": c.get("firstName"),
                            "last_name": c.get("lastName"),
                            "email": c.get("Contact", {}).get("email") if isinstance(c.get("Contact"), dict) else None,
                        }
                        for c in customers
                    ],
                    "total": int(data.get("@attributes", {}).get("count", 0)),
                }

            elif tool_name == "lightspeed_create_customer":
                payload = {
                    "firstName": arguments["first_name"],
                    "lastName": arguments["last_name"],
                }
                if "company" in arguments:
                    payload["company"] = arguments["company"]
                contact: dict[str, Any] = {}
                if "email" in arguments:
                    contact["email"] = arguments["email"]
                if "phone" in arguments:
                    contact["phone"] = arguments["phone"]
                if contact:
                    payload["Contact"] = contact
                r = await client.post(f"{base}/Customer.json", headers=headers, json={"Customer": payload})
                r.raise_for_status()
                data = r.json()
                c = data.get("Customer", {})
                return {"customer_id": c.get("customerID"), "first_name": c.get("firstName"), "last_name": c.get("lastName")}

            elif tool_name == "lightspeed_get_inventory":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "item_id" in arguments:
                    params["itemID"] = arguments["item_id"]
                if "shop_id" in arguments:
                    params["shopID"] = arguments["shop_id"]
                r = await client.get(f"{base}/ItemShop.json", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                inv = data.get("ItemShop", [])
                if isinstance(inv, dict):
                    inv = [inv]
                return {
                    "inventory": [
                        {
                            "item_id": i.get("itemID"),
                            "shop_id": i.get("shopID"),
                            "quantity": i.get("qoh"),
                            "reorder_point": i.get("reorderPoint"),
                        }
                        for i in inv
                    ],
                    "total": int(data.get("@attributes", {}).get("count", 0)),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("lightspeed_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
