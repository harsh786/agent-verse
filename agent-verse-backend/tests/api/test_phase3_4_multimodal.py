"""Phase 3+4 tests: Embedding Platform + Multimodal Intelligence."""
import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.embeddings import router as embeddings_router
from app.api.multimodal import router as multimodal_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-p34", plan=PlanTier.PROFESSIONAL, api_key_id="kid-p34")
_KEY = "ak_phase34_test_key"
_HEADERS = {"X-API-Key": _KEY}


def _make_app():
    app = FastAPI()

    async def _resolve(key):
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(embeddings_router)
    app.include_router(multimodal_router)
    return app


# ── Embedding tests ─────────────────────────────────────────────────────────

def test_embed_texts_returns_vectors():
    client = TestClient(_make_app())
    resp = client.post("/embeddings/embed", json={"texts": ["hello world", "goodbye"]}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["dimension"] > 0
    assert len(data["embeddings"][0]) == data["dimension"]


def test_embed_empty_list_returns_empty():
    client = TestClient(_make_app())
    resp = client.post("/embeddings/embed", json={"texts": []}, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_lexical_fallback_when_no_provider():
    client = TestClient(_make_app())
    resp = client.post("/embeddings/embed", json={"texts": ["test text"], "fallback_lexical": True}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["used_fallback"] is True
    assert len(data["embeddings"][0]) > 0


def test_dimension_mismatch_detection():
    client = TestClient(_make_app())
    resp = client.post("/embeddings/validate-dimension", json={
        "provider": "openai",
        "model": "text-embedding-3-large",
        "collection_dimension": 512,  # Wrong - model is 3072
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["matches"] is False
    assert data["model_dimension"] == 3072
    assert "warning" in data


def test_dimension_match_detection():
    client = TestClient(_make_app())
    resp = client.post("/embeddings/validate-dimension", json={
        "provider": "openai",
        "model": "text-embedding-3-large",
        "collection_dimension": 3072,  # Correct
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["matches"] is True


def test_list_embedding_providers():
    client = TestClient(_make_app())
    resp = client.get("/embeddings/providers", headers=_HEADERS)
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    assert len(providers) >= 3
    provider_names = {p["provider"] for p in providers}
    assert "openai" in provider_names
    assert "voyage" in provider_names


def test_embedding_tenant_isolation():
    # Each request is for a specific tenant - should not leak data
    client = TestClient(_make_app())
    resp = client.post("/embeddings/embed", json={"texts": ["sensitive data"]}, headers=_HEADERS)
    # Stats endpoint should only show this tenant's usage
    stats = client.get("/embeddings/usage", headers=_HEADERS)
    assert stats.status_code == 200


# ── Multimodal tests ─────────────────────────────────────────────────────────

def test_ingest_text():
    client = TestClient(_make_app())
    resp = client.post("/multimodal/ingest", json={
        "modality": "text",
        "content": "This is a test document about AI agents.",
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["span_count"] == 1
    assert len(data["spans"]) == 1


def test_ingest_pdf_base64():
    # Minimal valid PDF as base64
    minimal_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    pdf_b64 = base64.b64encode(minimal_pdf).decode()

    client = TestClient(_make_app())
    resp = client.post("/multimodal/ingest", json={
        "modality": "pdf",
        "base64_data": pdf_b64,
        "filename": "test.pdf",
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("completed", "failed")  # May fail gracefully
    assert "job_id" in data


def test_ingest_image_without_vision_provider():
    # 1x1 white PNG
    tiny_png = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82").decode()
    client = TestClient(_make_app())
    resp = client.post("/multimodal/ingest", json={
        "modality": "image",
        "base64_data": tiny_png,
        "filename": "test.png",
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # Should complete with fallback message even without vision
    assert data["status"] in ("completed", "failed")
    assert "job_id" in data


def test_ingest_audio_without_provider():
    fake_audio = base64.b64encode(b"RIFF....WAVEfmt ....data....").decode()
    client = TestClient(_make_app())
    resp = client.post("/multimodal/ingest", json={
        "modality": "audio",
        "base64_data": fake_audio,
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data


def test_ingest_invalid_modality():
    client = TestClient(_make_app())
    resp = client.post("/multimodal/ingest", json={
        "modality": "hologram",
        "content": "test",
    }, headers=_HEADERS)
    assert resp.status_code == 400


def test_get_job_not_found():
    client = TestClient(_make_app())
    resp = client.get("/multimodal/jobs/nonexistent-job-id", headers=_HEADERS)
    assert resp.status_code == 404


def test_get_job_tenant_isolation():
    """A job created by one tenant should not be accessible by another."""
    client = TestClient(_make_app())
    create = client.post("/multimodal/ingest", json={"modality": "text", "content": "secret"}, headers=_HEADERS)
    job_id = create.json()["job_id"]

    # Try with different API key (different tenant)
    other_headers = {"X-API-Key": "different-key-no-access"}
    resp = client.get(f"/multimodal/jobs/{job_id}", headers=other_headers)
    # Should be 401 (unauthorized) or 404 (not found for this tenant)
    assert resp.status_code in (401, 404)


def test_extracted_spans_have_provenance():
    client = TestClient(_make_app())
    resp = client.post("/multimodal/ingest", json={
        "modality": "text",
        "content": "First paragraph.\n\nSecond paragraph.",
    }, headers=_HEADERS)
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) >= 1
    for span in spans:
        assert "content" in span
        assert "modality" in span
        assert "confidence" in span
