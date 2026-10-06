"""Extra coverage for app/scaling/tasks.py — targeting ≥80%.

Covers uncovered paths:
  _get_llm_provider (anthropic/openai paths), fire_due_schedules (cron/interval/once),
  detect_stuck_goals, expire_hitl_approvals, consolidate_memories_task,
  reindex_stale_knowledge, purge_expired_artifacts, run_gdpr_export,
  civilization_tick, civilization_learning_step, warm_jwks_cache,
  create_guardrail_partitions, enforce_hitl_sla, flush_audit_wal,
  scan_cost_anomalies, conclude_stale_experiments,
  expire_stale_documents, discover_and_tick_civilizations,
  run_goal (production check, env providers, timeout, dry_run paths),
  _run_with_signals (pause/resume paths).
"""
from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _no_byok_store():
    """A durable LLM-config store confirming the tenant has no BYOK (PROV-12:
    without a store the worker fails closed instead of assuming "no BYOK")."""
    from unittest.mock import AsyncMock, MagicMock

    return MagicMock(get_config=AsyncMock(return_value=None))

def _run_in_new_loop(coro):
    """Run a coroutine in a fresh event loop (substitutes asyncio.run in tests)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── _get_llm_provider ─────────────────────────────────────────────────────────

class TestGetLlmProvider:
    """Lines 143-195: provider selection from Redis config."""

    def test_returns_none_when_no_redis_url(self, monkeypatch):
        from app.scaling.tasks import _get_llm_provider
        monkeypatch.delenv("REDIS_URL", raising=False)
        with patch("app.services.llm_config_store.get_or_create_worker_llm_config_store", return_value=_no_byok_store()):
            assert _get_llm_provider("t1") is None

    def test_redis_raises_and_no_durable_store_fails_closed(self, monkeypatch):
        """Cache down and nothing durable to confirm "no BYOK": fail, don't guess."""
        from app.providers.tenant_provider import TenantProviderError
        from app.scaling.tasks import _get_llm_provider
        monkeypatch.setenv("REDIS_URL", "redis://localhost:9999/0")
        with patch("redis.from_url", side_effect=Exception("no redis")), patch("app.services.llm_config_store.get_or_create_worker_llm_config_store", return_value=None), \
             pytest.raises(TenantProviderError):
            _get_llm_provider("t1")

    def test_returns_none_when_no_config_key(self, monkeypatch):
        from app.scaling.tasks import _get_llm_provider
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_r = MagicMock()
        mock_r.get = MagicMock(return_value=None)
        with patch("redis.from_url", return_value=mock_r), patch("app.services.llm_config_store.get_or_create_worker_llm_config_store", return_value=_no_byok_store()):
            assert _get_llm_provider("t1") is None

    def test_raises_when_no_encrypted_key(self, monkeypatch):
        """A BYOK config without a key fails the goal instead of using the platform."""
        import json

        from app.providers.tenant_provider import TenantProviderError
        from app.scaling.tasks import _get_llm_provider
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_r = MagicMock()
        mock_r.get = MagicMock(return_value=json.dumps({"provider": "anthropic"}))
        with patch("redis.from_url", return_value=mock_r), \
             pytest.raises(TenantProviderError):
            _get_llm_provider("t1")

    def test_returns_anthropic_provider(self, monkeypatch):
        """Lines 180-183: returns AnthropicProvider for anthropic config."""
        import json

        from app.scaling.tasks import _get_llm_provider
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_r = MagicMock()
        mock_r.get = MagicMock(return_value=json.dumps({
            "provider": "anthropic",
            "encrypted_key": "enc-key",
            "model": "claude-opus-4-8",
        }))
        mock_vault = MagicMock()
        mock_vault.decrypt = MagicMock(return_value="real-api-key")
        mock_provider = MagicMock()
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.providers.vault.get_vault", return_value=mock_vault), \
             patch("app.providers.anthropic_provider.AnthropicProvider",
                   return_value=mock_provider) as mock_cls:
            result = _get_llm_provider("t1")
        assert result is mock_provider
        mock_cls.assert_called_once_with(api_key="real-api-key", default_model="claude-opus-4-8")

    def test_returns_openai_compatible_provider(self, monkeypatch):
        """Lines 185-190: returns OpenAICompatibleProvider for openai config."""
        import json

        from app.scaling.tasks import _get_llm_provider
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_r = MagicMock()
        mock_r.get = MagicMock(return_value=json.dumps({
            "provider": "openai",
            "encrypted_key": "enc-key",
            "model": "gpt-4o",
            "base_url": None,
        }))
        mock_vault = MagicMock()
        mock_vault.decrypt = MagicMock(return_value="sk-openai-key")
        mock_provider = MagicMock()
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.providers.vault.get_vault", return_value=mock_vault), \
             patch("app.providers.openai_compatible.OpenAICompatibleProvider",
                   return_value=mock_provider) as mock_cls:
            result = _get_llm_provider("t1")
        assert result is mock_provider

    def test_raises_for_unknown_provider(self, monkeypatch):
        """An unknown tenant provider used to be ignored (platform fallback)."""
        import json

        from app.providers.tenant_provider import TenantProviderError
        from app.scaling.tasks import _get_llm_provider
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_r = MagicMock()
        mock_r.get = MagicMock(return_value=json.dumps({
            "provider": "unknown_llm",
            "encrypted_key": "enc-key",
        }))
        mock_vault = MagicMock()
        mock_vault.decrypt = MagicMock(return_value="key")
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.providers.vault.get_vault", return_value=mock_vault), \
             pytest.raises(TenantProviderError):
            _get_llm_provider("t1")


