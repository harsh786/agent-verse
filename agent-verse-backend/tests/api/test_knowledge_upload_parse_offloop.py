"""KB-07: parsing an upload never blocks the API event loop.

``_extract_upload_segments`` parsed up to 50 MiB PDFs / big workbooks
synchronously on the event loop, freezing the replica for every tenant.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import knowledge as knowledge_api
from tests.api.test_knowledge_extra4 import H, _create_collection, _make_app


def _slow_parse(content_bytes: bytes, *, ext: str, filename: str, **_: Any) -> Any:
    time.sleep(0.5)
    return [(None, "Parsed text about the retention policy for customer records.")], None


async def test_other_work_proceeds_while_a_slow_parse_runs() -> None:
    finished: list[str] = []

    async def parse() -> None:
        await knowledge_api._extract_upload_segments_async(b"x", ext="pdf", filename="big.pdf")
        finished.append("parse")

    async def other_request() -> None:
        await asyncio.sleep(0.05)
        finished.append("other")

    with patch.object(knowledge_api, "_extract_upload_segments", _slow_parse):
        await asyncio.gather(parse(), other_request())

    assert finished == ["other", "parse"]


async def test_parse_runs_in_a_worker_thread_and_is_bounded() -> None:
    threads: list[int] = []
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def _tracking(content_bytes: bytes, *, ext: str, filename: str, **_: Any) -> Any:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        threads.append(threading.get_ident())
        time.sleep(0.1)
        with lock:
            in_flight -= 1
        return [(None, "text")], None

    with patch.object(knowledge_api, "_extract_upload_segments", _tracking):
        await asyncio.gather(
            *[
                knowledge_api._extract_upload_segments_async(b"x", ext="txt", filename="a")
                for _ in range(8)
            ]
        )

    assert threading.get_ident() not in threads
    assert peak <= knowledge_api._UPLOAD_PARSE_CONCURRENCY


def test_the_upload_route_parses_off_the_loop() -> None:
    calls: list[str] = []

    async def _spy(content_bytes: bytes, *, ext: str, filename: str, **_: Any) -> Any:
        calls.append(filename)
        return [(None, "Parsed text about the retention policy for customer records.")], None

    from tests.api.test_knowledge_upload_embedding_limits import _CountingEmbedder

    client = TestClient(_make_app(embedder=_CountingEmbedder()), raise_server_exceptions=False)
    cid = _create_collection(client)
    with patch.object(knowledge_api, "_extract_upload_segments_async", _spy):
        resp = client.post(
            "/knowledge/ingest/file",
            files={"file": ("notes.txt", io.BytesIO(b"hello " * 50), "text/plain")},
            data={"collection_id": cid},
            headers=H,
        )
    assert resp.status_code == 201, resp.text
    assert calls == ["notes.txt"]
