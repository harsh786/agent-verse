"""Automatic per-goal selection of multi-agent patterns (WS-10 item 2).

Supervisor decomposition and multi-agent debate previously engaged ONLY via
per-agent boolean flags (``enable_supervisor`` / ``enable_debate``) — never
automatically from the goal's own characteristics. Reasoning patterns (ToT,
self-consistency, peer-review) already auto-select via the runtime profile;
this closes the same gap for the multi-agent dimension with ONE reachable,
characteristic-driven selector.

The selector is deliberately conservative: it only routes genuinely complex /
high-stakes goals, so the advanced multi-agent tier stays quiet for ordinary
work. Engagement of the *distributed autonomous* tier remains governed by the
existing ``coordination_ready`` gate and the default-off
``agent_auto_multi_agent_enabled`` setting — this selector decides *which*
pattern a goal warrants (real, observable), while those gates decide *whether*
it may run (safe by default).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Complexities considered "big enough" to benefit from decomposition/deliberation.
_ADVANCED_COMPLEXITIES = frozenset({"complex", "expert"})
# Domains where parallel decomposition / adversarial cross-examination pays off.
_DECOMPOSABLE_DOMAINS = frozenset({"technical", "analytical", "operational"})
_HIGH_RISK = frozenset({"high", "critical"})


@dataclass(frozen=True, slots=True)
class MultiAgentSelection:
    """Result of multi-agent auto-selection for one goal."""

    patterns: frozenset[str] = field(default_factory=frozenset)
    reasons: tuple[tuple[str, str], ...] = ()

    @property
    def supervisor(self) -> bool:
        return "supervisor" in self.patterns

    @property
    def debate(self) -> bool:
        return "debate" in self.patterns


def select_multi_agent_patterns(
    *,
    complexity: str,
    domain: str,
    multi_step: bool,
    risk: str,
) -> MultiAgentSelection:
    """Decide the multi-agent pattern(s) a goal warrants from its properties.

    Pure and side-effect free so it can be reused by any execution seam and
    unit-tested in isolation.

    - ``supervisor``: a complex/expert multi-step goal in a decomposable domain —
      it pays to split the goal across delegated sub-agents.
    - ``debate``: an expert goal that is either analytical or high/critical risk —
      contested or high-stakes reasoning benefits from adversarial cross-checking.
    """
    complexity_l = (complexity or "").lower()
    domain_l = (domain or "").lower()
    risk_l = (risk or "").lower()

    patterns: set[str] = set()
    reasons: list[tuple[str, str]] = []

    if (
        complexity_l in _ADVANCED_COMPLEXITIES
        and multi_step
        and domain_l in _DECOMPOSABLE_DOMAINS
    ):
        patterns.add("supervisor")
        reasons.append(
            ("supervisor", f"complexity={complexity_l} multi_step domain={domain_l}")
        )

    if complexity_l == "expert" and (domain_l == "analytical" or risk_l in _HIGH_RISK):
        patterns.add("debate")
        reasons.append(
            ("debate", f"complexity=expert domain={domain_l} risk={risk_l}")
        )

    return MultiAgentSelection(patterns=frozenset(patterns), reasons=tuple(reasons))
