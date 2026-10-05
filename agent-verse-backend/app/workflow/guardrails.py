"""Guardrails for workflow steps that call out of the platform (P8b-2).

Only the agent graph and the governed tool gate evaluated guardrails; a
workflow ``llm`` / ``rag`` step sent its prompt to the model and returned the
answer, and an ``http`` step sent its request and returned the response body,
all unscreened — no tenant rule and no baseline applied, and nothing was
recorded.

:func:`screen_step_content` runs the tenant's rules plus the platform baseline
(``GuardrailsEngine.evaluate``) on one piece of step traffic:

========================  =========================  ==============
traffic                   layer                      baseline
========================  =========================  ==============
LLM prompt (input)        ``step``                   prompt injection
LLM answer (output)       ``tool_output``            secret formats
HTTP request (url+body)   ``tool_args``              injection, secrets
HTTP response body        ``tool_output``            secret formats
========================  =========================  ==============

* ``block`` / ``quarantine`` / ``require_hitl`` → :class:`WorkflowGuardrailBlockedError`:
  the step fails (nothing is sent / nothing is returned) with an honest error
  naming the rules, never the content; it is not retried.
* ``redact`` → the redacted text is what is sent / returned.
* Violations are recorded by the engine in the durable guardrails-v2 store
  (``guardrail_violations``, ``goal_id = workflow:<run_id>``).
* A check that cannot run (the tenant's rules cannot be loaded, the engine
  errors) raises :class:`WorkflowGuardrailUnavailableError` — fail closed.
* Prompt-injection rules evaluate only the untrusted part of the traffic (the
  values interpolated into the author's template, retrieved context), so the
  author's own wording ("Act as a reviewer") is never mistaken for an injection.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.guardrails_v2.models import GuardrailLayer
from app.observability.logging import get_logger

_log = get_logger(__name__)

_EXPR_RE = re.compile(r"\{\{[^}]+\}\}")


class WorkflowGuardrailBlockedError(RuntimeError):
    """A guardrail rule blocked a workflow step's input or output."""


class WorkflowGuardrailUnavailableError(RuntimeError):
    """The guardrail check for a workflow step could not be completed."""


def _interpolated_string(template: str, resolved: str) -> str:
    """The parts of *resolved* that are not the template's own literal text."""
    statics = [s for s in _EXPR_RE.split(template) if s]
    if not _EXPR_RE.search(template):
        return "" if template == resolved else resolved
    parts: list[str] = []
    pos = 0
    for literal in statics:
        idx = resolved.find(literal, pos)
        if idx < 0:
            return resolved  # cannot align: treat everything as untrusted
        parts.append(resolved[pos:idx])
        pos = idx + len(literal)
    parts.append(resolved[pos:])
    return "\n".join(p for p in parts if p)


def interpolated_text(template: Any, resolved: Any) -> str:
    """The untrusted (interpolated) text of *resolved*, given its *template*."""
    if isinstance(template, str):
        if isinstance(resolved, str):
            return _interpolated_string(template, resolved)
        return json.dumps(resolved, default=str) if _EXPR_RE.search(template) else ""
    if isinstance(template, dict) and isinstance(resolved, dict):
        return "\n".join(
            filter(None, (interpolated_text(template.get(k), v) for k, v in resolved.items()))
        )
    if isinstance(template, list | tuple) and isinstance(resolved, list | tuple):
        pairs = zip(template, resolved, strict=False)
        return "\n".join(filter(None, (interpolated_text(t, r) for t, r in pairs)))
    if template == resolved:
        return ""
    return resolved if isinstance(resolved, str) else json.dumps(resolved, default=str)


def _engine() -> Any:
    from app.guardrails_v2 import engine as engine_mod

    return engine_mod.guardrails_engine


