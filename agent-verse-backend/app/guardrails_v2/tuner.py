"""GuardrailTuner — corpus-driven effectiveness analysis for guardrail rules.

Uses :meth:`GuardrailEngine.simulate` (dry-run, records no violations) over a
labelled corpus to measure how well a tenant's guardrail rules separate benign
from malicious content, and to recommend tuning:

* rules that fire on *benign* samples are over-aggressive (false positives);
* malicious samples that pass every rule are coverage gaps (false negatives).

This turns ``simulate`` from a one-shot dry-run into the feedback signal that
lets an operator tune thresholds/rules with evidence instead of guesswork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CorpusSample:
    """One labelled example. ``should_block`` is the ground truth."""

    content: str
    should_block: bool
    layer: str = "final_output"


@dataclass
class GuardrailEffectivenessReport:
    total: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    # rule name → number of benign samples it wrongly triggered on.
    over_aggressive_rules: dict[str, int] = field(default_factory=dict)
    # malicious samples that passed every rule.
    missed_attacks: int = 0
    recommendations: list[str] = field(default_factory=list)


class GuardrailTuner:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def evaluate_corpus(
        self, tenant_id: str, samples: list[CorpusSample]
    ) -> GuardrailEffectivenessReport:
        tp = fp = tn = fn = 0
        over: dict[str, int] = {}

        for sample in samples:
            sim = await self._engine.simulate(sample.content, sample.layer, tenant_id)
            # Any enforcement action (block or HITL) counts as "would stop it".
            enforced = bool(sim.get("would_block") or sim.get("would_require_hitl"))

            if sample.should_block and enforced:
                tp += 1
            elif sample.should_block and not enforced:
                fn += 1
            elif (not sample.should_block) and enforced:
                fp += 1
                for triggered in sim.get("triggered_rules", []):
                    name = str(triggered.get("rule_name", "unknown"))
                    over[name] = over.get(name, 0) + 1
            else:
                tn += 1

        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        recommendations: list[str] = []
        for name, count in sorted(over.items(), key=lambda kv: -kv[1]):
            recommendations.append(
                f"Rule '{name}' fired on {count} benign sample(s) — relax it or narrow "
                f"its scope to cut false positives."
            )
        if fn:
            recommendations.append(
                f"{fn} malicious sample(s) passed every rule — add or tighten a rule to "
                f"cover this attack class."
            )
        if not recommendations:
            recommendations.append("No tuning needed: no false positives or missed attacks.")

        return GuardrailEffectivenessReport(
            total=len(samples),
            true_positives=tp,
            false_positives=fp,
            true_negatives=tn,
            false_negatives=fn,
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(f1, 4),
            over_aggressive_rules=over,
            missed_attacks=fn,
            recommendations=recommendations,
        )
