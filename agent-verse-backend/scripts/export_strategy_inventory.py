"""Generate the normative six-phase strategy inventory and capability table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.orchestration.strategy_certification import (
    REQUIRED_CERTIFICATION_CATEGORIES,
    CertificationEvaluator,
)
from app.orchestration.strategy_registry import (
    DEFAULT_STRATEGY_ALIASES,
    StrategyCategory,
    build_default_registry,
)

ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs/architecture/agent-pattern-capability-inventory.json"
CAPABILITIES_PATH = ROOT / "docs/CAPABILITIES.md"
SOURCE = "agent-verse-backend/app/orchestration/strategy_registry.py"

PHASES = {
    StrategyCategory.AGENT: 2,
    StrategyCategory.RAG: 3,
    StrategyCategory.SAFETY: 5,
    StrategyCategory.MEMORY: 6,
    StrategyCategory.OPTIMISATION: 5,
}
PROGRAMS = {
    StrategyCategory.AGENT: "01",
    StrategyCategory.RAG: "12",
    StrategyCategory.SAFETY: "10",
    StrategyCategory.MEMORY: "11",
    StrategyCategory.OPTIMISATION: "10",
}


def build_rows() -> list[dict[str, object]]:
    registry = build_default_registry()
    aliases_by_owner: dict[str, list[str]] = {}
    for alias, owner in DEFAULT_STRATEGY_ALIASES:
        aliases_by_owner.setdefault(owner, []).append(alias)
    certification = CertificationEvaluator()
    rows: list[dict[str, object]] = []
    for capability in registry.list_all():
        actual = certification.derive_state(capability, ()).state.value
        intended = (
            "implemented"
            if capability.adapter_descriptor is not None
            else capability.state.value
        )
        rows.append(
            {
                "canonical_id": capability.strategy_id,
                "aliases": sorted(aliases_by_owner.get(capability.strategy_id, [])),
                "learning_phase": PHASES[capability.category],
                "current_registry_state": capability.state.value,
                "actual_production_state": actual,
                "source_evidence": SOURCE,
                "owner": {
                    "program": PROGRAMS[capability.category],
                    "task": "capability implementation and certification",
                },
                "intended_final_state": intended,
                "deprecation_rationale": None,
                "dependencies": list(capability.spec.dependencies),
                "readiness_requirements": list(capability.readiness_requirements),
                "certification_requirements": sorted(REQUIRED_CERTIFICATION_CATEGORIES),
            }
        )
    return sorted(rows, key=lambda row: (row["learning_phase"], row["canonical_id"]))


def render_inventory(rows: list[dict[str, object]]) -> str:
    return json.dumps(
        {"schema_version": 1, "generated_from": SOURCE, "capabilities": rows},
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_capabilities(rows: list[dict[str, object]]) -> str:
    lines = [
        "<!-- BEGIN GENERATED STRATEGY RUNTIME -->",
        "## Strategy Runtime (generated)",
        "",
        "Generated from executable registry metadata and evidence-derived state. Do not edit.",
        "",
        "| Capability | Version | Registry state | Production state | Readiness |",
        "|---|---:|---|---|---|",
    ]
    registry = build_default_registry()
    by_id = {item.strategy_id: item for item in registry.list_all()}
    for row in rows:
        capability = by_id[str(row["canonical_id"])]
        lines.append(
            f"| `{capability.strategy_id}` | `{capability.adapter_version}` | "
            f"{capability.state.value} | {row['actual_production_state']} | "
            f"{', '.join(capability.readiness_requirements)} |"
        )
    lines.extend(["", "<!-- END GENERATED STRATEGY RUNTIME -->"])
    return "\n".join(lines) + "\n"


def merge_capabilities(existing: str, generated: str) -> str:
    start = "<!-- BEGIN GENERATED STRATEGY RUNTIME -->"
    end = "<!-- END GENERATED STRATEGY RUNTIME -->"
    if start in existing and end in existing:
        prefix, remainder = existing.split(start, 1)
        _, suffix = remainder.split(end, 1)
        return prefix + generated.rstrip("\n") + suffix
    return existing.rstrip() + "\n\n" + generated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = build_rows()
    generated_capabilities = render_capabilities(rows)
    existing_capabilities = (
        CAPABILITIES_PATH.read_text() if CAPABILITIES_PATH.exists() else "# Capabilities\n"
    )
    outputs = {
        INVENTORY_PATH: render_inventory(rows),
        CAPABILITIES_PATH: merge_capabilities(
            existing_capabilities,
            generated_capabilities,
        ),
    }
    if args.check:
        stale = [
            path
            for path, content in outputs.items()
            if not path.exists() or path.read_text() != content
        ]
        if stale:
            raise SystemExit(f"generated strategy files are stale: {', '.join(map(str, stale))}")
        return
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


if __name__ == "__main__":
    main()
