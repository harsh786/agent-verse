"""Coverage for app.scaling.tasks._notify_expired_approvals (G-12 notification
fan-out for auto-expired HITL approvals) — previously entirely uncovered."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _session(execute_side_effect):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


def _db_factory(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


class TestNotifyExpiredApprovals:
    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty(self):
        from app.scaling.tasks import _notify_expired_approvals

        assert await _notify_expired_approvals([]) == []

    @pytest.mark.asyncio
    async def test_no_notification_service_returns_empty(self):
        from app.scaling.tasks import _notify_expired_approvals

        session = _session(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[("r1", "t1", "g1", "deploy")]))
            ]
        )
        db_factory = _db_factory(session)
        mock_app = MagicMock()
        mock_app.state = MagicMock(spec=[])  # no notification_service attr

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.main.app", mock_app),
        ):
            result = await _notify_expired_approvals(["r1"])
        assert result == []

    @pytest.mark.asyncio
    async def test_success_notifies_each_row(self):
        from app.scaling.tasks import _notify_expired_approvals

        session = _session(
            execute_side_effect=[
                MagicMock(
                    fetchall=MagicMock(
                        return_value=[
                            ("r1", "t1", "g1", "deploy"),
                            ("r2", "t1", "g2", "delete"),
                        ]
                    )
                )
            ]
        )
        db_factory = _db_factory(session)
        mock_notif = MagicMock()
        mock_notif.notify_approval_timeout = AsyncMock(return_value=None)
        mock_app = MagicMock()
        mock_app.state = MagicMock(notification_service=mock_notif)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.main.app", mock_app),
        ):
            result = await _notify_expired_approvals(["r1", "r2"])

        assert result == ["r1", "r2"]
        assert mock_notif.notify_approval_timeout.await_count == 2

    @pytest.mark.asyncio
    async def test_per_row_notify_failure_is_skipped(self):
        from app.scaling.tasks import _notify_expired_approvals

        session = _session(
            execute_side_effect=[
                MagicMock(
                    fetchall=MagicMock(
                        return_value=[
                            ("r1", "t1", "g1", "deploy"),
                            ("r2", "t1", "g2", "delete"),
                        ]
                    )
                )
            ]
        )
        db_factory = _db_factory(session)
        mock_notif = MagicMock()
        mock_notif.notify_approval_timeout = AsyncMock(
            side_effect=[RuntimeError("boom"), None]
        )
        mock_app = MagicMock()
        mock_app.state = MagicMock(notification_service=mock_notif)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.main.app", mock_app),
        ):
            result = await _notify_expired_approvals(["r1", "r2"])

        assert result == ["r2"]

    @pytest.mark.asyncio
    async def test_outer_exception_returns_empty(self):
        from app.scaling.tasks import _notify_expired_approvals

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = await _notify_expired_approvals(["r1"])
        assert result == []
