"""SAP ERP MCP server — purchase orders, materials, inventory, and procurement.

Environment:
  SAP_CLIENT_ID: SAP OAuth2 client ID
  SAP_CLIENT_SECRET: SAP OAuth2 client secret
  SAP_BASE_URL: SAP system base URL (e.g. https://mycompany.s4hana.cloud)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    base = os.getenv("SAP_BASE_URL", "")
    return f"{base.rstrip('/')}/sap/opu/odata/sap" if base else ""


TOOL_DEFINITIONS = [
    {
        "name": "sap_list_purchase_orders",
        "description": "List purchase orders from SAP ERP with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "vendor": {"type": "string", "description": "Filter by vendor number"},
                "plant": {"type": "string", "description": "Filter by plant code"},
                "status": {"type": "string", "description": "Filter by status: A (active), B (blocked)"},
                "top": {"type": "integer", "description": "Maximum records to return"},
            },
        },
    },
    {
        "name": "sap_create_purchase_order",
        "description": "Create a new purchase order in SAP ERP",
        "parameters": {
            "type": "object",
            "properties": {
                "vendor": {"type": "string", "description": "Vendor number"},
                "plant": {"type": "string", "description": "Receiving plant code"},
                "items": {
                    "type": "array",
                    "description": "PO line items with material, quantity, and price",
                    "items": {"type": "object"},
                },
                "currency": {"type": "string", "description": "Currency code (e.g. USD)"},
            },
            "required": ["vendor", "plant", "items"],
        },
    },
    {
        "name": "sap_list_materials",
        "description": "List material master records in SAP with optional search",
        "parameters": {
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "Plant code"},
                "material_type": {"type": "string", "description": "Material type (e.g. ROH, FERT)"},
                "search": {"type": "string", "description": "Search by material description"},
                "top": {"type": "integer", "description": "Maximum records"},
            },
        },
    },
    {
        "name": "sap_get_inventory",
        "description": "Get current stock levels for materials in a plant",
        "parameters": {
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "Plant code"},
                "material": {"type": "string", "description": "Specific material number"},
                "storage_location": {"type": "string", "description": "Storage location code"},
            },
        },
    },
    {
        "name": "sap_list_vendors",
        "description": "List vendor master records in SAP",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Search by vendor name or number"},
                "country": {"type": "string", "description": "Filter by country code"},
                "top": {"type": "integer", "description": "Maximum records"},
            },
        },
    },
    {
        "name": "sap_create_goods_receipt",
        "description": "Post a goods receipt for a purchase order in SAP",
        "parameters": {
            "type": "object",
            "properties": {
                "purchase_order": {"type": "string", "description": "Purchase order number"},
                "plant": {"type": "string", "description": "Receiving plant code"},
                "items": {
                    "type": "array",
                    "description": "Items received with quantities",
                    "items": {"type": "object"},
                },
                "posting_date": {"type": "string", "description": "Goods receipt date YYYY-MM-DD"},
            },
            "required": ["purchase_order", "plant"],
        },
    },
]


async def _get_token(client: httpx.AsyncClient) -> str:
    client_id = os.getenv("SAP_CLIENT_ID", "")
    client_secret = os.getenv("SAP_CLIENT_SECRET", "")
    sap_base = os.getenv("SAP_BASE_URL", "")
    r = await client.post(
        f"{sap_base}/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    r.raise_for_status()
    return r.json().get("access_token", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    client_id = os.getenv("SAP_CLIENT_ID", "")
    client_secret = os.getenv("SAP_CLIENT_SECRET", "")
    sap_base = os.getenv("SAP_BASE_URL", "")
    if not client_id or not client_secret:
        return {"error": "SAP_CLIENT_ID and SAP_CLIENT_SECRET not configured"}
    if not sap_base:
        return {"error": "SAP_BASE_URL not configured"}

    base_url = _base_url()
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            token = await _get_token(client)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }

            if tool_name == "sap_list_purchase_orders":
                params: dict[str, Any] = {"$format": "json"}
                if "vendor" in arguments:
                    params["$filter"] = f"Supplier eq '{arguments['vendor']}'"
                if "top" in arguments:
                    params["$top"] = arguments["top"]
                r = await client.get(
                    f"{base_url}/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sap_create_purchase_order":
                payload: dict[str, Any] = {
                    "Supplier": arguments["vendor"],
                    "Plant": arguments["plant"],
                    "to_PurchaseOrderItem": {"results": arguments["items"]},
                }
                if "currency" in arguments:
                    payload["DocumentCurrency"] = arguments["currency"]
                r = await client.post(
                    f"{base_url}/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sap_list_materials":
                params = {"$format": "json"}
                if "top" in arguments:
                    params["$top"] = arguments["top"]
                r = await client.get(
                    f"{base_url}/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentItem",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sap_get_inventory":
                params = {"$format": "json"}
                filters = []
                if "plant" in arguments:
                    filters.append(f"Plant eq '{arguments['plant']}'")
                if "material" in arguments:
                    filters.append(f"Material eq '{arguments['material']}'")
                if filters:
                    params["$filter"] = " and ".join(filters)
                r = await client.get(
                    f"{base_url}/API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sap_list_vendors":
                params = {"$format": "json"}
                if "top" in arguments:
                    params["$top"] = arguments["top"]
                r = await client.get(
                    f"{base_url}/API_BUSINESS_PARTNER/A_Supplier",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sap_create_goods_receipt":
                payload = {
                    "PurchaseOrder": arguments["purchase_order"],
                    "Plant": arguments["plant"],
                    "PostingDate": arguments.get("posting_date", ""),
                    "to_MaterialDocumentItem": {"results": arguments.get("items", [])},
                }
                r = await client.post(
                    f"{base_url}/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentHeader",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
