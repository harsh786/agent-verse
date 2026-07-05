"""Tests for golden datasets router."""


def test_golden_datasets_router_importable():
    from app.api.golden_datasets import router
    assert router is not None


def test_has_list_endpoint():
    from app.api.golden_datasets import router
    paths = [r.path for r in router.routes]
    assert any("golden-datasets" in p for p in paths)


def test_has_items_endpoint():
    from app.api.golden_datasets import router
    paths = [r.path for r in router.routes]
    assert any("items" in p for p in paths)


def test_has_promote_goal_endpoint():
    from app.api.golden_datasets import router
    paths = [r.path for r in router.routes]
    assert any("promote-goal" in p for p in paths)


def test_dataset_create_request_importable():
    from app.api.golden_datasets import DatasetCreateRequest
    req = DatasetCreateRequest(name="test", split="regression")
    assert req.name == "test"
    assert req.split == "regression"


def test_dataset_item_request_importable():
    from app.api.golden_datasets import DatasetItemRequest
    req = DatasetItemRequest(goal="run test")
    assert req.goal == "run test"
    assert req.human_label is None
