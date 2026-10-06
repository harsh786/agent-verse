"""OCR-FAIR: the process-wide OCR slots are shared fairly across tenants.

The page slots were one FIFO queue: per-document fairness kept one huge scan
from starving a second document, but a tenant OCR'ing many documents at once (a
ZIP of scans, a connector sync, several uploads) queued all their pages ahead
of another tenant's one-page upload. Slots are now handed out round-robin
across tenants, then across the documents of a tenant, FIFO within a document.
"""

from __future__ import annotations

import asyncio
import contextvars
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from app.ocr import concurrency as oc
from app.ocr.engine import OcrEngine
from app.providers.guarded_completion import tenant_charge_scope


@pytest.fixture(autouse=True)
def _pool() -> Any:
    oc.reset_ocr_concurrency(oc.OcrLimits(2, 2, 2, 300))
    yield
    oc.reset_ocr_concurrency()


async def _grant_order(sem: oc.ProcessSemaphore, waiters: list[tuple[str, str, str]]) -> list[str]:
    """Queue ``(label, tenant, document)`` waiters behind a held slot; return the
    order in which the slot is handed to them."""
    order: list[str] = []

    async def _take(label: str, tenant: str, document: str) -> None:
        await sem.acquire(tenant=tenant, document=document)
        order.append(label)
        await asyncio.sleep(0)
        sem.release()

    await sem.acquire()
    tasks = []
    for label, tenant, document in waiters:
        tasks.append(asyncio.create_task(_take(label, tenant, document)))
        await asyncio.sleep(0)  # queue in this order
    sem.release()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert sem.in_use == 0 and sem.waiting == 0
    return order


async def test_a_tenant_with_a_long_queue_does_not_starve_another_tenant() -> None:
    waiters = [(f"A{i}", "tenant-a", f"doc-a{i % 3}") for i in range(9)]
    waiters.append(("B0", "tenant-b", "doc-b"))
    order = await _grant_order(oc.ProcessSemaphore(1), waiters)
    # FIFO handed B the slot only after all nine of A's pages.
    assert order.index("B0") <= 1


async def test_tenants_are_served_round_robin() -> None:
    waiters = [(f"A{i}", "a", "d") for i in range(3)] + [(f"B{i}", "b", "d") for i in range(3)]
    waiters += [("C0", "c", "d")]
    order = await _grant_order(oc.ProcessSemaphore(1), waiters)
    assert order == ["A0", "B0", "C0", "A1", "B1", "A2", "B2"]


async def test_documents_of_one_tenant_are_served_round_robin_fifo_within_each() -> None:
    waiters = [("x1", "t", "x"), ("x2", "t", "x"), ("x3", "t", "x"), ("y1", "t", "y")]
    order = await _grant_order(oc.ProcessSemaphore(1), waiters)
    assert order == ["x1", "y1", "x2", "x3"]


async def test_callers_without_keys_keep_plain_fifo_order() -> None:
    order = await _grant_order(oc.ProcessSemaphore(1), [(str(i), "", "") for i in range(5)])
    assert order == ["0", "1", "2", "3", "4"]


async def test_a_cancelled_fair_waiter_never_leaks_a_slot() -> None:
    sem = oc.ProcessSemaphore(1)
    await sem.acquire()
    gone = asyncio.create_task(sem.acquire(tenant="a", document="d"))
    stays = asyncio.create_task(sem.acquire(tenant="b", document="d"))
    await asyncio.sleep(0.01)
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    sem.release()
    await asyncio.wait_for(stays, 1)
    sem.release()
    assert sem.in_use == 0 and sem.waiting == 0


def test_the_ocr_tenant_comes_from_the_charge_scope_or_an_explicit_scope() -> None:
    assert oc.current_ocr_tenant() == ""
    with tenant_charge_scope(SimpleNamespace(tenant_id="t-req")):
        assert oc.current_ocr_tenant() == "t-req"
        with oc.ocr_tenant_scope("t-explicit"):
            assert oc.current_ocr_tenant() == "t-explicit"


def test_document_scope_is_reentrant() -> None:
    with oc.ocr_document_scope() as outer:
        with oc.ocr_document_scope() as inner:
            assert inner == outer
    with oc.ocr_document_scope() as other:
        assert other != outer


async def test_one_tenants_many_documents_do_not_starve_another_tenants_upload() -> None:
    """Two slots. Tenant A OCRs six 3-page scans at once; tenant B then uploads a
    one-page scan. B's page gets one of the next free slots instead of waiting
    behind every page A has queued."""
    started: list[str] = []
    label: contextvars.ContextVar[str] = contextvars.ContextVar("label", default="")

    def _render(_path: Any, n: int, *, dpi: int) -> Any:
        return Image.new("L", (100 + n, 40), 255)

    async def _ocr_page(engine: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        started.append(label.get())
        await asyncio.sleep(0.02)
        return "text", 0.9, "tesseract"

    def _page_count(path: Any) -> int:
        return int(Path(path).read_bytes().rsplit(b"-", 1)[1])

    async def _doc(tenant: str, pages: int) -> None:
        label.set(tenant)
        with tenant_charge_scope(SimpleNamespace(tenant_id=tenant)):
            result = await OcrEngine().extract(
                pdf_bytes=f"%PDF-{tenant}-{pages}".encode(), extract_fields=False
            )
        assert result.page_count == pages

    with (
        patch("app.ocr.engine.pdf_page_count", _page_count),
        patch("app.ocr.engine.render_pdf_page_image", _render),
        patch.object(OcrEngine, "_ocr_page", _ocr_page),
    ):
        big = [asyncio.create_task(_doc("tenant-a", 3)) for _ in range(6)]
        await asyncio.sleep(0.005)  # A holds both slots and has queued its pages
        await _doc("tenant-b", 1)
        a_before_b = started.index("tenant-b")
        await asyncio.gather(*big)
    assert started.count("tenant-a") == 18
    # Two slots already held by A when B arrived, at most one more A page before B
    # (FIFO started every queued A page first: 12 of them).
    assert a_before_b <= 3
