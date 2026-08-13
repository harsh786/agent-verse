"""Field-level minimization and taint preservation across coordination hops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.intelligence.guardrails import GuardrailChecker


@dataclass(frozen=True, slots=True)
class DisclosedContext:
    fields: dict[str, Any]
    redacted_fields: tuple[str, ...]
    source_digest: str
    provenance_chain: tuple[str, ...]
    tainted: bool
    trust_label: str


def disclose_context(
    context: dict[str, Any],
    *,
    allowed_fields: frozenset[str],
    recipient_clearance: frozenset[str],
    field_classifications: dict[str, str],
    provenance_chain: tuple[str, ...] = (),
    inherited_taint: bool = False,
) -> DisclosedContext:
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
    safe: dict[str, Any] = {}
    redacted: list[str] = []
    injection = False
    guardrail = GuardrailChecker()
    for key in sorted(context):
        classification = field_classifications.get(key, "internal")
        if key not in allowed_fields or classification not in recipient_clearance:
            redacted.append(key)
            continue
        value = context[key]
        if isinstance(value, str) and guardrail.check_goal(value):
            safe[key] = {"quoted_untrusted_data": "[REDACTED]"}
            injection = True
        else:
            safe[key] = value
    tainted = inherited_taint or injection
    return DisclosedContext(
        fields=safe,
        redacted_fields=tuple(redacted),
        source_digest=hashlib.sha256(canonical.encode()).hexdigest(),
        provenance_chain=provenance_chain,
        tainted=tainted,
        trust_label="untrusted" if tainted else "trusted",
    )


__all__ = ["DisclosedContext", "disclose_context"]
