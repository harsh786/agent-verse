"""Structured execution plan — parses LLM output into topologically sortable steps."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredStep:
    """A single step in a structured execution plan."""

    id: str
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    risk: str = "read"
    expected_output: str = ""


@dataclass
class StructuredPlan:
    """An ordered set of :class:`StructuredStep` objects with dependency information."""

    steps: list[StructuredStep] = field(default_factory=list)

    @classmethod
    def from_llm_response(cls, text: str) -> StructuredPlan:
        """Parse an LLM response into a :class:`StructuredPlan`.

        Accepts two formats:
        1. Structured JSON:  ``{"steps": [{...}, ...]}``
        2. Legacy text list: numbered / bulleted lines
        """
        text = text.strip()

        # ── try structured JSON first ──────────────────────────────────────
        try:
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                data = json.loads(json_match.group())
                if "steps" in data and isinstance(data["steps"], list):
                    steps: list[StructuredStep] = []
                    for raw in data["steps"]:
                        if isinstance(raw, dict):
                            steps.append(
                                StructuredStep(
                                    id=str(raw.get("id", f"s{len(steps) + 1}")),
                                    description=str(raw.get("description", "")),
                                    tool=raw.get("tool") or None,
                                    arguments=dict(raw.get("arguments") or {}),
                                    depends_on=list(raw.get("depends_on") or []),
                                    risk=str(raw.get("risk", "read")),
                                    expected_output=str(raw.get("expected_output", "")),
                                )
                            )
                        elif isinstance(raw, str):
                            # Legacy string inside a JSON steps array
                            steps.append(
                                StructuredStep(id=f"s{len(steps) + 1}", description=raw)
                            )
                    return cls(steps=steps)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # ── legacy: plain text list ────────────────────────────────────────
        steps = []
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            # Strip leading markers: "1.", "- ", "Step 1:", etc.
            line = re.sub(
                r"^(step\s*\d+:?|\d+[\.\)]\s*|-\s*)",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            if line:
                steps.append(StructuredStep(id=f"s{i + 1}", description=line))

        return cls(steps=steps)

    def execution_waves(self) -> list[list[StructuredStep]]:
        """Return steps grouped into topological execution waves.

        Steps within the same wave have no un-met dependencies and can
        execute in parallel.  Each successive wave depends on all prior waves
        having completed.
        """
        if not self.steps:
            return []

        completed: set[str] = set()
        remaining = list(self.steps)
        waves: list[list[StructuredStep]] = []

        # Guard against infinite loops caused by cycles or missing dep IDs.
        max_iterations = len(self.steps) + 1
        iterations = 0

        while remaining:
            iterations += 1
            if iterations > max_iterations:
                # Cycle detected — dump everything into a final wave.
                waves.append(remaining)
                break

            wave = [s for s in remaining if all(dep in completed for dep in s.depends_on)]
            if not wave:
                # No forward progress possible — dump remaining as last wave.
                waves.append(remaining)
                break

            waves.append(wave)
            completed.update(s.id for s in wave)
            remaining = [s for s in remaining if s.id not in completed]

        return waves

    def to_step_list(self) -> list[str]:
        """Backward-compatible conversion to ``list[str]`` of step descriptions."""
        return [s.description for s in self.steps]