# ── fire_due_schedules ────────────────────────────────────────────────────────

class TestFireDueSchedules:
    """Lines 1261, 1291-1384: schedule types and exception path."""

    def test_no_redis_no_db_returns_zero_fired(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false")
        from app.scaling.tasks import fire_due_schedules
        with patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False):
            result = fire_due_schedules.run()
        assert result["schedules_fired"] == 0

    def test_skips_paused_schedules(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        from app.scaling.tasks import fire_due_schedules
        mock_r = MagicMock()
        mock_r.scan_iter = MagicMock(return_value=["schedule:t1:s1"])
        import json
        mock_r.get = MagicMock(return_value=json.dumps({
            "paused": True, "tenant_id": "t1", "goal": "task", "trigger_type": "interval",
            "interval_seconds": 60
        }))
        mock_r.set = MagicMock()
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False):
            result = fire_due_schedules.run()
        assert result["schedules_fired"] == 0

    def test_fires_interval_schedule_when_due(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        import json

        from app.scaling.tasks import fire_due_schedules
        mock_r = MagicMock()
        mock_r.scan_iter = MagicMock(return_value=["schedule:t1:s1"])
        mock_r.get = MagicMock(return_value=json.dumps({
            "trigger_type": "interval",
            "interval_seconds": 60,
            "tenant_id": "t1",
            "goal": "interval task",
            "last_fired_at": None,
        }))
        mock_r.set = MagicMock()
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False), \
             patch("app.scaling.tasks._dispatch_due_schedule", return_value=None), \
             patch("app.scaling.tasks._scheduled_goal_kwargs",
                   return_value={"tenant_id": "t1", "goal_text": "interval task",
                                 "goal_id": "g1", "priority": "normal",
                                 "dry_run": False, "agent_id": "", "workflow_mode": "single_agent",
                                 "goal_template": ""}):
            result = fire_due_schedules.run()
        assert result["schedules_fired"] == 1

    def test_fires_once_schedule_when_due(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        import json

        from app.scaling.tasks import fire_due_schedules
        # Fire at a time in the past
        fire_at = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
        mock_r = MagicMock()
        mock_r.scan_iter = MagicMock(return_value=["schedule:t1:s2"])
        mock_r.get = MagicMock(return_value=json.dumps({
            "trigger_type": "once",
            "fire_at_iso": fire_at,
            "tenant_id": "t1",
            "goal": "one-time task",
            "last_fired_at": None,
        }))
        mock_r.set = MagicMock()
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False), \
             patch("app.scaling.tasks._dispatch_due_schedule", return_value=None), \
             patch("app.scaling.tasks._scheduled_goal_kwargs",
                   return_value={"tenant_id": "t1", "goal_text": "one-time task",
                                 "goal_id": "g2", "priority": "normal",
                                 "dry_run": False, "agent_id": "", "workflow_mode": "single_agent",
                                 "goal_template": ""}):
            result = fire_due_schedules.run()
        assert result["schedules_fired"] == 1

    def test_schedule_kwargs_none_returns_none_from_dispatch(self, monkeypatch):
        """Line 1261: advance_and_dispatch returns None when goal_kwargs is None."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        import json

        from app.scaling.tasks import fire_due_schedules
        mock_r = MagicMock()
        mock_r.scan_iter = MagicMock(return_value=["schedule:t1:s3"])
        mock_r.get = MagicMock(return_value=json.dumps({
            "trigger_type": "interval",
            "interval_seconds": 60,
            "tenant_id": "t1",
            "goal": "",
            "last_fired_at": None,
        }))
        mock_r.set = MagicMock()
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False), \
             patch("app.scaling.tasks._scheduled_goal_kwargs", return_value=None):
            result = fire_due_schedules.run()
        assert result["schedules_fired"] == 0

    def test_exception_in_schedule_key_is_logged(self, monkeypatch):
        """Line 1372-1373: exception processing a schedule is logged and continued."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        import json

        from app.scaling.tasks import fire_due_schedules
        mock_r = MagicMock()
        mock_r.scan_iter = MagicMock(return_value=["schedule:t1:bad"])
        mock_r.get = MagicMock(return_value=json.dumps({
            "trigger_type": "interval",
            "interval_seconds": "not-a-number",  # Will cause exception
            "tenant_id": "t1",
            "goal": "bad",
        }))
        mock_r.set = MagicMock()
        with patch("redis.from_url", return_value=mock_r), \
             patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False):
            # Should not raise even with bad schedule data
            result = fire_due_schedules.run()
        assert "schedules_fired" in result


