"""e2e_full: the SSRF egress guard is enabled on the live URL-ingestion path.

The Raccoon-plan flagship (SSRF guard ENABLED in e2e): ``POST /knowledge/ingest/url``
must reject internal / loopback / link-local / cloud-metadata targets with a 400
*before* any fetch, proving ``assert_public_url`` runs on the wired path — not a
mock. Booted app, real Postgres+Redis.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# Addresses an SSRF guard must refuse: cloud metadata, loopback, link-local, and
# RFC-1918 private ranges.
_BLOCKED_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP metadata
    "http://127.0.0.1:8000/admin",  # loopback
    "http://localhost/internal",  # loopback name
    "http://10.0.0.5/secret",  # RFC-1918
    "http://192.168.1.1/router",  # RFC-1918
    "http://[::1]/loopback",  # IPv6 loopback
]


async def _collection(tenant_client: Any) -> str:
    resp = await tenant_client.post(
        "/knowledge/collections", json={"name": f"ssrf-{uuid.uuid4().hex[:8]}"}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["collection_id"])


@pytest.mark.parametrize("url", _BLOCKED_URLS)
async def test_internal_url_is_blocked(tenant_client: Any, url: str) -> None:
    collection_id = await _collection(tenant_client)
    resp = await tenant_client.post(
        "/knowledge/ingest/url",
        json={"collection_id": collection_id, "url": url, "source_type": "web"},
    )
    assert resp.status_code == 400, (
        f"SSRF guard did not block {url!r}: {resp.status_code} {resp.text}"
    )
    assert "blocked" in resp.text.lower(), resp.text
