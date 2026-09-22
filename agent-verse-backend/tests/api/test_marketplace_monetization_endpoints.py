"""Marketplace monetization API — auth guard, DB-unavailable paths, purchase
lookup, and the Stripe-unavailable fallback (the `stripe` package is not an
installed dependency in this environment, which exercises the real
ImportError -> 503 branch of `_stripe()`).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.marketplace_monetization import (
    OnboardAuthorRequest,
    PricingRequest,
    onboard_author,
    purchase_template,
    set_template_price,
)
from app.tenancy.context import PlanTier, TenantContext


def _tenant(tenant_id="t1"):
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row is not None else []


def _fake_session(execute_side_effect=None):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=session)
    session.execute = AsyncMock(side_effect=execute_side_effect or (lambda *a, **k: _FakeResult()))
    session.commit = AsyncMock()
    return session


def _request(*, tenant=None, db=None):
    request = MagicMock()
    request.state = SimpleNamespace(tenant=tenant)
    request.app.state = SimpleNamespace(db_session_factory=db)
    return request


# ── auth guard ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_price_requires_tenant():
    with pytest.raises(HTTPException) as exc:
        await set_template_price(
            PricingRequest(template_id="tpl-1", price_usd=5.0), _request(tenant=None)
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_purchase_requires_tenant():
    with pytest.raises(HTTPException) as exc:
        await purchase_template("tpl-1", _request(tenant=None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_onboard_requires_tenant():
    with pytest.raises(HTTPException) as exc:
        await onboard_author(
            OnboardAuthorRequest(payout_email="a@b.com"), _request(tenant=None)
        )
    assert exc.value.status_code == 401


# ── set price ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_price_no_db_returns_503():
    with pytest.raises(HTTPException) as exc:
        await set_template_price(
            PricingRequest(template_id="tpl-1", price_usd=5.0),
            _request(tenant=_tenant(), db=None),
        )
    assert exc.value.status_code == 503


# ── purchase ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purchase_no_db_returns_503():
    with pytest.raises(HTTPException) as exc:
        await purchase_template("tpl-1", _request(tenant=_tenant(), db=None))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_purchase_unknown_template_returns_404():
    session = _fake_session(lambda *a, **k: _FakeResult(None))

    def db():
        return session

    with pytest.raises(HTTPException) as exc:
        await purchase_template("missing-tpl", _request(tenant=_tenant(), db=db))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_purchase_free_template_skips_stripe():
    session = _fake_session(lambda *a, **k: _FakeResult((0.0,)))

    def db():
        return session

    result = await purchase_template("free-tpl", _request(tenant=_tenant(), db=db))
    assert result == {"status": "free", "template_id": "free-tpl"}


@pytest.mark.asyncio
async def test_purchase_paid_template_without_stripe_package_returns_503():
    """`stripe` is not installed in this environment — the real ImportError
    branch of `_stripe()` must surface as a clean 503, not an unhandled
    ModuleNotFoundError."""
    session = _fake_session(lambda *a, **k: _FakeResult((29.99,)))

    def db():
        return session

    with pytest.raises(HTTPException) as exc:
        await purchase_template("paid-tpl", _request(tenant=_tenant(), db=db))
    assert exc.value.status_code == 503
    assert "stripe" in exc.value.detail.lower()


# ── onboard author ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_onboard_author_without_stripe_package_returns_503():
    with pytest.raises(HTTPException) as exc:
        await onboard_author(
            OnboardAuthorRequest(payout_email="author@example.com"),
            _request(tenant=_tenant()),
        )
    assert exc.value.status_code == 503
    assert "stripe" in exc.value.detail.lower()


# ── with a configured (fake) stripe package ─────────────────────────────
#
# `stripe` is not an installed dependency here, so these tests inject a
# fake module into sys.modules to exercise the success/error branches of
# `_stripe()` and its callers that otherwise never run in this environment.


@pytest.fixture
def fake_stripe(monkeypatch):
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(stripe_api_key="sk_test_123"),
    )
    yield fake


@pytest.mark.asyncio
async def test_onboard_author_stripe_installed_but_unconfigured_returns_503(monkeypatch):
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: SimpleNamespace(stripe_api_key="")
    )
    with pytest.raises(HTTPException) as exc:
        await onboard_author(
            OnboardAuthorRequest(payout_email="author@example.com"),
            _request(tenant=_tenant()),
        )
    assert exc.value.status_code == 503
    assert "not configured" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_onboard_author_success_returns_onboarding_url(fake_stripe):
    fake_stripe.Account.create.return_value = SimpleNamespace(id="acct_123")
    fake_stripe.AccountLink.create.return_value = SimpleNamespace(url="https://stripe/onboard")

    result = await onboard_author(
        OnboardAuthorRequest(payout_email="author@example.com"),
        _request(tenant=_tenant("t1")),
    )

    assert result == {"onboarding_url": "https://stripe/onboard", "stripe_account_id": "acct_123"}
    fake_stripe.Account.create.assert_called_once()
    assert fake_stripe.Account.create.call_args.kwargs["metadata"] == {"tenant_id": "t1"}


@pytest.mark.asyncio
async def test_onboard_author_stripe_error_returns_500(fake_stripe):
    fake_stripe.Account.create.side_effect = RuntimeError("stripe API error")

    with pytest.raises(HTTPException) as exc:
        await onboard_author(
            OnboardAuthorRequest(payout_email="author@example.com"),
            _request(tenant=_tenant()),
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_purchase_paid_template_success_returns_client_secret(fake_stripe):
    fake_stripe.PaymentIntent.create.return_value = SimpleNamespace(client_secret="secret_abc")
    session = _fake_session(lambda *a, **k: _FakeResult((29.99,)))

    def db():
        return session

    result = await purchase_template("paid-tpl", _request(tenant=_tenant("t1"), db=db))

    assert result == {"client_secret": "secret_abc", "amount_usd": 29.99}
    kwargs = fake_stripe.PaymentIntent.create.call_args.kwargs
    assert kwargs["amount"] == 2999
    assert kwargs["metadata"]["template_id"] == "paid-tpl"
    assert kwargs["metadata"]["buyer_tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_purchase_paid_template_stripe_error_returns_500(fake_stripe):
    fake_stripe.PaymentIntent.create.side_effect = RuntimeError("card declined")
    session = _fake_session(lambda *a, **k: _FakeResult((10.0,)))

    def db():
        return session

    with pytest.raises(HTTPException) as exc:
        await purchase_template("paid-tpl", _request(tenant=_tenant(), db=db))
    assert exc.value.status_code == 500
