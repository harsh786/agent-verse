"""Notification service — sends alerts when HITL approval is required.

Supports Slack webhooks and generic HTTP webhooks out of the box.
Designed to be extended with email, PagerDuty, etc.
Uses only open-source libraries (httpx).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.net.ssrf_guard import assert_public_url_async, public_async_client
from app.observability.logging import get_logger

logger = get_logger(__name__)

# channel_type → the config key its delivery URL lives under. ``url`` and
# ``webhook_url`` are accepted for every type (QA-5: the UI saved Teams channels
# under ``webhook_url`` while delivery read ``url``) and normalized at create.
_URL_KEY: dict[str, str] = {"slack": "webhook_url", "teams": "url", "webhook": "url"}
_URL_ALIASES = ("url", "webhook_url")
SUPPORTED_CHANNEL_TYPES = tuple(sorted(_URL_KEY))


def _configured_url(config: dict[str, Any], channel_type: str) -> str:
    """The channel's delivery URL: its canonical key first, then the alias."""
    primary = _URL_KEY.get(channel_type, "url")
    for key in (primary, *(k for k in _URL_ALIASES if k != primary)):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_channel_config(channel_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate a new channel's config and store its URL under one canonical key.

    Raises ``ValueError`` (a clear message the API answers with 422) for an
    unsupported ``channel_type`` or a missing / non-http(s) delivery URL — such
    a channel could never deliver, and used to fail only when it was needed.
    """
    ctype = str(channel_type or "").strip().lower()
    if ctype not in _URL_KEY:
        raise ValueError(
            f"channel_type must be one of: {', '.join(SUPPORTED_CHANNEL_TYPES)}"
            f" (got {channel_type!r})"
        )
    key = _URL_KEY[ctype]
    url = _configured_url(config, ctype)
    if not url:
        raise ValueError(
            f"a {ctype} channel requires config.{key} (its incoming webhook URL)"
        )
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"config.{key} must be an http(s) URL")
    rest = {k: v for k, v in config.items() if k not in _URL_ALIASES}
    return {**rest, key: url}


@dataclass
class NotificationChannel:
    channel_id: str
    tenant_id: str
    channel_type: str  # "slack" | "webhook" | "teams" (SUPPORTED_CHANNEL_TYPES)
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class NotificationService:
    """Dispatches notifications when approval is required or goals complete.

    Open source only — uses httpx for all HTTP calls.
    """

    def __init__(self) -> None:
        self._channels: dict[str, list[NotificationChannel]] = {}
        self._db: Any = None
        # Tenants whose persisted channels have been hydrated into the cache.
        self._loaded_tenants: set[str] = set()
        # Strong refs for the legacy fire-and-forget writes: asyncio only keeps
        # weak refs to tasks, so an unreferenced write could be GC'd mid-flight.
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    def set_db(self, db_factory: Any) -> None:
        """Wire in async SQLAlchemy session factory (called during lifespan).

        Only the request-path (tenant-RLS) factory belongs here. There is no
        startup warm-up any more: channels are hydrated lazily, per tenant, under
        that tenant's RLS context (see ``ensure_tenant_loaded``).
        """
        self._db = db_factory
        self._loaded_tenants.clear()

    async def ensure_tenant_loaded(self, tenant_id: str) -> None:
        """Hydrate *tenant_id*'s persisted channels once per process (idempotent)."""
        if self._db is None or not tenant_id or tenant_id in self._loaded_tenants:
            return
        if await self.sync_from_db(tenant_id):
            self._loaded_tenants.add(tenant_id)

    async def sync_from_db(self, tenant_id: str | None = None) -> bool:
        """Load one tenant's persisted channels into the in-memory cache.

        Runs inside a transaction with the tenant GUC set, plus an explicit
        ``tenant_id`` predicate. The previous no-argument form was a cross-tenant
        startup scan issued WITHOUT the GUC: under the NOBYPASSRLS application
        role the FORCE'd ``notification_channels`` policy filtered it down to
        zero rows, so no channel ever survived a restart. A call without a
        tenant is now a no-op. Returns True when the load succeeded.
        """
        if self._db is None:
            return False
        if not tenant_id:
            logger.debug("notification_sync_skipped_no_tenant")
            return False
        try:
            from sqlalchemy import text as _t

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    _t(
                        "SELECT channel_id, tenant_id, channel_type, config, enabled"
                        " FROM notification_channels WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                )
                rows = result.fetchall()
            for row in rows:
                if row[1] != tenant_id:  # defense in depth: never cache a foreign row
                    continue
                ch = NotificationChannel(
                    channel_id=row[0],
                    tenant_id=row[1],
                    channel_type=row[2],
                    config=row[3] or {},
                    enabled=row[4],
                )
                cached = self._channels.setdefault(tenant_id, [])
                if not any(c.channel_id == ch.channel_id for c in cached):
                    cached.append(ch)
            return True
        except Exception as exc:
            logger.warning("notification_sync_failed", tenant_id=tenant_id, error=str(exc))
            return False

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def add_channel(self, channel: NotificationChannel) -> None:
        """Cache a channel and persist it in the background (legacy sync API).

        Request paths should prefer ``add_channel_async`` so the row exists
        before the caller is told it was created.
        """
        self._channels.setdefault(channel.tenant_id, []).append(channel)
        if self._db is not None:
            self._spawn(self._persist_channel(channel))

    async def add_channel_async(self, channel: NotificationChannel) -> None:
        """Cache a channel and persist it before returning."""
        await self.ensure_tenant_loaded(channel.tenant_id)
        self._channels.setdefault(channel.tenant_id, []).append(channel)
        if self._db is not None:
            await self._persist_channel(channel)

    async def _persist_channel(self, channel: NotificationChannel) -> None:
        """Persist a channel to the DB under its tenant's RLS context."""
        try:
            import json as _json

            from sqlalchemy import text as _t

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, channel.tenant_id),
            ):
                await session.execute(
                    _t("""
                    INSERT INTO notification_channels
                        (channel_id, tenant_id, channel_type, config, enabled)
                    VALUES (:cid, :tid, :ctype, CAST(:cfg AS jsonb), :enabled)
                    ON CONFLICT (channel_id) DO UPDATE
                        SET config = EXCLUDED.config, enabled = EXCLUDED.enabled
                        WHERE notification_channels.tenant_id = EXCLUDED.tenant_id
                """),
                    {
                        "cid": channel.channel_id,
                        "tid": channel.tenant_id,
                        "ctype": channel.channel_type,
                        "cfg": _json.dumps(channel.config),
                        "enabled": channel.enabled,
                    },
                )
        except Exception as exc:
            logger.warning("notification_persist_failed", error=str(exc))

    def get_channels(self, tenant_id: str) -> list[NotificationChannel]:
        return [c for c in self._channels.get(tenant_id, []) if c.enabled]

    def remove_channel(self, channel_id: str, tenant_id: str) -> bool:
        """Remove a cached channel and delete it in the background (legacy sync API)."""
        channels = self._channels.get(tenant_id, [])
        before = len(channels)
        self._channels[tenant_id] = [c for c in channels if c.channel_id != channel_id]
        removed = len(self._channels[tenant_id]) < before
        if removed and self._db is not None:
            self._spawn(self._delete_channel(channel_id, tenant_id))
        return removed

    async def remove_channel_async(self, channel_id: str, tenant_id: str) -> bool:
        """Remove a channel from the cache and the DB; True if it existed for *tenant_id*."""
        await self.ensure_tenant_loaded(tenant_id)
        channels = self._channels.get(tenant_id, [])
        before = len(channels)
        self._channels[tenant_id] = [c for c in channels if c.channel_id != channel_id]
        removed = len(self._channels[tenant_id]) < before
        if self._db is not None:
            deleted = await self._delete_channel(channel_id, tenant_id)
            removed = removed or deleted
        return removed

    async def _delete_channel(self, channel_id: str, tenant_id: str) -> bool:
        """Delete a channel row under its tenant's RLS context; True if a row went."""
        try:
            from sqlalchemy import text as _t

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    _t(
                        "DELETE FROM notification_channels"
                        " WHERE channel_id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": channel_id, "tid": tenant_id},
                )
            rowcount = getattr(result, "rowcount", 0)
            return isinstance(rowcount, int) and rowcount > 0
        except Exception as exc:
            logger.warning("notification_delete_failed", error=str(exc))
            return False

    async def notify_approval_required(
        self,
        *,
        request_id: str,
        goal_id: str,
        action: str,
        risk_level: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Send notification to all tenant channels."""
        await self.ensure_tenant_loaded(tenant_id)
        channels = self.get_channels(tenant_id)
        if not channels:
            return {"sent": 0, "channels": []}

        # G-17: Build magic link URLs for one-click approve/reject from email/Slack
        from app.core.config import get_settings as _get_settings

        try:
            _base = _get_settings().public_base_url
        except Exception:
            _base = "http://localhost:5173"
        # Same signed format as the approval email (``?sig=&exp=``, bound to the
        # request, tenant and action). These links used to carry ``?token=`` —
        # which the endpoint never reads, so every click answered 403.
        from app.integrations.email.approval_sender import build_decision_urls

        try:
            approve_url, reject_url = build_decision_urls(
                _base, request_id, tenant_id=tenant_id
            )
        except RuntimeError:
            # No link-signing secret in production: send the notice without
            # one-click links rather than unsigned ones.
            logger.error("hitl_email_secret_missing", request_id=request_id)
            approve_url = reject_url = f"{_base.rstrip('/')}/approvals"

        message = {
            "type": "approval_required",
            "request_id": request_id,
            "goal_id": goal_id,
            "action": action,
            "risk_level": risk_level,
            "approve_url": approve_url,
            "reject_url": reject_url,
            "text": (
                f"\u26a0\ufe0f *Approval Required*\n"
                f"Goal: `{goal_id}`\n"
                f"Action: `{action}`\n"
                f"Risk: `{risk_level}`\n"
                f"Request ID: `{request_id}`\n"
                f"\u2705 Approve: {approve_url}\n"
                f"\u274c Reject: {reject_url}"
            ),
        }

        results = []
        for channel in channels:
            try:
                await self._send(channel, message)
                results.append({"channel_id": channel.channel_id, "status": "sent"})
            except Exception as exc:
                logger.warning("notification_failed", channel_id=channel.channel_id, error=str(exc))
                results.append(
                    {"channel_id": channel.channel_id, "status": "failed", "error": str(exc)}
                )

        return {"sent": sum(1 for r in results if r["status"] == "sent"), "channels": results}

    async def notify_approval_timeout(
        self,
        *,
        request_id: str,
        goal_id: str,
        action: str,
        tenant_id: str,
        auto_rejected: bool = True,
    ) -> dict[str, Any]:
        """G-12: Notify when an approval request has timed out.

        Called from HITLGateway.expire_timed_out_requests() for each expired request.
        """
        await self.ensure_tenant_loaded(tenant_id)
        channels = self.get_channels(tenant_id)
        if not channels:
            return {"sent": 0, "channels": []}

        outcome = "automatically rejected" if auto_rejected else "expired"
        message = {
            "type": "approval_timeout",
            "request_id": request_id,
            "goal_id": goal_id,
            "action": action,
            "text": (
                f"\u23f0 *Approval Timed Out*\n"
                f"Goal: `{goal_id}`\n"
                f"Action: `{action}` was {outcome}\n"
                f"Request ID: `{request_id}`"
            ),
        }
        results = []
        for channel in channels:
            try:
                await self._send(channel, message)
                results.append({"channel_id": channel.channel_id, "status": "sent"})
            except Exception as exc:
                logger.warning("notification_failed", channel_id=channel.channel_id, error=str(exc))
                results.append(
                    {"channel_id": channel.channel_id, "status": "failed", "error": str(exc)}
                )
        return {"sent": sum(1 for r in results if r["status"] == "sent"), "channels": results}

    async def notify_goal_complete(self, *, goal_id: str, status: str, tenant_id: str) -> None:
        """Notify when a goal reaches a terminal state."""
        await self.ensure_tenant_loaded(tenant_id)
        channels = self.get_channels(tenant_id)
        message = {
            "type": "goal_terminal",
            "goal_id": goal_id,
            "status": status,
            "text": f"{'✅' if status == 'complete' else '❌'} Goal `{goal_id}` {status}",
        }
        for channel in channels:
            try:
                await self._send(channel, message)
            except Exception as exc:
                logger.warning("goal_notification_failed", error=str(exc))

    async def notify_budget_alert(self, alert: dict[str, Any]) -> None:
        """Send a budget threshold alert to every channel of the tenant (COST-03)."""
        tenant_id = str(alert.get("tenant_id", ""))
        await self.ensure_tenant_loaded(tenant_id)
        who = f"agent `{alert['agent_id']}`" if alert.get("agent_id") else "tenant"
        message = {
            **alert,
            "text": (
                f"Budget alert: {who} has used {alert.get('threshold_pct')}% of its daily "
                f"budget (${alert.get('spent_usd')} of ${alert.get('limit_usd')})"
            ),
        }
        for channel in self.get_channels(tenant_id):
            try:
                await self._send(channel, message)
            except Exception as exc:
                logger.warning("budget_alert_notification_failed", error=str(exc))

    async def _send(self, channel: NotificationChannel, message: dict[str, Any]) -> None:
        """Deliver *message* to *channel*; raises when it was not delivered.

        An unknown channel type or a missing URL used to return silently and be
        counted as "sent". Webhook URLs are tenant-supplied, so every hop goes
        through the SSRF guard (no internal / metadata addresses, redirects
        re-validated) — they used to be posted with raw httpx.
        """
        ctype = channel.channel_type
        if ctype not in _URL_KEY:
            raise ValueError(f"unsupported notification channel type {ctype!r}")
        # Either key: channels created before QA-5 stored Teams under webhook_url.
        url = _configured_url(channel.config or {}, ctype)
        payload: dict[str, Any]
        if ctype in {"slack", "teams"}:
            # Slack and Teams incoming webhooks take a ``{"text": ...}`` message;
            # Teams answered the raw internal dict with an error.
            payload = {"text": str(message.get("text") or json.dumps(message))}
        else:
            payload = message
        if not url:
            raise ValueError(f"{channel.channel_type} channel has no URL configured")
        await _post_public(url, payload)


async def _post_public(url: str, payload: dict[str, Any]) -> None:
    """POST *payload* to a tenant-supplied webhook URL behind the SSRF guard.

    The URL is validated (public address, http/https) and redirects are NOT
    followed — a 3xx is a delivery failure, so a public URL cannot bounce the
    request to an internal address. The connection is IP-pinned
    (``public_async_client``): the host is resolved and re-checked at connect
    time and the socket dials the checked address. A plain client resolved the
    name again after the check, so a rebinding DNS answer reached loopback.
    """
    await assert_public_url_async(url, context="notification webhook")
    async with public_async_client(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        if getattr(resp, "is_redirect", False) is True:
            raise ValueError("notification webhook answered with a redirect; not followed")
        resp.raise_for_status()
