"""Comprehensive tests for app/services/notification_service.py — targeting 90%+ coverage."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.notification_service import NotificationChannel, NotificationService


@pytest.fixture(autouse=True)
def _public_webhook_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Example webhook hosts don't resolve in tests; the SSRF guard is covered
    by its own tests (and test_send_blocks_internal_webhook_url below)."""
    monkeypatch.setattr(
        "app.services.notification_service.assert_public_url_async",
        AsyncMock(return_value=["93.184.216.34"]),
    )


def _slack_channel(
    channel_id: str = "ch-1",
    tenant_id: str = "t1",
    webhook_url: str = "https://hooks.slack.com/test",
    enabled: bool = True,
) -> NotificationChannel:
    return NotificationChannel(
        channel_id=channel_id,
        tenant_id=tenant_id,
        channel_type="slack",
        config={"webhook_url": webhook_url},
        enabled=enabled,
    )


def _webhook_channel(
    channel_id: str = "ch-2",
    tenant_id: str = "t1",
    url: str = "https://webhook.example.com/notify",
    enabled: bool = True,
    channel_type: str = "webhook",
) -> NotificationChannel:
    return NotificationChannel(
        channel_id=channel_id,
        tenant_id=tenant_id,
        channel_type=channel_type,
        config={"url": url},
        enabled=enabled,
    )


# ── NotificationChannel ───────────────────────────────────────────────────────

class TestNotificationChannel:
    def test_defaults(self) -> None:
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="slack"
        )
        assert ch.enabled is True
        assert ch.config == {}

    def test_disabled_channel(self) -> None:
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="slack", enabled=False
        )
        assert ch.enabled is False


# ── NotificationService — channel management ──────────────────────────────────

class TestNotificationServiceChannelManagement:
    def test_add_channel(self) -> None:
        svc = NotificationService()
        ch = _slack_channel()
        svc.add_channel(ch)
        channels = svc.get_channels("t1")
        assert len(channels) == 1
        assert channels[0].channel_id == "ch-1"

    def test_get_channels_filters_disabled(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel(channel_id="enabled"))
        svc.add_channel(_slack_channel(channel_id="disabled", enabled=False))
        channels = svc.get_channels("t1")
        assert len(channels) == 1
        assert channels[0].channel_id == "enabled"

    def test_get_channels_empty_for_unknown_tenant(self) -> None:
        svc = NotificationService()
        assert svc.get_channels("unknown") == []

    def test_remove_channel_returns_true(self) -> None:
        svc = NotificationService()
        ch = _slack_channel()
        svc.add_channel(ch)
        removed = svc.remove_channel("ch-1", "t1")
        assert removed is True
        assert len(svc.get_channels("t1")) == 0

    def test_remove_channel_unknown_returns_false(self) -> None:
        svc = NotificationService()
        removed = svc.remove_channel("nonexistent", "t1")
        assert removed is False

    def test_multiple_tenants_isolated(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel(channel_id="c1", tenant_id="t1"))
        svc.add_channel(_slack_channel(channel_id="c2", tenant_id="t2"))
        assert len(svc.get_channels("t1")) == 1
        assert len(svc.get_channels("t2")) == 1

    def test_set_db(self) -> None:
        svc = NotificationService()
        mock_db = MagicMock()
        svc.set_db(mock_db)
        assert svc._db == mock_db


# ── notify_approval_required ──────────────────────────────────────────────────

class TestNotifyApprovalRequired:
    async def test_no_channels_returns_sent_zero(self) -> None:
        svc = NotificationService()
        result = await svc.notify_approval_required(
            request_id="r1", goal_id="g1", action="deploy",
            risk_level="high", tenant_id="t1",
        )
        assert result["sent"] == 0
        assert result["channels"] == []

    async def test_slack_channel_sends_notification(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r1", goal_id="g1", action="deploy",
                risk_level="high", tenant_id="t1",
            )

        assert result["sent"] == 1
        assert result["channels"][0]["status"] == "sent"

    async def test_webhook_channel_sends_notification(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r2", goal_id="g2", action="delete",
                risk_level="medium", tenant_id="t1",
            )

        assert result["sent"] == 1

    async def test_teams_channel_sends_notification(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel(channel_id="ch-teams", channel_type="teams"))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r3", goal_id="g3", action="act",
                risk_level="low", tenant_id="t1",
            )

        assert result["sent"] == 1

    async def test_channel_send_failure_tracked(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel())

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.NetworkError("connection refused")
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r1", goal_id="g1", action="deploy",
                risk_level="high", tenant_id="t1",
            )

        assert result["sent"] == 0
        assert result["channels"][0]["status"] == "failed"
        assert "error" in result["channels"][0]

    async def test_multiple_channels_partial_success(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel(channel_id="ok"))
        svc.add_channel(_slack_channel(channel_id="fail", webhook_url="https://fail.example.com"))

        call_count = 0

        async def post_side_effect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if "fail" in url:
                raise httpx.NetworkError("Fail channel")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.post = post_side_effect

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r1", goal_id="g1", action="act",
                risk_level="high", tenant_id="t1",
            )

        assert result["sent"] == 1


