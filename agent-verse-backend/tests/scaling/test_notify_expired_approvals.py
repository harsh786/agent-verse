"""Coverage for app.scaling.tasks._notify_expired_approvals (G-12 notification
fan-out for auto-expired HITL approvals) — previously entirely uncovered.

The expired ids span every tenant, so the read is cross-tenant SYSTEM work: it
must run on the maintenance role (``get_system_session_factory``) inside
``system_session``. It used to run on the application role with no RLS
context, which under the NOBYPASSRLS role matched zero rows — so no timeout
notification was ever sent. The application factory is booby-trapped below so
a regression back onto it fails loudly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _session(execute_side_effect):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    # session.begin() is a sync method returning an async context manager.
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    return session


def _db_factory(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


class _RecordingSystemSession:
    """Stand-in for app.db.rls.system_session that records the session it scoped."""

    def __init__(self) -> None:
        self.sessions: list[object] = []

    @asynccontextmanager
    async def __call__(self, session):
        self.sessions.append(session)
        yield session


def _app_role_forbidden():
    raise AssertionError(
        "_notify_expired_approvals read cross-tenant rows on the application role"
    )


def _patches(db_factory, sys_session, mock_app):
    return (
        patch("app.db.session.get_system_session_factory", return_value=db_factory),
        patch("app.db.session.get_session_factory", side_effect=_app_role_forbidden),
        patch("app.db.rls.system_session", sys_session),
        patch("app.main.app", mock_app),
    )


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
        sys_session = _RecordingSystemSession()

        p1, p2, p3, p4 = _patches(db_factory, sys_session, mock_app)
        with p1, p2, p3, p4:
            result = await _notify_expired_approvals(["r1"])
        assert result == []
        assert sys_session.sessions == [session]

    @pytest.mark.asyncio
    async def test_success_notifies_each_row(self):
        from app.scaling.tasks import _notify_expired_approvals

        session = _session(
            execute_side_effect=[
                MagicMock(
                    fetchall=MagicMock(
                        return_value=[
                            ("r1", "t1", "g1", "deploy"),
                            ("r2", "t2", "g2", "delete"),
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
        sys_session = _RecordingSystemSession()

        p1, p2, p3, p4 = _patches(db_factory, sys_session, mock_app)
        with p1, p2, p3, p4:
            result = await _notify_expired_approvals(["r1", "r2"])

        assert result == ["r1", "r2"]
        assert mock_notif.notify_approval_timeout.await_count == 2
        # Each notification carries the row's own tenant (rows span tenants).
        tenants = [
            c.kwargs["tenant_id"] for c in mock_notif.notify_approval_timeout.await_args_list
        ]
        assert tenants == ["t1", "t2"]
        # The read ran inside system_session, in a transaction, on the system factory.
        assert sys_session.sessions == [session]
        session.begin.assert_called_once()
        db_factory.assert_called_once()

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
        sys_session = _RecordingSystemSession()

        p1, p2, p3, p4 = _patches(db_factory, sys_session, mock_app)
        with p1, p2, p3, p4:
            result = await _notify_expired_approvals(["r1", "r2"])

        assert result == ["r2"]

    @pytest.mark.asyncio
    async def test_outer_exception_returns_empty(self):
        from app.scaling.tasks import _notify_expired_approvals

        with patch(
            "app.db.session.get_system_session_factory", side_effect=RuntimeError("no db")
        ):
            result = await _notify_expired_approvals(["r1"])
        assert result == []
