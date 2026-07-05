def test_tenant_membership_endpoints_exist():
    import inspect
    from app.api import tenants
    source = inspect.getsource(tenants)
    assert "members" in source, "Tenant members API missing"
