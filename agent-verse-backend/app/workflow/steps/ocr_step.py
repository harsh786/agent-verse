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
        data, content_type, filename = self._resolve_bytes(resolved)
        if data is None:
            return {
                "error": "ocr step requires one of document_base64, image_base64, pdf_base64",
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
                try:
                    return base64.b64decode(b64), ct, filename
                except Exception:
                    return None, None, None
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
