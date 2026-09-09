"""e2e_full (WS-5): RPA scrape → structured report → downloadable PDF.

Drives the real app over the ASGI client: a tenant calls ``POST /rpa/report``
for a URL; the endpoint runs the RPA executor's scrape sequence, assembles a
provenance-tagged report, renders it to a real PDF via fpdf2, persists it
through the RPA artifact store, and returns the PDF inline. We assert a genuine,
non-empty PDF comes back — no browser is required (the executor's simulation
fallback still produces a report), so this is deterministic in CI.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_rpa_report_returns_real_pdf(tenant_client: Any) -> None:
    resp = await tenant_client.post(
        "/rpa/report",
        json={
            "url": "https://example.com/quarterly",
            "selectors": ["main"],
            "title": "Quarterly scrape",
            "goal_id": "e2e-rpa-report",
        },
    )
    assert resp.status_code == 201, f"{resp.status_code} {resp.text}"
    body = resp.json()

    # The report is structured + provenance-tagged.
    assert body["source_url"] == "https://example.com/quarterly"
    assert body["title"] == "Quarterly scrape"
    assert body["size_bytes"] > 500

    # A genuine, non-empty PDF is returned inline and can be downloaded.
    pdf = base64.b64decode(body["pdf_base64"])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500
    # A stored artifact reference is exposed for later retrieval.
    assert body["artifact_name"].endswith(".pdf")


async def test_rpa_report_blocks_ssrf_targets(tenant_client: Any) -> None:
    """The report endpoint fetches the URL server-side, so internal/metadata
    targets must be rejected before any navigation (SSRF guard)."""
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/admin",
        "http://127.0.0.1/",
    ):
        resp = await tenant_client.post("/rpa/report", json={"url": url})
        assert resp.status_code == 400, f"expected 400 for {url}, got {resp.status_code}"
        assert "blocked url" in resp.text.lower()