# ── notify_goal_outcome ───────────────────────────────────────────────────────

class TestNotifyGoalOutcome:
    async def test_notify_complete_status(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc.notify_goal_outcome(goal_id="g1", status="complete", tenant_id="t1")

        mock_client.post.assert_called_once()

    async def test_notify_failed_status(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc.notify_goal_outcome(goal_id="g1", status="failed", tenant_id="t1")

        mock_client.post.assert_called_once()

    async def test_notify_no_channels_noop(self) -> None:
        svc = NotificationService()
        await svc.notify_goal_outcome(goal_id="g1", status="complete", tenant_id="t1")

    async def test_send_error_is_logged_not_raised(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel())

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.NetworkError("err")
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            # Must not raise
            await svc.notify_goal_outcome(goal_id="g1", status="failed", tenant_id="t1")


# ── _send (internal routing) ──────────────────────────────────────────────────

class TestSendInternal:
    # A channel that cannot deliver used to return silently and be counted as
    # "sent"; it now raises so notify_* reports it failed.
    async def test_slack_no_webhook_url_raises(self) -> None:
        svc = NotificationService()
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="slack",
            config={}  # no webhook_url
        )
        with pytest.raises(ValueError, match="no URL"):
            await svc._send(ch, {"text": "test"})

    async def test_webhook_no_url_raises(self) -> None:
        svc = NotificationService()
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="webhook",
            config={}  # no url
        )
        with pytest.raises(ValueError, match="no URL"):
            await svc._send(ch, {"type": "test"})

    async def test_unknown_channel_type_raises(self) -> None:
        svc = NotificationService()
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="pagerduty",
            config={"key": "val"}
        )
        with pytest.raises(ValueError, match="unsupported"):
            await svc._send(ch, {"type": "test"})

    async def test_misconfigured_channel_is_counted_failed_not_sent(self) -> None:
        svc = NotificationService()
        svc.add_channel(
            NotificationChannel(channel_id="c1", tenant_id="t1", channel_type="webhook", config={})
        )
        result = await svc.notify_approval_required(
            request_id="r", goal_id="g", action="a", risk_level="low", tenant_id="t1"
        )
        assert result["sent"] == 0
        assert result["channels"][0]["status"] == "failed"

    async def test_send_blocks_internal_webhook_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tenant webhook URLs go through the SSRF guard (were posted raw)."""
        from app.net import ssrf_guard

        monkeypatch.setattr(
            "app.services.notification_service.assert_public_url_async",
            ssrf_guard.assert_public_url_async,
        )
        svc = NotificationService()
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="webhook",
            config={"url": "http://169.254.169.254/latest/meta-data"},
        )
        with patch("httpx.AsyncClient") as mock_httpx, pytest.raises(ssrf_guard.SSRFError):
            await svc._send(ch, {"type": "test"})
        mock_httpx.assert_not_called()


# ── sync_from_db ──────────────────────────────────────────────────────────────


def _fake_factory(session: AsyncMock):
    """Session factory whose sessions support ``async with session.begin()``."""

    @asynccontextmanager
    async def _begin():
        yield None

    session.begin = MagicMock(side_effect=lambda: _begin())

    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _sql_calls(session: AsyncMock) -> list[tuple[str, dict]]:
    out = []
    for call in session.execute.await_args_list:
        stmt = str(call.args[0])
        params = call.args[1] if len(call.args) > 1 else {}
        out.append((stmt, params))
    return out


class TestSyncFromDb:
    async def test_sync_no_db_noop(self) -> None:
        svc = NotificationService()
        assert await svc.sync_from_db("t1") is False  # no exception

    async def test_sync_without_tenant_is_a_noop_not_a_cross_tenant_scan(self) -> None:
        """The old startup form scanned every tenant WITHOUT the RLS GUC, which
        the NOBYPASSRLS role filters to zero rows. It must not touch the DB."""
        mock_session = AsyncMock()
        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        assert await svc.sync_from_db() is False
        mock_session.execute.assert_not_awaited()

    async def test_sync_from_db_loads_channels(self) -> None:
        rows = [("ch-db", "t1", "slack", {"webhook_url": "https://h.slack.com"}, True)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        assert await svc.sync_from_db("t1") is True
        channels = svc.get_channels("t1")
        assert len(channels) == 1
        assert channels[0].channel_id == "ch-db"

        # Tenant GUC set before the SELECT, and the SELECT carries a tenant predicate.
        calls = _sql_calls(mock_session)
        assert "set_config('app.tenant_id'" in calls[0][0]
        assert calls[0][1] == {"tid": "t1"}
        select = next(c for c in calls if "FROM notification_channels" in c[0])
        assert "WHERE tenant_id = :tid" in select[0]
        assert select[1] == {"tid": "t1"}

    async def test_sync_from_db_never_caches_a_foreign_row(self) -> None:
        rows = [("ch-x", "someone-else", "slack", {}, True)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        await svc.sync_from_db("t1")
        assert svc.get_channels("t1") == []
        assert svc.get_channels("someone-else") == []

    async def test_sync_from_db_deduplicates(self) -> None:
        rows = [("ch-db", "t1", "slack", {"webhook_url": "https://s.com"}, True)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        await svc.sync_from_db("t1")
        await svc.sync_from_db("t1")  # second sync should not duplicate
        channels = svc.get_channels("t1")
        assert len(channels) == 1

    async def test_sync_from_db_error_suppressed(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))

        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        assert await svc.sync_from_db("t1") is False  # must not raise


class TestLazyTenantHydration:
    async def test_ensure_tenant_loaded_runs_once_per_tenant(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("c1", "t1", "webhook", {}, True)]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        await svc.ensure_tenant_loaded("t1")
        first = mock_session.execute.await_count
        await svc.ensure_tenant_loaded("t1")
        assert mock_session.execute.await_count == first  # cached
        assert [c.channel_id for c in svc.get_channels("t1")] == ["c1"]

    async def test_failed_load_is_retried(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("down"))
        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        await svc.ensure_tenant_loaded("t1")
        assert "t1" not in svc._loaded_tenants

    async def test_notify_hydrates_the_tenant_before_dispatch(self) -> None:
        """A channel persisted by another replica must still be notified."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("c1", "t1", "webhook", {"url": "https://hook.example.com"}, True)
        ]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        with patch.object(svc, "_send", new=AsyncMock()) as send:
            result = await svc.notify_approval_required(
                request_id="r1", goal_id="g1", action="deploy", risk_level="high",
                tenant_id="t1",
            )
        assert result["sent"] == 1
        send.assert_awaited_once()


