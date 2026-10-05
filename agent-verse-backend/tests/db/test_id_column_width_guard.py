"""P8b-4 guard: no ``tenant_id`` narrower than 64 may come back.

Migration e7b1c4d9a2f6 widened every ``tenant_id`` (and the id-carrying
``id`` / ``*_id`` columns) to ``VARCHAR(64)`` so a dashed 36-char tenant id fits
everywhere. These checks fail when a later migration or a model declares a
narrower ``tenant_id`` again. The live-schema check (every column of a migrated
database) is ``tests/db/test_widen_id_columns_migration.py`` (integration).
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
_WIDEN_REVISION = "e7b1c4d9a2f6"
_MIN = 64

# tenant_id declared with an explicit width, in the forms migrations use.
_NARROW_PATTERNS = (
    # sa.Column("tenant_id", sa.String(32), ...)
    re.compile(r"""["']tenant_id["']\s*,\s*(?:sa\.|sqlalchemy\.)?(?:String|VARCHAR)\(\s*(\d+)"""),
    # tenant_id: Mapped[str] = mapped_column(String(32) ...
    re.compile(r"tenant_id\s*:\s*Mapped\[[^\]]*\]\s*=\s*mapped_column\(\s*(?:sa\.)?String\(\s*(\d+)"),
    # raw SQL: tenant_id VARCHAR(32) / ALTER COLUMN tenant_id TYPE varchar(32)
    re.compile(
        r"tenant_id\"?\s+(?:TYPE\s+)?(?:VARCHAR|CHARACTER\s+VARYING|CHAR)\s*\(\s*(\d+)",
        re.IGNORECASE,
    ),
    # op.alter_column("t", "tenant_id", type_=sa.String(32))
    re.compile(r"""["']tenant_id["'][^)\n]*type_\s*=\s*(?:sa\.)?String\(\s*(\d+)"""),
)


def narrow_tenant_id_declarations(source: str) -> list[str]:
    """Each ``tenant_id`` declaration in *source* narrower than 64 chars."""
    found = []
    for pattern in _NARROW_PATTERNS:
        for m in pattern.finditer(source):
            if int(m.group(1)) < _MIN:
                line = source.count("\n", 0, m.start()) + 1
                found.append(f"line {line}: {m.group(0)}")
    return found


@pytest.mark.parametrize(
    "snippet",
    [
        'sa.Column("tenant_id", sa.String(32), nullable=False)',
        "tenant_id: Mapped[str] = mapped_column(String(36), index=True)",
        "CREATE TABLE x (id TEXT, tenant_id VARCHAR(32) NOT NULL)",
        'op.execute("ALTER TABLE x ALTER COLUMN tenant_id TYPE varchar(36)")',
        'op.alter_column("x", "tenant_id", type_=sa.String(32))',
    ],
)
def test_the_scanner_flags_a_narrow_tenant_id(snippet: str) -> None:
    assert narrow_tenant_id_declarations(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        'sa.Column("tenant_id", sa.String(64), nullable=False)',
        "tenant_id: Mapped[str] = mapped_column(String(64), index=True)",
        "CREATE TABLE x (tenant_id TEXT NOT NULL, goal_id VARCHAR(32))",
    ],
)
def test_the_scanner_accepts_wide_tenant_ids(snippet: str) -> None:
    assert narrow_tenant_id_declarations(snippet) == []


def _revisions_after_widening() -> list[Path]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "app/db/migrations"))
    script = ScriptDirectory.from_config(cfg)
    later = []
    for rev in script.walk_revisions("base", "heads"):
        if rev.revision == _WIDEN_REVISION:
            break
        later.append(Path(str(rev.path)))
    else:  # pragma: no cover - the widening revision must stay in the chain
        raise AssertionError(f"revision {_WIDEN_REVISION} is not in the migration chain")
    return later


def test_no_migration_after_the_widening_declares_a_narrow_tenant_id() -> None:
    offenders = {
        path.name: hits
        for path in _revisions_after_widening()
        if (hits := narrow_tenant_id_declarations(path.read_text()))
    }
    assert offenders == {}, (
        "a migration declares tenant_id narrower than VARCHAR(64) (a dashed UUID "
        f"tenant id must fit; see e7b1c4d9a2f6): {offenders}"
    )


def test_no_model_declares_a_narrow_tenant_id() -> None:
    from sqlalchemy import String
    from sqlalchemy.orm import mapperlib

    import app.db.models as models_pkg

    for mod in pkgutil.walk_packages(models_pkg.__path__, "app.db.models."):
        importlib.import_module(mod.name)
    for extra in ("app.chat.models", "app.org.models", "app.skills_runtime.tenant_store"):
        importlib.import_module(extra)

    narrow: list[str] = []
    seen: set[int] = set()
    for registry in list(mapperlib._mapper_registries):
        metadata = registry.metadata
        if id(metadata) in seen:
            continue
        seen.add(id(metadata))
        for table in metadata.tables.values():
            col = table.columns.get("tenant_id")
            if col is not None and isinstance(col.type, String) and col.type.length:
                if col.type.length < _MIN:
                    narrow.append(f"{table.name}.tenant_id String({col.type.length})")
    assert narrow == []
