#!/usr/bin/env python3
"""Evaluate signed agent-pattern evidence before a blue/green traffic switch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evals.certification_policy import CanaryEvidence, CertificationPolicy, decide_rollout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    evidence = None
    if args.evidence is not None:
        evidence = CanaryEvidence.model_validate_json(args.evidence.read_text())
    result = decide_rollout(CertificationPolicy(), evidence)
    print(json.dumps({"environment": args.environment, **result.model_dump()}, sort_keys=True))
    return 0 if args.dry_run or result.decision == "promote" else 2


if __name__ == "__main__":
    raise SystemExit(main())
