"""a10-F230-01/02: goal templates — built-ins stay deleted, the pack fails honestly,
the list is bounded, and every route maps a store failure to 503.

* F230-01: the "seeded" marker lived in process memory, so a restart (or another
  replica) re-seeded every built-in the tenant had deleted. Deleting a built-in
  now records a tombstone that seeding skips. A FAILED content-pack load used to
  seed the 15-template fallback instead (a different set, for good); seeding is
  now skipped and retried after a back-off, and the pack is parsed once per
  process instead of once per tenant.
* F230-02: the list was unbounded and only it mapped DB errors to 503.

Real-Postgres coverage: tests/api/test_templates_tombstones_pg.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.templates as tmpl
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-tmpl-builtins", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "ak_test_templates_builtins"
H = {"X-API-Key": _KEY}

_PACK = [
    {"name": f"Pack {i}", "description": "d", "goal_text": f"Do {{{{x}}}} #{i}", "domain": "ops"}
    for i in range(5)
]


class _Loader:
    calls = 0
    fail = False

    def load_all(self) -> _Loader:
        type(self).calls += 1
        if type(self).fail:
            raise RuntimeError("Content validation failed with 1 errors")
        return self

    @property
    def goal_templates(self) -> list[dict[str, Any]]:
        return _PACK


@pytest.fixture(autouse=True)
def _fresh_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.content.loader as loader_mod

    _Loader.calls, _Loader.fail = 0, False
    monkeypatch.setattr(loader_mod, "ContentLoader", _Loader)
    monkeypatch.setattr(tmpl, "_content_cache", {"templates": None, "failed_at": None})


def _client(store: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tmpl.router)
    monkeypatch.setattr(tmpl, "template_store", store)
    return TestClient(app, raise_server_exceptions=False)


def test_deleted_builtin_is_not_reseeded(monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmpl._TemplateStore()
    client = _client(store, monkeypatch)
    listed = client.get("/templates", headers=H).json()
    assert {t["name"] for t in listed} == {t["name"] for t in _PACK}
    victim = next(t for t in listed if t["name"] == "Pack 2")
    assert client.delete(f"/templates/{victim['id']}", headers=H).status_code == 204

    store._seeded_tenants.clear()  # what a restart / another replica does
    names = {t["name"] for t in client.get("/templates", headers=H).json()}
    assert "Pack 2" not in names
    assert len(names) == 4


def test_deleting_a_custom_template_records_no_tombstone(monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmpl._TemplateStore(seed_builtins=False)
    client = _client(store, monkeypatch)
    created = client.post("/templates", json={"name": "Mine", "goal_text": "x"}, headers=H).json()
    assert client.delete(f"/templates/{created['id']}", headers=H).status_code == 204
    assert store._mem_tombstones == set()


def test_failed_content_pack_skips_seeding_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    _Loader.fail = True
    store = tmpl._TemplateStore()
    client = _client(store, monkeypatch)
    assert client.get("/templates", headers=H).json() == []  # no 15-template fallback
    assert _CTX.tenant_id not in store._seeded_tenants
    calls = _Loader.calls
    client.get("/templates", headers=H)
    assert _Loader.calls == calls  # inside the back-off: not re-parsed per request

    _Loader.fail = False
    tmpl._content_cache["failed_at"] = -1e9  # back-off elapsed, pack fixed
    names = {t["name"] for t in client.get("/templates", headers=H).json()}
    assert names == {t["name"] for t in _PACK}


def test_empty_pack_still_uses_the_hard_coded_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_Loader, "goal_templates", property(lambda self: []))
    store = tmpl._TemplateStore()
    names = {t["name"] for t in _client(store, monkeypatch).get("/templates", headers=H).json()}
    assert names == {t["name"] for t in tmpl._BUILTIN_TEMPLATES}


def test_content_pack_is_parsed_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmpl._TemplateStore()
    client = _client(store, monkeypatch)
    client.get("/templates", headers=H)
    store._seeded_tenants.clear()
    client.get("/templates", headers=H)
    assert _Loader.calls == 1


def test_list_is_paged_and_search_runs_before_the_page_is_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmpl._TemplateStore()
    client = _client(store, monkeypatch)
    page1 = client.get("/templates?limit=2", headers=H).json()
    page2 = client.get("/templates?limit=2&offset=2", headers=H).json()
    page3 = client.get("/templates?limit=2&offset=4", headers=H).json()
    ids = [t["id"] for t in page1 + page2 + page3]
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1
    assert len(set(ids)) == 5
    hits = client.get("/templates?search=%234&limit=1", headers=H).json()
    assert [t["name"] for t in hits] == ["Pack 4"]
    assert client.get("/templates?limit=100000", headers=H).status_code == 422


class _BrokenStore(tmpl._TemplateStore):
    def __init__(self) -> None:
        super().__init__(seed_builtins=False)

        def _db() -> Any:
            raise ConnectionError("postgres://u:pw@db refused")

        self._db = _db


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/templates", None),
        ("get", "/templates/t1", None),
        ("post", "/templates", {"name": "n", "goal_text": "g"}),
        ("put", "/templates/t1", {"name": "n", "goal_text": "g"}),
        ("delete", "/templates/t1", None),
        ("post", "/templates/t1/instantiate", {"parameters": {}}),
    ],
)
def test_every_route_maps_store_failure_to_503(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: Any
) -> None:
    client = _client(_BrokenStore(), monkeypatch)
    kwargs: dict[str, Any] = {"headers": H}
    if body is not None:
        kwargs["json"] = body
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 503
    assert "pw@db" not in r.text
