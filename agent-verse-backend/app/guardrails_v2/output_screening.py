"""Screening of every user-facing output of a goal (P8b-1).

The baseline ``FINAL_OUTPUT`` PII rule only ever replaced ``cited_answer``.
``GET /goals/{id}``'s ``result_artifact`` is the last step's output, and the
per-step outputs reach clients through the goal's events (GET /goals/{id},
``/events``, the SSE stream, replay / timeline, A2A artifacts, the chat
bridge) — so a step output carrying an email, phone, card number or secret was
served verbatim to anyone reading the goal.

Policy (one for the API and the worker):

* **Redact before persisting.** Every goal event is screened by
  :func:`screen_goal_event` in the two places events are written —
  ``GoalService._dispatch_event`` (API) and ``run_goal``'s
  ``append_submitted_goal_event`` (Celery worker) — before it is stored in the
  event log, published to Redis (SSE / other replicas) or kept in memory. Every
  read surface derives from those events, so none of them sees the raw value;
  raw secrets never reach ``goal_events``. Screened events carry
  ``"_screened": true``.
* **Redact-on-read backstop for history written before this policy.** The
  event-store readers pass any event WITHOUT ``_screened`` through
  :func:`redact_legacy_event` (baseline PII + secret redaction, deterministic,
  no rule lookup, no violation rows).
* **What is screened.** Every string in an event except structural fields
  (``type``, ids, timestamps, tool / model names, the user's own ``goal`` text):
  outputs, answers, summaries, errors, plan steps.
* **Baseline.** Secrets are always redacted. PII (email, phone, card, SSN) is
  span-redacted (``***REDACTED***``) — unless the tenant has DISABLED its
  baseline PII output rule (``gr-default:<tenant>:pii-final-output``).
* **Tenant rules.** The tenant's own enabled rules on the ``final_output`` or
  ``tool_output`` layer (bundle rules included, baseline defaults excluded)
  are evaluated on the raw text: ``block`` / ``quarantine`` /
  ``require_hitl`` withhold the field, ``redact`` redacts the matched spans
  (a rule whose match cannot be located withholds the field), ``log`` / ``warn``
  leave it. Toxicity rules use the deterministic classifier here, never an LLM
  per event. No violation rows are written at this boundary — the step-output
  check in the executor records them once per output.
* **Fail closed.** When the tenant's rules cannot be loaded every screened field
  is withheld (never served unscreened).
* **Live token stream.** ``token_chunk`` events carry the cumulative streamed
  text. It is baseline-redacted and its last ``_TOKEN_HOLDBACK`` characters are
  held back (a PII value still arriving cannot be shown half-typed); ``token``
  is recomputed as the delta of the screened text. A tenant with its own output
  rules gets no live token stream (``None`` — the caller drops the event): the
  screened ``step_complete`` carries the output.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.sanitization import redact_sensitive_text
from app.guardrails_v2.engine import (
    _PII_PATTERNS,
    _SECRET_PATTERNS,
    GuardrailRulesUnavailableError,
)
from app.guardrails_v2.models import GuardrailAction, GuardrailLayer, GuardrailRule

_log = logging.getLogger(__name__)

REDACTED = "***REDACTED***"
BLOCKED_OUTPUT = "[Output blocked by guardrail policy]"
WITHHELD_OUTPUT = "[Output withheld: the guardrail check could not be completed]"
SCREENED_FLAG = "_screened"

# Characters of the streamed text held back from a live token_chunk: longer than
# any PII / secret value the baseline recognises, so one still arriving is never
# shown before it is complete (and redacted).
_TOKEN_HOLDBACK = 96

_OUTPUT_LAYERS = frozenset({GuardrailLayer.FINAL_OUTPUT, GuardrailLayer.TOOL_OUTPUT})
_BLOCKING_ACTIONS = frozenset(
    {GuardrailAction.BLOCK, GuardrailAction.QUARANTINE, GuardrailAction.REQUIRE_HITL}
)

# Structural fields: identifiers, enums and timestamps clients key on. Redacting
# them would break the UI (an approval id, a step id) and they carry no output.
_STRUCTURAL_KEYS = frozenset(
    {
        "type",
        "_seq",
        SCREENED_FLAG,
        "seq",
        "sequence",
        "status",
        "ts",
        "timestamp",
        "tool",
        "tool_name",
        "server_id",
        "model",
        "provider",
        # The user's own submitted goal text (GET /goals/{id} returns it as is).
        "goal",
    }
)

_PII_REGEXES = [re.compile(p) for p, _ in _PII_PATTERNS]
_SECRET_REGEXES = [re.compile(p) for p, _ in _SECRET_PATTERNS]


def _is_structural(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return key in _STRUCTURAL_KEYS or key == "id" or key.endswith(("_id", "_ids", "_at"))


def redact_baseline(text: str, *, pii: bool = True) -> str:
    """Baseline redaction: secrets always, PII spans unless *pii* is off."""
    out = redact_sensitive_text(text)
    for pattern in _SECRET_REGEXES:
        out = pattern.sub(REDACTED, out)
    if pii:
        for pattern in _PII_REGEXES:
            out = pattern.sub(REDACTED, out)
    return out


def _map_strings(value: Any, fn: Any) -> Any:
    """Apply *fn* to every non-structural string inside *value*."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {
            k: (v if _is_structural(k) else _map_strings(v, fn)) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_map_strings(v, fn) for v in value]
    return value