async def screen_step_content(
    content: str,
    *,
    layer: GuardrailLayer,
    state: Any,
    step_id: str,
    step_type: str,
    direction: str,
    untrusted: str | None = None,
) -> str:
    """Screen one piece of a workflow step's traffic; return the text to use.

    Raises :class:`WorkflowGuardrailBlockedError` on a blocking verdict and
    :class:`WorkflowGuardrailUnavailableError` when the check cannot run.
    """
    tenant_id = str((state or {}).get("tenant_id") or "")
    if not tenant_id:
        raise WorkflowGuardrailUnavailableError(
            f"{step_type} step {step_id!r}: no tenant to evaluate guardrails for"
        )
    run_id = str((state or {}).get("run_id") or "")
    engine = _engine()
    try:
        engine.ensure_default_rules(tenant_id)
        verdict: dict[str, Any] = await engine.evaluate(
            content=content,
            layer=layer,
            tenant_id=tenant_id,
            goal_id=f"workflow:{run_id}" if run_id else None,
            step_description=f"workflow {step_type} step {step_id} ({direction})",
            injection_content=untrusted,
        )
    except Exception as exc:
        _log.warning(
            "workflow_guardrail_unavailable",
            step_id=step_id,
            direction=direction,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        raise WorkflowGuardrailUnavailableError(
            f"{step_type} step {step_id!r}: the guardrail check on its {direction} "
            f"could not be completed ({type(exc).__name__}); the step failed closed"
        ) from exc
    rules = sorted(
        {str(v.get("rule_name") or "policy") for v in verdict.get("violations") or []}
    )
    if verdict.get("blocked") or verdict.get("hitl_required"):
        _log.warning(
            "workflow_guardrail_blocked", step_id=step_id, direction=direction, rules=rules
        )
        raise WorkflowGuardrailBlockedError(
            f"{step_type} step {step_id!r}: its {direction} was blocked by guardrail "
            f"policy ({', '.join(rules) or 'policy'})"
        )
    redacted = verdict.get("redacted_content")
    if isinstance(redacted, str) and redacted != content:
        _log.info("workflow_guardrail_redacted", step_id=step_id, direction=direction, rules=rules)
        return redacted
    return content


async def screen_step_json(
    value: Any,
    *,
    layer: GuardrailLayer,
    state: Any,
    step_id: str,
    step_type: str,
    direction: str,
    untrusted: str | None = None,
) -> Any:
    """:func:`screen_step_content` for a JSON value (an HTTP body)."""
    text = json.dumps(value, default=str)
    screened = await screen_step_content(
        text,
        layer=layer,
        state=state,
        step_id=step_id,
        step_type=step_type,
        direction=direction,
        untrusted=untrusted,
    )
    if screened == text:
        return value
    try:
        return json.loads(screened)
    except ValueError as exc:
        # The redaction cut through the JSON: nothing redacted can be used.
        raise WorkflowGuardrailBlockedError(
            f"{step_type} step {step_id!r}: its {direction} could not be redacted safely"
        ) from exc


_VAULT_MARKER = re.compile(r"\[vault:([^\]]+)\]")


def unmask_vault(value: Any, resolve_secret: Any) -> Any:
    """Put the vault secrets back into a value screened with ``[vault:NAME]`` markers.

    The request is screened as rendered WITHOUT the vault (secrets the author
    deliberately sends — an API key in the URL — are not the traffic being
    policed); a redacted request is re-filled with them before it is sent.
    """
    if isinstance(value, str):
        return _VAULT_MARKER.sub(lambda m: str(resolve_secret(m.group(1))), value)
    if isinstance(value, dict):
        return {k: unmask_vault(v, resolve_secret) for k, v in value.items()}
    if isinstance(value, list):
        return [unmask_vault(v, resolve_secret) for v in value]
    return value


__all__ = [
    "WorkflowGuardrailBlockedError",
    "WorkflowGuardrailUnavailableError",
    "interpolated_text",
    "screen_step_content",
    "screen_step_json",
    "unmask_vault",
]
