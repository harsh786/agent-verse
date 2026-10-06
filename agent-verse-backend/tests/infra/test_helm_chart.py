"""Tests for the legacy Helm chart (helm/agentverse)."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

HELM_DIR = Path(__file__).parents[2] / "helm" / "agentverse"


def test_chart_yaml_exists():
    assert (HELM_DIR / "Chart.yaml").exists()


def test_values_yaml_exists():
    assert (HELM_DIR / "values.yaml").exists()


def test_templates_directory_exists():
    assert (HELM_DIR / "templates").is_dir()


def test_backend_deployment_template_exists():
    assert (HELM_DIR / "templates" / "deployment.yaml").exists()


def test_values_yaml_is_valid():
    content = (HELM_DIR / "values.yaml").read_text()
    data = yaml.safe_load(content)
    assert "backend" in data
    assert "worker" in data


def test_helpers_tpl_exists():
    assert (HELM_DIR / "templates" / "_helpers.tpl").exists()


def test_notes_txt_exists():
    assert (HELM_DIR / "templates" / "NOTES.txt").exists()


# ── NF-16 follow-up: the legacy chart migrates once and app pods wait for it ──
#
# The legacy chart's API image CMD ran `alembic upgrade head` on start (every
# replica raced to migrate, as the role the API connects with) and the workers /
# beat had no wait at all: they started on whatever schema they found. It now
# mirrors infra/helm/agentverse: one migrate Job per release revision runs as the
# schema owner, the API runs uvicorn only, and every app pod waits in a
# wait-for-schema initContainer.

TEMPLATES = HELM_DIR / "templates"
APP_WORKLOADS = {
    "deployment.yaml": ("backend", "agentverse.backendImage"),
    "worker-deployment.yaml": ("worker", "agentverse.workerImage"),
    "subgoal-worker-deployment.yaml": ("subgoal-worker", "agentverse.subgoalWorkerImage"),
    "schedule-worker-deployment.yaml": ("schedule-worker", "agentverse.scheduleWorkerImage"),
    "maintenance-worker-deployment.yaml": (
        "maintenance-worker",
        "agentverse.maintenanceWorkerImage",
    ),
    "beat-deployment.yaml": ("beat", "agentverse.workerImage"),
}
_MIGRATIONS_IF = {
    "if .Values.migrations.enabled": True,
    "if not .Values.migrations.enabled": False,
}
_DIRECTIVE = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}")
_HELPER = re.compile(r'include "([^"]+)" \.')
_ENV_HELPERS = {"agentverse.appSecretEnv", "agentverse.migrationEnv"}


def _render(fname: str, *, migrations: bool) -> dict[str, Any] | None:
    """A minimal render: resolve migrations.enabled, keep included helper names.

    Not helm: other conditionals count as true, a whole-line env helper include
    becomes an ``__include__`` list entry (other whole-line includes are dropped),
    and inline values become placeholders — enough
    to check the structure the wait / migrate logic depends on.
    """
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", (TEMPLATES / fname).read_text(), flags=re.S)
    keep: list[bool] = []
    out: list[str] = []
    for line in text.splitlines():
        whole = re.fullmatch(r"(\s*)\{\{-?\s*(.*?)\s*-?\}\}\s*", line)
        if whole:
            indent, body = whole.groups()
            if body.startswith(("if ", "with ")):
                keep.append(_MIGRATIONS_IF[body] == migrations if body in _MIGRATIONS_IF else True)
            elif body == "end":
                keep.pop()
            elif all(keep) and (m := _HELPER.search(body)) and m.group(1) in _ENV_HELPERS:
                out.append(f"{indent}- __include__: {m.group(1)}")
            continue
        if not all(keep):
            continue
        out.append(_DIRECTIVE.sub(lambda m: _placeholder(m.group(1)), line))
    assert not keep, f"{fname}: unbalanced if/end"
    doc = yaml.safe_load("\n".join(out))
    return doc if isinstance(doc, dict) else None


def _placeholder(body: str) -> str:
    helper = _HELPER.search(body)
    return f"IMG({helper.group(1)})" if helper else "X"


def _includes(container: dict[str, Any]) -> set[str]:
    return {e["__include__"] for e in container.get("env") or [] if "__include__" in e}


def _pod(doc: dict[str, Any]) -> dict[str, Any]:
    return dict(doc["spec"]["template"]["spec"])


@pytest.mark.parametrize("migrations", [True, False])
@pytest.mark.parametrize("fname", sorted(APP_WORKLOADS))
def test_legacy_helm_app_pods_wait_for_the_migrated_schema(fname: str, migrations: bool) -> None:
    name, image_helper = APP_WORKLOADS[fname]
    doc = _render(fname, migrations=migrations)
    assert doc and doc["kind"] == "Deployment", fname
    pod = _pod(doc)
    main = pod["containers"][0]
    assert main["name"] == name
    assert main["image"] == f"IMG({image_helper})"
    inits = {c["name"]: c for c in pod.get("initContainers") or []}
    if fname == "deployment.yaml" and not migrations:
        # The API migrates on start itself: waiting first would deadlock.
        assert not inits
        return
    wait = inits["wait-for-schema"]
    assert wait["command"] == ["python", "-m", "app.db.wait_for_schema"]
    assert wait["image"] == main["image"]  # same code, same migrations, same head
    # Read as the role the app connects with (DATABASE_URL from appSecretEnv).
    assert "agentverse.appSecretEnv" in _includes(wait)
    assert "agentverse.appSecretEnv" in _includes(main)


@pytest.mark.parametrize("migrations", [True, False])
def test_legacy_helm_api_migrates_only_when_there_is_no_migrate_job(migrations: bool) -> None:
    doc = _render("deployment.yaml", migrations=migrations)
    assert doc
    api = _pod(doc)["containers"][0]
    if migrations:
        # Never the image CMD (`alembic upgrade head && uvicorn`), never the owner DSN.
        assert api["command"][0] == "uvicorn"
        assert "alembic" not in " ".join(api["command"])
        assert "agentverse.migrationEnv" not in _includes(api)
    else:
        assert "command" not in api  # the image CMD migrates, then serves
        assert "agentverse.migrationEnv" in _includes(api)


def test_legacy_helm_migrate_job_runs_once_per_release_as_the_owner() -> None:
    assert _render("migrate-job.yaml", migrations=False) is None
    job = _render("migrate-job.yaml", migrations=True)
    assert job and job["kind"] == "Job"
    raw = (TEMPLATES / "migrate-job.yaml").read_text()
    assert "agentverse-db-migrate-{{ .Release.Revision }}" in raw  # Jobs are immutable
    assert "helm.sh/hook" not in raw  # a hook would deadlock `helm --wait`
    pod = _pod(job)
    assert pod["restartPolicy"] == "OnFailure"
    assert not pod.get("initContainers")  # it must not wait on itself
    (container,) = pod["containers"]
    assert container["command"] == ["alembic", "upgrade", "head"]
    assert container["image"] == "IMG(agentverse.backendImage)"
    # Owner DSN + the app role it provisions; DATABASE_URL is the single-role fallback.
    assert "agentverse.migrationEnv" in _includes(container)
    db = next(e for e in container["env"] if e.get("name") == "DATABASE_URL")
    assert db["valueFrom"]["secretKeyRef"] == {"name": "agentverse-secrets", "key": "database-url"}
    helpers = (TEMPLATES / "_helpers.tpl").read_text()
    for var in ("MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD"):
        assert f"- name: {var}" in helpers


def test_legacy_helm_migrations_enabled_by_default() -> None:
    values = yaml.safe_load((HELM_DIR / "values.yaml").read_text())
    assert values["migrations"]["enabled"] is True
    assert values["migrations"]["backoffLimit"] > 0