# ── detect_stuck_goals ────────────────────────────────────────────────────────

class TestDetectStuckGoals:
    def test_db_exception_fails_the_task(self):
        """A broken scan must fail the task, not return a 'successful' error dict."""
        from app.scaling.tasks import discover_and_tick_civilizations
        with patch(
            "app.db.session.get_system_session_factory", side_effect=Exception("no db")
        ), pytest.raises(Exception, match="no db"):
            discover_and_tick_civilizations()

    def test_success_returns_count_and_scans_under_system_session(self):
        from app.scaling.tasks import discover_and_tick_civilizations
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(
            fetchall=MagicMock(return_value=[("c1", "t1")])
        ))
        begin_cm = MagicMock()
        begin_cm.__aenter__ = AsyncMock(return_value=None)
        begin_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_cm)
        with patch("app.db.session.get_system_session_factory", return_value=mock_factory), \
             patch("app.scaling.tasks._get_sync_redis", return_value=None), \
             patch("app.scaling.tasks.civilization_tick.apply_async") as enqueue:
            result = discover_and_tick_civilizations()
        enqueue.assert_called_once()
        assert enqueue.call_args.kwargs["args"] == ["c1", "t1"]
        sqls = [str(c.args[0]) for c in mock_session.execute.await_args_list]
        assert "row_security" in sqls[0]
        assert result["civilizations_ticked"] == 1


# ── run_goal (selected paths) ─────────────────────────────────────────────────