class TestPersistenceUnderRls:
    async def test_add_channel_async_persists_under_tenant_guc(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: []))
        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        await svc.add_channel_async(_webhook_channel(channel_id="c9", tenant_id="t1"))

        calls = _sql_calls(mock_session)
        insert_idx = next(i for i, c in enumerate(calls) if "INSERT INTO notification_channels" in c[0])
        # The GUC for THIS tenant is the statement immediately before the INSERT.
        assert "set_config('app.tenant_id'" in calls[insert_idx - 1][0]
        assert calls[insert_idx - 1][1] == {"tid": "t1"}
        assert calls[insert_idx][1]["tid"] == "t1"
        assert "WHERE notification_channels.tenant_id = EXCLUDED.tenant_id" in calls[insert_idx][0]
        assert [c.channel_id for c in svc.get_channels("t1")] == ["c9"]

    async def test_remove_channel_async_deletes_with_tenant_predicate(self) -> None:
        delete_result = MagicMock(rowcount=1)
        load_result = MagicMock()
        load_result.fetchall.return_value = []
        mock_session = AsyncMock()

        async def _execute(stmt, params=None):
            return delete_result if "DELETE" in str(stmt) else load_result

        mock_session.execute = AsyncMock(side_effect=_execute)
        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        # Not in this replica's cache, but present in the DB → still removed.
        assert await svc.remove_channel_async("c-remote", "t1") is True

        calls = _sql_calls(mock_session)
        delete_idx = next(i for i, c in enumerate(calls) if "DELETE FROM" in c[0])
        assert "tenant_id = :tid" in calls[delete_idx][0]
        assert calls[delete_idx][1] == {"cid": "c-remote", "tid": "t1"}
        assert calls[delete_idx - 1][1] == {"tid": "t1"}  # GUC for this tenant

    async def test_remove_channel_async_foreign_id_is_not_found(self) -> None:
        mock_session = AsyncMock()
        load_result = MagicMock()
        load_result.fetchall.return_value = []

        async def _execute(stmt, params=None):
            return MagicMock(rowcount=0) if "DELETE" in str(stmt) else load_result

        mock_session.execute = AsyncMock(side_effect=_execute)
        svc = NotificationService()
        svc.set_db(_fake_factory(mock_session))
        assert await svc.remove_channel_async("someone-elses", "t1") is False
