from app.org.feature_flags import FeatureFlagService


def test_default_is_disabled_for_any_tenant():
    svc = FeatureFlagService()
    assert svc.is_enabled("org_autonomy_enabled", "t1") is False


def test_global_enable_still_applies_to_any_tenant():
    svc = FeatureFlagService()
    svc.enable("org_autonomy_enabled")
    assert svc.is_enabled("org_autonomy_enabled", "t1") is True
    assert svc.is_enabled("org_autonomy_enabled", "anything-else") is True


def test_per_tenant_enable_is_isolated_to_that_tenant():
    svc = FeatureFlagService()
    # global stays OFF
    svc.enable_for_tenant("org_autonomy_enabled", "t1")
    assert svc.is_enabled("org_autonomy_enabled", "t1") is True
    assert svc.is_enabled("org_autonomy_enabled", "t2") is False


def test_per_tenant_disable_overrides_global_enable():
    svc = FeatureFlagService()
    svc.enable("org_autonomy_enabled")
    svc.disable_for_tenant("org_autonomy_enabled", "t2")
    assert svc.is_enabled("org_autonomy_enabled", "t2") is False
    assert svc.is_enabled("org_autonomy_enabled", "t1") is True


def test_env_allowlist_enables_listed_tenants_only(monkeypatch):
    monkeypatch.setenv("AV_ORG_AUTONOMY_ENABLED_TENANTS", "t1,t3")
    svc = FeatureFlagService()  # global stays OFF
    assert svc.is_enabled("org_autonomy_enabled", "t1") is True
    assert svc.is_enabled("org_autonomy_enabled", "t3") is True
    assert svc.is_enabled("org_autonomy_enabled", "t2") is False


def test_unrelated_flag_without_override_behaves_as_before():
    svc = FeatureFlagService()
    assert svc.is_enabled("org_os_enabled", "t1") is False
    svc.enable("org_os_enabled")
    assert svc.is_enabled("org_os_enabled", "t1") is True
    assert svc.is_enabled("org_os_enabled") is True
    assert svc.is_enabled("org_os_enabled", None) is True
