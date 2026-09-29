"""Real-world knowledge base: upload a PDF and a text file, verify embeddings and retrieval.

Runs the booted app (real Postgres+pgvector, Redis, least-privilege RLS when
``E2E_LEAST_PRIVILEGE=1``) against the REAL embedding model — the on-prem
Qwen3-Embedding (1024-d) or NVIDIA nemotron (2048-d) — through the same endpoint the frontend uploads with
(``POST /knowledge/ingest/file``). Nothing is faked: the PDF is a real multi-page
PDF, parsed by the server, chunked, embedded by the real model, stored in
``knowledge_chunks_<dim>`` and retrieved by real hybrid search.

Opt-in (on-prem needs the cluster, e.g. over VPN)::

    set -a && . providers.env && set +a
    REAL_PROVIDERS=1 E2E_LEAST_PRIVILEGE=1 uv run pytest \
        tests/e2e_full/test_knowledge_real_documents_e2e.py -m slow
    # NVIDIA instead: ONPREM_ENABLED=false KB_E2E_EMBEDDER=nvidia \
    #   NVIDIA_EMBED_MODEL=nvidia/nemotron-3-embed-1b NVIDIA_EMBED_DIM=2048 …
"""

from __future__ import annotations

import math
import os
import uuid
from typing import Any

import httpx
import pytest

# Which real embedder the booted app uses: the on-prem Qwen3-Embedding (1024-d,
# default) or NVIDIA nemotron (2048-d) with KB_E2E_EMBEDDER=nvidia — the app picks
# NVIDIA when NVIDIA_EMBED_MODEL is set (app/main.py), so set that too.
_NVIDIA = os.getenv("KB_E2E_EMBEDDER") == "nvidia"
_DIM = (
    int(os.getenv("NVIDIA_EMBED_DIM", "2048"))
    if _NVIDIA
    else int(os.getenv("ONPREM_EMBEDDING_DIM", "1024"))
)
_CONFIGURED = (
    bool(os.getenv("NVIDIA_API_KEY") and os.getenv("NVIDIA_EMBED_MODEL"))
    if _NVIDIA
    else bool(os.getenv("ONPREM_EMBEDDING_BASE_URL"))
)

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("REAL_PROVIDERS") != "1" or not _CONFIGURED,
        reason="real-provider test: set REAL_PROVIDERS=1 and the embedder env (providers.env)",
    ),
]

# Distinctive facts, one per PDF page, so retrieval can be checked page by page.
_PDF_PAGES = [
    (
        "Northwind Traders Employee Handbook - Section 4: Leave Policy",
        "Every full-time employee receives 22 days of paid annual leave per calendar year. "
        "Unused leave of up to 5 days may be carried over into the first quarter of the next "
        "year. Sick leave is separate and does not reduce the annual leave balance.",
    ),
    (
        "Section 7: Travel and Expense Policy",
        "Meal expenses on business travel are reimbursed up to INR 1,500 per day. Economy "
        "class is mandatory for flights shorter than six hours. Expense reports must be "
        "filed within 30 days of returning from the trip.",
    ),
    (
        "Section 9: Information Security",
        "VPN credentials are rotated every 90 days. Laptops must use full-disk encryption. "
        "Suspected phishing emails are reported to the security desk at extension 4411.",
    ),
]

_TXT = """Northwind X200 Router - Customer FAQ

Q: What is the warranty period for the X200 router?
A: The X200 router carries an 18-month limited hardware warranty from the date of purchase.

Q: When is phone support available?
A: Phone support is available Monday to Saturday, 9am to 6pm IST.

Q: How do I factory reset the X200?
A: Hold the recessed reset button for 12 seconds until the status light blinks amber.
"""


