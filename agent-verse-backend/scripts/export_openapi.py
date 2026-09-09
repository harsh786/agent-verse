#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema to openapi.json.

Usage: python scripts/export_openapi.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import create_app


def _render(schema: dict[str, object]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> None:
    app = create_app()
    schema = app.openapi()
    output_path = Path(__file__).parent.parent / "openapi.json"
    rendered = _render(schema)
    if "--check" in sys.argv[1:]:
        current = output_path.read_text() if output_path.exists() else ""
        if current != rendered:
            print("OpenAPI schema drift detected; run scripts/export_openapi.py")
            raise SystemExit(1)
        print("OpenAPI schema is up to date")
        return
    output_path.write_text(rendered)
    print(f"OpenAPI schema exported to {output_path}")
    print(f"  - {len(schema.get('paths', {}))} endpoints")
    print(f"  - {len(schema.get('components', {}).get('schemas', {}))} schemas")


if __name__ == "__main__":
    main()
