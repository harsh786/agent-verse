def test_builder_router_importable():
    from app.api.builder import router
    assert router is not None

def test_builder_has_projects_endpoint():
    from app.api.builder import router
    paths = [r.path for r in router.routes]
    assert any("/projects" in p for p in paths)