def redact_legacy_event(event: dict[str, Any]) -> dict[str, Any]:
    """Read-side backstop for an event stored before write-time screening.

    An event already screened when it was written is returned unchanged (its
    tenant's policy applied then); any other gets the baseline redaction.
    """
    if not isinstance(event, dict) or event.get(SCREENED_FLAG):
        return event
    redacted: dict[str, Any] = _map_strings(event, redact_baseline)
    return redacted


@dataclass
class OutputPolicy:
    """What applies to one tenant's user-facing output."""

    pii_baseline: bool = True
    tenant_rules: list[GuardrailRule] = field(default_factory=list)
    unavailable: bool = False


def _engine(engine: Any) -> Any:
    if engine is not None:
        return engine
    from app.guardrails_v2 import engine as engine_mod

    return engine_mod.guardrails_engine


async def load_output_policy(tenant_id: str, *, engine: Any = None) -> OutputPolicy:
    """The tenant's output policy; ``unavailable`` when its rules cannot be read."""
    eng = _engine(engine)
    if not tenant_id:
        return OutputPolicy()
    try:
        await eng.ensure_tenant_loaded(tenant_id)
    except GuardrailRulesUnavailableError as exc:
        _log.warning("output_screening_rules_unavailable tenant=%s: %s", tenant_id, exc)
        return OutputPolicy(unavailable=True)
    except Exception as exc:
        _log.warning("output_screening_rules_load_failed tenant=%s: %s", tenant_id, exc)
        return OutputPolicy(unavailable=True)
    baseline_pii_id = f"gr-default:{tenant_id}:pii-final-output"
    pii_baseline = True
    rules: list[GuardrailRule] = []
    for rule in eng.all_rules(tenant_id):
        if rule.rule_id == baseline_pii_id:
            pii_baseline = bool(rule.enabled)
            continue
        if not rule.enabled or rule.rule_id.startswith("gr-default:"):
            continue
        if _OUTPUT_LAYERS.intersection(rule.layers):
            rules.append(rule)
    return OutputPolicy(pii_baseline=pii_baseline, tenant_rules=rules)


def _redact_rule_match(text: str, rule: GuardrailRule, result: dict[str, Any]) -> str | None:
    """*text* with *rule*'s matches redacted; ``None`` when they cannot be located."""
    if rule.rule_type == "pii_detection":
        return redact_baseline(text, pii=True)
    if rule.rule_type == "keyword_block":
        out = text
        for kw in rule.config.get("keywords", []) or []:
            if isinstance(kw, str) and kw:
                out = re.sub(re.escape(kw), REDACTED, out, flags=re.IGNORECASE)
        return out
    if rule.rule_type == "regex_match":
        pattern = rule.config.get("pattern", "")
        if not pattern or any(m in ("regex_timeout", "invalid_pattern") for m in
                              result.get("matches", [])):
            return None
        try:
            import regex as _regex_engine

            sub: str = _regex_engine.sub(pattern, REDACTED, text, timeout=0.25)
        except Exception:
            return None
        return sub
    return None  # injection / toxicity: no span to cut out


