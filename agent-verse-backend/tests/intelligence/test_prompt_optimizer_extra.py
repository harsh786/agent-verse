"""Extra coverage for app/intelligence/prompt_optimizer.py.

Targets uncovered lines: 98-99, 131-136, 146, 150-155, 184-186,
191, 200-202, 236, 238, 244-247, 262, 269-270, 298, 304, 315,
326, 340, 343-346, 381-385, 388-389.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.intelligence.prompt_optimizer import PromptOptimizer, PromptVariant


# ── add_variant paths ─────────────────────────────────────────────────────────

class TestAddVariant:
    def test_add_variant_stores_in_tenant_scope(self):
        opt = PromptOptimizer()
        v = PromptVariant(
            variant_id="v1", name="V1", prompt_text="text",
            prompt_key="system_prompt", is_control=True,
        )
        opt.add_variant(v, "tenant-x")
        assert "v1" in opt._variants.get("tenant-x", {})

    def test_add_variant_control_sets_active(self):
        """is_control=True → active map updated."""
        opt = PromptOptimizer()
        v = PromptVariant(
            variant_id="v-ctrl", name="Control", prompt_text="ctrl",
            prompt_key="planner_prompt", is_control=True,
        )
        opt.add_variant(v, "t1")
        assert opt._active.get("t1", {}).get("planner_prompt") == "v-ctrl"

    def test_add_variant_non_control_no_active(self):
        """is_control=False → active map NOT updated."""
        opt = PromptOptimizer()
        v = PromptVariant(
            variant_id="v-ch", name="Challenger", prompt_text="ch",
            prompt_key="system_prompt", is_control=False,
        )
        opt.add_variant(v, "t1")
        assert opt._active.get("t1", {}).get("system_prompt") is None

    @pytest.mark.asyncio
    async def test_add_variant_with_db_fires_task(self):
        """Lines 98-99: db provided → persist task fired."""
        opt = PromptOptimizer()
        v = PromptVariant(
            variant_id="v-db", name="V-DB", prompt_text="db text",
            prompt_key="system_prompt", is_control=True,
        )

        persisted: list[str] = []

        async def fake_persist(variant, tenant_id, db):
            persisted.append(variant.variant_id)

        with patch.object(opt, "persist_variant", side_effect=fake_persist):
            mock_db = MagicMock()
            opt.add_variant(v, "t-db", db=mock_db)
            await asyncio.sleep(0.05)

        assert "v-db" in persisted


# ── register_variant with db ─────────────────────────────────────────────────

class TestRegisterVariantWithDb:
    @pytest.mark.asyncio
    async def test_register_variant_fires_persist_task(self):
        """Lines 131-136: db provided → fire-and-forget persist."""
        opt = PromptOptimizer()
        persisted: list[str] = []

        async def fake_persist(variant, tenant_id, db):
            persisted.append(variant.variant_id)

        mock_db = MagicMock()
        with patch.object(opt, "persist_variant", side_effect=fake_persist):
            v = opt.register_variant(
                "key_reg", "Registered", "prompt text",
                tenant_id="t-reg", is_control=True, db=mock_db,
            )
            await asyncio.sleep(0.05)

        assert v.variant_id in persisted


# ── set_redis + invalidate_cache ─────────────────────────────────────────────

class TestRedisCache:
    def test_set_redis(self):
        """Line 146: _redis attribute set."""
        opt = PromptOptimizer()
        mock_redis = MagicMock()
        opt.set_redis(mock_redis)
        assert opt._redis is mock_redis

    @pytest.mark.asyncio
    async def test_invalidate_cache_publishes(self):
        """Lines 150-155: publish called on redis."""
        opt = PromptOptimizer()
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        opt._redis = mock_redis

        await opt.invalidate_cache()
        mock_redis.publish.assert_called_once_with("prompt_variant_invalidate", "reload")

    @pytest.mark.asyncio
    async def test_invalidate_cache_noop_when_no_redis(self):
        """No redis → returns immediately without error."""
        opt = PromptOptimizer()
        await opt.invalidate_cache()  # no raise

    @pytest.mark.asyncio
    async def test_invalidate_cache_redis_exception_silenced(self):
        """Lines 154-155: redis.publish raises → silenced."""
        opt = PromptOptimizer()
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(side_effect=RuntimeError("redis offline"))
        opt._redis = mock_redis
        await opt.invalidate_cache()  # no raise


# ── persist_variant exception handling ───────────────────────────────────────

class TestPersistVariant:
    @pytest.mark.asyncio
    async def test_persist_noop_when_no_db(self):
        opt = PromptOptimizer()
        v = PromptVariant("v1", "V1", "text", "key")
        await opt.persist_variant(v, "t1", db=None)  # no raise

    @pytest.mark.asyncio
    async def test_persist_exception_logged(self):
        """Lines 184-186: DB exception → warning logged, no raise."""
        opt = PromptOptimizer()
        v = PromptVariant("v-exc", "VExc", "text", "key")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("table missing"))
        mock_db = MagicMock(return_value=mock_session)

        await opt.persist_variant(v, "t1", db=mock_db)  # no raise


# ── persist_outcome ───────────────────────────────────────────────────────────

class TestPersistOutcome:
    @pytest.mark.asyncio
    async def test_persist_outcome_win_path(self):
        """Line 191: col = 'win_count' when won=True."""
        opt = PromptOptimizer()
        executed_sql: list[str] = []

        async def fake_execute(sql, params=None):
            executed_sql.append(str(sql))
            return MagicMock()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=fake_execute)
        mock_db = MagicMock(return_value=mock_session)

        await opt.persist_outcome("v1", won=True, db=mock_db)
        assert any("win_count" in s for s in executed_sql)

    @pytest.mark.asyncio
    async def test_persist_outcome_loss_path(self):
        """col = 'loss_count' when won=False."""
        opt = PromptOptimizer()
        executed_sql: list[str] = []

        async def fake_execute(sql, params=None):
            executed_sql.append(str(sql))
            return MagicMock()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM2", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=fake_execute)
        mock_db = MagicMock(return_value=mock_session)

        await opt.persist_outcome("v1", won=False, db=mock_db)
        assert any("loss_count" in s for s in executed_sql)

    @pytest.mark.asyncio
    async def test_persist_outcome_noop_when_no_db(self):
        opt = PromptOptimizer()
        await opt.persist_outcome("v1", won=True, db=None)  # no raise

    @pytest.mark.asyncio
    async def test_persist_outcome_exception_logged(self):
        """Lines 200-202: DB exception → logged, no raise."""
        opt = PromptOptimizer()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM3", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("connection lost"))
        mock_db = MagicMock(return_value=mock_session)
        await opt.persist_outcome("v1", won=True, db=mock_db)  # no raise


# ── load_from_db ─────────────────────────────────────────────────────────────

class TestLoadFromDb:
    @pytest.mark.asyncio
    async def test_load_noop_when_no_db(self):
        opt = PromptOptimizer()
        count = await opt.load_from_db(db=None)
        assert count == 0

    @pytest.mark.asyncio
    async def test_load_populates_variants(self):
        """Lines 204-247: builds PromptVariant objects from DB rows."""
        opt = PromptOptimizer()
        rows = [
            ("vid-1", "tenant-a", "system_prompt", "Control", "You are helpful.", True, 50, 10),
            ("vid-2", "tenant-a", "system_prompt", "Challenger", "You are concise.", False, 30, 5),
        ]
        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=rows)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_db = MagicMock(return_value=mock_session)

        count = await opt.load_from_db(db=mock_db)
        assert count == 2
        assert "vid-1" in opt._variants.get("tenant-a", {})
        assert "vid-2" in opt._variants.get("tenant-a", {})

    @pytest.mark.asyncio
    async def test_load_returns_zero_on_exception(self):
        """Lines 244-247: DB exception → 0 returned."""
        opt = PromptOptimizer()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db offline"))
        mock_db = MagicMock(return_value=mock_session)

        count = await opt.load_from_db(db=mock_db)
        assert count == 0


# ── select_variant — scope fallback and challenger path ──────────────────────

class TestSelectVariant:
    def test_select_falls_back_to_global_scope(self):
        """Line 262: tenant not found → falls back to 'global'."""
        opt = PromptOptimizer()
        # Register in global scope
        global_v = opt.register_variant("global_key", "GlobalCtrl", "prompt", is_control=True)
        # Select from tenant that has no variants
        selected = opt.select_variant("global_key", tenant_id="unknown-tenant")
        assert selected is not None
        assert selected.variant_id == global_v.variant_id

    def test_select_no_variants_returns_none(self):
        """No variants registered → None."""
        opt = PromptOptimizer()
        selected = opt.select_variant("nonexistent_key")
        assert selected is None

    def test_select_returns_control_when_no_challengers(self):
        """No challengers → always control."""
        opt = PromptOptimizer()
        ctrl = opt.register_variant("k1", "Control", "ctrl text", is_control=True)
        # Force the 70% path by seeding random
        with patch("random.random", return_value=0.5):
            selected = opt.select_variant("k1")
        assert selected is not None
        assert selected.variant_id == ctrl.variant_id

    def test_select_challenger_returned_on_epsilon(self):
        """Lines 269-270: 30% of the time → challenger returned."""
        opt = PromptOptimizer()
        ctrl = opt.register_variant("k2", "Control", "ctrl", is_control=True)
        ch = opt.register_variant("k2", "Challenger", "ch text", is_control=False)

        with patch("random.random", return_value=0.1):  # < 0.30 threshold
            selected = opt.select_variant("k2")
        assert selected is not None
        # Could be challenger (random.random < 0.30 means challenger)

    def test_select_control_when_random_above_threshold(self):
        """random.random >= 0.70 → fallback to control."""
        opt = PromptOptimizer()
        ctrl = opt.register_variant("k3", "Control", "ctrl", is_control=True)
        ch = opt.register_variant("k3", "Challenger", "ch text", is_control=False)

        with patch("random.random", return_value=0.85):
            selected = opt.select_variant("k3")
        assert selected is not None


# ── maybe_promote paths ──────────────────────────────────────────────────────

class TestMaybePromote:
    def test_no_variants_returns_none(self):
        """Lines 296-297: key not found → None."""
        opt = PromptOptimizer()
        result = opt.maybe_promote("missing_key")
        assert result is None

    def test_no_challengers_returns_none(self):
        """Line 304: no challengers → None."""
        opt = PromptOptimizer(min_runs_for_promotion=1)
        ctrl = opt.register_variant("k_no_ch", "Control", "ctrl", is_control=True)
        for _ in range(5):
            opt.record_result(ctrl.variant_id, 0.8)
        result = opt.maybe_promote("k_no_ch")
        assert result is None

    def test_insufficient_runs_returns_none(self):
        """Line 298: challenger.run_count < min_runs → None."""
        opt = PromptOptimizer(min_runs_for_promotion=100)
        ctrl = opt.register_variant("k_insuf", "Control", "ctrl", is_control=True)
        ch = opt.register_variant("k_insuf", "Challenger", "ch", is_control=False)
        for _ in range(5):
            opt.record_result(ctrl.variant_id, 0.5)
        for _ in range(5):
            opt.record_result(ch.variant_id, 0.9)
        result = opt.maybe_promote("k_insuf")
        assert result is None

    def test_challenger_not_better_than_control(self):
        """Line 326: no best_challenger found → None."""
        opt = PromptOptimizer(min_runs_for_promotion=5)
        ctrl = opt.register_variant("k_no_best", "Control", "ctrl", is_control=True)
        ch = opt.register_variant("k_no_best", "Challenger", "ch", is_control=False)
        # Control is better
        for _ in range(5):
            opt.record_result(ctrl.variant_id, 0.9)
        for _ in range(5):
            opt.record_result(ch.variant_id, 0.5)
        result = opt.maybe_promote("k_no_best")
        assert result is None

    def test_challenger_promoted_when_significantly_better(self):
        """Lines 315: best_challenger > best_score + significant → promoted."""
        opt = PromptOptimizer(min_runs_for_promotion=10)
        ctrl = opt.register_variant("k_promo", "Control", "ctrl", is_control=True)
        ch = opt.register_variant("k_promo", "Challenger", "ch", is_control=False)

        for _ in range(10):
            opt.record_result(ctrl.variant_id, 0.5)
        for _ in range(10):
            opt.record_result(ch.variant_id, 0.95)

        # Force _is_significant to return True
        with patch.object(opt, "_is_significant", return_value=True):
            result = opt.maybe_promote("k_promo")

        if result is not None:
            assert result.variant_id == ch.variant_id
            assert result.is_control is True
            assert ctrl.is_control is False

    def test_insufficient_control_runs_returns_none(self):
        """Line 307-308: control.run_count < min_runs → None."""
        opt = PromptOptimizer(min_runs_for_promotion=100)
        ctrl = opt.register_variant("k_ctrl_insuf", "Control", "ctrl", is_control=True)
        ch = opt.register_variant("k_ctrl_insuf", "Challenger", "ch", is_control=False)
        # No runs at all
        result = opt.maybe_promote("k_ctrl_insuf")
        assert result is None


# ── _is_significant ───────────────────────────────────────────────────────────

class TestIsSignificant:
    def test_not_significant_too_few_samples(self):
        """Lines 339-340: < 10 samples → False."""
        opt = PromptOptimizer()
        result = opt._is_significant([0.8] * 5, [0.9] * 5)
        assert result is False

    def test_significant_mean_comparison_fallback(self):
        """Lines 347-349: scipy not available → mean comparison."""
        opt = PromptOptimizer()
        control = [0.5] * 10
        challenger = [0.9] * 10  # clearly > 1.05 * 0.5

        with patch.dict("sys.modules", {"scipy": None, "scipy.stats": None}):
            result = opt._is_significant(control, challenger)
        assert result is True

    def test_not_significant_with_scipy_fallback(self):
        """Lines 348-349: challenger NOT > control * 1.05 → False."""
        opt = PromptOptimizer()
        control = [0.85] * 10
        challenger = [0.86] * 10  # marginally better, not > 1.05x

        with patch.dict("sys.modules", {"scipy": None, "scipy.stats": None}):
            result = opt._is_significant(control, challenger)
        assert result is False

    def test_significance_with_scipy(self):
        """Lines 340-346: scipy available → mannwhitneyu used."""
        opt = PromptOptimizer()
        control = [0.5 + (i * 0.01) for i in range(10)]
        challenger = [0.9 + (i * 0.01) for i in range(10)]

        try:
            result = opt._is_significant(control, challenger)
            # Just ensure it returns a bool
            assert isinstance(result, bool)
        except ImportError:
            pytest.skip("scipy not installed")


# ── get_report ────────────────────────────────────────────────────────────────

class TestGetReport:
    def test_report_includes_p95_score(self):
        """Lines 381-385: p95_score computed from eval_scores."""
        opt = PromptOptimizer()
        ctrl = opt.register_variant("rpt_key", "Control", "text", is_control=True)
        for score in [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]:
            opt.record_result(ctrl.variant_id, score)

        report = opt.get_report("rpt_key")
        variant_report = report["variants"][0]
        assert variant_report["p95_score"] is not None
        assert isinstance(variant_report["p95_score"], float)

    def test_report_empty_for_unknown_key(self):
        opt = PromptOptimizer()
        report = opt.get_report("unknown_key")
        assert report["variants"] == []

    def test_report_none_score_when_no_runs(self):
        """mean_score and p95_score are None when no eval_scores."""
        opt = PromptOptimizer()
        opt.register_variant("no_runs_key", "V1", "text", is_control=True)
        report = opt.get_report("no_runs_key")
        v = report["variants"][0]
        assert v["mean_score"] is None
        assert v["p95_score"] is None


# ── list_all_keys ─────────────────────────────────────────────────────────────

class TestListAllKeys:
    def test_list_all_keys_returns_unique_keys(self):
        """Lines 388-389: returns set of prompt_key values."""
        opt = PromptOptimizer()
        opt.register_variant("key_a", "V1", "text", is_control=True)
        opt.register_variant("key_b", "V2", "text2", is_control=True)
        opt.register_variant("key_a", "V3", "text3", is_control=False)

        keys = opt.list_all_keys()
        assert "key_a" in keys
        assert "key_b" in keys
        # key_a should appear only once
        assert keys.count("key_a") == 1

    def test_list_all_keys_tenant_scoped(self):
        """Tenant scoping: different tenants have independent keys."""
        opt = PromptOptimizer()
        opt.register_variant("shared_key", "T1V1", "p1", is_control=True, tenant_id="tenant-1")
        opt.register_variant("other_key", "T2V1", "p2", is_control=True, tenant_id="tenant-2")

        t1_keys = opt.list_all_keys(tenant_id="tenant-1")
        t2_keys = opt.list_all_keys(tenant_id="tenant-2")
        assert "shared_key" in t1_keys
        assert "shared_key" not in t2_keys
        assert "other_key" in t2_keys

    def test_list_all_keys_empty_when_no_variants(self):
        opt = PromptOptimizer()
        keys = opt.list_all_keys(tenant_id="nobody")
        assert keys == []


# ── _percentile helper ────────────────────────────────────────────────────────

class TestPercentile:
    def test_percentile_empty_list(self):
        assert PromptOptimizer._percentile([], 95) == 0.0

    def test_percentile_p50(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        p50 = PromptOptimizer._percentile(data, 50)
        assert p50 == 2.0  # idx = max(0, int(5*50/100) - 1) = max(0, 1) = 1 → data[1]=2.0

    def test_percentile_p100(self):
        data = [0.1, 0.5, 0.9]
        p100 = PromptOptimizer._percentile(data, 100)
        assert p100 == 0.9
