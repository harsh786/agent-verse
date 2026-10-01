"""CONNECTOR-DEPS: every connector the Sources catalogue offers can run in the image.

Twelve connectors (neo4j, clickhouse, azure_blob, influxdb, snowflake, bigquery,
gcs, pubsub, gdrive, kafka, mysql, duckdb, mqtt, youtube) were registered and
offered in the Sources UI while their SDKs were not runtime dependencies at all,
so they could never sync. The SDKs now ship through the ``connectors`` extra,
which the Dockerfile installs. These tests keep four things in sync: the
registry, the connector source code, ``app.ingestion.connector_sdks`` and
pyproject.toml / the Dockerfile.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
import tomllib
from pathlib import Path

import pytest

from app.ingestion.connector_registry import _REGISTRY, load_all_connectors
from app.ingestion.connector_sdks import CONNECTOR_SDKS, missing_sdks

BACKEND = Path(__file__).resolve().parents[2]

# Third-party modules a connector may import without an SDK entry, and why.
_CORE_OR_OPTIONAL = {
    "app",  # the backend itself
    "httpx",  # core dependency
    "botocore",  # ships with boto3 (core)
    "bson",  # ships with pymongo (core)
    "trafilatura",  # optional extraction quality; web_crawl falls back to a regex strip
    "MySQLdb",  # optional alternative driver; the mysql connector prefers PyMySQL
    "requests",  # ships with youtube-transcript-api (the youtube connector bounds its session)
}


def _registered() -> dict[str, type]:
    load_all_connectors()
    return dict(_REGISTRY)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_packages() -> set[str]:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    reqs = [
        *data["project"]["dependencies"],
        *data["project"]["optional-dependencies"]["connectors"],
    ]
    return {_norm(re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0]) for r in reqs}


def _third_party_imports(source: str) -> set[str]:
    tops: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            tops.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            tops.add(node.module)
    return {m for m in tops if m.split(".")[0] not in sys.stdlib_module_names}


def test_every_sdk_a_connector_imports_is_declared_in_the_table() -> None:
    gaps: dict[str, set[str]] = {}
    for source_type, cls in _registered().items():
        source = inspect.getsource(sys.modules[cls.__module__])
        covered = {req.module.split(".")[0] for req in CONNECTOR_SDKS.get(source_type, ())}
        for module in _third_party_imports(source):
            top = module.split(".")[0]
            if top in _CORE_OR_OPTIONAL or top in covered:
                continue
            if top == "google" and any(m.startswith("google") for m in covered):
                continue
            gaps.setdefault(source_type, set()).add(module)
    assert not gaps, f"connector imports an SDK missing from CONNECTOR_SDKS: {gaps}"


def test_every_table_entry_is_a_registered_connector() -> None:
    assert set(CONNECTOR_SDKS) <= set(_registered())


def test_every_sdk_package_ships_in_the_image() -> None:
    declared = _declared_packages()
    missing = {
        f"{source_type}: {req.package}"
        for source_type, reqs in CONNECTOR_SDKS.items()
        for req in reqs
        if _norm(req.package) not in declared
    }
    assert not missing, f"not a core dependency nor in the 'connectors' extra: {missing}"


def test_dockerfile_installs_the_connectors_extra() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text()
    sync = next(line for line in dockerfile.splitlines() if "uv sync" in line)
    assert "--extra connectors" in sync


@pytest.mark.parametrize("source_type", sorted(CONNECTOR_SDKS))
def test_sdk_is_importable_with_the_extra_installed(source_type: str) -> None:
    # The dev environment installs the extra (dependency-groups.dev includes
    # agent-verse-backend[connectors]). No connector SDK here is platform-specific:
    # all of them publish wheels (or are pure Python) for linux x86_64/aarch64 and
    # macOS arm64, so there is no skip.
    assert missing_sdks(source_type) == []
