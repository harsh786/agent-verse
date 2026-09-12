"""OcrStepNode — OCR a document/image mid-workflow (WS-14).

A workflow step type that runs the ONE ``OcrEngine`` (via ``extract_any``) so a
workflow can extract text from any document/image inline — the same engine used
by goal/agent execution (the ``extract_document`` tool) and the org missions.
No OCR logic is duplicated here; this node only marshals the step input into the
engine and the result back into the workflow state.

Input keys (resolved from the step ``input`` dict):
  * ``document_base64`` (+ optional ``content_type`` / ``filename``) — any format
  * ``image_base64`` — an image
  * ``pdf_base64`` — a PDF
  * ``url`` — an http(s) URL to download and OCR (SSRF-guarded, size-capped)
  * ``file_path`` — a server-local file, allowed ONLY under the directories in
    ``WORKFLOW_FILE_ALLOWED_DIRS`` (secure-by-default: refused when unset)
Output (``step_outputs[step_id]``): ``raw_text``, ``document_type``,
``source_format``, ``degraded``, ``degradation_reason``, ``page_count``.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log_mod = get_logger(__name__)


def _resolve_allowed_file(path: str) -> str | None:
    """Return the real path only if it is a regular file under a configured
    allowlist directory; otherwise ``None``.

    ``file_path`` can be templated from external (webhook) inputs, so reading it
    unrestricted is arbitrary file access. Reads are confined to the directories
    in ``WORKFLOW_FILE_ALLOWED_DIRS`` (os.pathsep-separated absolute paths),
    resolved through symlinks so ``..`` and symlink escapes cannot leave the root.
    Secure-by-default: an empty/unset allowlist refuses every ``file_path``.
    """
    import os

    raw = os.getenv("WORKFLOW_FILE_ALLOWED_DIRS", "") or ""
    allowed = [d for d in raw.split(os.pathsep) if d.strip()]
    if not allowed:
        _log_mod.warning("ocr_file_path_denied_no_allowlist", path=path[:120])
        return None
    try:
        real = os.path.realpath(path)
    except Exception:
        return None
    if not os.path.isfile(real):
        return None
    for base in allowed:
        try:
            base_real = os.path.realpath(base)
        except Exception:
            continue
        if real == base_real or real.startswith(base_real + os.sep):
            return real
    _log_mod.warning("ocr_file_path_denied_outside_allowlist", path=real[:120])
    return None

_log = get_logger(__name__)


class OcrStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self._ocr_engine = services.get("ocr_engine")
        self._provider = services.get("llm_provider") or services.get("provider")

    def _engine(self) -> Any:
        if self._ocr_engine is None:
            from app.ocr.engine import OcrEngine

            self._ocr_engine = OcrEngine()
        return self._ocr_engine

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        resolved = self.ctx.resolve_dict(self.step.input, state)
        _log.info("ocr_step_executing", step_id=self.step.id)
        start = time.monotonic()

        # Test-run mock override (parity with the other step nodes).
        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output: dict[str, Any] = (state["mock_overrides"] or {})[self.step.id]
        else:
            output = await self._run_ocr(resolved)

        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "step_timings": {**(state.get("step_timings") or {}), self.step.id: duration_ms},
        }

    async def _run_ocr(self, resolved: dict[str, Any]) -> dict[str, Any]:
        # URL ingestion (async, SSRF-guarded) takes precedence, then base64 /
        # file_path (sync). Lets a workflow OCR a remote document directly.
        data: bytes | None = None
        content_type = resolved.get("content_type")
        filename = resolved.get("filename")
        url = resolved.get("url")
        if url:
            data, content_type, filename = await self._fetch_url_bytes(str(url), content_type)
            if data is None:
                return {
                    "error": f"ocr step could not fetch url: {str(url)[:120]}",
                    "raw_text": "",
                    "degraded": True,
                }
        else:
            data, content_type, filename = self._resolve_bytes(resolved)
        if data is None:
            return {
                "error": "ocr step requires one of url, document_base64, image_base64, "
                "pdf_base64, or an allowlisted file_path",
                "raw_text": "",
                "degraded": True,
            }
        try:
            result = await self._engine().extract_any(
                data, content_type=content_type, filename=filename, provider=self._provider
            )
        except Exception as exc:  # never crash the run; surface as a degraded output
            _log.warning("ocr_step_failed", step_id=self.step.id, error=str(exc))
            return {"error": str(exc), "raw_text": "", "degraded": True}

        return {
            "raw_text": result.raw_text,
            "document_type": result.document_type.value,
            "source_format": result.source_format,
            "degraded": result.degraded,
            "degradation_reason": result.degradation_reason,
            "overall_confidence": round(result.overall_confidence, 4),
            "page_count": result.page_count,
        }

    _MAX_URL_BYTES = 25 * 1024 * 1024  # 25 MB download cap
    _MAX_REDIRECTS = 3

    @staticmethod
    async def _fetch_url_bytes(
        url: str, content_type: str | None
    ) -> tuple[bytes | None, str | None, str | None]:
        """Download a document over http(s) for OCR — SSRF-guarded, size-capped.

        Security:
        * SSRF — the initial URL AND every redirect hop are validated with the
          same ``SSRFGuard`` the HTTP step uses, so neither a templated URL nor a
          server-controlled redirect (``Location: http://169.254.169.254/…``) can
          reach internal/link-local addresses. Auto-redirects are disabled; hops
          are followed manually (max ``_MAX_REDIRECTS``) so each is re-validated.
        * Resource cap — the body is STREAMED with a running byte counter and the
          transfer is aborted the instant it exceeds the cap (and the declared
          Content-Length is rejected up front), so a hostile server cannot force
          us to buffer an unbounded response before the size is checked.
        """
        import os as _os
        from urllib.parse import urljoin, urlparse

        import httpx

        from app.workflow.security import SSRFBlockedError, SSRFGuard

        guard = SSRFGuard()
        cap = OcrStepNode._MAX_URL_BYTES
        current = url
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                for _hop in range(OcrStepNode._MAX_REDIRECTS + 1):
                    try:
                        guard.validate(current)
                    except SSRFBlockedError as exc:
                        _log.warning("ocr_url_ssrf_blocked", url=current[:120], error=str(exc))
                        return None, None, None
                    async with client.stream("GET", current) as resp:
                        # Follow one redirect hop manually so the target is re-validated.
                        if resp.is_redirect:
                            loc = resp.headers.get("location")
                            if not loc:
                                return None, None, None
                            current = urljoin(current, loc)
                            continue
                        resp.raise_for_status()
                        # Reject an oversized declared length before reading a byte.
                        clen = resp.headers.get("content-length")
                        if clen and clen.isdigit() and int(clen) > cap:
                            _log.warning("ocr_url_too_large_declared", url=current[:120], size=clen)
                            return None, None, None
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in resp.aiter_bytes():
                            total += len(chunk)
                            if total > cap:
                                _log.warning("ocr_url_too_large", url=current[:120], size=total)
                                return None, None, None
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        ct = (
                            content_type
                            or resp.headers.get("content-type", "").split(";")[0]
                            or None
                        )
                        name = _os.path.basename(urlparse(current).path) or "document"
                        return body, ct, name
                _log.warning("ocr_url_too_many_redirects", url=url[:120])
                return None, None, None
        except Exception as exc:
            _log.warning("ocr_url_fetch_failed", url=url[:120], error=str(exc))
            return None, None, None

    @staticmethod
    def _resolve_bytes(resolved: dict[str, Any]) -> tuple[bytes | None, str | None, str | None]:
        content_type = resolved.get("content_type")
        filename = resolved.get("filename")
        for key, ct in (
            ("document_base64", content_type),
            ("image_base64", content_type or "image/png"),
            ("pdf_base64", "application/pdf"),
        ):
            b64 = resolved.get(key)
            if b64:
                # Cap the encoded payload BEFORE decoding — base64 is templatable
                # from external (webhook) input, so an unbounded decode is a memory
                # exhaustion vector. 4/3 encoding ratio → cap the string accordingly.
                if len(str(b64)) > (OcrStepNode._MAX_URL_BYTES // 3) * 4 + 16:
                    _log.warning("ocr_base64_too_large", key=key, size=len(str(b64)))
                    return None, None, None
                try:
                    data = base64.b64decode(b64)
                except Exception:
                    return None, None, None
                if len(data) > OcrStepNode._MAX_URL_BYTES:
                    _log.warning("ocr_base64_decoded_too_large", key=key, size=len(data))
                    return None, None, None
                return data, ct, filename
        # file_path — a server-local file. SECURITY: file_path can be templated
        # from external (webhook) inputs, so an unrestricted read is arbitrary
        # file access (e.g. /etc/passwd, another tenant's data). Only read files
        # under an explicitly-configured allowlist of directories, resolved
        # through symlinks to block traversal. Secure-by-default: with no
        # allowlist set, file_path is refused entirely (base64 inputs still work).
        file_path = resolved.get("file_path")
        if file_path:
            safe = _resolve_allowed_file(str(file_path))
            if safe is None:
                return None, None, None
            try:
                import os as _os

                with open(safe, "rb") as _f:
                    return _f.read(), content_type, filename or _os.path.basename(safe)
            except Exception:
                return None, None, None
        return None, None, None
