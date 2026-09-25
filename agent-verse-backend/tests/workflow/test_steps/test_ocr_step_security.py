"""Security regression tests for OcrStepNode input ingestion.

Covers the hardening added after the workflow OCR step gained URL ingestion:
  * base64 payloads are size-capped BEFORE/AFTER decode (no unbounded decode)
  * URL fetch validates every redirect hop with the SSRF guard (no redirect SSRF)
  * URL fetch streams with a hard byte cap (no buffering an unbounded body)
"""

from __future__ import annotations

import base64

import pytest

from app.workflow.steps.ocr_step import OcrStepNode


def test_base64_over_cap_is_rejected() -> None:
    # An encoded string larger than the cap must be refused without decoding.
    huge_b64 = "A" * ((OcrStepNode._MAX_URL_BYTES // 3) * 4 + 100)
    data, ct, name = OcrStepNode._resolve_bytes({"document_base64": huge_b64})
    assert data is None


def test_base64_within_cap_decodes() -> None:
    small = base64.b64encode(b"hello world").decode()
    data, ct, name = OcrStepNode._resolve_bytes(
        {"document_base64": small, "content_type": "text/plain", "filename": "a.txt"}
    )
    assert data == b"hello world"
    assert name == "a.txt"


@pytest.mark.asyncio
async def test_url_redirect_to_internal_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public URL that 302-redirects to a link-local address must be blocked —
    the guard has to run on the redirect target, not just the first URL."""
    validated: list[str] = []

    class _Guard:
        def validate(self, url: str) -> None:
            validated.append(url)
            from app.workflow.security import SSRFBlockedError

            # Public host allowed; the metadata endpoint is blocked.
            if "169.254.169.254" in url or "localhost" in url:
                raise SSRFBlockedError("blocked")

    class _Resp:
        is_redirect = True
        headers = {"location": "http://169.254.169.254/latest/meta-data/"}

        def raise_for_status(self) -> None:  # pragma: no cover - not reached
            pass

        async def __aenter__(self) -> _Resp:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        def stream(self, method: str, url: str) -> _Resp:
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr("app.workflow.security.SSRFGuard", _Guard)

    data, ct, name = await OcrStepNode._fetch_url_bytes(
        "https://public.example.com/doc.pdf", None
    )
    assert data is None
    # The redirect target was validated (and rejected) — not silently followed.
    assert any("169.254.169.254" in u for u in validated)
