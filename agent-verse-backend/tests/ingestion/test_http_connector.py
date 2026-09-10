"""Generic HTTP/REST ingestion connector — cursor-based incremental delta.

Proves the universal-fallback connector: fetch any JSON endpoint, extract a
record list (flexible shapes / dot-path), yield RawDocuments with incremental
cursor advance, and fail closed on SSRF. httpx is patched (no real network); URLs
use a literal public IP so the SSRF guard passes without DNS.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connector_registry import get_connector, load_all_connectors
from app.ingestion.connectors.http_connector import (
    HttpApiConnector,
    _extract_records,
)
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-http",
        tenant_id="t1",
        name="http-src",
        family=SourceFamily.AGENT_GENERATED,
        source_type="http",
        connection_config=cc,
    )


def _patch_httpx(payload: Any, status: int = 200) -> Any:
    def _factory(*args: Any, **kwargs: Any) -> Any:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        resp = MagicMock()
        resp.json = MagicMock(return_value=payload)
        resp.raise_for_status = MagicMock(
            side_effect=(RuntimeError(f"HTTP {status}") if status >= 400 else None)
        )
        ctx.request = AsyncMock(return_value=resp)
        return ctx

    return patch("httpx.AsyncClient", _factory)


def test_registered_under_http_and_rest() -> None:
    load_all_connectors()  # trigger registration of all connector modules
    assert get_connector("http") is HttpApiConnector
    assert get_connector("rest") is HttpApiConnector


def test_extract_records_shapes() -> None:
    assert _extract_records([1, 2], "") == [1, 2]
    assert _extract_records({"results": [1]}, "") == [1]
    assert _extract_records({"data": {"items": [9]}}, "data.items") == [9]
    assert _extract_records({"nope": 1}, "") == []


@pytest.mark.asyncio
async def test_get_delta_yields_documents() -> None:
    payload = {"results": [
        {"id": "a", "title": "Alpha", "updated_at": "2026-01-01"},
        {"id": "b", "title": "Beta", "updated_at": "2026-01-02"},
    ]}
    conn = HttpApiConnector()
    cfg = _config(url="https://1.1.1.1/api", cursor_field="updated_at", id_field="id")
    with _patch_httpx(payload):
        docs = [(d.doc_id, c) async for d, c in conn.get_delta(cfg, None)]
    assert [d for d, _ in docs] == ["a", "b"]
    # Cursor advances to the latest updated_at.
    assert docs[-1][1] == "2026-01-02"


@pytest.mark.asyncio
async def test_get_delta_incremental_skips_old_records() -> None:
    payload = {"results": [
        {"id": "a", "updated_at": "2026-01-01"},  # <= cursor → skipped
        {"id": "b", "updated_at": "2026-01-05"},  # > cursor → kept
    ]}
    conn = HttpApiConnector()
    cfg = _config(url="https://1.1.1.1/api", cursor_field="updated_at")
    with _patch_httpx(payload):
        ids = [d.doc_id async for d, _ in conn.get_delta(cfg, "2026-01-03")]
    assert ids == ["b"]


@pytest.mark.asyncio
async def test_get_delta_content_fields_vs_full_json() -> None:
    payload = [{"id": "a", "body": "hello", "extra": "x"}]
    conn = HttpApiConnector()
    cfg = _config(url="https://1.1.1.1/api", content_fields=["body"])
    with _patch_httpx(payload):
        docs = [d async for d, _ in conn.get_delta(cfg, None)]
    assert docs[0].content == b"hello"
    assert docs[0].content_type == "text/plain"


@pytest.mark.asyncio
async def test_get_delta_blocks_internal_url() -> None:
    conn = HttpApiConnector()
    cfg = _config(url="http://169.254.169.254/latest/meta-data/")
    with _patch_httpx({"results": []}):
        with pytest.raises(Exception, match="SSRF"):
            _ = [d async for d, _ in conn.get_delta(cfg, None)]


@pytest.mark.asyncio
async def test_validate_connection_ok_and_blocked() -> None:
    conn = HttpApiConnector()
    with _patch_httpx({"results": [{"id": "a"}]}):
        health = await conn.validate_connection(_config(url="https://1.1.1.1/api"))
    assert health.ok is True
    assert health.metadata["records_sampled"] == 1

    blocked = await conn.validate_connection(_config(url="http://127.0.0.1/x"))
    assert blocked.ok is False
    assert "blocked" in (blocked.error or "")