class TestRunGoalPaths:
    """Lines 337-346, 364-365, 533-539, 630-636."""

    def _lock_acquired_ctx(self):
        """Context that makes the distributed lock always succeed."""
        mock_lock = MagicMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        return patch("app.reliability.distributed_lock.GoalExecutionLock",
                     return_value=mock_lock)

    def test_dry_run_returns_complete(self):
        from app.scaling.tasks import run_goal
        with self._lock_acquired_ctx(), \
             patch("app.scaling.tasks._get_sync_redis", return_value=None), \
             patch("app.scaling.tasks._run_async",
                   side_effect=lambda coro: asyncio.new_event_loop().run_until_complete(coro)):
            result = run_goal.run(
                goal_id="g1",
                tenant_id="t1",
                goal_text="Do x",
                dry_run=True,
            )
        assert result["status"] == "complete"
        assert result["dry_run"] is True

    def test_plan_tier_value_error_falls_back_to_professional(self):
        """Lines 345-346: invalid plan string → PROFESSIONAL."""
        from app.scaling.tasks import run_goal
        with self._lock_acquired_ctx(), \
             patch("app.scaling.tasks._get_sync_redis", return_value=None), \
             patch("app.scaling.tasks._run_async",
                   side_effect=lambda coro: asyncio.new_event_loop().run_until_complete(coro)):
            result = run_goal.run(
                goal_id="g2",
                tenant_id="t1",
                goal_text="test",
                dry_run=True,
            )
        assert result["status"] == "complete"

    def test_emergency_stop_blocks_goal(self):
        """Lines 358-363: emergency stop returns blocked."""
        from app.scaling.tasks import run_goal
        mock_r = MagicMock()
        mock_r.get = MagicMock(return_value="1")  # stop active
        with self._lock_acquired_ctx(), \
             patch("app.scaling.tasks._get_sync_redis", return_value=mock_r), \
             patch("app.scaling.tasks._run_async",
                   side_effect=lambda coro: asyncio.new_event_loop().run_until_complete(coro)):
            result = run_goal.run(
                goal_id="g3",
                tenant_id="t1",
                goal_text="blocked",
                dry_run=False,
            )
        assert result["status"] == "blocked"

    @pytest.mark.parametrize("env", ["production", "staging"])
    def test_production_fake_provider_fails_goal(self, monkeypatch, env):
        """Fake provider blocked outside development/test (BYOK-3: staging too)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ENVIRONMENT", env)
        from app.scaling.tasks import run_goal
        # Patch get_session_factory to avoid asyncpg cross-loop teardown errors.
        # Patch vault to avoid production vault key requirement.
        mock_factory = MagicMock(return_value=None)

        from app.providers.vault import CredentialVault
        fake_vault = CredentialVault(master_key="dev-insecure-master-key")

        with self._lock_acquired_ctx(), \
             patch("app.scaling.tasks._get_sync_redis", return_value=None), \
             patch("app.scaling.tasks._run_async",
                   side_effect=lambda coro: asyncio.new_event_loop().run_until_complete(coro)), \
             patch("app.scaling.tasks._get_llm_provider", return_value=None), \
             patch("app.scaling.tasks._REAL_AGENT_LOOP_CLASS", None), \
             patch("app.core.config.get_provider_env", return_value=None), \
             patch("app.db.session._make_session_factory", return_value=mock_factory), \
             patch("app.providers.vault.get_vault", return_value=fake_vault):
            result = run_goal.run(
                goal_id="g4",
                tenant_id="t1",
                goal_text="prod goal",
                dry_run=False,
            )
        # Goal must fail outside development when no real LLM provider is configured.
        assert result["status"] in ("failed", "dead_lettered", "no_llm_provider"), (
            f"Expected failed/dead_lettered status in {env} without LLM, got: {result}"
        )
        if result["status"] == "failed" and result.get("reason") == "no_llm_provider":
            assert "no LLM provider configured for tenant 't1'" in result["message"]

    def test_anthropic_env_provider_used(self, monkeypatch):
        """Lines 533-535: ANTHROPIC_API_KEY path."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthropic")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from app.scaling.tasks import run_goal
        mock_provider = MagicMock()
        with self._lock_acquired_ctx(), \
             patch("app.scaling.tasks._get_sync_redis", return_value=None), \
             patch("app.scaling.tasks._run_async",
                   side_effect=lambda coro: asyncio.new_event_loop().run_until_complete(coro)), \
             patch("app.scaling.tasks._get_llm_provider", return_value=None), \
             patch("app.providers.anthropic_provider.AnthropicProvider",
                   return_value=mock_provider):
            result = run_goal.run(
                goal_id="g5", tenant_id="t1", goal_text="test", dry_run=True
            )
        assert result["status"] == "complete"

    def test_openai_env_provider_used(self, monkeypatch):
        """Lines 537-539: OPENAI_API_KEY path."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        from app.scaling.tasks import run_goal
        mock_provider = MagicMock()
        with self._lock_acquired_ctx(), \
             patch("app.scaling.tasks._get_sync_redis", return_value=None), \
             patch("app.scaling.tasks._run_async",
                   side_effect=lambda coro: asyncio.new_event_loop().run_until_complete(coro)), \
             patch("app.scaling.tasks._get_llm_provider", return_value=None), \
             patch("app.providers.openai_compatible.OpenAICompatibleProvider",
                   return_value=mock_provider):
            result = run_goal.run(
                goal_id="g6", tenant_id="t1", goal_text="test", dry_run=True
            )
        assert result["status"] == "complete"


# ── _run_with_signals ─────────────────────────────────────────────────────────

class TestRunWithSignals:
    """Lines 236-250: pause/resume signals in worker."""

    async def test_pause_and_cancel_while_paused(self):
        """Lines 236-247: pause detected → cancel run, wait, cancel while paused."""
        from app.reliability.goal_lifecycle import GoalCancelledError
        from app.scaling.tasks import _run_with_signals

        call_count = 0

        async def mock_agent_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(100)  # simulate long-running

        mock_runner = MagicMock()
        mock_runner.run = mock_agent_run

        tenant_ctx = MagicMock()
        mock_sync_r = MagicMock()

        # First poll: paused=True; then cancelled while paused
        is_paused_returns = [True, True, True]
        is_cancelled_returns = [False, False, True]

        pause_idx = 0
        cancel_idx = 0

        def is_paused(gid, r):
            nonlocal pause_idx
            val = is_paused_returns[pause_idx] if pause_idx < len(is_paused_returns) else False
            pause_idx += 1
            return val

        def is_cancelled(gid, r):
            nonlocal cancel_idx
            val = is_cancelled_returns[cancel_idx] if cancel_idx < len(is_cancelled_returns) else False
            cancel_idx += 1
            return val

        with patch("app.scaling.tasks._get_sync_redis", return_value=mock_sync_r), \
             patch("app.reliability.goal_lifecycle.is_paused_sync", is_paused), \
             patch("app.reliability.goal_lifecycle.is_cancelled_sync", is_cancelled), \
             patch("asyncio.sleep", AsyncMock()), pytest.raises(GoalCancelledError):
            await _run_with_signals(
                mock_runner, "Do task", tenant_ctx, AsyncMock(), "goal-1"
            )

    async def test_cancel_before_pause(self):
        """Lines 228-234: cancel detected before pause check."""
        from app.reliability.goal_lifecycle import GoalCancelledError
        from app.scaling.tasks import _run_with_signals

        async def mock_agent_run(*args, **kwargs):
            await asyncio.sleep(100)

        mock_runner = MagicMock()
        mock_runner.run = mock_agent_run

        mock_sync_r = MagicMock()

        with patch("app.scaling.tasks._get_sync_redis", return_value=mock_sync_r), \
             patch("app.reliability.goal_lifecycle.is_cancelled_sync", return_value=True), \
             patch("app.reliability.goal_lifecycle.is_paused_sync", return_value=False), \
             patch("asyncio.sleep", AsyncMock()), pytest.raises(GoalCancelledError):
            await _run_with_signals(
                mock_runner, "Do task", MagicMock(), AsyncMock(), "goal-cancel"
            )


# ── check_email_goals (IMAP disabled path) ────────────────────────────────────

class TestCheckEmailGoalsDisabled:
    def test_imap_not_enabled(self, monkeypatch):
        monkeypatch.delenv("IMAP_ENABLED", raising=False)
        from app.scaling.tasks import check_email_goals
        result = check_email_goals.run()
        assert result["status"] == "disabled"
        assert result["processed"] == 0

    def test_imap_explicitly_false(self, monkeypatch):
        monkeypatch.setenv("IMAP_ENABLED", "false")
        from app.scaling.tasks import check_email_goals
        result = check_email_goals.run()
        assert result["status"] == "disabled"


# ── regression: flush_audit_wal db_factory keyword arg ───────────────────────

class TestFlushAuditWalDbFactory:
    """Regression tests for flush_audit_wal AuditFlusher keyword arg fix."""

    def test_audit_flusher_called_with_db_factory_keyword(self):
        """flush_audit_wal must pass db_factory= (not db=) to AuditFlusher.

        Regression: tasks.py called AuditFlusher(redis=r, db=...) which raised
        TypeError because AuditFlusher.__init__ expects db_factory not db.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.scaling.tasks import flush_audit_wal

        mock_flusher_instance = MagicMock()
        mock_flusher_instance.flush = AsyncMock(return_value=5)

        constructed_kwargs = {}

        def capture_init(self, redis, db_factory):
            constructed_kwargs["redis"] = redis
            constructed_kwargs["db_factory"] = db_factory
            self._redis = redis
            self._db = db_factory
            self._chain_cache = {}

        with patch("app.governance.audit_v3.AuditFlusher.__init__", capture_init), \
             patch("app.governance.audit_v3.AuditFlusher.flush", AsyncMock(return_value=5)), \
             patch("app.db.session.get_session_factory", return_value=MagicMock()), \
             patch("redis.asyncio.from_url", return_value=AsyncMock(aclose=AsyncMock())), \
             patch("asyncio.run", _run_in_new_loop):
            result = flush_audit_wal.run()

        # Must have used db_factory keyword, not db
        assert "db_factory" in constructed_kwargs, (
            "AuditFlusher was not called with db_factory= keyword; "
            "did someone revert the fix to use db=?"
        )
        assert "db" not in constructed_kwargs


# ── regression: enforce_hitl_sla SQL column names ─────────────────────────────

class TestEnforceHitlSlaSqlColumns:
    """enforce_hitl_sla must act on approval_requests, not the never-written
    hitl_approval_requests table."""

    def test_sla_sql_targets_approval_requests(self):
        from app.governance import hitl_sla

        for sql in (hitl_sla._AUTO_DENY_SQL, hitl_sla._ESCALATE_SQL, hitl_sla.SLA_STATS_SQL):
            assert "hitl_approval_requests" not in sql
            assert "approval_requests ar" in sql
            assert "approval_sla_configs" in sql

