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


async def _get_botframework_jwks() -> dict[str, Any]:
    """Fetch and cache Bot Framework's public signing keys."""
    global _jwks_cache, _jwks_fetched_at
    import httpx

    now = time.monotonic()
    if _jwks_cache and (now - _jwks_fetched_at) < _jwks_cache_ttl:
        return _jwks_cache
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


class MicrosoftTeamsAdapter(ChannelAdapter):
    """Microsoft Teams Bot Framework integration with Adaptive Cards."""

    channel_name = "teams"

    def __init__(self) -> None:
        self._app_id = os.getenv("TEAMS_APP_ID", "")
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

        ``raw_body`` is unused — Bot Framework authenticates via a signed
        JWT bearer token, not an HMAC over the request body. Accepted for
        signature-compatibility with the base ``ChannelAdapter``.

        The previous check only confirmed the Authorization header *looked
        like* a bearer token (started with "Bearer " and was over 20 chars) —
        any string matching that shape passed, with no signature, issuer, or
        audience check at all. That's not authentication: an attacker who
        knows (or guesses) an org_id can POST a forged activity with e.g.
        "Bearer aaaaaaaaaaaaaaaaaaaaaaa" and it verifies. Actually validate
        the JWT against Bot Framework's published signing keys, per
        https://learn.microsoft.com/azure/bot-service/rest-api/bot-framework-rest-connector-authentication.
        """
        auth = request_headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return False
        token = auth[len("Bearer ") :].strip()
        if not token:
            return False
        if not self._app_id:
            _log.warning("teams.verify_auth.no_app_id_configured")
            return False

        try:
            from jose import ExpiredSignatureError, JWTError
            from jose import jwt as _jwt
        except ImportError:
            _log.error("teams.verify_auth.python_jose_missing")
            return False

        try:
            jwks = await _get_botframework_jwks()
            _jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                audience=self._app_id,
                issuer=_BOTFRAMEWORK_ISSUER,
            )
            return True
        except ExpiredSignatureError:
            _log.warning("teams.verify_auth.token_expired")
            return False
        except JWTError as exc:
            _log.warning("teams.verify_auth.invalid_token", error=str(exc))
            return False
        except Exception as exc:
            # JWKS fetch failure, malformed token structure, etc. — fail
            # closed rather than let an unverifiable token through.
            _log.warning("teams.verify_auth.error", error=str(exc))
            return False
