"""Dispatch-level tests for finance/payments/support MCP servers.

Targets: stripe, paypal, quickbooks, shopify, freshdesk, freshservice,
         gorgias, zendesk, intercom, chargebee, xero, square, razorpay,
         woocommerce.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
    """Return a mock AsyncClient context manager.
    
    All HTTP method mocks are explicitly set to AsyncMock so that
    awaiting them works correctly regardless of Python version.
    """
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

_STRIPE = {"STRIPE_SECRET_KEY": "sk_test_123"}
_STRIPE_CUSTOMER = {"id": "cus_123", "email": "a@b.com", "name": "Alice", "object": "customer"}
_STRIPE_LIST = {"object": "list", "data": [_STRIPE_CUSTOMER], "has_more": False}


@pytest.mark.asyncio
async def test_stripe_list_customers():
    from app.mcp.servers.stripe_server import call_tool

    mc = mk_client(get=make_resp(data=_STRIPE_LIST))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_list_customers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_customer():
    from app.mcp.servers.stripe_server import call_tool

    mc = mk_client(post=make_resp(data=_STRIPE_CUSTOMER))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_create_customer", {"email": "a@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_get_customer():
    from app.mcp.servers.stripe_server import call_tool

    mc = mk_client(get=make_resp(data=_STRIPE_CUSTOMER))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_get_customer", {"customer_id": "cus_123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_payment_intent():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "pi_123", "amount": 1000, "currency": "usd", "status": "requires_payment_method", "client_secret": "pi_secret"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_create_payment_intent", {"amount": 1000, "currency": "usd"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_confirm_payment_intent():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "pi_123", "status": "succeeded"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "stripe_confirm_payment_intent", {"payment_intent_id": "pi_123"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_retrieve_payment_intent():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "pi_123", "status": "succeeded", "amount": 1000}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "stripe_retrieve_payment_intent", {"payment_intent_id": "pi_123"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_list_subscriptions():
    from app.mcp.servers.stripe_server import call_tool

    data = {"object": "list", "data": [{"id": "sub_123", "status": "active"}], "has_more": False}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_list_subscriptions", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_subscription():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "sub_456", "status": "active", "customer": "cus_123"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "stripe_create_subscription", {"customer": "cus_123", "price_id": "price_123"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_cancel_subscription():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "sub_456", "status": "canceled"}
    mc = mk_client(delete=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "stripe_cancel_subscription", {"subscription_id": "sub_456"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_refund():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "re_123", "amount": 500, "status": "succeeded"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "stripe_create_refund",
            {"payment_intent": "pi_123", "amount": 500},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_list_invoices():
    from app.mcp.servers.stripe_server import call_tool

    data = {"object": "list", "data": [{"id": "in_123", "status": "paid"}], "has_more": False}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_list_invoices", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_invoice():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "in_456", "status": "draft", "customer": "cus_123"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_create_invoice", {"customer": "cus_123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_list_products():
    from app.mcp.servers.stripe_server import call_tool

    data = {"object": "list", "data": [{"id": "prod_123", "name": "Pro Plan", "active": True}], "has_more": False}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_list_products", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_product():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "prod_456", "name": "Enterprise Plan", "active": True}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_create_product", {"name": "Enterprise Plan"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_list_prices():
    from app.mcp.servers.stripe_server import call_tool

    data = {"object": "list", "data": [{"id": "price_123", "unit_amount": 4999, "currency": "usd"}], "has_more": False}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_list_prices", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_price():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "price_456", "unit_amount": 9999, "currency": "usd", "product": "prod_123"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "stripe_create_price",
            {"product": "prod_123", "unit_amount": 9999, "currency": "usd"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_list_charges():
    from app.mcp.servers.stripe_server import call_tool

    data = {"object": "list", "data": [{"id": "ch_123", "amount": 1000, "status": "succeeded"}], "has_more": False}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_list_charges", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_create_payout():
    from app.mcp.servers.stripe_server import call_tool

    data = {"id": "po_123", "amount": 5000, "currency": "usd", "status": "pending"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _STRIPE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("stripe_create_payout", {"amount": 5000, "currency": "usd"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_stripe_missing_env():
    from app.mcp.servers.stripe_server import call_tool

    with patch.dict("os.environ", {"STRIPE_SECRET_KEY": ""}):
        os.environ.pop("STRIPE_SECRET_KEY", None)
        result = await call_tool("stripe_list_customers", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------

_PP = {
    "PAYPAL_CLIENT_ID": "pp-client",
    "PAYPAL_CLIENT_SECRET": "pp-secret",
    "PAYPAL_MODE": "sandbox",
}


@pytest.mark.asyncio
async def test_paypal_create_order():
    from app.mcp.servers.paypal_server import call_tool

    token_resp = make_resp(data={"access_token": "tok", "expires_in": 32400})
    order_resp = make_resp(data={"id": "ORDER-123", "status": "CREATED", "links": []})
    mc = mk_client()
    mc.post = AsyncMock(side_effect=[token_resp, order_resp])
    with patch.dict("os.environ", _PP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "paypal_create_order", {"amount": "50.00", "currency": "USD"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_paypal_capture_order():
    from app.mcp.servers.paypal_server import call_tool

    token_resp = make_resp(data={"access_token": "tok", "expires_in": 32400})
    capture_resp = make_resp(data={"id": "ORDER-123", "status": "COMPLETED"})
    mc = mk_client()
    mc.post = AsyncMock(side_effect=[token_resp, capture_resp])
    with patch.dict("os.environ", _PP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("paypal_capture_order", {"order_id": "ORDER-123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_paypal_get_order():
    from app.mcp.servers.paypal_server import call_tool

    token_resp = make_resp(data={"access_token": "tok", "expires_in": 32400})
    order_resp = make_resp(data={"id": "ORDER-123", "status": "COMPLETED", "purchase_units": []})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=order_resp)
    with patch.dict("os.environ", _PP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("paypal_get_order", {"order_id": "ORDER-123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_paypal_list_transactions():
    from app.mcp.servers.paypal_server import call_tool

    token_resp = make_resp(data={"access_token": "tok", "expires_in": 32400})
    tx_resp = make_resp(data={"transaction_details": [], "total_items": "0"})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=tx_resp)
    with patch.dict("os.environ", _PP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "paypal_list_transactions",
            {"start_date": "2024-01-01T00:00:00-0700", "end_date": "2024-01-31T23:59:59-0700"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_paypal_missing_env():
    from app.mcp.servers.paypal_server import call_tool

    with patch.dict("os.environ", {"PAYPAL_CLIENT_ID": ""}):
        os.environ.pop("PAYPAL_CLIENT_ID", None)
        result = await call_tool("paypal_create_order", {"amount": "10.00", "currency": "USD"})
    assert "error" in result


# ---------------------------------------------------------------------------
# QuickBooks
# ---------------------------------------------------------------------------

_QB = {
    "QUICKBOOKS_ACCESS_TOKEN": "qb-tok",
    "QUICKBOOKS_COMPANY_ID": "co-123",
}


@pytest.mark.asyncio
async def test_qb_get_company_info():
    from app.mcp.servers.quickbooks_server import call_tool

    mc = mk_client(get=make_resp(data={"CompanyInfo": {"CompanyName": "My Biz", "Country": "US", "FiscalYearStartMonth": "January"}}))
    with patch.dict("os.environ", _QB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("qb_query", {"query": "SELECT * FROM CompanyInfo"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_qb_create_invoice():
    from app.mcp.servers.quickbooks_server import call_tool

    mc = mk_client(post=make_resp(data={"Invoice": {"Id": "inv1", "DocNumber": "1001", "TotalAmt": 500.00, "Balance": 500.00, "DueDate": "2024-02-01", "CustomerRef": {"value": "cust1"}}}))
    with patch.dict("os.environ", _QB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "qb_create_invoice",
            {
                "customer_ref_id": "cust1",   # server uses customer_ref_id not customer_id
                "line_items": [{"amount": 500.00, "description": "Services"}],
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_qb_create_customer():
    from app.mcp.servers.quickbooks_server import call_tool

    mc = mk_client(post=make_resp(data={"Customer": {"Id": "cust2", "DisplayName": "Alice Smith", "Balance": 0.0, "Active": True}}))
    with patch.dict("os.environ", _QB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("qb_create_customer", {"display_name": "Alice Smith"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_qb_list_accounts():
    from app.mcp.servers.quickbooks_server import call_tool

    mc = mk_client(get=make_resp(data={"QueryResponse": {"Account": [{"Id": "a1", "Name": "Checking", "AccountType": "Bank", "CurrentBalance": 1000.0}]}}))
    with patch.dict("os.environ", _QB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("qb_list_accounts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_qb_create_payment():
    from app.mcp.servers.quickbooks_server import call_tool

    mc = mk_client(post=make_resp(data={"Payment": {"Id": "pay1", "TotalAmt": 500.0, "CustomerRef": {"value": "cust1"}}}))
    with patch.dict("os.environ", _QB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "qb_create_payment", {"customer_ref_id": "cust1", "total_amount": 500.0}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_qb_get_profit_loss():
    from app.mcp.servers.quickbooks_server import call_tool

    mc = mk_client(get=make_resp(data={"Header": {"ReportName": "ProfitAndLoss"}, "Rows": {"Row": []}}))
    with patch.dict("os.environ", _QB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("qb_get_profit_loss", {"start_date": "2024-01-01", "end_date": "2024-12-31"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_qb_missing_env():
    from app.mcp.servers.quickbooks_server import call_tool

    with patch.dict("os.environ", {"QUICKBOOKS_ACCESS_TOKEN": ""}):
        os.environ.pop("QUICKBOOKS_ACCESS_TOKEN", None)
        result = await call_tool("qb_query", {"query": "SELECT * FROM CompanyInfo"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Shopify
# ---------------------------------------------------------------------------

_SHOPIFY = {"SHOPIFY_ACCESS_TOKEN": "shopify-tok", "SHOPIFY_STORE_URL": "mystore.myshopify.com"}


@pytest.mark.asyncio
async def test_shopify_list_products():
    from app.mcp.servers.shopify_server import call_tool

    mc = mk_client(get=make_resp(data={"products": [{"id": 1, "title": "T-Shirt", "status": "active", "variants": [], "images": []}]}))
    with patch.dict("os.environ", _SHOPIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("shopify_list_products", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_shopify_get_product():
    from app.mcp.servers.shopify_server import call_tool

    mc = mk_client(get=make_resp(data={"product": {"id": 1, "title": "T-Shirt", "status": "active", "variants": [], "images": []}}))
    with patch.dict("os.environ", _SHOPIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("shopify_get_product", {"product_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_shopify_create_product():
    from app.mcp.servers.shopify_server import call_tool

    mc = mk_client(post=make_resp(data={"product": {"id": 2, "title": "New Shirt", "status": "draft"}}))
    with patch.dict("os.environ", _SHOPIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("shopify_create_product", {"title": "New Shirt"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_shopify_list_orders():
    from app.mcp.servers.shopify_server import call_tool

    mc = mk_client(get=make_resp(data={"orders": [{"id": 100, "order_number": 1001, "financial_status": "paid", "fulfillment_status": None, "total_price": "49.99", "created_at": "2024-01-01", "customer": {"email": "a@b.com"}, "line_items": []}]}))
    with patch.dict("os.environ", _SHOPIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("shopify_list_orders", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_shopify_get_order():
    from app.mcp.servers.shopify_server import call_tool

    mc = mk_client(get=make_resp(data={"order": {"id": 100, "order_number": 1001, "financial_status": "paid", "total_price": "49.99", "line_items": []}}))
    with patch.dict("os.environ", _SHOPIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("shopify_get_order", {"order_id": 100})
    assert "error" not in result


@pytest.mark.asyncio
async def test_shopify_list_customers():
    from app.mcp.servers.shopify_server import call_tool

    mc = mk_client(get=make_resp(data={"customers": [{"id": 200, "email": "a@b.com", "first_name": "Alice", "last_name": "Smith", "orders_count": 5, "total_spent": "199.95"}]}))
    with patch.dict("os.environ", _SHOPIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("shopify_list_customers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_shopify_create_customer():
    from app.mcp.servers.shopify_server import call_tool

    mc = mk_client(post=make_resp(data={"customer": {"id": 201, "email": "new@b.com", "first_name": "Bob"}}))
    with patch.dict("os.environ", _SHOPIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "shopify_create_customer", {"email": "new@b.com", "first_name": "Bob"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_shopify_missing_env():
    from app.mcp.servers.shopify_server import call_tool

    with patch.dict("os.environ", {"SHOPIFY_ACCESS_TOKEN": ""}):
        os.environ.pop("SHOPIFY_ACCESS_TOKEN", None)
        result = await call_tool("shopify_list_products", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Freshdesk
# ---------------------------------------------------------------------------

_FD = {"FRESHDESK_DOMAIN": "myco.freshdesk.com", "FRESHDESK_API_KEY": "fd-key"}


@pytest.mark.asyncio
async def test_freshdesk_list_tickets():
    from app.mcp.servers.freshdesk_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "subject": "Help!", "status": 2, "priority": 1, "requester_id": 100, "created_at": "2024-01-01", "updated_at": "2024-01-01", "type": "Question"}]))
    with patch.dict("os.environ", _FD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshdesk_list_tickets", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshdesk_get_ticket():
    from app.mcp.servers.freshdesk_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "subject": "Help!", "description": "I need help", "status": 2, "priority": 1, "requester_id": 100, "created_at": "2024-01-01", "updated_at": "2024-01-01"}))
    with patch.dict("os.environ", _FD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshdesk_get_ticket", {"ticket_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshdesk_create_ticket():
    from app.mcp.servers.freshdesk_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "subject": "New Issue", "status": 2, "requester_id": 100}))
    with patch.dict("os.environ", _FD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "freshdesk_create_ticket",
            {"subject": "New Issue", "email": "user@b.com", "description": "desc"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshdesk_update_ticket():
    from app.mcp.servers.freshdesk_server import call_tool

    mc = mk_client(put=make_resp(data={"id": 1, "status": 4}))
    with patch.dict("os.environ", _FD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshdesk_update_ticket", {"ticket_id": 1, "status": 4})
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshdesk_reply_ticket():
    from app.mcp.servers.freshdesk_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 10, "body": "We'll look into it"}))
    with patch.dict("os.environ", _FD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "freshdesk_reply_ticket", {"ticket_id": 1, "body": "We'll look into it"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshdesk_search_tickets():
    from app.mcp.servers.freshdesk_server import call_tool

    mc = mk_client(get=make_resp(data={"results": [{"id": 1, "subject": "Help!"}], "total": 1}))
    with patch.dict("os.environ", _FD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshdesk_search_tickets", {"query": "help"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshdesk_list_contacts():
    from app.mcp.servers.freshdesk_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 100, "name": "Alice", "email": "a@b.com", "phone": None, "created_at": "2024-01-01"}]))
    with patch.dict("os.environ", _FD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshdesk_list_contacts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshdesk_missing_env():
    from app.mcp.servers.freshdesk_server import call_tool

    with patch.dict("os.environ", {"FRESHDESK_API_KEY": ""}):
        os.environ.pop("FRESHDESK_API_KEY", None)
        result = await call_tool("freshdesk_list_tickets", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Freshservice
# ---------------------------------------------------------------------------

_FS = {"FRESHSERVICE_DOMAIN": "myco.freshservice.com", "FRESHSERVICE_API_KEY": "fs-key"}


@pytest.mark.asyncio
async def test_freshservice_list_tickets():
    from app.mcp.servers.freshservice_server import call_tool

    mc = mk_client(get=make_resp(data={"tickets": [{"id": 1, "subject": "Server down", "status": 2, "priority": 3, "created_at": "2024-01-01", "updated_at": "2024-01-01", "type": "Incident"}]}))
    with patch.dict("os.environ", _FS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshservice_list_tickets", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshservice_create_ticket():
    from app.mcp.servers.freshservice_server import call_tool

    mc = mk_client(post=make_resp(data={"ticket": {"id": 2, "subject": "New Incident", "status": 2}}))
    with patch.dict("os.environ", _FS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "freshservice_create_ticket",
            {"subject": "New Incident", "description": "desc", "email": "user@b.com"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_freshservice_missing_env():
    from app.mcp.servers.freshservice_server import call_tool

    with patch.dict("os.environ", {"FRESHSERVICE_API_KEY": ""}):
        os.environ.pop("FRESHSERVICE_API_KEY", None)
        result = await call_tool("freshservice_list_tickets", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Zendesk
# ---------------------------------------------------------------------------

_ZD = {"ZENDESK_SUBDOMAIN": "myco", "ZENDESK_EMAIL": "a@b.com", "ZENDESK_API_TOKEN": "zd-tok"}


@pytest.mark.asyncio
async def test_zendesk_list_tickets():
    from app.mcp.servers.zendesk_server import call_tool

    mc = mk_client(get=make_resp(data={"tickets": [{"id": 1, "subject": "Help", "status": "open", "priority": "normal", "requester_id": 100, "created_at": "2024-01-01", "updated_at": "2024-01-01"}], "next_page": None}))
    with patch.dict("os.environ", _ZD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zendesk_list_tickets", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zendesk_get_ticket():
    from app.mcp.servers.zendesk_server import call_tool

    mc = mk_client(get=make_resp(data={"ticket": {"id": 1, "subject": "Help", "description": "I need help", "status": "open", "priority": "normal", "requester_id": 100, "assignee_id": None, "created_at": "2024-01-01", "updated_at": "2024-01-01"}}))
    with patch.dict("os.environ", _ZD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zendesk_get_ticket", {"ticket_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zendesk_create_ticket():
    from app.mcp.servers.zendesk_server import call_tool

    mc = mk_client(post=make_resp(data={"ticket": {"id": 2, "subject": "New Issue", "status": "new"}}))
    with patch.dict("os.environ", _ZD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "zendesk_create_ticket", {"subject": "New Issue", "body": "desc"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_zendesk_update_ticket():
    from app.mcp.servers.zendesk_server import call_tool

    mc = mk_client(put=make_resp(data={"ticket": {"id": 1, "status": "solved"}}))
    with patch.dict("os.environ", _ZD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zendesk_update_ticket", {"ticket_id": 1, "status": "solved"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zendesk_missing_env():
    from app.mcp.servers.zendesk_server import call_tool

    with patch.dict("os.environ", {"ZENDESK_API_TOKEN": ""}):
        os.environ.pop("ZENDESK_API_TOKEN", None)
        result = await call_tool("zendesk_list_tickets", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Intercom
# ---------------------------------------------------------------------------

_IC = {"INTERCOM_ACCESS_TOKEN": "ic-tok"}


@pytest.mark.asyncio
async def test_intercom_list_conversations():
    from app.mcp.servers.intercom_server import call_tool

    mc = mk_client(get=make_resp(data={"conversations": [{"id": "c1", "state": "open", "assignee": None, "contact": {"email": "a@b.com"}, "created_at": 1704067200, "updated_at": 1704067201}], "pages": {}}))
    with patch.dict("os.environ", _IC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("intercom_list_conversations", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_intercom_create_contact():
    from app.mcp.servers.intercom_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "u1", "email": "new@b.com", "name": "Bob", "role": "user", "created_at": 1704067200}))
    with patch.dict("os.environ", _IC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("intercom_create_user", {"email": "new@b.com", "name": "Bob", "role": "user"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_intercom_send_message():
    from app.mcp.servers.intercom_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "m1", "message_type": "outbound", "body": "Hello"}))
    with patch.dict("os.environ", _IC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "intercom_create_note",
            {"user_id": "u1", "body": "Follow-up note", "admin_id": "admin1"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_intercom_missing_env():
    from app.mcp.servers.intercom_server import call_tool

    with patch.dict("os.environ", {"INTERCOM_ACCESS_TOKEN": ""}):
        os.environ.pop("INTERCOM_ACCESS_TOKEN", None)
        result = await call_tool("intercom_list_conversations", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Chargebee
# ---------------------------------------------------------------------------

_CB = {"CHARGEBEE_API_KEY": "cb-key", "CHARGEBEE_SITE": "mysite"}


@pytest.mark.asyncio
async def test_chargebee_list_subscriptions():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(get=make_resp(data={"list": [{"subscription": {"id": "sub1", "status": "active", "plan_id": "pro", "customer_id": "cust1", "current_term_end": 1735689600}}], "next_offset": None}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_list_subscriptions", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_chargebee_create_customer():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(post=make_resp(data={"customer": {"id": "cust2", "email": "a@b.com", "first_name": "Alice"}}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_create_customer", {"email": "a@b.com", "first_name": "Alice"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_chargebee_missing_env():
    from app.mcp.servers.chargebee_server import call_tool

    with patch.dict("os.environ", {"CHARGEBEE_API_KEY": ""}):
        os.environ.pop("CHARGEBEE_API_KEY", None)
        result = await call_tool("chargebee_list_subscriptions", {})
    assert "error" in result
