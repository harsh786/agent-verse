from app.org.brain_settings import resolve_autonomy_settings


def test_defaults_applied_when_settings_empty():
    s = resolve_autonomy_settings(None, monthly_budget_usd=300.0)
    assert s.paused is False
    assert s.cadence_seconds == 300
    assert s.max_concurrent == 2
    assert s.max_missions_per_day == 8
    assert s.blocked_threshold == 5
    assert s.collaboration_enabled is False


def test_zero_budget_fields_derive_from_monthly():
    s = resolve_autonomy_settings({"autonomy": {}}, monthly_budget_usd=300.0)
    assert s.daily_budget_usd == 10.0            # 300 / 30
    assert s.per_mission_cost_ceiling_usd == 30.0  # 300 * 0.10


def test_explicit_values_override_defaults_and_derivation():
    raw = {"autonomy": {"paused": True, "max_concurrent": 5, "daily_budget_usd": 4.0}}
    s = resolve_autonomy_settings(raw, monthly_budget_usd=300.0)
    assert s.paused is True
    assert s.max_concurrent == 5
    assert s.daily_budget_usd == 4.0  # explicit, not derived
