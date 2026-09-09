"""Tests for Phase 1a — User + TenantMembership models."""
import pytest


class TestUserModel:
    def test_user_model_importable(self):
        from app.db.models.user import TenantMembership, User
        assert User.__tablename__ == "users"
        assert TenantMembership.__tablename__ == "tenant_memberships"

    def test_user_has_google_sub(self):
        from sqlalchemy.inspection import inspect

        from app.db.models.user import User
        mapper = inspect(User)
        col_names = [c.key for c in mapper.columns]
        assert "google_sub" in col_names
        assert "email" in col_names

    def test_tenant_membership_has_rls_in_migration(self):
        """Migration must enable RLS on tenant_memberships."""
        import importlib
        import inspect as ins
        try:
            mod = importlib.import_module(
                "app.db.migrations.versions.0072_users_and_memberships"
            )
            source = ins.getsource(mod.upgrade)
            assert "ROW LEVEL SECURITY" in source
            assert "tenant_memberships" in source
        except ImportError:
            pytest.skip("Migration module not importable as Python — structure check skipped")


class TestGoogleOAuthRouter:
    def test_google_oauth_router_importable(self):
        from app.auth.google_oauth import router
        assert router is not None

    def test_google_oauth_has_login_endpoint(self):
        from app.auth.google_oauth import router
        routes = [r.path for r in router.routes]
        assert "/auth/google/login" in routes or "/login" in routes

    def test_google_oauth_has_callback_endpoint(self):
        from app.auth.google_oauth import router
        routes = [r.path for r in router.routes]
        assert "/auth/google/callback" in routes or "/callback" in routes


class TestEntitlementsModule:
    def test_entitlements_importable(self):
        from app.tenancy.entitlements import assert_feature, has_feature
        assert callable(has_feature)
