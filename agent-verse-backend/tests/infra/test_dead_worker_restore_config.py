"""a06-F099-03: the dead-worker restore settings reach every app workload, the same way.

Workers read CELERY_DEAD_WORKER_RESTORE_ENABLED (heartbeat + ownership records)
and the maintenance worker running the beat job reads both, so they come from the
one shared config (like the OCR knobs), with the Settings defaults.
"""

from __future__ import annotations

import re

from tests.infra.test_vault_key_distribution import (
    HELM_DIR,
    INFRA,
    LEGACY_HELM_DIR,
    _compose,
    _config_map_refs,
    _helm_values,
    _is_app_service,
    _k8s_app_workloads,
    _k8s_docs,
    _kustomized,
)

KEYS = ("CELERY_DEAD_WORKER_RESTORE_ENABLED", "CELERY_DEAD_WORKER_GRACE_SECONDS")
_DEFAULTS = {"CELERY_DEAD_WORKER_RESTORE_ENABLED": "true",
             "CELERY_DEAD_WORKER_GRACE_SECONDS": "120"}
_HELM_EXPR = {
    "CELERY_DEAD_WORKER_RESTORE_ENABLED":
        "{{ .Values.celeryDeadWorkerRestore.enabled | quote }}",
    "CELERY_DEAD_WORKER_GRACE_SECONDS":
        "{{ .Values.celeryDeadWorkerRestore.graceSeconds | quote }}",
}
_HELM_VALUES = {"enabled": True, "graceSeconds": 120}


def test_settings_defaults_match_the_deployments() -> None:
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["celery_dead_worker_restore_enabled"].default is True
    assert fields["celery_dead_worker_grace_seconds"].default == 120.0


def test_k8s_every_app_workload_gets_the_restore_settings_from_the_api_config_map() -> None:
    docs = _k8s_docs()
    config_maps = {
        d["metadata"]["name"]: dict(d.get("data") or {})
        for f, d in docs
        if d.get("kind") == "ConfigMap" and f in _kustomized()
    }
    workloads = [w for w in _k8s_app_workloads() if w[1] != "agentverse-db-migration"]
    api = next(c for _f, n, c in workloads if n == "agentverse-backend")
    carrying = [m for m in _config_map_refs(api) if set(KEYS) <= set(config_maps.get(m, {}))]
    assert len(carrying) == 1
    assert {k: config_maps[carrying[0]][k] for k in KEYS} == _DEFAULTS
    for fname, name, container in workloads:
        assert carrying[0] in _config_map_refs(container), f"{fname}:{name}"
        explicit = {e["name"] for e in container.get("env") or []} & set(KEYS)
        assert not explicit, f"{fname}:{name} overrides the shared value: {explicit}"


def test_helm_renders_the_restore_settings_into_the_shared_config_map() -> None:
    values = _helm_values(HELM_DIR)
    assert values["celeryDeadWorkerRestore"] == _HELM_VALUES
    text = (HELM_DIR / "configmaps.yaml").read_text()
    for key, expr in _HELM_EXPR.items():
        assert re.search(rf"^\s+{key}: {re.escape(expr)}\s*$", text, re.M), key
    assert not set(values["backend"].get("env") or {}) & set(KEYS)


def test_legacy_helm_carries_the_restore_settings_in_its_config_map() -> None:
    values = _helm_values(LEGACY_HELM_DIR)
    assert values["celeryDeadWorkerRestore"] == _HELM_VALUES
    text = (LEGACY_HELM_DIR / "configmap.yaml").read_text()
    for key, expr in _HELM_EXPR.items():
        assert re.search(rf"^\s+{key}: {re.escape(expr)}\s*$", text, re.M), key


def test_compose_prod_every_app_service_gets_the_restore_settings() -> None:
    services = {n: s for n, s in _compose("docker-compose.prod.yml").items() if _is_app_service(s)}
    assert {"backend", "worker", "schedule-worker", "subgoal-worker", "beat"} <= set(services)
    expected = {k: f"${{{k}:-{v}}}" for k, v in _DEFAULTS.items()}
    for name, svc in services.items():
        env = svc["environment"]
        assert {k: env.get(k) for k in KEYS} == expected, name


def test_env_example_documents_the_restore_settings() -> None:
    text = (INFRA.parent / ".env.example").read_text()
    for key, default in _DEFAULTS.items():
        assert any(re.match(rf"#?\s*{key}={default}$", ln) for ln in text.splitlines()), key
