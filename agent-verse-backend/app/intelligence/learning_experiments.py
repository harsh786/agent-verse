"""Durable-compatible sticky experiment assignment and promotion gates."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from app.memory.contracts import ExperimentSpec


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    assignment_id: str
    arm: str
    primary_metric: float
    guardrails_passed: bool


class LearningExperimentService:
    def __init__(self) -> None:
        self._specs: dict[tuple[str, str], ExperimentSpec] = {}
        self._outcomes: dict[tuple[str, str], ExperimentOutcome] = {}
        self._active_targets: dict[tuple[str, str, str], str] = {}

    def register(self, spec: ExperimentSpec) -> ExperimentSpec:
        key = (spec.tenant_id, spec.experiment_id)
        prior = self._specs.get(key)
        if prior is not None and prior != spec:
            raise ValueError("experiment spec is immutable")
        target = (spec.tenant_id, spec.kind, spec.target_key)
        active = self._active_targets.get(target)
        if spec.status == "running" and active not in {None, spec.experiment_id}:
            raise ValueError("an active experiment already owns this target")
        self._specs[key] = spec
        if spec.status == "running":
            self._active_targets[target] = spec.experiment_id
        return prior or spec

    def assign(self, spec: ExperimentSpec, *, fingerprint: str) -> tuple[str, str]:
        digest = hashlib.sha256(
            f"{spec.assignment_seed}:{spec.tenant_id}:{fingerprint}".encode()
        ).hexdigest()
        bucket = int(digest[:8], 16) % 100
        arm = (
            "candidate"
            if spec.status == "running" and not spec.kill_switch and bucket < spec.traffic_percent
            else "control"
        )
        return digest[:32], arm

    def record(self, tenant_id: str, outcome: ExperimentOutcome) -> ExperimentOutcome:
        key = (tenant_id, outcome.assignment_id)
        prior = self._outcomes.get(key)
        if prior is not None and prior != outcome:
            raise ValueError("experiment outcome is immutable")
        self._outcomes[key] = outcome
        return prior or outcome

    def promotion_ready(self, spec: ExperimentSpec) -> bool:
        arms: dict[str, list[ExperimentOutcome]] = defaultdict(list)
        for (tenant_id, _), outcome in self._outcomes.items():
            if tenant_id != spec.tenant_id:
                continue
            arms[outcome.arm].append(outcome)
        if any(len(arms[name]) < spec.min_samples_per_arm for name in ("control", "candidate")):
            return False
        if not all(item.guardrails_passed for item in arms["candidate"]):
            return False
        candidate = sum(item.primary_metric for item in arms["candidate"]) / len(arms["candidate"])
        control = sum(item.primary_metric for item in arms["control"]) / len(arms["control"])
        return candidate > control


__all__ = ["ExperimentOutcome", "LearningExperimentService"]
