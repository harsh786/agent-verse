#!/usr/bin/env python
"""Seed workflow templates from YAML files into the system_workflow_templates DB table.

Usage:
    uv run python scripts/seed_workflow_templates.py           # upsert into DB
    uv run python scripts/seed_workflow_templates.py --dry-run  # validate only

The script is idempotent: running it multiple times is safe.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed workflow templates")
    parser.add_argument("--dry-run", action="store_true", help="Validate YAML only, no DB write")
    args = parser.parse_args()

    from app.workflow.template_store import SystemTemplateStore, _TEMPLATES_DIR

    store = SystemTemplateStore()
    errors: list[str] = []
    loaded_slugs: list[str] = []

    template_files = sorted(_TEMPLATES_DIR.glob("*.yaml")) if _TEMPLATES_DIR.exists() else []
    print(f"Found {len(template_files)} YAML files in {_TEMPLATES_DIR}")

    for yaml_path in template_files:
        try:
            template = store._load_file(yaml_path)
            loaded_slugs.append(template.slug)
            print(f"  ✅ {yaml_path.name} → slug={template.slug!r} category={template.category!r}")
        except Exception as exc:
            errors.append(f"{yaml_path.name}: {exc}")
            print(f"  ❌ {yaml_path.name}: {exc}")

    print(f"\nLoaded: {len(loaded_slugs)}/{len(template_files)} templates")

    if errors:
        print(f"\n{len(errors)} ERRORS:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    if args.dry_run:
        print("\n✅ Dry-run complete — all templates are valid")
        return

    # DB upsert (only if not dry-run)
    import asyncio
    asyncio.run(_upsert_to_db(loaded_slugs, store))
    print(f"\n✅ Seeded {len(loaded_slugs)} templates into system_workflow_templates")


async def _upsert_to_db(slugs: list[str], store: "SystemTemplateStore") -> None:
    """Upsert all templates into the DB. Requires DATABASE_URL env var."""
    import json
    import os

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("⚠️  DATABASE_URL not set — skipping DB write")
        return

    try:
        import asyncpg  # type: ignore[import]
    except ImportError:
        print("⚠️  asyncpg not installed — skipping DB write")
        return

    conn = await asyncpg.connect(db_url)
    try:
        for slug in slugs:
            t = store.get(slug)
            await conn.execute(
                """
                INSERT INTO system_workflow_templates
                  (slug, name, description, category, tags, version, author,
                   complexity, definition_json, sample_input, is_active, popularity_score)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::jsonb, $10::jsonb, true, 0)
                ON CONFLICT (slug) DO UPDATE SET
                  name = EXCLUDED.name,
                  description = EXCLUDED.description,
                  category = EXCLUDED.category,
                  tags = EXCLUDED.tags,
                  version = EXCLUDED.version,
                  definition_json = EXCLUDED.definition_json,
                  sample_input = EXCLUDED.sample_input,
                  updated_at = NOW()
                """,
                t.slug,
                t.name,
                t.description,
                t.category,
                json.dumps(t.tags),
                t.version,
                t.author,
                t.complexity,
                json.dumps(t._definition_raw),
                json.dumps(t.sample_input),
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    main()