def _make_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    for title, body in _PDF_PAGES:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=11)
        pdf.multi_cell(0, 6, body, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def _embed_direct(text: str) -> list[float]:
    """Embed ``text`` as a passage with the real model directly (independent of the app)."""
    if _NVIDIA:
        base = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        body: dict[str, Any] = {
            "model": os.environ["NVIDIA_EMBED_MODEL"],
            "input": [text],
            "input_type": "passage",
            "truncate": "END",
        }
        headers = {"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}"}
    else:
        base = os.environ["ONPREM_EMBEDDING_BASE_URL"]
        body = {
            "model": os.environ.get("ONPREM_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
            "input": [text],
        }
        headers = {}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{base.rstrip('/')}/embeddings", json=body, headers=headers)
        r.raise_for_status()
        return list(r.json()["data"][0]["embedding"])


async def _chunks(owner_dsn: str, tenant_id: str, collection_id: str) -> list[dict[str, Any]]:
    """Stored chunks, read as the owner (bypasses RLS) to verify what really landed."""
    import asyncpg

    conn = await asyncpg.connect(owner_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        rows = await conn.fetch(
            "SELECT id::text, document_id::text, content, embedding::text AS emb, metadata "
            f"FROM knowledge_chunks_{_DIM} WHERE tenant_id::text = $1 "
            "AND collection_id::text = $2 "
            "ORDER BY chunk_index",
            tenant_id,
            collection_id,
        )
    finally:
        await conn.close()
    return [
        {**dict(r), "embedding": [float(x) for x in r["emb"].strip("[]").split(",")]}
        for r in rows
    ]


async def _signup(client: Any, app: Any) -> tuple[httpx.AsyncClient, str]:
    from httpx import ASGITransport, AsyncClient

    resp = await client.post(
        "/tenants/signup",
        json={"name": "KB Real", "email": f"kb-real-{uuid.uuid4().hex[:10]}@example.com"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return (
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://e2e-full",
            headers={"X-API-Key": body["api_key"]},
            timeout=300,
        ),
        body["tenant_id"],
    )


async def test_pdf_and_text_knowledge_bases_real_embeddings(
    app: Any, client: Any, _backends: tuple[str, str]
) -> None:
    owner_dsn = _backends[0]

    tc, tenant_id = await _signup(client, app)
    async with tc:
        # ── 1. Two knowledge bases ───────────────────────────────────────────
        r = await tc.post("/knowledge/collections", json={"name": "Employee Handbook"})
        assert r.status_code == 201, r.text
        pdf_col = r.json()["collection_id"]
        r = await tc.post("/knowledge/collections", json={"name": "X200 FAQ"})
        assert r.status_code == 201, r.text
        txt_col = r.json()["collection_id"]

        # ── 2. Upload a real PDF and a text file (the frontend's endpoint) ───
        pdf_bytes = _make_pdf()
        assert pdf_bytes.startswith(b"%PDF")
        r = await tc.post(
            "/knowledge/ingest/file",
            data={"collection_id": pdf_col},
            files={"file": ("handbook.pdf", pdf_bytes, "application/pdf")},
        )
        assert r.status_code == 201, r.text
        assert r.json()["chunks_created"] >= 1 and not r.json()["deduplicated"], r.text

        r = await tc.post(
            "/knowledge/ingest/file",
            data={"collection_id": txt_col},
            files={"file": ("x200_faq.txt", _TXT.encode(), "text/plain")},
        )
        assert r.status_code == 201, r.text
        assert r.json()["chunks_created"] >= 1, r.text

        # ── 3. What actually landed in pgvector ─────────────────────────────
        pdf_chunks = await _chunks(owner_dsn, tenant_id, pdf_col)
        txt_chunks = await _chunks(owner_dsn, tenant_id, txt_col)
        assert pdf_chunks and txt_chunks
        pdf_text = " ".join(c["content"] for c in pdf_chunks)
        # The PDF was parsed, not stored as raw bytes / a placeholder.
        assert "%PDF" not in pdf_text and "endobj" not in pdf_text, pdf_text[:300]
        assert "install pypdf" not in pdf_text
        for needle in ("22 days of paid annual leave", "INR 1,500 per day", "every 90 days"):
            assert needle in pdf_text, f"{needle!r} missing from parsed PDF text"
        assert "18-month limited hardware warranty" in " ".join(c["content"] for c in txt_chunks)

        for c in pdf_chunks + txt_chunks:
            vec = c["embedding"]
            assert len(vec) == _DIM
            norm = math.sqrt(sum(x * x for x in vec))
            assert norm > 0.5 and len({round(x, 6) for x in vec}) > _DIM // 4, norm  # real, not 0s
        # The stored vector IS the real model's embedding of that chunk's text.
        probe = pdf_chunks[0]
        assert _cos(probe["embedding"], await _embed_direct(probe["content"])) > 0.99

        # ── 4. Collection stats agree with the rows ─────────────────────────
        r = await tc.get(f"/knowledge/collections/{pdf_col}/stats")
        assert r.status_code == 200, r.text
        stats = r.json()
        assert stats.get("chunk_count", stats.get("total_chunks")) == len(pdf_chunks), stats

        # ── 5. Semantic retrieval finds the right passage ───────────────────
        async def top(col: str, q: str) -> list[dict[str, Any]]:
            resp = await tc.get(
                "/knowledge/search", params={"q": q, "collection_id": col, "top_k": 3}
            )
            assert resp.status_code == 200, resp.text
            return list(resp.json())

        hits = await top(pdf_col, "How much paid time off do staff get each year?")
        assert hits and "22 days" in hits[0]["content"], hits
        hits = await top(pdf_col, "What is the daily limit for food while travelling?")
        assert hits and "1,500" in hits[0]["content"], hits
        hits = await top(txt_col, "how long is the guarantee on the X200?")
        assert hits and "18-month" in hits[0]["content"], hits
        # Collections are isolated: the FAQ collection never returns handbook text.
        hits = await top(txt_col, "annual leave carry over")
        assert all("annual leave" not in h["content"] for h in hits), hits

        # ── 6. Grounded answer over both knowledge bases ────────────────────
        r = await tc.post(
            "/knowledge/chat",
            json={
                "question": "What is the meal reimbursement cap per day on business travel?",
                "collection_ids": [pdf_col, txt_col],
            },
        )
        assert r.status_code == 200, r.text
        chat = r.json()
        assert chat["chunks_retrieved"] >= 1 and chat["citations"], chat
        # Citations point at the stored chunk that actually holds the answer
        # (the response carries only a 300-char excerpt of each chunk).
        answer_chunk_ids = {c["id"] for c in pdf_chunks if "INR 1,500" in c["content"]}
        assert answer_chunk_ids & {c["chunk_id"] for c in chat["citations"]}, chat["citations"]
        assert "1,500" in chat["answer"] or "1500" in chat["answer"], chat["answer"]

        # ── 7. Re-uploading the same PDF is deduplicated ────────────────────
        r = await tc.post(
            "/knowledge/ingest/file",
            data={"collection_id": pdf_col},
            files={"file": ("handbook-copy.pdf", pdf_bytes, "application/pdf")},
        )
        assert r.status_code == 201 and r.json()["deduplicated"] is True, r.text
        assert len(await _chunks(owner_dsn, tenant_id, pdf_col)) == len(pdf_chunks)

        # ── 8. Another tenant cannot read these knowledge bases ─────────────
        other, _ = await _signup(client, app)
        async with other:
            resp = await other.get(
                "/knowledge/search",
                params={"q": "annual leave", "collection_id": pdf_col, "top_k": 3},
            )
            assert resp.status_code in (403, 404) or resp.json() == [], resp.text

        # ── 9. Deleting the document removes its vectors ────────────────────
        doc_id = pdf_chunks[0]["document_id"]
        r = await tc.delete(f"/knowledge/collections/{pdf_col}/documents/{doc_id}")
        assert r.status_code == 200, r.text
        assert r.json()["chunks_deleted"] == len(pdf_chunks)
        assert await _chunks(owner_dsn, tenant_id, pdf_col) == []
        hits = await top(pdf_col, "How much paid time off do staff get each year?")
        assert hits == [], hits
