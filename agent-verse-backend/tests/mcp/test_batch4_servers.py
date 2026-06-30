"""Unit tests for batch-4 MCP servers (servers 1-25).

Covers: Etsy, eBay, Ecwid, Magento, Squarespace, Lightspeed, ShipStation,
        Order Desk, Yotpo, Gumroad, Kajabi, Teachable, Thinkific, Substack,
        Storyblok, Vimeo, Wistia, Spotify, Pinterest, Hootsuite, Sprout Social,
        Buffer, Facebook Pages, Facebook Lead Ads, Facebook Conversions API.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ===========================================================================
# 1. ETSY
# ===========================================================================

_ETSY_ENV = {"ETSY_API_KEY": "key123", "ETSY_ACCESS_TOKEN": "tok123"}


@pytest.mark.asyncio
async def test_etsy_list_shops():
    from app.mcp.servers.etsy_server import call_tool

    resp = make_resp(200, {"results": [{"shop_id": "1", "shop_name": "CoolShop", "listing_active_count": 5, "currency_code": "USD"}], "count": 1})
    with patch.dict("os.environ", _ETSY_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("etsy_list_shops", {})
    assert "shops" in result
    assert result["shops"][0]["shop_name"] == "CoolShop"


@pytest.mark.asyncio
async def test_etsy_list_listings():
    from app.mcp.servers.etsy_server import call_tool

    resp = make_resp(200, {"results": [{"listing_id": "101", "title": "Blue Ring", "price": 20, "quantity": 3, "state": "active"}], "count": 1})
    with patch.dict("os.environ", _ETSY_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("etsy_list_listings", {"shop_id": "1"})
    assert "listings" in result
    assert result["listings"][0]["title"] == "Blue Ring"


@pytest.mark.asyncio
async def test_etsy_create_listing():
    from app.mcp.servers.etsy_server import call_tool

    resp = make_resp(201, {"listing_id": "202", "title": "New Art", "state": "draft"})
    with patch.dict("os.environ", _ETSY_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("etsy_create_listing", {
            "shop_id": "1", "title": "New Art", "description": "desc", "price": 15.0, "quantity": 2, "taxonomy_id": 68
        })
    assert result["listing_id"] == "202"


@pytest.mark.asyncio
async def test_etsy_get_shop_stats():
    from app.mcp.servers.etsy_server import call_tool

    resp = make_resp(200, {"shop_id": "1", "shop_name": "CoolShop", "listing_active_count": 10, "review_count": 50, "review_average": 4.8, "currency_code": "USD"})
    with patch.dict("os.environ", _ETSY_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("etsy_get_shop_stats", {"shop_id": "1"})
    assert result["shop_name"] == "CoolShop"


@pytest.mark.asyncio
async def test_etsy_missing_key():
    from app.mcp.servers.etsy_server import call_tool

    with patch.dict("os.environ", {"ETSY_API_KEY": "", "ETSY_ACCESS_TOKEN": "tok"}):
        result = await call_tool("etsy_list_shops", {})
    assert "error" in result and "ETSY_API_KEY" in result["error"]


# ===========================================================================
# 2. EBAY
# ===========================================================================

_EBAY_ENV = {"EBAY_APP_ID": "app123", "EBAY_OAUTH_TOKEN": "oauthxxx"}


@pytest.mark.asyncio
async def test_ebay_search_items():
    from app.mcp.servers.ebay_server import call_tool

    resp = make_resp(200, {"itemSummaries": [{"itemId": "i1", "title": "Vintage Watch", "price": {"value": "50"}, "condition": "Used"}], "total": 1})
    with patch.dict("os.environ", _EBAY_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("ebay_search_items", {"q": "vintage watch"})
    assert "items" in result
    assert result["items"][0]["title"] == "Vintage Watch"


@pytest.mark.asyncio
async def test_ebay_get_item():
    from app.mcp.servers.ebay_server import call_tool

    resp = make_resp(200, {"itemId": "v1|123|0", "title": "Camera", "price": {"value": "200"}, "condition": "New"})
    with patch.dict("os.environ", _EBAY_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("ebay_get_item", {"item_id": "v1|123|0"})
    assert result["title"] == "Camera"


@pytest.mark.asyncio
async def test_ebay_list_user_orders():
    from app.mcp.servers.ebay_server import call_tool

    resp = make_resp(200, {"orders": [{"orderId": "o1", "orderFulfillmentStatus": "NOT_STARTED"}], "total": 1})
    with patch.dict("os.environ", _EBAY_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("ebay_list_user_orders", {})
    assert "orders" in result


@pytest.mark.asyncio
async def test_ebay_missing_token():
    from app.mcp.servers.ebay_server import call_tool

    with patch.dict("os.environ", {"EBAY_APP_ID": "x", "EBAY_OAUTH_TOKEN": ""}):
        result = await call_tool("ebay_search_items", {"q": "test"})
    assert "EBAY_OAUTH_TOKEN" in result["error"]


# ===========================================================================
# 3. ECWID
# ===========================================================================

_ECWID_ENV = {"ECWID_SECRET_TOKEN": "secret123", "ECWID_STORE_ID": "99999"}


@pytest.mark.asyncio
async def test_ecwid_list_products():
    from app.mcp.servers.ecwid_server import call_tool

    resp = make_resp(200, {"items": [{"id": 1, "name": "T-Shirt", "price": 25.0, "sku": "TS01", "quantity": 10, "enabled": True}], "total": 1, "count": 1})
    with patch.dict("os.environ", _ECWID_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("ecwid_list_products", {})
    assert result["products"][0]["name"] == "T-Shirt"


@pytest.mark.asyncio
async def test_ecwid_create_product():
    from app.mcp.servers.ecwid_server import call_tool

    resp = make_resp(200, {"id": 42})
    with patch.dict("os.environ", _ECWID_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("ecwid_create_product", {"name": "Hoodie", "price": 50.0})
    assert result["id"] == 42


@pytest.mark.asyncio
async def test_ecwid_list_orders():
    from app.mcp.servers.ecwid_server import call_tool

    resp = make_resp(200, {"items": [{"id": 1, "orderNumber": "ORD001", "total": 75.0, "paymentStatus": "PAID", "fulfillmentStatus": "AWAITING_PROCESSING", "email": "a@b.com"}], "total": 1})
    with patch.dict("os.environ", _ECWID_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("ecwid_list_orders", {})
    assert result["orders"][0]["order_number"] == "ORD001"


@pytest.mark.asyncio
async def test_ecwid_missing_store_id():
    from app.mcp.servers.ecwid_server import call_tool

    with patch.dict("os.environ", {"ECWID_SECRET_TOKEN": "x", "ECWID_STORE_ID": ""}):
        result = await call_tool("ecwid_list_products", {})
    assert "ECWID_STORE_ID" in result["error"]


# ===========================================================================
# 4. MAGENTO
# ===========================================================================

_MAGENTO_ENV = {"MAGENTO_ACCESS_TOKEN": "magtoken", "MAGENTO_BASE_URL": "https://store.example.com"}


@pytest.mark.asyncio
async def test_magento_list_products():
    from app.mcp.servers.magento_server import call_tool

    resp = make_resp(200, {"items": [{"id": 1, "sku": "PROD-1", "name": "Widget", "price": 9.99, "status": 1, "type_id": "simple"}], "total_count": 1})
    with patch.dict("os.environ", _MAGENTO_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("magento_list_products", {})
    assert result["products"][0]["sku"] == "PROD-1"


@pytest.mark.asyncio
async def test_magento_create_product():
    from app.mcp.servers.magento_server import call_tool

    resp = make_resp(200, {"id": 5, "sku": "NEW-1", "name": "Gadget"})
    with patch.dict("os.environ", _MAGENTO_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("magento_create_product", {"sku": "NEW-1", "name": "Gadget", "price": 29.99})
    assert result["sku"] == "NEW-1"


@pytest.mark.asyncio
async def test_magento_list_orders():
    from app.mcp.servers.magento_server import call_tool

    resp = make_resp(200, {"items": [{"entity_id": "10", "increment_id": "000000010", "status": "pending", "grand_total": 100.0, "customer_email": "c@d.com", "created_at": "2024-01-01"}], "total_count": 1})
    with patch.dict("os.environ", _MAGENTO_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("magento_list_orders", {"status": "pending"})
    assert result["orders"][0]["increment_id"] == "000000010"


@pytest.mark.asyncio
async def test_magento_missing_base_url():
    from app.mcp.servers.magento_server import call_tool

    with patch.dict("os.environ", {"MAGENTO_ACCESS_TOKEN": "x", "MAGENTO_BASE_URL": ""}):
        result = await call_tool("magento_list_products", {})
    assert "MAGENTO_BASE_URL" in result["error"]


# ===========================================================================
# 5. SQUARESPACE
# ===========================================================================

_SS_ENV = {"SQUARESPACE_API_KEY": "sqkey123"}


@pytest.mark.asyncio
async def test_squarespace_list_products():
    from app.mcp.servers.squarespace_server import call_tool

    resp = make_resp(200, {"products": [{"id": "p1", "name": "Mug", "type": "PHYSICAL", "isVisible": True}], "pagination": {"hasNextPage": False}})
    with patch.dict("os.environ", _SS_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("squarespace_list_products", {})
    assert result["products"][0]["name"] == "Mug"


@pytest.mark.asyncio
async def test_squarespace_list_orders():
    from app.mcp.servers.squarespace_server import call_tool

    resp = make_resp(200, {"result": [{"id": "o1", "orderNumber": "1001", "fulfillmentStatus": "PENDING", "grandTotal": {"value": "50.00"}, "createdOn": "2024-01-01", "customerEmail": "x@y.com"}], "pagination": {}})
    with patch.dict("os.environ", _SS_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("squarespace_list_orders", {})
    assert result["orders"][0]["order_number"] == "1001"


@pytest.mark.asyncio
async def test_squarespace_get_order():
    from app.mcp.servers.squarespace_server import call_tool

    resp = make_resp(200, {"id": "o1", "orderNumber": "1001"})
    with patch.dict("os.environ", _SS_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("squarespace_get_order", {"order_id": "o1"})
    assert result["id"] == "o1"


@pytest.mark.asyncio
async def test_squarespace_missing_key():
    from app.mcp.servers.squarespace_server import call_tool

    with patch.dict("os.environ", {"SQUARESPACE_API_KEY": ""}):
        result = await call_tool("squarespace_list_products", {})
    assert "SQUARESPACE_API_KEY" in result["error"]


# ===========================================================================
# 6. LIGHTSPEED
# ===========================================================================

_LS_ENV = {"LIGHTSPEED_ACCESS_TOKEN": "lstok", "LIGHTSPEED_ACCOUNT_ID": "12345"}


@pytest.mark.asyncio
async def test_lightspeed_list_products():
    from app.mcp.servers.lightspeed_server import call_tool

    resp = make_resp(200, {"Item": [{"itemID": "1", "description": "Widget", "systemSku": "WGT-01", "Prices": None}], "@attributes": {"count": "1"}})
    with patch.dict("os.environ", _LS_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("lightspeed_list_products", {})
    assert result["items"][0]["item_id"] == "1"


@pytest.mark.asyncio
async def test_lightspeed_list_customers():
    from app.mcp.servers.lightspeed_server import call_tool

    resp = make_resp(200, {"Customer": [{"customerID": "10", "firstName": "Alice", "lastName": "Smith", "Contact": {"email": "alice@example.com"}}], "@attributes": {"count": "1"}})
    with patch.dict("os.environ", _LS_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("lightspeed_list_customers", {})
    assert result["customers"][0]["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_lightspeed_create_customer():
    from app.mcp.servers.lightspeed_server import call_tool

    resp = make_resp(200, {"Customer": {"customerID": "20", "firstName": "Bob", "lastName": "Jones"}})
    with patch.dict("os.environ", _LS_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("lightspeed_create_customer", {"first_name": "Bob", "last_name": "Jones"})
    assert result["customer_id"] == "20"


@pytest.mark.asyncio
async def test_lightspeed_missing_account():
    from app.mcp.servers.lightspeed_server import call_tool

    with patch.dict("os.environ", {"LIGHTSPEED_ACCESS_TOKEN": "x", "LIGHTSPEED_ACCOUNT_ID": ""}):
        result = await call_tool("lightspeed_list_products", {})
    assert "LIGHTSPEED_ACCOUNT_ID" in result["error"]


# ===========================================================================
# 7. SHIPSTATION
# ===========================================================================

_SHIP_ENV = {"SHIPSTATION_API_KEY": "sskey", "SHIPSTATION_API_SECRET": "sssecret"}


@pytest.mark.asyncio
async def test_shipstation_list_orders():
    from app.mcp.servers.shipstation_server import call_tool

    resp = make_resp(200, {"orders": [{"orderId": 1, "orderNumber": "SS-001", "orderStatus": "awaiting_shipment", "orderTotal": 45.0, "customerEmail": "e@f.com", "orderDate": "2024-01-01"}], "total": 1, "page": 1, "pages": 1})
    with patch.dict("os.environ", _SHIP_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("shipstation_list_orders", {})
    assert result["orders"][0]["order_number"] == "SS-001"


@pytest.mark.asyncio
async def test_shipstation_list_carriers():
    from app.mcp.servers.shipstation_server import call_tool

    resp = make_resp(200, [{"code": "stamps_com", "name": "Stamps.com"}])
    with patch.dict("os.environ", _SHIP_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("shipstation_list_carriers", {})
    assert "carriers" in result


@pytest.mark.asyncio
async def test_shipstation_list_stores():
    from app.mcp.servers.shipstation_server import call_tool

    resp = make_resp(200, [{"storeId": 1, "storeName": "My Store"}])
    with patch.dict("os.environ", _SHIP_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("shipstation_list_stores", {})
    assert "stores" in result


@pytest.mark.asyncio
async def test_shipstation_missing_secret():
    from app.mcp.servers.shipstation_server import call_tool

    with patch.dict("os.environ", {"SHIPSTATION_API_KEY": "x", "SHIPSTATION_API_SECRET": ""}):
        result = await call_tool("shipstation_list_orders", {})
    assert "SHIPSTATION_API_SECRET" in result["error"]


# ===========================================================================
# 8. ORDER DESK
# ===========================================================================

_OD_ENV = {"ORDER_DESK_STORE_ID": "store1", "ORDER_DESK_API_KEY": "odkey"}


@pytest.mark.asyncio
async def test_order_desk_list_orders():
    from app.mcp.servers.order_desk_server import call_tool

    resp = make_resp(200, {"orders": [{"id": "1", "order_id": "ORD-001", "fulfillment_status": "pending", "payment_status": "paid", "email": "g@h.com", "order_total": 30.0}], "count": 1})
    with patch.dict("os.environ", _OD_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("order_desk_list_orders", {})
    assert result["orders"][0]["order_id"] == "ORD-001"


@pytest.mark.asyncio
async def test_order_desk_list_inventory():
    from app.mcp.servers.order_desk_server import call_tool

    resp = make_resp(200, {"inventory_items": [{"id": "i1", "name": "Widget", "code": "WGT", "stock": 100}], "count": 1})
    with patch.dict("os.environ", _OD_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("order_desk_list_inventory", {})
    assert "items" in result


@pytest.mark.asyncio
async def test_order_desk_create_shipment():
    from app.mcp.servers.order_desk_server import call_tool

    resp = make_resp(200, {"shipment": {"id": "s1"}})
    with patch.dict("os.environ", _OD_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("order_desk_create_shipment", {"order_id": "1", "carrier": "UPS", "tracking_number": "1Z999999"})
    assert result["created"] is True


@pytest.mark.asyncio
async def test_order_desk_missing_keys():
    from app.mcp.servers.order_desk_server import call_tool

    with patch.dict("os.environ", {"ORDER_DESK_STORE_ID": "", "ORDER_DESK_API_KEY": "x"}):
        result = await call_tool("order_desk_list_orders", {})
    assert "ORDER_DESK_STORE_ID" in result["error"]


# ===========================================================================
# 9. YOTPO
# ===========================================================================

_YOTPO_ENV = {"YOTPO_APP_KEY": "yapp", "YOTPO_SECRET": "ysecret"}


@pytest.mark.asyncio
async def test_yotpo_list_reviews():
    from app.mcp.servers.yotpo_server import call_tool

    token_resp = make_resp(200, {"access_token": "utoken123"})
    reviews_resp = make_resp(200, {"reviews": [{"id": 1, "score": 5, "content": "Great!"}], "pagination": {}})

    mc = mk_client(get=reviews_resp, post=token_resp)
    with patch.dict("os.environ", _YOTPO_ENV), patch("httpx.AsyncClient", return_value=mc):
        result = await call_tool("yotpo_list_reviews", {})
    assert "reviews" in result


@pytest.mark.asyncio
async def test_yotpo_missing_secret():
    from app.mcp.servers.yotpo_server import call_tool

    with patch.dict("os.environ", {"YOTPO_APP_KEY": "x", "YOTPO_SECRET": ""}):
        result = await call_tool("yotpo_list_reviews", {})
    assert "YOTPO_SECRET" in result["error"]


# ===========================================================================
# 10. GUMROAD
# ===========================================================================

_GR_ENV = {"GUMROAD_ACCESS_TOKEN": "grtoken"}


@pytest.mark.asyncio
async def test_gumroad_list_products():
    from app.mcp.servers.gumroad_server import call_tool

    resp = make_resp(200, {"success": True, "products": [{"id": "abc", "name": "E-Book", "price": 999, "sales_count": 10, "published": True}]})
    with patch.dict("os.environ", _GR_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("gumroad_list_products", {})
    assert result["products"][0]["name"] == "E-Book"


@pytest.mark.asyncio
async def test_gumroad_create_product():
    from app.mcp.servers.gumroad_server import call_tool

    resp = make_resp(201, {"success": True, "product": {"id": "xyz", "name": "Course"}})
    with patch.dict("os.environ", _GR_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("gumroad_create_product", {"name": "Course", "price": 1999})
    assert result["id"] == "xyz"


@pytest.mark.asyncio
async def test_gumroad_list_sales():
    from app.mcp.servers.gumroad_server import call_tool

    resp = make_resp(200, {"success": True, "sales": [{"id": "s1", "product_name": "E-Book", "price": 999, "gumroad_fee": 99, "created_at": "2024-01-01", "purchaser_id": "u1"}]})
    with patch.dict("os.environ", _GR_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("gumroad_list_sales", {})
    assert result["sales"][0]["product_name"] == "E-Book"


@pytest.mark.asyncio
async def test_gumroad_missing_token():
    from app.mcp.servers.gumroad_server import call_tool

    with patch.dict("os.environ", {"GUMROAD_ACCESS_TOKEN": ""}):
        result = await call_tool("gumroad_list_products", {})
    assert "GUMROAD_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 11. KAJABI
# ===========================================================================

_KJ_ENV = {"KAJABI_API_KEY": "kjkey"}


@pytest.mark.asyncio
async def test_kajabi_list_products():
    from app.mcp.servers.kajabi_server import call_tool

    resp = make_resp(200, {"data": [{"id": "1", "title": "Course 101", "type": "course", "status": "published"}]})
    with patch.dict("os.environ", _KJ_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("kajabi_list_products", {})
    assert result["products"][0]["title"] == "Course 101"


@pytest.mark.asyncio
async def test_kajabi_list_members():
    from app.mcp.servers.kajabi_server import call_tool

    resp = make_resp(200, {"data": [{"id": "m1", "email": "student@test.com", "full_name": "Test User", "created_at": "2024-01-01"}], "total": 1})
    with patch.dict("os.environ", _KJ_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("kajabi_list_members", {})
    assert result["members"][0]["email"] == "student@test.com"


@pytest.mark.asyncio
async def test_kajabi_missing_key():
    from app.mcp.servers.kajabi_server import call_tool

    with patch.dict("os.environ", {"KAJABI_API_KEY": ""}):
        result = await call_tool("kajabi_list_products", {})
    assert "KAJABI_API_KEY" in result["error"]


# ===========================================================================
# 12. TEACHABLE
# ===========================================================================

_TC_ENV = {"TEACHABLE_API_KEY": "tckey"}


@pytest.mark.asyncio
async def test_teachable_list_courses():
    from app.mcp.servers.teachable_server import call_tool

    resp = make_resp(200, {"courses": [{"id": 1, "name": "Python Basics", "heading": "Learn Python", "is_published": True, "price": 0}], "meta": {"total": 1}})
    with patch.dict("os.environ", _TC_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("teachable_list_courses", {})
    assert result["courses"][0]["name"] == "Python Basics"


@pytest.mark.asyncio
async def test_teachable_list_users():
    from app.mcp.servers.teachable_server import call_tool

    resp = make_resp(200, {"users": [{"id": 1, "email": "student@test.com", "name": "Test Student", "role": "student"}], "meta": {}})
    with patch.dict("os.environ", _TC_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("teachable_list_users", {})
    assert result["users"][0]["email"] == "student@test.com"


@pytest.mark.asyncio
async def test_teachable_create_coupon():
    from app.mcp.servers.teachable_server import call_tool

    resp = make_resp(201, {"coupon": {"id": "c1", "code": "SAVE20"}})
    with patch.dict("os.environ", _TC_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("teachable_create_coupon", {"course_id": 1, "name": "20% Off", "code": "SAVE20", "discount_type": "percent", "amount_off": 20})
    assert result.get("coupon", {}).get("code") == "SAVE20"


@pytest.mark.asyncio
async def test_teachable_missing_key():
    from app.mcp.servers.teachable_server import call_tool

    with patch.dict("os.environ", {"TEACHABLE_API_KEY": ""}):
        result = await call_tool("teachable_list_courses", {})
    assert "TEACHABLE_API_KEY" in result["error"]


# ===========================================================================
# 13. THINKIFIC
# ===========================================================================

_TF_ENV = {"THINKIFIC_API_KEY": "tfkey", "THINKIFIC_SUBDOMAIN": "myschool"}


@pytest.mark.asyncio
async def test_thinkific_list_courses():
    from app.mcp.servers.thinkific_server import call_tool

    resp = make_resp(200, {"items": [{"id": 1, "name": "Intro Course", "slug": "intro", "product_id": 10, "published": True}], "meta": {"total": 1}})
    with patch.dict("os.environ", _TF_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("thinkific_list_courses", {})
    assert result["courses"][0]["name"] == "Intro Course"


@pytest.mark.asyncio
async def test_thinkific_create_user():
    from app.mcp.servers.thinkific_server import call_tool

    resp = make_resp(201, {"id": 99, "email": "new@user.com", "first_name": "New", "last_name": "User"})
    with patch.dict("os.environ", _TF_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("thinkific_create_user", {"email": "new@user.com", "first_name": "New", "last_name": "User"})
    assert result["id"] == 99


@pytest.mark.asyncio
async def test_thinkific_list_enrollments():
    from app.mcp.servers.thinkific_server import call_tool

    resp = make_resp(200, {"items": [{"id": 1, "user_id": 10, "course_id": 20, "activated_at": "2024-01-01"}], "meta": {}})
    with patch.dict("os.environ", _TF_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("thinkific_list_enrollments", {})
    assert "enrollments" in result


@pytest.mark.asyncio
async def test_thinkific_missing_subdomain():
    from app.mcp.servers.thinkific_server import call_tool

    with patch.dict("os.environ", {"THINKIFIC_API_KEY": "x", "THINKIFIC_SUBDOMAIN": ""}):
        result = await call_tool("thinkific_list_courses", {})
    assert "THINKIFIC_SUBDOMAIN" in result["error"]


# ===========================================================================
# 14. SUBSTACK
# ===========================================================================

_SUB_ENV = {"SUBSTACK_API_KEY": "subkey", "SUBSTACK_PUBLICATION": "mypub"}


@pytest.mark.asyncio
async def test_substack_list_posts():
    from app.mcp.servers.substack_server import call_tool

    resp = make_resp(200, [{"id": "p1", "title": "Hello World", "slug": "hello-world", "type": "post", "audience": "everyone", "post_date": "2024-01-01"}])
    with patch.dict("os.environ", _SUB_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("substack_list_posts", {})
    assert result["posts"][0]["title"] == "Hello World"


@pytest.mark.asyncio
async def test_substack_create_post():
    from app.mcp.servers.substack_server import call_tool

    resp = make_resp(201, {"id": "p2", "title": "New Post", "slug": "new-post", "draft": True})
    with patch.dict("os.environ", _SUB_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("substack_create_post", {"title": "New Post", "body": "<p>Content</p>"})
    assert result["id"] == "p2"


@pytest.mark.asyncio
async def test_substack_missing_publication():
    from app.mcp.servers.substack_server import call_tool

    with patch.dict("os.environ", {"SUBSTACK_API_KEY": "x", "SUBSTACK_PUBLICATION": ""}):
        result = await call_tool("substack_list_posts", {})
    assert "SUBSTACK_PUBLICATION" in result["error"]


# ===========================================================================
# 15. STORYBLOK
# ===========================================================================

_SB_ENV = {"STORYBLOK_ACCESS_TOKEN": "sbtoken"}


@pytest.mark.asyncio
async def test_storyblok_list_stories():
    from app.mcp.servers.storyblok_server import call_tool

    resp = make_resp(200, {"stories": [{"id": 1, "name": "Home", "slug": "home", "full_slug": "home", "published": True, "created_at": "2024-01-01"}], "total": 1})
    with patch.dict("os.environ", _SB_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("storyblok_list_stories", {"space_id": "12345"})
    assert result["stories"][0]["name"] == "Home"


@pytest.mark.asyncio
async def test_storyblok_create_story():
    from app.mcp.servers.storyblok_server import call_tool

    resp = make_resp(201, {"story": {"id": 2, "name": "About", "slug": "about"}})
    with patch.dict("os.environ", _SB_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("storyblok_create_story", {
            "space_id": "12345", "name": "About", "slug": "about", "content": {"component": "page", "title": "About"}
        })
    assert result["name"] == "About"


@pytest.mark.asyncio
async def test_storyblok_list_components():
    from app.mcp.servers.storyblok_server import call_tool

    resp = make_resp(200, {"components": [{"id": 1, "name": "page", "display_name": "Page"}]})
    with patch.dict("os.environ", _SB_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("storyblok_list_components", {"space_id": "12345"})
    assert result["components"][0]["name"] == "page"


@pytest.mark.asyncio
async def test_storyblok_missing_token():
    from app.mcp.servers.storyblok_server import call_tool

    with patch.dict("os.environ", {"STORYBLOK_ACCESS_TOKEN": ""}):
        result = await call_tool("storyblok_list_stories", {"space_id": "1"})
    assert "STORYBLOK_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 16. VIMEO
# ===========================================================================

_VIMEO_ENV = {"VIMEO_ACCESS_TOKEN": "vimtoken"}


@pytest.mark.asyncio
async def test_vimeo_list_videos():
    from app.mcp.servers.vimeo_server import call_tool

    resp = make_resp(200, {"data": [{"uri": "/videos/1", "name": "Demo Video", "duration": 120, "stats": {"plays": 50}, "created_time": "2024-01-01", "privacy": {"view": "anybody"}}], "total": 1, "paging": {}})
    with patch.dict("os.environ", _VIMEO_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("vimeo_list_videos", {})
    assert result["videos"][0]["name"] == "Demo Video"


@pytest.mark.asyncio
async def test_vimeo_get_video():
    from app.mcp.servers.vimeo_server import call_tool

    resp = make_resp(200, {"uri": "/videos/123", "name": "Tutorial", "description": "How-to", "duration": 300, "stats": {"plays": 100}, "privacy": {"view": "anybody"}, "link": "https://vimeo.com/123"})
    with patch.dict("os.environ", _VIMEO_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("vimeo_get_video", {"video_id": "123"})
    assert result["name"] == "Tutorial"


@pytest.mark.asyncio
async def test_vimeo_list_folders():
    from app.mcp.servers.vimeo_server import call_tool

    resp = make_resp(200, {"data": [{"uri": "/projects/1", "name": "My Project", "created_time": "2024-01-01"}], "total": 1})
    with patch.dict("os.environ", _VIMEO_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("vimeo_list_folders", {})
    assert result["folders"][0]["name"] == "My Project"


@pytest.mark.asyncio
async def test_vimeo_missing_token():
    from app.mcp.servers.vimeo_server import call_tool

    with patch.dict("os.environ", {"VIMEO_ACCESS_TOKEN": ""}):
        result = await call_tool("vimeo_list_videos", {})
    assert "VIMEO_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 17. WISTIA
# ===========================================================================

_WISTIA_ENV = {"WISTIA_API_PASSWORD": "wistiapass"}


@pytest.mark.asyncio
async def test_wistia_list_medias():
    from app.mcp.servers.wistia_server import call_tool

    resp = make_resp(200, [{"id": 1, "hashed_id": "abc123", "name": "Intro Video", "duration": 60.0, "created": "2024-01-01", "stats": {"plays": 20}}])
    with patch.dict("os.environ", _WISTIA_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("wistia_list_medias", {})
    assert result["medias"][0]["name"] == "Intro Video"


@pytest.mark.asyncio
async def test_wistia_list_projects():
    from app.mcp.servers.wistia_server import call_tool

    resp = make_resp(200, [{"id": 1, "hashed_id": "proj1", "name": "Marketing", "mediaCount": 5, "created": "2024-01-01"}])
    with patch.dict("os.environ", _WISTIA_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("wistia_list_projects", {})
    assert result["projects"][0]["name"] == "Marketing"


@pytest.mark.asyncio
async def test_wistia_create_project():
    from app.mcp.servers.wistia_server import call_tool

    resp = make_resp(201, {"id": 2, "hashed_id": "newproj", "name": "Sales"})
    with patch.dict("os.environ", _WISTIA_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("wistia_create_project", {"name": "Sales"})
    assert result["name"] == "Sales"


@pytest.mark.asyncio
async def test_wistia_missing_password():
    from app.mcp.servers.wistia_server import call_tool

    with patch.dict("os.environ", {"WISTIA_API_PASSWORD": ""}):
        result = await call_tool("wistia_list_medias", {})
    assert "WISTIA_API_PASSWORD" in result["error"]


# ===========================================================================
# 18. SPOTIFY
# ===========================================================================

_SPOT_ENV = {"SPOTIFY_ACCESS_TOKEN": "spottoken"}


@pytest.mark.asyncio
async def test_spotify_search_tracks():
    from app.mcp.servers.spotify_server import call_tool

    resp = make_resp(200, {"tracks": {"items": [{"id": "t1", "name": "Cool Song"}], "total": 1}})
    with patch.dict("os.environ", _SPOT_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("spotify_search_tracks", {"q": "cool song"})
    assert "tracks" in result


@pytest.mark.asyncio
async def test_spotify_get_track():
    from app.mcp.servers.spotify_server import call_tool

    resp = make_resp(200, {"id": "t1", "name": "Cool Song", "duration_ms": 200000, "popularity": 80, "artists": [{"name": "Artist A"}], "album": {"name": "Album 1"}, "preview_url": None, "external_urls": {}})
    with patch.dict("os.environ", _SPOT_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("spotify_get_track", {"track_id": "t1"})
    assert result["name"] == "Cool Song"


@pytest.mark.asyncio
async def test_spotify_list_playlists():
    from app.mcp.servers.spotify_server import call_tool

    resp = make_resp(200, {"items": [{"id": "pl1", "name": "My Playlist", "tracks": {"total": 10}, "public": True}], "total": 1})
    with patch.dict("os.environ", _SPOT_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("spotify_list_playlists", {})
    assert result["playlists"][0]["name"] == "My Playlist"


@pytest.mark.asyncio
async def test_spotify_missing_token():
    from app.mcp.servers.spotify_server import call_tool

    with patch.dict("os.environ", {"SPOTIFY_ACCESS_TOKEN": ""}):
        result = await call_tool("spotify_search_tracks", {"q": "test"})
    assert "SPOTIFY_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 19. PINTEREST
# ===========================================================================

_PIN_ENV = {"PINTEREST_ACCESS_TOKEN": "pintoken"}


@pytest.mark.asyncio
async def test_pinterest_list_boards():
    from app.mcp.servers.pinterest_server import call_tool

    resp = make_resp(200, {"items": [{"id": "b1", "name": "Travel", "description": "Places", "privacy": "PUBLIC", "pin_count": 30}], "bookmark": None})
    with patch.dict("os.environ", _PIN_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("pinterest_list_boards", {})
    assert result["boards"][0]["name"] == "Travel"


@pytest.mark.asyncio
async def test_pinterest_create_pin():
    from app.mcp.servers.pinterest_server import call_tool

    resp = make_resp(201, {"id": "pin1", "board_id": "b1", "title": "My Pin", "link": "https://example.com"})
    with patch.dict("os.environ", _PIN_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("pinterest_create_pin", {"board_id": "b1", "media_source_url": "https://example.com/img.jpg", "title": "My Pin"})
    assert result["id"] == "pin1"


@pytest.mark.asyncio
async def test_pinterest_create_board():
    from app.mcp.servers.pinterest_server import call_tool

    resp = make_resp(201, {"id": "b2", "name": "Food", "privacy": "PUBLIC"})
    with patch.dict("os.environ", _PIN_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("pinterest_create_board", {"name": "Food"})
    assert result["name"] == "Food"


@pytest.mark.asyncio
async def test_pinterest_missing_token():
    from app.mcp.servers.pinterest_server import call_tool

    with patch.dict("os.environ", {"PINTEREST_ACCESS_TOKEN": ""}):
        result = await call_tool("pinterest_list_boards", {})
    assert "PINTEREST_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 20. HOOTSUITE
# ===========================================================================

_HS_ENV = {"HOOTSUITE_ACCESS_TOKEN": "hstoken"}


@pytest.mark.asyncio
async def test_hootsuite_list_profiles():
    from app.mcp.servers.hootsuite_server import call_tool

    resp = make_resp(200, {"data": [{"id": "pro1", "type": "TWITTER_PROFILE", "username": "myhandle", "avatarUrl": "https://x.com/avatar.jpg"}]})
    with patch.dict("os.environ", _HS_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("hootsuite_list_profiles", {})
    assert result["profiles"][0]["username"] == "myhandle"


@pytest.mark.asyncio
async def test_hootsuite_schedule_post():
    from app.mcp.servers.hootsuite_server import call_tool

    resp = make_resp(200, {"data": {"id": "msg1", "state": "SCHEDULED", "scheduledSendTime": "2024-06-01T10:00:00Z"}})
    with patch.dict("os.environ", _HS_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("hootsuite_schedule_post", {"text": "Hello!", "social_profile_ids": ["pro1"], "scheduled_send_time": "2024-06-01T10:00:00Z"})
    assert result["id"] == "msg1"


@pytest.mark.asyncio
async def test_hootsuite_list_teams():
    from app.mcp.servers.hootsuite_server import call_tool

    resp = make_resp(200, {"data": [{"id": "team1", "name": "Marketing"}]})
    with patch.dict("os.environ", _HS_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("hootsuite_list_teams", {})
    assert "teams" in result


@pytest.mark.asyncio
async def test_hootsuite_missing_token():
    from app.mcp.servers.hootsuite_server import call_tool

    with patch.dict("os.environ", {"HOOTSUITE_ACCESS_TOKEN": ""}):
        result = await call_tool("hootsuite_list_profiles", {})
    assert "HOOTSUITE_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 21. SPROUT SOCIAL
# ===========================================================================

_SPROUT_ENV = {"SPROUT_SOCIAL_ACCESS_TOKEN": "sprouttoken"}


@pytest.mark.asyncio
async def test_sprout_social_list_profiles():
    from app.mcp.servers.sprout_social_server import call_tool

    resp = make_resp(200, {"data": [{"id": "sp1", "name": "My Page", "network_type": "FACEBOOK", "username": "mypage"}]})
    with patch.dict("os.environ", _SPROUT_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("sprout_social_list_profiles", {})
    assert result["profiles"][0]["name"] == "My Page"


@pytest.mark.asyncio
async def test_sprout_social_schedule_message():
    from app.mcp.servers.sprout_social_server import call_tool

    resp = make_resp(200, {"data": {"id": "m1", "status": "scheduled", "scheduled_at": "2024-06-01T10:00:00Z"}})
    with patch.dict("os.environ", _SPROUT_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("sprout_social_schedule_message", {"profile_ids": ["sp1"], "text": "Hello Sprout!"})
    assert result["id"] == "m1"


@pytest.mark.asyncio
async def test_sprout_social_list_tags():
    from app.mcp.servers.sprout_social_server import call_tool

    resp = make_resp(200, {"data": [{"id": "tag1", "name": "Campaign"}]})
    with patch.dict("os.environ", _SPROUT_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("sprout_social_list_tags", {})
    assert "tags" in result


@pytest.mark.asyncio
async def test_sprout_social_missing_token():
    from app.mcp.servers.sprout_social_server import call_tool

    with patch.dict("os.environ", {"SPROUT_SOCIAL_ACCESS_TOKEN": ""}):
        result = await call_tool("sprout_social_list_profiles", {})
    assert "SPROUT_SOCIAL_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 22. BUFFER
# ===========================================================================

_BUFFER_ENV = {"BUFFER_ACCESS_TOKEN": "buftoken"}


@pytest.mark.asyncio
async def test_buffer_list_profiles():
    from app.mcp.servers.buffer_server import call_tool

    resp = make_resp(200, [{"id": "pr1", "service": "twitter", "service_username": "myaccount", "formatted_username": "@myaccount"}])
    with patch.dict("os.environ", _BUFFER_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("buffer_list_profiles", {})
    assert result["profiles"][0]["username"] == "myaccount"


@pytest.mark.asyncio
async def test_buffer_get_profile_analytics():
    from app.mcp.servers.buffer_server import call_tool

    resp = make_resp(200, {"id": "pr1", "service": "twitter", "service_username": "myaccount", "statistics": {"followers": 1000}})
    with patch.dict("os.environ", _BUFFER_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("buffer_get_profile_analytics", {"profile_id": "pr1"})
    assert result["service"] == "twitter"


@pytest.mark.asyncio
async def test_buffer_list_sent_updates():
    from app.mcp.servers.buffer_server import call_tool

    resp = make_resp(200, {"updates": [{"id": "u1", "text": "Hello!", "status": "sent"}], "total": 1})
    with patch.dict("os.environ", _BUFFER_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("buffer_list_sent_updates", {"profile_id": "pr1"})
    assert result["updates"][0]["status"] == "sent"


@pytest.mark.asyncio
async def test_buffer_missing_token():
    from app.mcp.servers.buffer_server import call_tool

    with patch.dict("os.environ", {"BUFFER_ACCESS_TOKEN": ""}):
        result = await call_tool("buffer_list_profiles", {})
    assert "BUFFER_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 23. FACEBOOK PAGES
# ===========================================================================

_FBP_ENV = {"FACEBOOK_ACCESS_TOKEN": "fbtok", "FACEBOOK_PAGE_ID": "pgid123"}


@pytest.mark.asyncio
async def test_facebook_pages_list_pages():
    from app.mcp.servers.facebook_pages_server import call_tool

    resp = make_resp(200, {"data": [{"id": "pgid123", "name": "My Business", "fan_count": 5000, "category": "Brand"}]})
    with patch.dict("os.environ", _FBP_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("facebook_pages_list_pages", {})
    assert result["pages"][0]["name"] == "My Business"


@pytest.mark.asyncio
async def test_facebook_pages_create_post():
    from app.mcp.servers.facebook_pages_server import call_tool

    resp = make_resp(200, {"id": "pgid123_post1"})
    with patch.dict("os.environ", _FBP_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("facebook_pages_create_post", {"message": "Hello World!"})
    assert result["id"] == "pgid123_post1"


@pytest.mark.asyncio
async def test_facebook_pages_list_posts():
    from app.mcp.servers.facebook_pages_server import call_tool

    resp = make_resp(200, {"data": [{"id": "post1", "message": "Hello!", "created_time": "2024-01-01"}], "paging": {}})
    with patch.dict("os.environ", _FBP_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("facebook_pages_list_posts", {})
    assert result["posts"][0]["message"] == "Hello!"


@pytest.mark.asyncio
async def test_facebook_pages_missing_token():
    from app.mcp.servers.facebook_pages_server import call_tool

    with patch.dict("os.environ", {"FACEBOOK_ACCESS_TOKEN": "", "FACEBOOK_PAGE_ID": "pg1"}):
        result = await call_tool("facebook_pages_list_pages", {})
    assert "FACEBOOK_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 24. FACEBOOK LEAD ADS
# ===========================================================================

_FBLA_ENV = {"FACEBOOK_ACCESS_TOKEN": "fbtok"}


@pytest.mark.asyncio
async def test_facebook_lead_ads_list_lead_forms():
    from app.mcp.servers.facebook_lead_ads_server import call_tool

    resp = make_resp(200, {"data": [{"id": "f1", "name": "Contact Form", "status": "ACTIVE", "leads_count": 42}], "paging": {}})
    with patch.dict("os.environ", _FBLA_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("facebook_lead_ads_list_lead_forms", {"page_id": "pg1"})
    assert result["forms"][0]["name"] == "Contact Form"


@pytest.mark.asyncio
async def test_facebook_lead_ads_get_leads():
    from app.mcp.servers.facebook_lead_ads_server import call_tool

    resp = make_resp(200, {"data": [{"id": "l1", "created_time": "2024-01-01", "field_data": [{"name": "email", "values": ["x@y.com"]}]}], "paging": {}})
    with patch.dict("os.environ", _FBLA_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("facebook_lead_ads_get_leads", {"form_id": "f1"})
    assert result["leads"][0]["id"] == "l1"


@pytest.mark.asyncio
async def test_facebook_lead_ads_list_ad_accounts():
    from app.mcp.servers.facebook_lead_ads_server import call_tool

    resp = make_resp(200, {"data": [{"id": "act_123", "name": "My Ad Account", "account_status": 1, "currency": "USD"}], "paging": {}})
    with patch.dict("os.environ", _FBLA_ENV), patch("httpx.AsyncClient", return_value=mk_client(get=resp)):
        result = await call_tool("facebook_lead_ads_list_ad_accounts", {})
    assert result["ad_accounts"][0]["id"] == "act_123"


@pytest.mark.asyncio
async def test_facebook_lead_ads_missing_token():
    from app.mcp.servers.facebook_lead_ads_server import call_tool

    with patch.dict("os.environ", {"FACEBOOK_ACCESS_TOKEN": ""}):
        result = await call_tool("facebook_lead_ads_list_lead_forms", {"page_id": "pg1"})
    assert "FACEBOOK_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# 25. FACEBOOK CONVERSIONS
# ===========================================================================

_FBCV_ENV = {"FACEBOOK_PIXEL_ID": "px123", "FACEBOOK_ACCESS_TOKEN": "fbtok"}


@pytest.mark.asyncio
async def test_facebook_conversions_send_event():
    from app.mcp.servers.facebook_conversions_server import call_tool

    resp = make_resp(200, {"events_received": 1, "messages": []})
    with patch.dict("os.environ", _FBCV_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("facebook_conversions_send_event", {"event_name": "ViewContent", "event_source_url": "https://example.com/page"})
    assert result.get("events_received") == 1


@pytest.mark.asyncio
async def test_facebook_conversions_send_purchase():
    from app.mcp.servers.facebook_conversions_server import call_tool

    resp = make_resp(200, {"events_received": 1})
    with patch.dict("os.environ", _FBCV_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("facebook_conversions_send_purchase_event", {
            "value": 99.99, "currency": "USD", "user_email": "buyer@example.com"
        })
    assert result.get("events_received") == 1


@pytest.mark.asyncio
async def test_facebook_conversions_send_lead():
    from app.mcp.servers.facebook_conversions_server import call_tool

    resp = make_resp(200, {"events_received": 1})
    with patch.dict("os.environ", _FBCV_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("facebook_conversions_send_lead_event", {
            "user_email": "lead@example.com", "event_source_url": "https://example.com/signup"
        })
    assert result.get("events_received") == 1


@pytest.mark.asyncio
async def test_facebook_conversions_test_event():
    from app.mcp.servers.facebook_conversions_server import call_tool

    resp = make_resp(200, {"events_received": 1, "messages": ["Test event received"]})
    with patch.dict("os.environ", _FBCV_ENV), patch("httpx.AsyncClient", return_value=mk_client(post=resp)):
        result = await call_tool("facebook_conversions_test_event", {"test_event_code": "TEST12345"})
    assert result.get("events_received") == 1


@pytest.mark.asyncio
async def test_facebook_conversions_missing_pixel_id():
    from app.mcp.servers.facebook_conversions_server import call_tool

    with patch.dict("os.environ", {"FACEBOOK_PIXEL_ID": "", "FACEBOOK_ACCESS_TOKEN": "tok"}):
        result = await call_tool("facebook_conversions_send_event", {"event_name": "Lead"})
    assert "FACEBOOK_PIXEL_ID" in result["error"]


@pytest.mark.asyncio
async def test_facebook_conversions_missing_token():
    from app.mcp.servers.facebook_conversions_server import call_tool

    with patch.dict("os.environ", {"FACEBOOK_PIXEL_ID": "px1", "FACEBOOK_ACCESS_TOKEN": ""}):
        result = await call_tool("facebook_conversions_send_event", {"event_name": "Lead"})
    assert "FACEBOOK_ACCESS_TOKEN" in result["error"]


# ===========================================================================
# Unknown tool fallthrough tests
# ===========================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("module_name,env,args", [
    ("etsy_server", _ETSY_ENV, {}),
    ("ebay_server", _EBAY_ENV, {}),
    ("ecwid_server", _ECWID_ENV, {}),
    ("magento_server", _MAGENTO_ENV, {}),
    ("squarespace_server", _SS_ENV, {}),
    ("lightspeed_server", _LS_ENV, {}),
    ("shipstation_server", _SHIP_ENV, {}),
    ("order_desk_server", _OD_ENV, {}),
    ("gumroad_server", _GR_ENV, {}),
    ("kajabi_server", _KJ_ENV, {}),
    ("teachable_server", _TC_ENV, {}),
    ("thinkific_server", _TF_ENV, {}),
    ("storyblok_server", _SB_ENV, {}),
    ("vimeo_server", _VIMEO_ENV, {}),
    ("spotify_server", _SPOT_ENV, {}),
    ("pinterest_server", _PIN_ENV, {}),
    ("hootsuite_server", _HS_ENV, {}),
    ("sprout_social_server", _SPROUT_ENV, {}),
    ("buffer_server", _BUFFER_ENV, {}),
    ("facebook_pages_server", _FBP_ENV, {}),
    ("facebook_lead_ads_server", _FBLA_ENV, {}),
    ("facebook_conversions_server", _FBCV_ENV, {}),
])
async def test_unknown_tool(module_name: str, env: dict, args: dict):
    import importlib

    module = importlib.import_module(f"app.mcp.servers.{module_name}")
    with patch.dict("os.environ", env), patch("httpx.AsyncClient", return_value=mk_client()):
        result = await module.call_tool("nonexistent_tool_xyz", args)
    assert "error" in result
    assert "Unknown tool" in result["error"]
