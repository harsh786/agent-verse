"""Coverage for app/org/feature_flags.py beyond the per-tenant override tests
in test_feature_flags_per_tenant.py: the FeatureFlagService CRUD surface,
enable_phase() progressive rollout, the module-level singleton helpers, and
the three PART 43 org cron coroutines (_run_org_intelligence_cron,
_run_org_digest_cron, _run_org_twin_sync).

All DB/session boundaries are faked in-process (AsyncMock), matching the
pattern already used in tests/scaling/test_tasks_org_maintenance_coverage.py
for the Celery wrappers around these same coroutines.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.org.feature_flags import (
    ORG_FEATURE_FLAGS,
    FeatureFlagService,
    get_feature_flags,
    is_feature_enabled,
)


# ── FeatureFlagService CRUD ──────────────────────────────────────────────────


class TestFeatureFlagServiceBasics:
    def test_defaults_to_module_level_flags(self):
        svc = FeatureFlagService()
        assert svc.get_all() == ORG_FEATURE_FLAGS
        # get_all returns a copy, not the live dict.
        svc.get_all()["org_os_enabled"] = True
        assert svc.get_all()["org_os_enabled"] is False

    def test_custom_flags_override_defaults(self):
        svc = FeatureFlagService(flags={"custom_flag": True})
        assert svc.is_enabled("custom_flag") is True
        assert svc.is_enabled("org_os_enabled") is False  # not in custom dict

    def test_enable_and_disable_flip_global_state(self):
        svc = FeatureFlagService()
        assert svc.is_enabled("org_os_enabled") is False
        svc.enable("org_os_enabled")
        assert svc.is_enabled("org_os_enabled") is True
        svc.disable("org_os_enabled")
        assert svc.is_enabled("org_os_enabled") is False

    def test_enable_unknown_flag_adds_it(self):
        svc = FeatureFlagService()
        svc.enable("brand_new_flag")
        assert svc.get_all()["brand_new_flag"] is True

    def test_always_on_flags_default_true(self):
        svc = FeatureFlagService()
        assert svc.is_enabled("loop_detector_enabled") is True
        assert svc.is_enabled("command_bar_enabled") is True
        assert svc.is_enabled("org_chart_enabled") is True

    def test_tenant_override_missing_falls_back_to_global(self):
        svc = FeatureFlagService()
        svc.enable("org_os_enabled")
        # No override recorded for this tenant -> falls through to global.
        assert svc.is_enabled("org_os_enabled", "tenant-x") is True

    def test_get_all_reflects_mutations(self):
        svc = FeatureFlagService()
        svc.enable("org_os_enabled")
        assert svc.get_all()["org_os_enabled"] is True


class TestEnablePhase:
    def test_phase_0_enables_nothing(self):
        svc = FeatureFlagService()
        enabled = svc.enable_phase(0)
        assert enabled == []
        assert svc.get_all() == ORG_FEATURE_FLAGS

    def test_phase_1_enables_org_os_only(self):
        svc = FeatureFlagService()
        enabled = svc.enable_phase(1)
        assert enabled == ["org_os_enabled"]
        assert svc.is_enabled("org_os_enabled") is True

    def test_phase_2_enables_team_and_orchestrator(self):
        svc = FeatureFlagService()
        enabled = svc.enable_phase(2)
        assert set(enabled) == {
            "org_os_enabled",
            "team_formation_enabled",
            "meta_orchestrator_enabled",
        }
        for flag in enabled:
            assert svc.is_enabled(flag) is True

    def test_phase_3_enables_memory_tiers(self):
        svc = FeatureFlagService()
        enabled = svc.enable_phase(3)
        assert "dept_memory_enabled" in enabled
        assert "org_memory_enabled" in enabled
        assert svc.is_enabled("dept_memory_enabled") is True

    def test_phase_4_enables_gateways_and_analytics(self):
        svc = FeatureFlagService()
        enabled = svc.enable_phase(4)
        assert "gateway_telegram_enabled" in enabled
        assert "digital_twin_enabled" in enabled
        assert svc.is_enabled("org_digest_enabled") is True

    def test_phase_5_enables_remaining_advanced_features(self):
        svc = FeatureFlagService()
        enabled = svc.enable_phase(5)
        assert "self_improvement_enabled" in enabled
        assert "gateway_a2a_enabled" in enabled
        assert svc.is_enabled("plugin_system_enabled") is True

    def test_unknown_phase_returns_empty_list(self):
        svc = FeatureFlagService()
        assert svc.enable_phase(99) == []

    def test_phases_are_additive_not_exclusive(self):
        svc = FeatureFlagService()
        svc.enable_phase(1)
        svc.enable_phase(2)
        assert svc.is_enabled("org_os_enabled") is True
        assert svc.is_enabled("team_formation_enabled") is True


class TestModuleSingleton:
    def test_get_feature_flags_returns_singleton(self):
        import app.org.feature_flags as ff_module

        ff_module._feature_flags = None
        try:
            first = get_feature_flags()
            second = get_feature_flags()
            assert first is second
        finally:
            ff_module._feature_flags = None

    def test_is_feature_enabled_uses_singleton(self):
        import app.org.feature_flags as ff_module

        ff_module._feature_flags = None
        try:
            assert is_feature_enabled("org_os_enabled") is False
            get_feature_flags().enable("org_os_enabled")
            assert is_feature_enabled("org_os_enabled") is True
        finally:
            ff_module._feature_flags = None

    def test_is_feature_enabled_passes_through_tenant_id(self):
        import app.org.feature_flags as ff_module

        ff_module._feature_flags = None
        try:
            get_feature_flags().enable_for_tenant("org_os_enabled", "tenant-1")
            assert is_feature_enabled("org_os_enabled", "tenant-1") is True
            assert is_feature_enabled("org_os_enabled", "tenant-2") is False
        finally:
            ff_module._feature_flags = None


# ── PART 43 cron coroutines ──────────────────────────────────────────────────


class _AsyncCtx:
    def __init__(self, value=None):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def _null_rls_ctx(*_a, **_kw):
    return _AsyncCtx(None)


class TestRunOrgIntelligenceCron:
    @pytest.mark.asyncio
    async def test_no_active_orgs_returns_zero_counts(self):
        from app.org.feature_flags import _run_org_intelligence_cron

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db_cm = _AsyncCtx(session)

        with (
            patch("app.db.session.get_session_factory", return_value=lambda: db_cm),
            patch("app.db.rls.sqlalchemy_rls_context", _null_rls_ctx),
        ):
            result = await _run_org_intelligence_cron()

        assert result == {"processed": 0, "insights": 0}

    @pytest.mark.asyncio
    async def test_processes_orgs_and_counts_bottlenecks(self):
        from app.org.feature_flags import _run_org_intelligence_cron

        org_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        list_session = AsyncMock()
        list_session.execute = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=[(org_id, tenant_id)]))
        )
        per_org_session = AsyncMock()

        db_factory_calls = [_AsyncCtx(list_session), _AsyncCtx(per_org_session)]

        def _db_factory():
            return db_factory_calls.pop(0)

        mock_svc = MagicMock()
        mock_svc.get_org_health_score = AsyncMock(return_value=0.9)
        mock_svc.get_bottlenecks = AsyncMock(return_value=["slow_step", "stuck_review"])

        with (
            patch("app.db.session.get_session_factory", return_value=_db_factory),
            patch("app.db.rls.sqlalchemy_rls_context", _null_rls_ctx),
            patch("app.org.analytics.OrgAnalyticsService", return_value=mock_svc),
        ):
            result = await _run_org_intelligence_cron()

        assert result == {"processed": 1, "insights": 2}
        mock_svc.get_org_health_score.assert_awaited_once_with(str(org_id))

    @pytest.mark.asyncio
    async def test_per_org_failure_is_swallowed_and_does_not_stop_batch(self):
        from app.org.feature_flags import _run_org_intelligence_cron

        org_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        list_session = AsyncMock()
        list_session.execute = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=[(org_id, tenant_id)]))
        )

        def _db_factory():
            return _AsyncCtx(list_session)

        with (
            patch("app.db.session.get_session_factory", return_value=_db_factory),
            patch(
                "app.db.rls.sqlalchemy_rls_context",
                side_effect=RuntimeError("rls unavailable"),
            ),
        ):
            result = await _run_org_intelligence_cron()

        assert result == {"processed": 0, "insights": 0}

    @pytest.mark.asyncio
    async def test_top_level_db_failure_returns_zero_counts(self):
        from app.org.feature_flags import _run_org_intelligence_cron

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("db down")):
            result = await _run_org_intelligence_cron()

        assert result == {"processed": 0, "insights": 0}


class TestRunOrgDigestCron:
    @pytest.mark.asyncio
    async def test_no_active_orgs_returns_zero_counts(self):
        from app.org.feature_flags import _run_org_digest_cron

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))

        with (
            patch("app.db.session.get_session_factory", return_value=lambda: _AsyncCtx(session)),
            patch("app.db.rls.sqlalchemy_rls_context", _null_rls_ctx),
        ):
            result = await _run_org_digest_cron()

        assert result == {"processed": 0, "digests": 0}

    @pytest.mark.asyncio
    async def test_generates_digest_per_active_org(self):
        from app.org.feature_flags import _run_org_digest_cron

        org_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        list_session = AsyncMock()
        list_session.execute = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=[(org_id, tenant_id)]))
        )
        per_org_session = AsyncMock()
        db_factory_calls = [_AsyncCtx(list_session), _AsyncCtx(per_org_session)]

        mock_digest_svc = MagicMock()
        mock_digest_svc.generate = AsyncMock(return_value=None)

        with (
            patch("app.db.session.get_session_factory", return_value=lambda: db_factory_calls.pop(0)),
            patch("app.db.rls.sqlalchemy_rls_context", _null_rls_ctx),
            patch("app.org.digest.DigestGenerator", return_value=mock_digest_svc),
        ):
            result = await _run_org_digest_cron()

        assert result == {"processed": 1, "digests": 1}
        mock_digest_svc.generate.assert_awaited_once_with(str(org_id), str(tenant_id))

    @pytest.mark.asyncio
    async def test_per_org_digest_failure_is_swallowed(self):
        from app.org.feature_flags import _run_org_digest_cron

        org_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        list_session = AsyncMock()
        list_session.execute = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=[(org_id, tenant_id)]))
        )

        with (
            patch("app.db.session.get_session_factory", return_value=lambda: _AsyncCtx(list_session)),
            patch(
                "app.db.rls.sqlalchemy_rls_context",
                side_effect=RuntimeError("rls unavailable"),
            ),
        ):
            result = await _run_org_digest_cron()

        assert result == {"processed": 0, "digests": 0}

    @pytest.mark.asyncio
    async def test_top_level_failure_returns_zero_counts(self):
        from app.org.feature_flags import _run_org_digest_cron

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("db down")):
            result = await _run_org_digest_cron()

        assert result == {"processed": 0, "digests": 0}


class TestRunOrgTwinSync:
    @pytest.mark.asyncio
    async def test_missing_org_id_returns_none_without_syncing(self):
        from app.org.feature_flags import _run_org_twin_sync

        mock_twin_cls = MagicMock()
        with patch("app.org.digital_twin.OrgDigitalTwin", mock_twin_cls):
            result = await _run_org_twin_sync({"event_type": "mission.completed"})

        assert result is None
        mock_twin_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_syncs_twin_when_org_id_present(self):
        from app.org.feature_flags import _run_org_twin_sync

        mock_twin = MagicMock()
        mock_twin.sync = AsyncMock(return_value=None)

        with patch("app.org.digital_twin.OrgDigitalTwin", return_value=mock_twin):
            event = {"org_id": "org-123", "event_type": "mission.completed"}
            result = await _run_org_twin_sync(event)

        assert result is None
        mock_twin.sync.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_sync_failure_is_swallowed(self):
        from app.org.feature_flags import _run_org_twin_sync

        mock_twin = MagicMock()
        mock_twin.sync = AsyncMock(side_effect=RuntimeError("twin backend down"))

        with patch("app.org.digital_twin.OrgDigitalTwin", return_value=mock_twin):
            # Must not raise -- failures are logged and swallowed.
            result = await _run_org_twin_sync({"org_id": "org-123", "event_type": "x"})

        assert result is None


class TestCronDbFactoryImportRegression:
    """Regression test for a bug where the cron coroutines did
    ``from app.db.session import db_factory``, but ``app.db.session`` has no
    such attribute -- only ``get_session_factory()`` (a callable that
    *returns* a session-factory). The bad import raised ImportError inside
    the coroutines' own try/except, which silently swallowed it and made
    both org-intelligence-cron and org-digest-cron permanent no-ops in
    production (they always returned zero counts, never touching the DB).
    """

    def test_app_db_session_has_no_db_factory_attribute(self):
        import app.db.session as session_module

        assert not hasattr(session_module, "db_factory")
        assert hasattr(session_module, "get_session_factory")

    def test_app_org_digest_has_no_org_digest_service_attribute(self):
        # Companion bug: the digest cron also referenced a non-existent
        # ``OrgDigestService`` class -- the real class is ``DigestGenerator``
        # with a ``generate(org_id, tenant_id, since=None)`` signature (not
        # ``generate(org_id)``). Both mistakes combined meant
        # org-digest-cron raised ImportError on every single run.
        import app.org.digest as digest_module

        assert not hasattr(digest_module, "OrgDigestService")
        assert hasattr(digest_module, "DigestGenerator")

    @pytest.mark.asyncio
    async def test_intelligence_cron_actually_calls_get_session_factory(self):
        from app.org.feature_flags import _run_org_intelligence_cron

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        factory = MagicMock(return_value=_AsyncCtx(session))

        with (
            patch("app.db.session.get_session_factory", return_value=factory) as get_factory,
            patch("app.db.rls.sqlalchemy_rls_context", _null_rls_ctx),
        ):
            result = await _run_org_intelligence_cron()

        get_factory.assert_called_once()
        factory.assert_called_once()
        assert result == {"processed": 0, "insights": 0}

    @pytest.mark.asyncio
    async def test_digest_cron_actually_calls_get_session_factory(self):
        from app.org.feature_flags import _run_org_digest_cron

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        factory = MagicMock(return_value=_AsyncCtx(session))

        with (
            patch("app.db.session.get_session_factory", return_value=factory) as get_factory,
            patch("app.db.rls.sqlalchemy_rls_context", _null_rls_ctx),
        ):
            result = await _run_org_digest_cron()

        get_factory.assert_called_once()
        factory.assert_called_once()
        assert result == {"processed": 0, "digests": 0}
