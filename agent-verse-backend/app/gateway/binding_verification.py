"""Ownership proof for tenant-created gateway bindings (TRG-42).

A binding routes a bot / number / workspace's messages into a tenant, so a
tenant must prove it controls that addressee before it can bind it — otherwise
the first tenant to claim another organisation's bot would receive its traffic.
The proof is possession of the platform credential that only the owner holds,
checked against the platform's own API (fixed hosts; the addressee is validated
to a strict shape first, so nothing user-controlled reaches a URL host):

* telegram — the bot token: ``getMe`` must return the bound bot id;
* whatsapp — the Cloud API access token: ``GET /{phone_number_id}`` must succeed
  and return that id;
* slack    — the workspace bot token: ``auth.test`` must return the bound team id;
* teams    — not here: the Microsoft 365 tenant is proven by the inbound code
  flow on ``/channels/mappings`` (a Bot-Framework-signed message from that
  organisation); a gateway binding then only adds the tenant's app id;
* webhook  — nothing external to own: the addressee is generated server-side,
  so it cannot be squatted.

DEF-3 — the binding is then wired to (and verified by) the platform itself:
Telegram ``setWebhook`` registers the binding's URL with its ``secret_token``
(:func:`register_telegram_webhook`, when ``GATEWAY_PUBLIC_BASE_URL`` is set), so
every update arrives with that secret; WhatsApp's subscription handshake must
echo the binding's server-generated ``hub.verify_token``.
"""

from __future__ import annotations

import re
from typing import Any

_TELEGRAM_ID = re.compile(r"^\d{3,20}$")
_TELEGRAM_TOKEN = re.compile(r"^(\d{3,20}):[A-Za-z0-9_-]{20,}$")
_WHATSAPP_ID = re.compile(r"^\d{5,30}$")
_SLACK_TEAM = re.compile(r"^T[A-Z0-9]{4,20}$")
# Telegram's documented secret_token alphabet and length.
TELEGRAM_SECRET_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")

_TELEGRAM_API = "https://api.telegram.org"
_GRAPH_API = "https://graph.facebook.com/v19.0"
_SLACK_AUTH_TEST = "https://slack.com/api/auth.test"


class BindingOwnershipError(ValueError):
    """The tenant did not prove it controls the addressee (or the input is malformed)."""


def _client() -> Any:
    import httpx

    return httpx.AsyncClient(timeout=10.0, follow_redirects=False)


async def verify_ownership(
    channel: str, addressee: str, *, outbound_token: str, http: Any = None
) -> None:
    """Raise :class:`BindingOwnershipError` unless the token owns ``addressee``."""
    channel = channel.strip().lower()
    client = http if http is not None else _client()
    owns_client = http is None
    try:
        if channel == "telegram":
            await _telegram(addressee, outbound_token, client)
        elif channel == "whatsapp":
            await _whatsapp(addressee, outbound_token, client)
        elif channel == "slack":
            await _slack(addressee, outbound_token, client)
        else:
            raise BindingOwnershipError(f"{channel} bindings are not verified by token")
    finally:
        if owns_client:
            await client.aclose()


async def _json(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def _telegram(bot_id: str, token: str, client: Any) -> None:
    if not _TELEGRAM_ID.match(bot_id):
        raise BindingOwnershipError("Telegram addressee must be the numeric bot id")
    match = _TELEGRAM_TOKEN.match(token or "")
    if match is None or match.group(1) != bot_id:
        raise BindingOwnershipError("outbound_token must be this bot's Bot API token")
    try:
        resp = await client.get(f"{_TELEGRAM_API}/bot{token}/getMe")
    except Exception as exc:
        raise BindingOwnershipError(
            f"Telegram could not be reached: {type(exc).__name__}"
        ) from None
    data = await _json(resp)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    if resp.status_code != 200 or not data.get("ok") or str(result.get("id")) != bot_id:
        raise BindingOwnershipError("Telegram did not confirm the token belongs to this bot")


async def _whatsapp(phone_number_id: str, token: str, client: Any) -> None:
    if not _WHATSAPP_ID.match(phone_number_id):
        raise BindingOwnershipError("WhatsApp addressee must be the numeric phone_number_id")
    if not token:
        raise BindingOwnershipError("outbound_token (the WhatsApp Cloud API token) is required")
    try:
        resp = await client.get(
            f"{_GRAPH_API}/{phone_number_id}",
            params={"fields": "id"},
            headers={"Authorization": f"Bearer {token}"},
        )
    except Exception as exc:
        raise BindingOwnershipError(f"Meta could not be reached: {type(exc).__name__}") from None
    data = await _json(resp)
    if resp.status_code != 200 or str(data.get("id")) != phone_number_id:
        raise BindingOwnershipError("Meta did not confirm the token can use this phone number")


async def _slack(team_id: str, token: str, client: Any) -> None:
    if not _SLACK_TEAM.match(team_id):
        raise BindingOwnershipError("Slack addressee must be the workspace team id (T...)")
    if not (token or "").startswith("xoxb-"):
        raise BindingOwnershipError("outbound_token must be the workspace's bot token (xoxb-)")
    try:
        resp = await client.post(_SLACK_AUTH_TEST, headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:
        raise BindingOwnershipError(f"Slack could not be reached: {type(exc).__name__}") from None
    data = await _json(resp)
    if resp.status_code != 200 or not data.get("ok") or str(data.get("team_id")) != team_id:
        raise BindingOwnershipError("Slack did not confirm the token belongs to this workspace")


def public_base_url() -> str:
    """``GATEWAY_PUBLIC_BASE_URL`` (https origin + optional path prefix) or "".

    Platforms only deliver webhooks to a public HTTPS URL; anything else is
    treated as unset rather than registered."""
    import os
    from urllib.parse import urlsplit

    raw = os.getenv("GATEWAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        return ""
    return raw


async def register_telegram_webhook(
    token: str, url: str, secret_token: str, *, http: Any = None
) -> None:
    """``setWebhook`` the bot to ``url`` with ``secret_token`` (Telegram then
    sends it in ``X-Telegram-Bot-Api-Secret-Token`` on every update). Raises
    :class:`BindingOwnershipError` when Telegram does not confirm."""
    if not TELEGRAM_SECRET_TOKEN.match(secret_token or ""):
        raise BindingOwnershipError("Telegram secret_token must be 1-256 of A-Z a-z 0-9 _ -")
    if _TELEGRAM_TOKEN.match(token or "") is None:
        raise BindingOwnershipError("outbound_token must be this bot's Bot API token")
    client = http if http is not None else _client()
    try:
        resp = await client.post(
            f"{_TELEGRAM_API}/bot{token}/setWebhook",
            json={
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message", "edited_message"],
            },
        )
    except Exception as exc:
        raise BindingOwnershipError(
            f"Telegram could not be reached: {type(exc).__name__}"
        ) from None
    finally:
        if http is None:
            await client.aclose()
    data = await _json(resp)
    if resp.status_code != 200 or not data.get("ok") or data.get("result") is not True:
        detail = str(data.get("description") or f"HTTP {resp.status_code}")[:200]
        raise BindingOwnershipError(f"Telegram refused setWebhook: {detail}")
