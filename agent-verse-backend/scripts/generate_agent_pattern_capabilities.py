#!/usr/bin/env python3
"""Generate capability truth from the sealed runtime registry and derived evidence state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.orchestration.strategy_certification import CertificationEvaluator
from app.orchestration.strategy_registry import StrategyCategory, build_default_registry

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "docs/generated/agent-pattern-capabilities.json"
DEFAULT_MARKDOWN = ROOT / "docs/generated/agent-pattern-capabilities.md"
PROGRAMS = {
    StrategyCategory.AGENT: "01-11",
    StrategyCategory.RAG: "12",
    StrategyCategory.SAFETY: "10",
    StrategyCategory.MEMORY: "11",
    StrategyCategory.OPTIMISATION: "10",
}
PHASES = {
    StrategyCategory.AGENT: 2,
    StrategyCategory.RAG: 3,
    StrategyCategory.SAFETY: 5,
    StrategyCategory.MEMORY: 6,
    StrategyCategory.OPTIMISATION: 5,
}


def build_manifest() -> dict[str, Any]:
    registry = build_default_registry()
    evaluator = CertificationEvaluator()
    rows: list[dict[str, Any]] = []
    for capability in registry.list_all():
        derived = evaluator.derive_state(capability, ())
        coordination_surface = capability.strategy_id in {
            "magentic_one", "mixture_of_agents", "camel", "generative_agents",
            "decentralized_swarm", "market_auction",
        }
        rows.append(
            {
                "capability_id": capability.strategy_id,
                "family": capability.spec.family.value,
                "learning_phase": PHASES[capability.category],
                "adapter_version": capability.adapter_version,
                "implementation_state": capability.state.value,
                "operational_readiness": derived.state.value,
                "certification_state": (
                    "certified" if derived.state.value == "certified" else "not_certified"
                ),
                "required_dependencies": list(capability.spec.dependencies),
                "readiness_requirements": list(capability.readiness_requirements),
                "surfaces": {
                    "api": coordination_surface,
                    "python_sdk": coordination_surface,
                    "typescript_sdk": coordination_surface,
                    "frontend": coordination_surface,
                },
                "safe_explanation_fields": [
                    "status", "reason_codes", "evidence", "cost", "limits"
                ],
                "limits": capability.default_limits.model_dump(mode="json"),
                "owner": {
                    "program": PROGRAMS[capability.category],
                    "task": "runtime implementation and evidence certification",
                },
                "source_evidence": (
                    "agent-verse-backend/app/orchestration/strategy_registry.py"
                ),
                "evidence_links": [],
            }
        )
    rows.sort(key=lambda row: (row["learning_phase"], row["family"], row["capability_id"]))
    return {
        "schema_version": 1,
        "generated_from": "sealed runtime registry plus certification evidence",
        "capabilities": rows,
    }


def render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Agent pattern capabilities",
        "",
        "Generated from the runtime registry and evidence evaluator. Do not edit by hand.",
        "Certification is evidence-derived; missing or stale evidence is shown as not certified.",
        "",
        "| Capability | Family | Version | Implementation | Readiness | Certification |",
        "|---|---|---:|---|---|---|",
    ]
    for row in manifest["capabilities"]:
        lines.append(
            f"| `{row['capability_id']}` | {row['family']} | `{row['adapter_version']}` | "
            f"{row['implementation_state']} | {row['operational_readiness']} | "
            f"{row['certification_state']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest()
    outputs = {
        args.json: json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        args.markdown: render_markdown(manifest),
    }
    stale = [path for path, content in outputs.items() if not path.exists() or path.read_text() != content]
    if args.check:
        if stale:
            raise SystemExit(f"generated capability files are stale: {', '.join(map(str, stale))}")
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
