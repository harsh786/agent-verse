"""ZIP members of one upload are extracted / OCR'd concurrently (OCR-PAR-4).

A ZIP of scans used to be processed member after member, and every member's OCR
also held one of the replica's two upload *parse* slots (KNOWLEDGE_UPLOAD_PARSE_
CONCURRENCY), so at most two OCR jobs ran per replica however many CPUs the OCR
pool had. Members now run OCR_PAGE_CONCURRENCY at a time (OCR itself is bounded
by the process-wide OCR pool), only that many inflated members are held at once,
and the members keep their archive order in the response, the chunks and the
skipped list.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any
from unittest.mock import patch

import pytest

from app.ocr import concurrency as oc
from tests.api.test_knowledge_upload_archive import _chunks, _client, _upload


@pytest.fixture(autouse=True)
def _pool() -> Any:
    oc.reset_ocr_concurrency(oc.OcrLimits(4, 3, 2, 300))
    yield
    oc.reset_ocr_concurrency()


def _zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class _OcrSpy:
    def __init__(self, *, delays: dict[str, float] | None = None,
                 empty: frozenset[str] = frozenset(), refuse: str | None = None) -> None:
        self.delays = delays or {}
        self.empty = empty
        self.refuse = refuse
        self.in_flight = 0
        self.peak = 0
        self.calls: list[str] = []

    async def __call__(self, data: bytes, *, filename: str, **_: Any) -> tuple[str, str]:
        from app.ingestion.document_text import DocumentParseError

        self.calls.append(filename)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delays.get(filename, 0.05))
            if filename == self.refuse:
                from app.providers.guarded_completion import DecisionBudgetExceededError

                raise DecisionBudgetExceededError("ocr budget exhausted")
            if filename in self.empty:
                raise DocumentParseError(f"{filename}: no text could be extracted from the image")
            return f"Scanned berth permit text from {filename}.", "tesseract"
        finally:
            self.in_flight -= 1


def _scans(n: int) -> dict[str, bytes | str]:
    return {f"scans/permit-{i:02d}.png": b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64
            for i in range(1, n + 1)}


def test_image_members_are_ocrd_concurrently_and_keep_archive_order() -> None:
    files = _scans(7)
    # Early members finish last: completion order is the reverse of archive order.
    spy = _OcrSpy(delays={name: 0.02 * (8 - i) for i, name in enumerate(files, start=1)})
    app, client, cid = _client()
    with patch("app.ingestion.document_text.extract_image_text", spy):
        r = _upload(client, cid, _zip(files))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["archive"]["members_indexed"] == list(files)
    assert spy.peak == 3  # OCR_PAGE_CONCURRENCY members at once, more than the 2 parse slots
    texts = {c.metadata["archive_member"]: c.content for c in _chunks(app)}
    for name in files:
        assert f"from {name}" in texts[name]  # each member keeps its own text


def test_skipped_members_keep_archive_order_with_their_reasons() -> None:
    files = _scans(5)
    names = list(files)
    files["notes/readme.txt"] = "Berth 4 is closed for dredging until March."
    spy = _OcrSpy(empty=frozenset({names[1], names[3]}),
                  delays={names[1]: 0.15, names[3]: 0.01})
    _app, client, cid = _client()
    with patch("app.ingestion.document_text.extract_image_text", spy):
        r = _upload(client, cid, _zip(files))
    assert r.status_code == 201, r.text
    archive = r.json()["archive"]
    assert archive["members_indexed"] == [names[0], names[2], names[4], "notes/readme.txt"]
    assert [s["name"] for s in archive["members_skipped"]] == [names[1], names[3]]
    assert all("no text" in s["reason"] for s in archive["members_skipped"])


def test_a_budget_refusal_stops_the_archive_instead_of_ocring_every_member() -> None:
    files = _scans(20)
    first = next(iter(files))
    spy = _OcrSpy(refuse=first, delays={first: 0.01})
    app, client, cid = _client()
    with patch("app.ingestion.document_text.extract_image_text", spy):
        r = _upload(client, cid, _zip(files))
    assert r.status_code != 201
    assert len(spy.calls) < 20  # no further OCR spend once refused
    assert _chunks(app) == []


async def test_connector_archive_members_run_concurrently_in_order() -> None:
    """The connector / pipeline archive parser (parser_registry) as well."""
    from app.ingestion.parser_registry import ParserRegistry

    files = _scans(6)
    spy_peak = 0
    in_flight = 0

    async def _parse(self: Any, content: bytes, ct: Any, *, filename: str = "", **_: Any) -> Any:
        nonlocal spy_peak, in_flight
        in_flight += 1
        spy_peak = max(spy_peak, in_flight)
        await asyncio.sleep(0.01 * (7 - int(filename[-6:-4])))
        in_flight -= 1
        return f"text of {filename}", {}

    meta: dict[str, object] = {}
    with patch.object(ParserRegistry, "parse_bytes_async", _parse):
        text = await ParserRegistry()._parse_archive(_zip(files), "bundle.zip", meta, None, None)
    sections = text.split("\n\n")
    assert [s.split("\n", 1)[0] for s in sections] == [f"bundle.zip/{n}" for n in files]
    assert spy_peak == 3
    assert meta["archive_members_indexed"] == 6
