"""RPAStepNode — browser automation (scrape) + report generation in a workflow.

Runs the ONE RPA scrape pipeline (``run_scrape_report``) — open URL → extract
text → screenshot — via the shared ``RPAExecutor`` (real Playwright when
available, deterministic simulation otherwise), assembles a provenance-tagged
``ScrapeReport``, and (by default) renders it to a real PDF stored in the RPA
artifact store. No RPA logic is duplicated here; this node only marshals the
step input into the executor and the report back into the workflow state.

Input keys (resolved from the step ``input`` dict):
  * ``url`` (required) — the page to automate/scrape
  * ``selectors`` — optional list of CSS selectors to extract (else whole page)
  * ``title`` — optional report title
  * ``generate_pdf`` — render + store a PDF deliverable (default ``True``)
  * ``allow_http_fetch`` — allow the browser-less real-HTTP fallback (default ``True``)
Output (``step_outputs[step_id]``): ``report`` (title, source_url, sections,
screenshot_count, provenance), ``section_count`` and, when a PDF was produced,
``report_pdf_uri``.
"""

from __future__ import annotations

import time
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log = get_logger(__name__)


class RPAStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self._artifact_store = services.get("rpa_artifact_store")
        self._executor = services.get("rpa_executor")
        self._provider = services.get("llm_provider") or services.get("provider")

    def _build_executor(self) -> tuple[Any, Any, Any]:
        from app.rpa.artifacts import RPAArtifactStore
        from app.rpa.executor import RPAExecutor

        store = self._artifact_store or RPAArtifactStore()
        # A stateful browser session keeps one page alive across the
        # open→extract→screenshot sequence, so the screenshot step actually
        # captures the page opened earlier (the standalone path opens a fresh
        # browser per call and can't screenshot prior state → no screenshots).
        session_manager: Any = None
        try:
            from app.rpa.session_manager import BrowserSessionManager

            session_manager = BrowserSessionManager(headless=True)
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("rpa_session_manager_unavailable", error=str(exc)[:120])
        executor = self._executor or RPAExecutor(
            artifact_store=store,
            vision_provider=self._provider,
            session_manager=session_manager,
        )
        return executor, store, session_manager

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        resolved = self.ctx.resolve_dict(self.step.input, state)
        _log.info("rpa_step_executing", step_id=self.step.id)
        start = time.monotonic()

        # Test-run mock override (parity with the other step nodes).
        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output: dict[str, Any] = (state["mock_overrides"] or {})[self.step.id]
        else:
            output = await self._run(resolved, state)

        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "step_timings": {**(state.get("step_timings") or {}), self.step.id: duration_ms},
        }

    async def _run(self, resolved: dict[str, Any], state: WorkflowState) -> dict[str, Any]:
        url = str(resolved.get("url", "") or "").strip()
        if not url:
            return {"error": "rpa step requires a 'url'", "degraded": True}

        selectors = resolved.get("selectors")
        if isinstance(selectors, str):
            selectors = [selectors]
        title = resolved.get("title")
        generate_pdf = resolved.get("generate_pdf", True)
        allow_http_fetch = bool(resolved.get("allow_http_fetch", True))
        goal_id = str(state.get("run_id", "") or "")
        tenant_id = str(state.get("tenant_id", "") or "")

        import uuid as _uuid

        from app.rpa.report import build_and_store_report_pdf, run_scrape_report

        session_id = _uuid.uuid4().hex
        executor, store, session_manager = self._build_executor()
        try:
            report, _results = await run_scrape_report(
                executor,
                url=url,
                tenant_id=tenant_id,
                goal_id=goal_id,
                selectors=list(selectors) if selectors else None,
                title=title if isinstance(title, str) else None,
                session_id=session_id,
                allow_http_fetch=allow_http_fetch,
            )
        except Exception as exc:  # never crash the run; surface as degraded
            _log.warning("rpa_step_failed", step_id=self.step.id, error=str(exc))
            return {"error": str(exc), "degraded": True}
        finally:
            # Always tear down the live browser session so it doesn't leak.
            if session_manager is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    await session_manager.close(session_id, tenant_id)

        output: dict[str, Any] = {
            "report": {
                "title": report.title,
                "source_url": report.source_url,
                "scraped_at": report.scraped_at,
                "sections": [
                    {"heading": s.heading, "body": s.body} for s in report.sections
                ],
                "screenshot_count": len(report.screenshots),
                "provenance": report.provenance,
            },
            "section_count": len(report.sections),
            "degraded": report.is_empty,
        }

        if generate_pdf:
            try:
                artifact = await build_and_store_report_pdf(
                    report, artifact_store=store, goal_id=goal_id, name="rpa-report.pdf"
                )
                output["report_pdf_uri"] = getattr(artifact, "uri", None)
            except Exception as exc:
                _log.warning("rpa_step_pdf_failed", step_id=self.step.id, error=str(exc))
                output["report_pdf_error"] = str(exc)

        return output
