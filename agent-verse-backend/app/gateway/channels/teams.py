"""Microsoft Teams Bot Framework adapter (via Azure Bot Service).

Setup: TEAMS_APP_ID + TEAMS_APP_PASSWORD env vars.
Webhook: POST /v1/gateway/{org_id}/teams/messages
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

# Bot Framework's fixed OpenID metadata endpoint (not the tenant's own AAD —
# Bot Framework issues its own bot-to-bot tokens via api.botframework.com,
# separate from a user's Azure AD sign-in). Cache JWKS like app/auth/keycloak.py
# does, to avoid a network round-trip on every inbound Teams message.
_BOTFRAMEWORK_OPENID_CONFIG = "https://login.botframework.com/v1/.well-known/openidconfiguration"
_BOTFRAMEWORK_ISSUER = "https://api.botframework.com"
_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at = 0.0
_jwks_cache_ttl = 3600.0
# A token whose ``kid`` is not in the cached set forces a refetch (Microsoft
# rotates its signing keys), but at most this often, so a flood of tokens with
# made-up key ids cannot turn every request into an outbound JWKS fetch.
_JWKS_FORCED_REFRESH_MIN_SECONDS = 300.0
_jwks_forced_at = 0.0
# Bot Framework's documented clock-skew allowance.
_CLOCK_SKEW_SECONDS = 300


async def _get_botframework_jwks(*, force: bool = False) -> dict[str, Any]:
    """Fetch and cache Bot Framework's public signing keys.

    ``force`` refetches before the TTL (a token named a key id the cache does
    not have), rate-limited by ``_JWKS_FORCED_REFRESH_MIN_SECONDS``.
    """
    global _jwks_cache, _jwks_fetched_at, _jwks_forced_at
    import httpx

    now = time.monotonic()
    fresh = bool(_jwks_cache) and (now - _jwks_fetched_at) < _jwks_cache_ttl
    if fresh and not force:
        return _jwks_cache
    if force and _jwks_cache and (now - _jwks_forced_at) < _JWKS_FORCED_REFRESH_MIN_SECONDS:
        return _jwks_cache
    if force:
        _jwks_forced_at = now
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            config = (await client.get(_BOTFRAMEWORK_OPENID_CONFIG)).json()
            jwks_uri = config["jwks_uri"]
            resp = await client.get(jwks_uri)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_fetched_at = now
            return _jwks_cache
    except Exception as exc:
        _log.warning("teams.jwks_fetch_failed", error=str(exc))
        if _jwks_cache:
            return _jwks_cache
        raise


def _bearer(headers: dict[str, str]) -> str:
    auth = headers.get("authorization", "") or headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    return auth[len("Bearer ") :].strip()


def _signing_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    for key in keys or []:
        if isinstance(key, dict) and key.get("kid") == kid:
            return key
    return None


def _same_service_url(a: str, b: str) -> bool:
    return a.strip().rstrip("/").lower() == b.strip().rstrip("/").lower()


async def decode_bot_framework_token(
    headers: dict[str, str], activity: dict[str, Any]
) -> dict[str, Any] | None:
    """Verify an inbound Bot Framework (channel → bot) token; its claims, or None.

    Checks everything the Bot Framework connector authentication spec requires
    EXCEPT the audience (the caller decides which app ids it accepts — see
    :func:`verify_bot_framework_token`):

    * RS256 signature by a key in Bot Framework's OpenID JWKS (cached; an
      unknown ``kid`` forces one rate-limited refetch, for key rotation);
    * ``iss`` is ``https://api.botframework.com``; ``exp`` / ``nbf`` with the
      documented 5-minute clock skew;
    * the signing key's ``endorsements`` include the activity's ``channelId``
      (when both are present);
    * the ``serviceurl`` claim equals the activity's ``serviceUrl`` — a token
      captured from one conversation cannot carry an activity for another
      service endpoint.

    Fails closed: any error (JWKS unreachable, malformed token) returns None.
    """
    token = _bearer(headers)
    if not token:
        return None
    try:
        from jose import ExpiredSignatureError, JWTError
        from jose import jwt as _jwt
    except ImportError:
        _log.error("teams.verify_auth.python_jose_missing")
        return None
    try:
        header = _jwt.get_unverified_header(token)
        kid = str(header.get("kid") or "")
        if header.get("alg") != "RS256" or not kid:
            return None
        jwks = await _get_botframework_jwks()
        key = _signing_key(jwks, kid)
        if key is None:
            key = _signing_key(await _get_botframework_jwks(force=True), kid)
        if key is None:
            _log.warning("teams.verify_auth.unknown_signing_key")
            return None
        channel_id = str(activity.get("channelId") or "") if isinstance(activity, dict) else ""
        endorsements = key.get("endorsements")
        if channel_id and isinstance(endorsements, list) and channel_id not in endorsements:
            _log.warning("teams.verify_auth.key_not_endorsed", channel_id=channel_id)
            return None
        claims: dict[str, Any] = _jwt.decode(
            token,
            {"keys": [key]},
            algorithms=["RS256"],
            issuer=_BOTFRAMEWORK_ISSUER,
            options={"verify_aud": False, "leeway": _CLOCK_SKEW_SECONDS},
        )
    except ExpiredSignatureError:
        _log.warning("teams.verify_auth.token_expired")
        return None
    except JWTError as exc:
        _log.warning("teams.verify_auth.invalid_token", error=str(exc))
        return None
    except Exception as exc:
        # JWKS fetch failure, malformed token structure, etc. — fail closed
        # rather than let an unverifiable token through.
        _log.warning("teams.verify_auth.error", error=str(exc))
        return None
    service_url = str(activity.get("serviceUrl") or "") if isinstance(activity, dict) else ""
    if service_url:
        claimed = str(claims.get("serviceurl") or claims.get("serviceUrl") or "")
        if not claimed or not _same_service_url(claimed, service_url):
            _log.warning("teams.verify_auth.service_url_mismatch")
            return None
    return claims


def token_audience(claims: dict[str, Any]) -> str:
    aud = claims.get("aud")
    if isinstance(aud, list):
        return str(aud[0]) if len(aud) == 1 else ""
    return str(aud or "")


async def verify_bot_framework_token(
    headers: dict[str, str], activity: dict[str, Any], *, audiences: tuple[str, ...]
) -> dict[str, Any] | None:
    """:func:`decode_bot_framework_token` plus an audience in ``audiences``."""
    wanted = tuple(a for a in audiences if a)
    if not wanted:
        _log.warning("teams.verify_auth.no_app_id_configured")
        return None
    claims = await decode_bot_framework_token(headers, activity)
    if claims is None:
        return None
    if token_audience(claims) not in wanted:
        _log.warning("teams.verify_auth.wrong_audience")
        return None
    return claims


class MicrosoftTeamsAdapter(ChannelAdapter):
    """Microsoft Teams Bot Framework integration with Adaptive Cards."""

    channel_name = "teams"

    def __init__(self, app_id: str | None = None) -> None:
        # ``app_id`` — a tenant binding's own Bot Framework app (TRG-42); the
        # platform app from TEAMS_APP_ID otherwise.
        self._app_id = app_id or os.getenv("TEAMS_APP_ID", "")
        self._app_password = os.getenv("TEAMS_APP_PASSWORD", "")
        self._token: str | None = None

    async def normalize(
        self,
        raw_payload: dict[str, Any],
        tenant_id: str,
        org_id: str,
    ) -> OrgCommand:
        with _tracer.start_as_current_span("teams.normalize"):
            command_id = str(uuid.uuid4())
            try:
                activity_type = raw_payload.get("type", "")
                if activity_type == "message":
                    text = raw_payload.get("text", "").strip()
                    from_obj = raw_payload.get("from", {})
                    raw_payload.get("serviceUrl", "")
                    conversation = raw_payload.get("conversation", {})
                    return OrgCommand(
                        command_id=command_id,
                        tenant_id=tenant_id,
                        org_id=org_id,
                        text=text,
                        actor_id=from_obj.get("id", ""),
                        actor_name=from_obj.get("name", ""),
                        actor_channel="teams",
                        conversation_id=conversation.get("id", ""),
                        raw_payload=raw_payload,
                    )
            except Exception as exc:
                _log.warning("teams.normalize.failed", error=str(exc))

            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text="",
                actor_channel="teams",
                raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Format as Adaptive Card activity."""
        card_body: list[dict[str, Any]] = [
            {"type": "TextBlock", "text": response.text, "wrap": True}
        ]
        actions: list[dict[str, Any]] = []
        for action in response.actions[:6]:
            style = "positive" if action.action_type == "approve" else "default"
            actions.append(
                {
                    "type": "Action.Submit",
                    "title": action.label,
                    "style": style,
                    "data": {"action_id": action.action_id},
                }
            )

        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": card_body,
        }
        if actions:
            card["actions"] = actions

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }

    async def verify_auth(
        self,
        request_headers: dict[str, str],
        raw_payload: dict[str, Any],
        raw_body: bytes | None = None,
    ) -> bool:
        """Verify the Bot Framework JWT bearer token on an inbound activity.

        ``raw_body`` is unused — Bot Framework authenticates via a signed JWT
        bearer token, not an HMAC over the request body. The previous check only
        confirmed the header *looked like* a bearer token; now the token is
        validated per
        https://learn.microsoft.com/azure/bot-service/rest-api/bot-framework-rest-connector-authentication
        (see :func:`decode_bot_framework_token`) for THIS adapter's app id.
        """
        claims = await verify_bot_framework_token(
            request_headers, raw_payload, audiences=(self._app_id or "",)
        )
        return claims is not None