async def screen_text(text: str, policy: OutputPolicy, *, engine: Any = None) -> str:
    """One user-facing string under *policy* (see the module docstring)."""
    if not text:
        return text
    if policy.unavailable:
        return WITHHELD_OUTPUT
    out = text
    if policy.tenant_rules:
        eng = _engine(engine)
        for rule in policy.tenant_rules:
            try:
                result = await eng.match_rule(rule, text, use_llm=False)
            except Exception as exc:
                _log.warning("output_screening_rule_failed rule=%s: %s", rule.rule_id, exc)
                return WITHHELD_OUTPUT
            if not result.get("triggered"):
                continue
            if rule.action in _BLOCKING_ACTIONS:
                return BLOCKED_OUTPUT
            if rule.action == GuardrailAction.REDACT:
                redacted = _redact_rule_match(out, rule, result)
                if redacted is None:
                    return BLOCKED_OUTPUT
                out = redacted
    return redact_baseline(out, pii=policy.pii_baseline)


async def _screen_value(value: Any, policy: OutputPolicy, engine: Any) -> Any:
    if isinstance(value, str):
        return await screen_text(value, policy, engine=engine)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            out[k] = v if _is_structural(k) else await _screen_value(v, policy, engine)
        return out
    if isinstance(value, list | tuple):
        return [await _screen_value(v, policy, engine) for v in value]
    return value


_WHITESPACE = re.compile(r"\s")


def _held_back(raw: str, *, pii: bool) -> str:
    """The screened part of the streamed text that is safe to show now.

    The last ``_TOKEN_HOLDBACK`` raw characters are held back; the cut is moved
    to a whitespace boundary and never lands inside a value the baseline
    redacts, so a value is either shown whole-and-redacted or not at all, and
    each shown text extends the previous one.
    """
    cut = len(raw) - _TOKEN_HOLDBACK
    if cut <= 0:
        return ""
    spaces = [m.end() for m in _WHITESPACE.finditer(raw, 0, cut)]
    cut = spaces[-1] if spaces else 0
    patterns = _SECRET_REGEXES + (_PII_REGEXES if pii else [])
    moved = True
    while moved and cut > 0:
        moved = False
        for pattern in patterns:
            for m in pattern.finditer(raw):
                if m.start() < cut < m.end():
                    cut, moved = m.start(), True
    return redact_baseline(raw[:cut], pii=pii)


def _screen_token_chunk(event: dict[str, Any], policy: OutputPolicy) -> dict[str, Any] | None:
    if policy.unavailable or policy.tenant_rules:
        return None
    cumulative = str(event.get("cumulative") or "")
    token = str(event.get("token") or "")
    previous = cumulative[: len(cumulative) - len(token)] if cumulative.endswith(token) else ""
    shown = _held_back(cumulative, pii=policy.pii_baseline)
    shown_before = _held_back(previous, pii=policy.pii_baseline)
    delta = shown[len(shown_before):] if shown.startswith(shown_before) else ""
    screened = {
        k: (v if _is_structural(k) or k in ("cumulative", "token") else _map_strings(
            v, lambda s: redact_baseline(s, pii=policy.pii_baseline)))
        for k, v in event.items()
    }
    screened["cumulative"] = shown
    screened["token"] = delta
    screened[SCREENED_FLAG] = True
    return screened


async def screen_goal_event(
    event: dict[str, Any], tenant_id: str, *, engine: Any = None
) -> dict[str, Any] | None:
    """*event* with every user-facing string screened; ``None`` = drop it.

    Never raises: an unexpected error withholds every screened field.
    """
    try:
        policy = await load_output_policy(tenant_id, engine=engine)
        if event.get("type") == "token_chunk":
            return _screen_token_chunk(event, policy)
        screened: dict[str, Any] = await _screen_value(event, policy, engine)
    except Exception as exc:
        _log.error("output_screening_failed tenant=%s: %s", tenant_id, exc)
        if event.get("type") == "token_chunk":
            return None
        screened = _map_strings(event, lambda s: WITHHELD_OUTPUT if s else s)
    screened[SCREENED_FLAG] = True
    return screened


__all__ = [
    "BLOCKED_OUTPUT",
    "REDACTED",
    "SCREENED_FLAG",
    "WITHHELD_OUTPUT",
    "OutputPolicy",
    "load_output_policy",
    "redact_baseline",
    "redact_legacy_event",
    "screen_goal_event",
    "screen_text",
]
