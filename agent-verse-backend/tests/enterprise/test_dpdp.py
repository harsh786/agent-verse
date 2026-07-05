"""Test DPDP compliance module structure and rule logic."""


def test_dpdp_router_exists():
    from app.api.dpdp import router
    routes = [r.path for r in router.routes]
    assert any("consent" in r for r in routes)
    assert any("erasure" in r for r in routes)
    assert any("grievance" in r for r in routes)


def test_consent_request_model():
    from app.api.dpdp import ConsentRequest
    r = ConsentRequest(data_principal_id="user-123", purpose="analytics", consent_given=True)
    assert r.data_principal_id == "user-123"
    assert r.consent_given is True


def test_erasure_request_model():
    from app.api.dpdp import ErasureRequest
    r = ErasureRequest(data_principal_id="user-456", reason="Data no longer needed")
    assert r.data_principal_id == "user-456"
