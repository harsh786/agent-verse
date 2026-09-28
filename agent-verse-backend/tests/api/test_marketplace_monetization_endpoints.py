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


@pytest.mark.asyncio
async def test_set_price_on_a_template_the_caller_does_not_own_is_404():
    """Regression: no ownership predicate (and a non-existent column)."""
    session = _fake_session(lambda *a, **k: MagicMock(rowcount=0))
    with pytest.raises(HTTPException) as exc:
        await set_template_price(
            PricingRequest(template_id="someone-elses", price_usd=5.0),
            _request(tenant=_tenant("t1"), db=lambda: session),
        )
    assert exc.value.status_code == 404
    sqls = [str(c.args[0]) for c in session.execute.await_args_list]
    assert any("WHERE id = :tmpl_id AND tenant_id = :tid" in q for q in sqls)


def test_pricing_rejects_negative_price():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PricingRequest(template_id="t", price_usd=-1.0)


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

    upd = MagicMock(rowcount=0)
    session = _fake_session(lambda *a, **k: upd)

    result = await onboard_author(
        OnboardAuthorRequest(payout_email="author@example.com"),
        _request(tenant=_tenant("t1"), db=lambda: session),
    )

    assert result == {"onboarding_url": "https://stripe/onboard", "stripe_account_id": "acct_123"}
    fake_stripe.Account.create.assert_called_once()
    assert fake_stripe.Account.create.call_args.kwargs["metadata"] == {"tenant_id": "t1"}
    # Regression: the account id was returned but never stored.
    sqls = [str(c.args[0]) for c in session.execute.await_args_list]
    assert any("INSERT INTO marketplace_author_accounts" in q for q in sqls)
    insert = next(
        c for c in session.execute.await_args_list
        if "INSERT INTO marketplace_author_accounts" in str(c.args[0])
    )
    assert insert.args[1]["acct"] == "acct_123" and insert.args[1]["tid"] == "t1"


@pytest.mark.asyncio
async def test_onboard_author_persist_failure_is_503(fake_stripe):
    fake_stripe.Account.create.return_value = SimpleNamespace(id="acct_9")
    fake_stripe.AccountLink.create.return_value = SimpleNamespace(url="u")

    def _boom(*a, **k):
        raise RuntimeError("db down")

    session = _fake_session(_boom)
    with pytest.raises(HTTPException) as exc:
        await onboard_author(
            OnboardAuthorRequest(payout_email="author@example.com"),
            _request(tenant=_tenant("t1"), db=lambda: session),
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_onboard_author_stripe_error_returns_502(fake_stripe):
    fake_stripe.Account.create.side_effect = RuntimeError("stripe API error")

    with pytest.raises(HTTPException) as exc:
        await onboard_author(
            OnboardAuthorRequest(payout_email="author@example.com"),
            _request(tenant=_tenant(), db=lambda: _fake_session()),
        )
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_purchase_paid_template_success_returns_client_secret(fake_stripe):
    fake_stripe.PaymentIntent.create.return_value = SimpleNamespace(client_secret="secret_abc")
    session = _fake_session(lambda *a, **k: _FakeResult((29.99,)))

    def db():
        return session

    result = await purchase_template("paid-tpl", _request(tenant=_tenant("t1"), db=db))

    assert result["client_secret"] == "secret_abc" and result["amount_usd"] == 29.99
    # A pending purchase row is recorded (it used to write nothing).
    assert result["status"] == "pending" and result["purchase_id"]
    sqls = [str(c.args[0]) for c in session.execute.await_args_list]
    assert any("INSERT INTO marketplace_purchases" in q for q in sqls)
    # Column is `id` (not the non-existent `template_id`).
    assert any("FROM marketplace_templates WHERE id = :tid" in q for q in sqls)
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
    assert exc.value.status_code == 502
