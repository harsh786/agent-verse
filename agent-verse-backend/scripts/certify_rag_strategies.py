#!/usr/bin/env python3
"""Generate a fail-closed certification report for all canonical RAG strategies."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.rag.catalogue import RAG_CAPABILITY_CATALOGUE
from app.rag.certification import (
    RAGCertificationRunner,
    write_markdown_report,
    write_report,
)
from app.rag.contracts import RAGStrategy


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="core")
    parser.add_argument("--environment", default="local")
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    supplied: dict[str, dict[str, Any]] = {}
    if args.evidence_json is not None:
        supplied = json.loads(args.evidence_json.read_text())

    async def probe(strategy: RAGStrategy) -> dict[str, Any]:
        evidence = dict(supplied.get(strategy.value, {}))
        capability = RAG_CAPABILITY_CATALOGUE[strategy]
        capability.create_adapter()
        evidence.setdefault("adapter_version", capability.adapter_version)
        evidence.setdefault("evidence_reference", f"missing-evidence://{strategy.value}")
        return evidence

    report = await RAGCertificationRunner(probe).run_all(
        environment=f"{args.environment}:{args.profile}", live=args.live
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    write_report(report, args.output_json)
    write_markdown_report(report, args.output_markdown)
    if args.require_pass and not all(item.passed for item in report.results):
        return 2
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(_arguments())))


if __name__ == "__main__":
    main()
