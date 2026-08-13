#!/usr/bin/env python3
"""Verify tamper-evident audit bundles without mutating audit state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.bundle is None:
        print(json.dumps({"status": "hold", "reason": "bundle_required"}))
        return 0 if args.verify_only else 2
    bundle = json.loads(args.bundle.read_text())
    supplied = bundle.pop("manifest_digest", "")
    valid = supplied == _digest(bundle) and bundle.get("retention_lock") == "compliance"
    print(json.dumps({"status": "verified" if valid else "invalid"}, sort_keys=True))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
