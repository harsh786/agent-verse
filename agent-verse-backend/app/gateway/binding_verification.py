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
"""

from __future__ import annotations

import re
from typing import Any

_TELEGRAM_ID = re.compile(r"^\d{3,20}$")
_TELEGRAM_TOKEN = re.compile(r"^(\d{3,20}):[A-Za-z0-9_-]{20,}$")
_WHATSAPP_ID = re.compile(r"^\d{5,30}$")
_SLACK_TEAM = re.compile(r"^T[A-Z0-9]{4,20}$")

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
