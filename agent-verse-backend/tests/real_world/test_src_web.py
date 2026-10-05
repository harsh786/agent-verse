"""WEB-URL-* / WEB-CRAWL-*: HTTP URL ingestion and web crawl on the live stack (P1d / A10).

The stack fetches a programmable site (``web_site.py``: the fixture server in a
container on the compose network, ``rw-web`` / ``rw-web-b`` operator-allowlisted for
the ingestion egress guard). Each scenario registers realistic pages under its own
prefix, drives ``POST /knowledge/ingest/url`` or a ``web_crawl`` Source over HTTP, and
asserts the outcome from both sides: what was indexed (chunks, counts, citations,
answers) and what the site saw (requests, hosts, timing between requests).

Environment: the web fixture (``RW_WEB_CONTROL_URL``, default 127.0.0.1:58080;
SKIPPED without it), ``RW_PG_CONTAINER`` / ``RW_REDIS_CONTAINER`` (WEB-URL-REINGEST-HOLD
releases its hold there: the API has no release route).
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, body_of, mask, wait_until
from tests.real_world.metrics import answer_correct, norm, record
from tests.real_world.web_site import (
    CHROME,
    WebSite,
    docx_bytes,
    other_host,
    page,
    pdf_bytes,
)

FAMILY = "web"


@pytest.fixture
def site() -> Iterator[WebSite]:
    s = WebSite.open()
    yield s
    s.close()


def _ingest(api: LiveAPI, cid: str, url: str, timeout: float = 180
            ) -> tuple[int, dict[str, Any], float]:
    started = time.monotonic()
    resp = api.post("/knowledge/ingest/url", timeout=timeout,
                    json={"collection_id": cid, "url": url, "source_type": "web"})
    body = body_of(resp)
    return resp.status_code, body if isinstance(body, dict) else {"raw": str(body)[:300]}, \
        round(time.monotonic() - started, 2)


def _cited(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    return str(hit.get("source_url") or meta.get("source_url") or hit.get("source")
               or meta.get("source") or "")


def _search(api: LiveAPI, cid: str, q: str, k: int = 10,
            filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": q, "collection_id": cid, "top_k": k}
    if filters:
        params["filters"] = json.dumps(filters)
    body = api.json_ok("GET", "/knowledge/search", params=params)
    return list(body if isinstance(body, list) else body.get("results", []))


def _doc_text(api: LiveAPI, cid: str, document_id: str, queries: list[str]) -> str:
    """Every stored chunk of one document reachable by the given queries."""
    seen: dict[str, str] = {}
    for q in queries:
        for h in _search(api, cid, q[:300], k=20, filters={"document_id": document_id}):
            seen[str(h.get("chunk_id"))] = str(h.get("content") or "")
    return "\n".join(seen.values())


def _rank(api: LiveAPI, cid: str, q: str, needle: str, url: str | None = None,
          k: int = 10) -> int | None:
    for i, h in enumerate(_search(api, cid, q, k=k)):
        if norm(needle) in norm(h.get("content")) and (url is None or _cited(h) == url):
            return i + 1
    return None


def _ask(api: LiveAPI, cid: str, q: str, answer_any: list[str], url: str,
         row: dict[str, Any]) -> list[str]:
    """/rag/query: the answer states the fact and cites ``url`` (retried on 429/503)."""
    status, body, ms = kb.rag_query(api, cid, q, top_k=5)
    retries = 0
    while status in (429, 503) and retries < 2:
        retries += 1
        time.sleep(20)
        status, body, ms = kb.rag_query(api, cid, q, top_k=5)
    row.update(rag_http=status, rag_ms=round(ms), rag_retries=retries)
    if status != 200:
        return [f"{q!r}: /rag/query -> {status} {mask(body)[:160]}"]
    answer = kb.answer_text(body)
    cites = sorted({_cited(c) for c in body.get("citations") or []})
    row.update(answer=answer[:200], cited=cites[:5])
    soft = []
    if not answer_correct(answer, {"answer_any": answer_any}):
        soft.append(f"{q!r}: wrong answer {answer[:140]!r}")
    if url not in cites:
        soft.append(f"{q!r}: does not cite {url} ({cites[:4]})")
    return soft


def _count(api: LiveAPI, cid: str) -> int:
    return int(kb.documents_page(api, cid, 1, 0).get("total") or 0)


# ════════════════════════════════════════════════════════════════════════════
#  HTTP URL ingestion
# ════════════════════════════════════════════════════════════════════════════


REEFER = ("A reefer plug-in at the Hosur yard costs 2,450 INR per container per day.",
          "Plug-in requests close 48 hours before the vessel's estimated arrival.",
          "Pre-trip inspections are done by technician Revathi Subramaniam on bay R7.")


@pytest.mark.scenario("WEB-URL-BOILERPLATE")
def test_url_html_boilerplate(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                              site: WebSite) -> None:
    """A help-center page wrapped in navigation, a cookie banner, a sidebar, a footer,
    inline CSS and JS: only the article is indexed, and it answers with its URL."""
    url = site.url("help/reefer-plug-in")
    site.put("help/reefer-plug-in", page(
        "Reefer plug-in charges at Hosur", *REEFER,
        links=(("/help/gate-hours", "Gate hours"), ("/help/dg-cargo", "Dangerous goods"))))
    cid = srcs.create_collection(api, cleanup, "rw-web-url")
    status, body, secs = _ingest(api, cid, url)
    evidence.update(collection_id=cid, http=status, secs=secs,
                    response={k: body.get(k) for k in ("document_id", "chunks_ingested",
                                                       "content_kind", "title", "detail")})
    assert status == 201, f"ingest/url -> {status}: {mask(body)[:300]}"
    text = _doc_text(api, cid, str(body["document_id"]),
                     ["reefer plug-in Hosur", *REEFER, *CHROME])
    soft = [f"not indexed: {f!r}" for f in REEFER if norm(f) not in norm(text)]
    soft += [f"page chrome indexed: {c!r}" for c in CHROME if norm(c) in norm(text)]
    row: dict[str, Any] = {"rank": _rank(api, cid, "How much is a reefer plug-in at Hosur?",
                                         "2,450 INR", url)}
    if row["rank"] is None:
        soft.append("the plug-in charge chunk is not in the top 10 with the page URL")
    soft += _ask(api, cid, "How much does a reefer plug-in cost per day at the Hosur yard?",
                 ["2,450", "2450"], url, row)
    evidence["question"] = row
    record(evidence, ingest_s=secs, chunks=int(body.get("chunks_ingested") or 0))
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WEB-URL-FORMATS")
def test_url_non_html_documents(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                site: WebSite) -> None:
    """PDF (served as octet-stream from a download link), DOCX, plain text and Markdown
    by URL: each is extracted like an upload of the file, never indexed as bytes."""
    tariff = pdf_bytes([
        ["Kestrel Logistics tariff schedule 2026", "Section 1: storage",
         "Dry container storage after free time: 640 INR per TEU per day."],
        ["Section 2: penalties", "Late gate-in after cut-off: 9,800 INR per box.",
         "Reefer monitoring outside office hours: 1,150 INR per shift."],
    ])
    policy = docx_bytes("Seal verification policy", [
        "Every inbound box is seal-checked by two people at gate 3.",
        "A seal mismatch is escalated to the duty manager Arvind Kulkarni within 15 minutes."],
        table=[["Gate", "Scanner"], ["Gate 3", "SC-9 optical"], ["Gate 5", "SC-11 RFID"]])
    cases = {
        "pdf": ("downloads/file?id=tariff-2026", tariff, "application/octet-stream",
                "Late gate-in after cut-off: 9,800 INR per box.", "page 2"),
        "docx": ("policies/seal-verification.docx", policy,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "Arvind Kulkarni", None),
        "txt": ("notes/changelog.txt",
                "Release 4.2 of the gate app records the seal number photo for every box.\n"
                "Release 4.3 adds offline mode for gate 5.\n", "text/plain; charset=utf-8",
                "Release 4.3 adds offline mode for gate 5.", None),
        "md": ("runbooks/yard-crane.md",
               "# Yard crane runbook\n\n## Wind stop\n\nRTG cranes stop at a sustained wind "
               "speed of 54 km/h; resume below 45 km/h for 10 minutes.\n",
               "text/markdown", "sustained wind speed of 54 km/h", None),
    }
    cid = srcs.create_collection(api, cleanup, "rw-web-formats")
    soft: list[str] = []
    rows: dict[str, Any] = {}
    for kind, (rel, data, ctype, fact, where) in cases.items():
        site.put(rel, data, content_type=ctype)
        url = site.url(rel)
        status, body, secs = _ingest(api, cid, url)
        row: dict[str, Any] = {"http": status, "secs": secs, "kind": body.get("content_kind"),
                               "pages": body.get("pages"), "chunks": body.get("chunks_ingested")}
        rows[kind] = row
        if status != 201:
            soft.append(f"{kind}: ingest/url -> {status}: {mask(body)[:200]}")
            continue
        if body.get("content_kind") != kind:
            soft.append(f"{kind}: read as {body.get('content_kind')!r}")
        text = _doc_text(api, cid, str(body["document_id"]), [fact])
        if norm(fact) not in norm(text):
            soft.append(f"{kind}: fact not indexed: {fact!r}")
        if "%PDF" in text or "word/document.xml" in text or "\x00" in text:
            soft.append(f"{kind}: raw bytes indexed")
        hits = _search(api, cid, fact, k=5)
        top = next((h for h in hits if norm(fact) in norm(h.get("content"))), None)
        row["cited"] = _cited(top) if top else None
        if top is None or _cited(top) != url:
            soft.append(f"{kind}: fact not retrievable with its URL ({row['cited']})")
        if where == "page 2" and top is not None:
            page_no = str((top.get("metadata") or {}).get("page", top.get("page")))
            row["page"] = page_no
            if page_no != "2":
                soft.append(f"pdf: fact cited on page {page_no}, it is on page 2")
    qrow: dict[str, Any] = {}
    soft += _ask(api, cid, "What is the penalty for a late gate-in after the cut-off?",
                 ["9,800", "9800"], site.url(cases["pdf"][0]), qrow)
    evidence.update(collection_id=cid, cases=rows, question=qrow)
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WEB-URL-REDIRECTS")
def test_url_redirects(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                       site: WebSite) -> None:
    """Temporary and permanent redirects are followed (the move is reported); a
    redirect to an internal address and a redirect loop are refused, nothing indexed."""
    site.put("v2/gate-hours", page("Gate hours", "Gate 3 is open from 06:00 to 23:30 daily."))
    site.redirect("gate-hours", site.url("v2/gate-hours"), 301)
    site.redirect("gate-hours-308", site.url("v2/gate-hours"), 308)
    site.redirect("tmp/gate-hours", "/" + site.path("v2/gate-hours").lstrip("/"), 302)
    internal = {"metadata": "http://169.254.169.254/latest/meta-data/iam/",
                "postgres": "http://postgres:5432/", "redis": "http://redis:6379/",
                "backend": "http://backend:8000/health", "localhost": "http://localhost:8000/"}
    for name, target in internal.items():
        site.redirect(f"evil/{name}", target, 302)
    site.redirect("loop/a", site.url("loop/b"), 302)
    site.redirect("loop/b", site.url("loop/a"), 302)
    cid = srcs.create_collection(api, cleanup, "rw-web-redirects")
    soft: list[str] = []
    rows: dict[str, Any] = {}
    for rel, code in (("gate-hours", 301), ("gate-hours-308", 308), ("tmp/gate-hours", 302)):
        status, body, secs = _ingest(api, cid, site.url(rel))
        moved = body.get("moved_permanently")
        rows[rel] = {"http": status, "final_url": body.get("final_url"), "moved": moved,
                     "secs": secs}
        if status not in (200, 201):
            soft.append(f"{rel}: {status} {mask(body)[:160]}")
            continue
        if body.get("final_url") != site.url("v2/gate-hours"):
            soft.append(f"{rel}: final_url {body.get('final_url')!r}")
        if code in (301, 308) and not (moved and moved.get("to") == site.url("v2/gate-hours")
                                       and moved.get("status") == code):
            soft.append(f"{rel}: permanent move not reported ({moved})")
        if code == 302 and moved:
            soft.append(f"{rel}: a temporary redirect reported as a move ({moved})")
    before = _count(api, cid)
    for name in internal:
        status, body, secs = _ingest(api, cid, site.url(f"evil/{name}"))
        rows[f"evil/{name}"] = {"http": status, "secs": secs,
                                "detail": mask(body.get("detail"))[:160]}
        if status != 400 or "blocked" not in str(body.get("detail", "")).lower():
            soft.append(f"redirect to {name}: {status} {mask(body)[:160]} (expected 400 blocked)")
    status, body, secs = _ingest(api, cid, site.url("loop/a"))
    rows["loop"] = {"http": status, "secs": secs, "detail": mask(body.get("detail"))[:160]}
    if status != 422:
        soft.append(f"redirect loop: {status} {mask(body)[:160]} (expected 422)")
    if _count(api, cid) != before:
        soft.append("a refused URL indexed something")
    evidence.update(collection_id=cid, results=rows)
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WEB-URL-FAILURES")
def test_url_honest_failures(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                             site: WebSite) -> None:
    """404 / 410 / 500 / 503, a page that never answers and a closed port: each is an
    honest error naming the cause, in bounded time, with nothing indexed."""
    site.put("missing", "Not here", status=404, content_type="text/plain")
    site.put("gone", "Removed", status=410, content_type="text/plain")
    site.put("crash", "Internal error", status=500, content_type="text/plain")
    site.put("busy", "Busy", status=503, content_type="text/plain")
    site.put("slow", page("Slow", "Never arrives in time."), delay_s=45)
    cid = srcs.create_collection(api, cleanup, "rw-web-failures")
    cases = {"missing": (502, "HTTP 404"), "gone": (502, "HTTP 410"), "crash": (502, "HTTP 500"),
             "busy": (502, "HTTP 503"), "slow": (504, "timed out")}
    soft: list[str] = []
    rows: dict[str, Any] = {}
    for rel, (want, needle) in cases.items():
        status, body, secs = _ingest(api, cid, site.url(rel))
        rows[rel] = {"http": status, "secs": secs, "detail": mask(body.get("detail"))[:160]}
        if status != want or needle.lower() not in str(body.get("detail", "")).lower():
            soft.append(f"{rel}: {status} {mask(body)[:160]} (expected {want} {needle!r})")
        if secs > 40:
            soft.append(f"{rel}: took {secs} s")
    status, body, secs = _ingest(api, cid, "http://rw-web:9/closed-port")
    rows["closed-port"] = {"http": status, "secs": secs, "detail": mask(body.get("detail"))[:160]}
    if status != 502:
        soft.append(f"closed port: {status} {mask(body)[:160]} (expected 502)")
    if _count(api, cid):
        soft.append("a failed URL indexed something")
    evidence.update(collection_id=cid, results=rows)
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WEB-URL-LARGE")
def test_url_large_pages_are_bounded(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                     site: WebSite) -> None:
    """A 12 MiB HTML page and a 60 MB download are refused (413) quickly, without the
    stack reading them whole; a 1.5 MB page that is mostly markup is indexed."""
    chunk = "<div class='row'><span>filler cell</span></div>\n"
    site.put("huge.html", chunk.encode() * (12 * 1024 * 1024 // len(chunk) + 1))
    site.put("dump.bin", b"\x00" * (1024 * 1024), content_type="application/octet-stream",
             repeat=60)
    article = " ".join(f"Berth {i} handles feeder vessels up to {150 + i} m LOA."
                       for i in range(40))
    heavy = page("Berth register", article, "The longest berth is Berth 39 at 189 m LOA.")
    padding = "<div class='ad-slot'>" + ("<span class='px'></span>" * 30000) + "</div>"
    site.put("berths", heavy.replace("</body>", padding + "</body>"))
    cid = srcs.create_collection(api, cleanup, "rw-web-large")
    soft: list[str] = []
    rows: dict[str, Any] = {}
    for rel in ("huge.html", "dump.bin"):
        status, body, secs = _ingest(api, cid, site.url(rel))
        rows[rel] = {"http": status, "secs": secs, "detail": mask(body.get("detail"))[:160]}
        if status != 413:
            soft.append(f"{rel}: {status} {mask(body)[:160]} (expected 413)")
        if secs > 30:
            soft.append(f"{rel}: took {secs} s")
    status, body, secs = _ingest(api, cid, site.url("berths"))
    rows["berths"] = {"http": status, "secs": secs, "chunks": body.get("chunks_ingested")}
    if status != 201:
        soft.append(f"berths: {status} {mask(body)[:160]}")
    elif _rank(api, cid, "Which is the longest berth?", "Berth 39 at 189 m") is None:
        soft.append("the berth fact is not retrievable")
    health = api.get("/health/ready")
    rows["ready_after"] = health.status_code
    if health.status_code != 200:
        soft.append(f"/health/ready {health.status_code} after the large pages")
    evidence.update(collection_id=cid, results=rows)
    assert not soft, "; ".join(soft)


def _docker(container_env: str, default: str, *cmd: str) -> str:
    container = os.getenv(container_env, default)
    out = subprocess.run(["docker", "exec", container, *cmd], capture_output=True, text=True,
                         timeout=60, check=True)
    return out.stdout.strip()


def _release_hold(hold_id: str, tenant_id: str) -> None:
    _docker("RW_PG_CONTAINER", "agentverse-backend-postgres-1", "psql", "-U", "agentverse",
            "-d", "agentverse", "-Atc",
            f"UPDATE legal_holds SET status='released', released_at=now() "
            f"WHERE id = '{hold_id}'")
    _docker("RW_REDIS_CONTAINER", "agentverse-backend-redis-1", "redis-cli", "DEL",
            f"legal_hold:{tenant_id}")


@pytest.mark.scenario("WEB-URL-REINGEST-HOLD")
def test_url_reingest_replaces_and_honours_legal_hold(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any], site: WebSite,
        tenant_id: str) -> None:
    """Re-ingesting a URL replaces its document (one document, the new text served,
    the old not); an unchanged page is a no-op; a document under legal hold is not
    replaced — neither by ingest/url nor by the reingest endpoint — until released."""
    rel = "help/free-time"
    url = site.url(rel)
    v1, v2, v3 = ("Import containers get 4 days of free time at the Hosur yard.",
                  "Import containers get 6 days of free time at the Hosur yard from November.",
                  "Import containers get 7 days of free time at the Hosur yard from January.")
    site.put(rel, page("Free time", v1))
    cid = srcs.create_collection(api, cleanup, "rw-web-reingest")
    soft: list[str] = []
    s1, b1, _ = _ingest(api, cid, url)
    assert s1 == 201, f"first ingest -> {s1}: {mask(b1)[:200]}"
    doc_id = str(b1["document_id"])
    site.put(rel, page("Free time", v2))
    s2, b2, _ = _ingest(api, cid, url)
    evidence["replace"] = {"http": s2, "document_id": b2.get("document_id"),
                           "replaced": b2.get("replaced")}
    if s2 != 201 or b2.get("document_id") != doc_id or not b2.get("replaced"):
        soft.append(f"re-ingest did not replace the document: {s2} {mask(b2)[:200]}")
    text = _doc_text(api, cid, doc_id, ["free time Hosur", v1, v2])
    if norm("6 days") not in norm(text) or norm("4 days") in norm(text):
        soft.append("after re-ingest the old text is served or the new one is missing")
    if _count(api, cid) != 1:
        soft.append(f"{_count(api, cid)} documents for one URL")
    s3, b3, _ = _ingest(api, cid, url)
    if not b3.get("deduplicated") or b3.get("document_id") != doc_id:
        soft.append(f"unchanged re-ingest: {s3} {mask(b3)[:200]}")

    hold = api.post("/governance/legal-hold", json={
        "reason": "P1d WEB-URL-REINGEST-HOLD", "name": f"rw-hold-{site.key}",
        "resource_type": "document", "resource_ids": [doc_id]})
    assert hold.status_code == 200, f"legal hold -> {hold.status_code}: {mask(hold.text)[:200]}"
    hold_id = str(hold.json()["id"])
    try:
        site.put(rel, page("Free time", v3))
        s4, b4, _ = _ingest(api, cid, url)
        re4 = api.post(f"/knowledge/collections/{cid}/documents/{doc_id}/reingest", timeout=180)
        evidence["held"] = {"ingest_http": s4, "ingest_detail": mask(b4.get("detail"))[:160],
                            "reingest_http": re4.status_code,
                            "reingest_detail": mask(body_of(re4))[:160]}
        if s4 != 409 or "legal hold" not in str(b4.get("detail", "")).lower():
            soft.append(f"ingest/url replaced a held document: {s4} {mask(b4)[:200]}")
        if re4.status_code != 409:
            soft.append(f"reingest replaced a held document: {re4.status_code}")
        text = _doc_text(api, cid, doc_id, ["free time Hosur", v2, v3])
        if norm("6 days") not in norm(text) or norm("7 days") in norm(text):
            soft.append("the held version was changed")
    finally:
        _release_hold(hold_id, tenant_id)
    s5, b5, _ = _ingest(api, cid, url)
    evidence["released"] = {"http": s5, "replaced": b5.get("replaced")}
    text = _doc_text(api, cid, doc_id, ["free time Hosur", v2, v3])
    if s5 != 201 or norm("7 days") not in norm(text):
        soft.append(f"after release the re-ingest did not replace: {s5} {mask(b5)[:200]}")
    assert not soft, "; ".join(soft)


CHARSETS = [
    # (case, encoding, Content-Type header, extra <head>, sentence, query, lang)
    ("de-cp1252-meta", "cp1252", "text/html", '<meta charset="windows-1252">',
     "Die Abholung in München kostet 38 € pro Palette – Gebühren für Übergröße extra.",
     "Wie viel kostet die Abholung in München pro Palette?", "de"),
    ("ja-sjis-header", "shift_jis", "text/html; charset=Shift_JIS", "",
     "東京都江東区の青海コンテナ埠頭は午前八時から午後五時まで搬入を受け付けます。",
     "青海コンテナ埠頭の搬入受付時間", "ja"),
    ("ru-koi8-httpequiv", "koi8_r", "text/html",
     '<meta http-equiv="Content-Type" content="text/html; charset=KOI8-R">',
     "Склад в Новосибирске принимает контейнеры с восьми утра до восьми вечера.",
     "Когда склад в Новосибирске принимает контейнеры?", "ru"),
    ("hi-utf8-undeclared", "utf-8", "text/html", "",
     "चेन्नई बंदरगाह पर रीफर कंटेनर के लिए प्रतिदिन 2,100 रुपये शुल्क लगता है।",
     "चेन्नई बंदरगाह रीफर कंटेनर शुल्क", "hi"),
    ("ar-cp1256-meta", "cp1256", "text/html", '<meta charset="windows-1256">',
     "يفتح مستودع جبل علي من الساعة السابعة صباحاً حتى العاشرة مساءً.",
     "متى يفتح مستودع جبل علي؟", "ar"),
    ("el-xml-decl", "iso-8859-7", "application/xhtml+xml", "",
     "Η αποθήκη του Πειραιά δέχεται εμπορευματοκιβώτια έως τις οκτώ το βράδυ.",
     "Πότε δέχεται εμπορευματοκιβώτια η αποθήκη του Πειραιά;", "el"),
]


@pytest.mark.scenario("WEB-URL-CHARSET")
def test_url_charsets_and_languages(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                    site: WebSite) -> None:
    """Pages in windows-1252 / Shift_JIS / KOI8-R / windows-1256 / ISO-8859-7 declared
    by meta, header, http-equiv or XML declaration, and undeclared UTF-8 Hindi: the
    exact sentence is indexed and found by a query in the same language."""
    cid = srcs.create_collection(api, cleanup, "rw-web-charset")
    soft: list[str] = []
    rows: dict[str, Any] = {}
    for case, enc, ctype, head, sentence, query, lang in CHARSETS:
        html = page(f"Lager {case}", sentence, chrome=False, head=head, lang=lang)
        if case == "el-xml-decl":
            html = f'<?xml version="1.0" encoding="ISO-8859-7"?>\n{html}'
        site.put(f"intl/{case}", html.encode(enc), content_type=ctype)
        url = site.url(f"intl/{case}")
        status, body, secs = _ingest(api, cid, url)
        row: dict[str, Any] = {"http": status, "secs": secs}
        rows[case] = row
        if status != 201:
            soft.append(f"{case}: {status} {mask(body)[:160]}")
            continue
        text = _doc_text(api, cid, str(body["document_id"]), [sentence, query])
        row["indexed"] = sentence in text
        if sentence not in text:
            soft.append(f"{case}: sentence not indexed verbatim ({text[:80]!r})")
        row["rank"] = _rank(api, cid, query, sentence[:20], url)
        if row["rank"] is None:
            soft.append(f"{case}: not found by a {lang} query")
    qrow: dict[str, Any] = {}
    soft += _ask(api, cid, "What does a reefer container cost per day at Chennai port?",
                 ["2,100", "2100"], site.url("intl/hi-utf8-undeclared"), qrow)
    evidence.update(collection_id=cid, cases=rows, question=qrow)
    assert not soft, "; ".join(soft)


# ════════════════════════════════════════════════════════════════════════════
#  Web crawl (web_crawl Source)
# ════════════════════════════════════════════════════════════════════════════


ARTICLES = {
    "articles/gate-hours": ("Gate hours", "Gate 3 opens at 06:00 and closes at 23:30 daily."),
    "articles/dg-cargo": ("Dangerous goods",
                          "Class 1 explosives are never accepted at the Hosur yard."),
    "articles/reefer": ("Reefer monitoring",
                        "Reefer temperatures are logged every 15 minutes by the RMS-4 system."),
    "articles/customs": ("Customs holds",
                         "A customs hold is released only after the CHA uploads form KX-17."),
    "articles/weighbridge": ("Weighbridge",
                             "The weighbridge at gate 5 is calibrated every 90 days by Metrolab."),
    "articles/empties": ("Empty returns",
                         "Empty containers are returned to depot E-2 at Mathur within 5 days."),
    "articles/7": ("Rail siding",
                   "The rail siding loads rakes of 45 wagons between 22:00 and 04:00."),
}


def _crawl_source(api: LiveAPI, cleanup: Any, cid: str, seeds: list[str], **cc: Any
                  ) -> str:
    config = {"seed_urls": seeds, "max_depth": 3, "max_pages": 60, "crawl_delay_seconds": 0.5,
              **cc}
    return str(sj.create_source(api, cleanup, family=FAMILY, source_type="web_crawl",
                                config=config, collection_id=cid)["id"])


def _build_site(site: WebSite) -> None:
    """A help center: home → sections → articles, with every crawl hazard on it."""
    p = site.path
    nav = (("/" + p("articles/gate-hours").lstrip("/") + "?utm_source=newsletter#top", "Gate"),
           (p("articles/dg-cargo") + "?ref=footer", "DG"),
           (p("section/operations"), "Operations"), (p("section/compliance"), "Compliance"),
           (p("private/salaries"), "Salaries"),
           (f"http://{other_host()}{p('articles/elsewhere')}", "Partner site"),
           ("http://169.254.169.254/latest/meta-data/", "metadata"),
           ("http://postgres:5432/", "db"), ("mailto:help@kestrel.example", "mail"),
           (p("go/internal"), "internal redirect"), (p("calendar") + "?month=1", "Calendar"),
           (p("article.php") + "?id=7&utm_campaign=x", "Rail siding (old link)"),
           (p("print/7"), "Rail siding (print)"))
    site.put("", page("Kestrel Help Center", "Answers for shippers, CHAs and hauliers.",
                      links=nav))
    site.put("section/operations", page("Operations", "Yard operations articles.", links=(
        (p("articles/gate-hours"), "Gate hours"), (p("articles/reefer"), "Reefer"),
        (p("articles/weighbridge"), "Weighbridge"), (p("articles/empties"), "Empty returns"),
        (p("section/compliance"), "Compliance"))))
    site.put("section/compliance", page("Compliance", "Compliance articles.", links=(
        (p("articles/dg-cargo"), "DG"), (p("articles/customs"), "Customs"),
        (p("section/operations"), "Operations"), (p("deep/level-3"), "Archive"))))
    for rel, (title, fact) in ARTICLES.items():
        if rel == "articles/7":
            continue
        site.put(rel, page(title, fact, "See the operations section for more.",
                           links=((p("section/operations"), "Operations"),)))
    rail_title, rail_fact = ARTICLES["articles/7"]
    canonical = f'<link rel="canonical" href="{site.url("articles/7")}">'
    rail = page(rail_title, rail_fact, head=canonical)
    site.put("articles/7", rail)
    site.put("article.php", rail)  # the old dynamic URL of the same article
    site.put("print/7", rail)  # a print view: same text, other URL
    site.put("private/salaries", page("Salaries", "Confidential pay bands."))
    site.redirect("go/internal", "http://169.254.169.254/latest/meta-data/iam/", 302)
    # An endless calendar: month N links month N+1 (bounded by depth / max_pages).
    for month in range(1, 13):
        site.put(f"calendar?month={month}",
                 page(f"Calendar {month}", f"Vessel calls in month {month}.",
                      links=((p("calendar") + f"?month={month + 1}", "Next month"),)),
                 absolute=False)
    site.put("deep/level-3", page("Archive", "Old notices.", links=((p("deep/level-4"), "4"),)))
    site.put("deep/level-4", page("Archive 4", "Older notices: depth four."))
    site.put("orphan", page("Monsoon advisory",
                            "During monsoon alerts the yard stops reefer stacking above "
                            "three tiers."))
    site.put("sitemap.xml", (
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/'
        f'schemas/sitemap/0.9"><url><loc>{site.url("orphan")}</loc></url>'
        f"<url><loc>{site.url('articles/customs')}</loc></url>"
        "<url><loc>http://169.254.169.254/latest/meta-data/</loc></url></urlset>"),
        content_type="application/xml")
    site.robots(f"User-agent: *\nDisallow: {p('private/')}\nCrawl-delay: 1\n")


@pytest.mark.scenario("WEB-CRAWL-SITE")
def test_crawl_site(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                    site: WebSite) -> None:
    """Depth and page limits, same-host scope, robots.txt (Disallow + Crawl-delay), a
    sitemap with an orphan page, a canonical article reachable by three URLs, query-
    string and fragment variants, link loops, an endless calendar, internal links and
    an internal redirect: the right pages, each once, answered with its URL."""
    _build_site(site)
    cid = srcs.create_collection(api, cleanup, "rw-web-crawl")
    sid = _crawl_source(api, cleanup, cid, [site.url("")], max_depth=3,
                        sitemap_url=site.url("sitemap.xml"))
    started = time.time()
    job = sj.sync(api, sid, timeout=900)
    evidence.update(collection_id=cid, source_id=sid, sync=job)
    docs, total = kb.all_documents(api, cid)
    hits = [h for h in site.hits() if h["t"] >= started - 1]
    paths = [h["path"] for h in hits]
    evidence["requests"] = len(hits)
    soft: list[str] = []
    if str(job.get("status")).lower() not in ("completed", "partial"):
        soft.append(f"sync {job.get('status')}: {job.get('error_message')}")
    expected = {"", "section/operations", "section/compliance", "orphan", "articles/7",
                *[r for r in ARTICLES if r != "articles/7"], "deep/level-3",
                "calendar?month=1", "calendar?month=2"}
    sources = {str(d.get("source") or d.get("source_url") or d.get("title") or "")
               for d in docs}
    evidence["documents"] = sorted(sources)[:40]
    for rel in expected:
        if not any(s == site.url(rel) for s in sources):
            soft.append(f"not indexed: {rel or '/'}")
    if total != len(expected):
        soft.append(f"{total} documents, expected {len(expected)}")
    if any("/private/" in pth for pth in paths):
        soft.append("robots.txt Disallow ignored: /private/ was requested")
    if any("deep/level-4" in pth for pth in paths) or any("month=3" in pth for pth in paths):
        soft.append("max_depth ignored (depth 4 fetched)")
    if any(h["host"].startswith("rw-web-b") for h in site.all_hits("/site/")
           if h["t"] >= started - 1):
        soft.append("crawl left the site (rw-web-b requested)")
    dupes = {pth: paths.count(pth) for pth in set(paths) if paths.count(pth) > 1}
    if dupes:
        soft.append(f"pages requested more than once: {dupes}")
    times = sorted(h["t"] for h in hits)
    gaps = [round(b - a, 2) for a, b in itertools.pairwise(times)]
    evidence["min_gap_s"] = min(gaps) if gaps else None
    if gaps and min(gaps) < 0.9:
        soft.append(f"Crawl-delay 1 s not honoured (min gap {min(gaps)} s)")
    rail = [s for s in sources if "articles/7" in s or "article.php" in s or "print/7" in s]
    if rail != [site.url("articles/7")]:
        soft.append(f"canonical / duplicate article indexed as {rail}")
    rows: dict[str, Any] = {}
    soft += _ask(api, cid, "When does the rail siding load rakes?", ["22:00", "10 pm", "2200"],
                 site.url("articles/7"), rows.setdefault("rail", {}))
    soft += _ask(api, cid, "What do I need to upload to release a customs hold?", ["KX-17"],
                 site.url("articles/customs"), rows.setdefault("customs", {}))
    evidence["questions"] = rows
    record(evidence, crawl_s=job.get("wall_s"), documents=total, requests=len(hits))
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WEB-CRAWL-INCREMENTAL")
def test_crawl_incremental(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                           site: WebSite) -> None:
    """Second sync after a page changed, one was added and one removed (404): the
    changed page is updated (old text gone), the new one indexed, unchanged pages
    skipped; reconcile then removes the deleted page."""
    p = site.path
    pages = {f"news/{i}": (f"Notice {i}", f"Notice {i}: berth {i} maintenance window is "
                                          f"Tuesday {i}:00.") for i in range(1, 6)}
    links = tuple((p(rel), title) for rel, (title, _) in pages.items())
    site.put("", page("Notices", "Yard notices.", links=links))
    for rel, (title, fact) in pages.items():
        site.put(rel, page(title, fact))
    site.robots("User-agent: *\nDisallow:\n")
    cid = srcs.create_collection(api, cleanup, "rw-web-incr")
    sid = _crawl_source(api, cleanup, cid, [site.url("")], max_depth=2, crawl_delay_seconds=0.2)
    job1 = sj.sync(api, sid, timeout=600)
    count1 = _count(api, cid)
    site.put("news/2", page("Notice 2", "Notice 2: berth 2 maintenance moved to Friday 14:00."))
    site.put("news/5", "Not found", status=404, content_type="text/plain")
    site.put("news/6", page("Notice 6", "Notice 6: new gate scanner SC-12 live at gate 1."))
    site.put("", page("Notices", "Yard notices.", links=(*links, (p("news/6"), "Notice 6"))))
    job2 = sj.sync(api, sid, timeout=600)
    evidence.update(collection_id=cid, source_id=sid, sync1=job1, sync2=job2, count1=count1)
    soft: list[str] = []
    if count1 != 6:
        soft.append(f"first crawl: {count1} documents, expected 6")
    if job2.get("docs_indexed") != 3:  # home (new link), news/2, news/6
        soft.append(f"second sync indexed {job2.get('docs_indexed')}, expected 3 "
                    "(home, changed news/2, new news/6)")
    if (job2.get("docs_skipped") or 0) < 3:
        soft.append(f"unchanged pages not skipped: {job2}")
    if _rank(api, cid, "berth 2 maintenance", "Friday 14:00", site.url("news/2")) is None:
        soft.append("the changed page's new text is not served")
    stale = [h for h in _search(api, cid, "berth 2 maintenance Tuesday", k=10)
             if "Tuesday 2:00" in str(h.get("content"))]
    if stale:
        soft.append("the changed page's old text is still served")
    if _rank(api, cid, "gate scanner SC-12", "SC-12", site.url("news/6")) is None:
        soft.append("the new page is not indexed")
    before = _count(api, cid)
    status = sj.reconcile(api, sid)
    try:
        wait_until(lambda: _count(api, cid), timeout=240, interval=6,
                   desc="reconcile removes the deleted page", done=lambda n: n == before - 1)
    except AssertionError:
        soft.append(f"reconcile ({status}) did not remove the deleted page: "
                    f"{before} -> {_count(api, cid)}")
    if _rank(api, cid, "berth 5 maintenance", "berth 5") is not None:
        soft.append("the deleted page is still served after reconcile")
    evidence["after_reconcile"] = _count(api, cid)
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WEB-CRAWL-RETRY")
def test_crawl_failed_pages_retried(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                    site: WebSite) -> None:
    """A page that answers 503 and a dead seed: the sync is partial with both counted
    and both in the retry queue, the 503 page retryable; once the page recovers the
    operator retry indexes it and resolves the entry."""
    p = site.path
    site.put("", page("Tariffs", "Tariff index.", links=((p("tariff/storage"), "Storage"),)))
    site.put("tariff/storage", page("Storage tariff",
                                    "Dry storage costs 640 INR per TEU per day."),
             fail_first=1000, fail_status=503)
    site.robots("User-agent: *\nDisallow:\n")
    cid = srcs.create_collection(api, cleanup, "rw-web-retry")
    sid = _crawl_source(api, cleanup, cid, [site.url(""), site.url("missing-seed")],
                        crawl_delay_seconds=0.2)
    job = sj.sync(api, sid, timeout=600)
    entries = sj.dlq(api, sid)
    evidence.update(collection_id=cid, source_id=sid, sync=job,
                    dlq=[{"doc": e.get("doc_id"), "error": mask(e.get("error_message"))[:160],
                          "permanent": e.get("permanent_failure")} for e in entries])
    soft: list[str] = []
    if str(job.get("status")).lower() != "partial" or job.get("docs_failed") != 2:
        soft.append(f"sync should be partial with 2 failures: {job}")
    flaky = [e for e in entries if "503" in str(e.get("error_message"))]
    dead = [e for e in entries if "404" in str(e.get("error_message"))]
    if not flaky or flaky[0].get("permanent_failure"):
        soft.append("the 503 page is not a retryable DLQ entry")
    if not dead:
        soft.append("the dead seed (404) is not in the DLQ")
    site.put("tariff/storage", page("Storage tariff",
                                    "Dry storage costs 640 INR per TEU per day."))
    if flaky:
        resp = api.post(f"/ingestion/dlq/{flaky[0]['id']}/retry")
        evidence["retry_http"] = resp.status_code
        try:
            wait_until(lambda: _rank(api, cid, "dry storage cost per TEU", "640 INR",
                                     site.url("tariff/storage")), timeout=240, interval=6,
                       desc="the recovered page indexed by the operator retry")
        except AssertionError:
            soft.append("the operator retry did not index the recovered page")
        time.sleep(5)
        still = [e for e in sj.dlq(api, sid) if e.get("id") == flaky[0]["id"]]
        if still:
            soft.append("the retried entry is still open")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WEB-CRAWL-SSRF")
def test_crawl_ssrf_on_every_hop(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                 site: WebSite) -> None:
    """Internal seeds are refused on save; a crawl whose pages link and redirect to
    internal addresses (metadata, postgres, redis, backend, localhost) never requests
    them — a refused seed redirect is a counted, permanent failure."""
    cid = srcs.create_collection(api, cleanup, "rw-web-ssrf")
    soft: list[str] = []
    refused: dict[str, Any] = {}
    for seed in ("http://169.254.169.254/latest/meta-data/", "http://postgres:5432/",
                 "http://redis:6379/", "http://backend:8000/health", "http://localhost:8000/",
                 "http://127.0.0.1:8000/", "file:///etc/passwd", "http://[::1]:8000/"):
        out = sj.create_source(api, cleanup, family=FAMILY, source_type="web_crawl",
                               config={"seed_urls": [seed]}, collection_id=cid, expect=422)
        refused[seed] = out["_http"]
    p = site.path
    site.put("", page("Links", "Links to internal places.", links=(
        ("http://169.254.169.254/latest/meta-data/", "meta"), ("http://postgres:5432/", "pg"),
        ("http://backend:8000/health", "api"), (p("hop"), "hop"))))
    site.redirect("hop", "http://redis:6379/", 302)
    site.redirect("seed-hop", "http://169.254.169.254/latest/meta-data/iam/", 307)
    site.robots("User-agent: *\nDisallow:\n")
    sid = _crawl_source(api, cleanup, cid, [site.url(""), site.url("seed-hop")],
                        crawl_delay_seconds=0.2)
    job = sj.sync(api, sid, timeout=600)
    entries = sj.dlq(api, sid)
    evidence.update(collection_id=cid, refused_on_save=refused, sync=job,
                    dlq=[{"doc": e.get("doc_id"), "error": mask(e.get("error_message"))[:160],
                          "permanent": e.get("permanent_failure")} for e in entries])
    blocked = [e for e in entries if "blocked" in str(e.get("error_message")).lower()]
    if len(blocked) != 1 or len(entries) != 1:
        soft.append(f"the internal seed redirect is not the one 'blocked' failure: "
                    f"{evidence['dlq']}")
    if str(job.get("status")).lower() != "partial" or job.get("docs_failed") != 1:
        soft.append(f"sync should be partial with 1 failure: {job}")
    if _count(api, cid) != 1:
        soft.append(f"{_count(api, cid)} documents (only the public page should be)")
    assert not soft, "; ".join(soft)
