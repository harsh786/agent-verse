"""e2e_full: the workflow template gallery and marketplace serve the system templates.

``WorkflowService`` called ``SystemTemplateStore.list_all()`` / ``get_by_slug()``,
which did not exist; the AttributeError was logged and swallowed, so
``GET /api/v1/workflows/templates`` and ``/marketplace`` were always empty, a
template could never be fetched by slug, and "instantiate" always 404'd.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1/workflows"


async def test_templates_and_marketplace_list_the_system_templates(tenant_client: Any) -> None:
    listing = await tenant_client.get(f"{_API}/templates", params={"per_page": 100})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] >= 20 and len(body["items"]) == body["total"]
    slugs = {t["slug"] for t in body["items"]}
    assert "aml-screening" in slugs

    page = await tenant_client.get(f"{_API}/templates", params={"per_page": 5, "page": 2})
    assert page.status_code == 200 and len(page.json()["items"]) == 5

    category = body["items"][0]["category"]
    by_cat = await tenant_client.get(f"{_API}/templates", params={"category": category})
    assert by_cat.json()["total"] >= 1
    assert all(t["category"] == category for t in by_cat.json()["items"])

    market = await tenant_client.get(f"{_API}/marketplace", params={"q": "aml"})
    assert market.status_code == 200, market.text
    assert "aml-screening" in {t["slug"] for t in market.json()["items"]}


async def test_template_by_slug_and_instantiate(tenant_client: Any) -> None:
    got = await tenant_client.get(f"{_API}/templates/aml-screening")
    assert got.status_code == 200, got.text
    assert got.json()["slug"] == "aml-screening" and got.json()["definition"]
    assert (await tenant_client.get(f"{_API}/templates/no-such-template")).status_code == 404

    created = await tenant_client.post(
        f"{_API}/templates/aml-screening/instantiate", json={"name": "My AML"}
    )
    assert created.status_code == 201, created.text
    wf_id = created.json()["id"]
    wf = await tenant_client.get(f"{_API}/{wf_id}")
    assert wf.status_code == 200, wf.text
    assert wf.json()["name"] == "My AML"
