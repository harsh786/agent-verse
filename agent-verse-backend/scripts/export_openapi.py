#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema to openapi.json.

Usage: python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import create_app  # noqa: E402


def main() -> None:
    app = create_app()
    schema = app.openapi()
    output_path = Path(__file__).parent.parent / "openapi.json"
    with open(output_path, "w") as f:
        json.dump(schema, f, indent=2)
    print(f"OpenAPI schema exported to {output_path}")
    print(f"  - {len(schema.get('paths', {}))} endpoints")
    print(f"  - {len(schema.get('components', {}).get('schemas', {}))} schemas")


if __name__ == "__main__":
    main()
