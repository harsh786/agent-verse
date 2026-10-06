"""OCR-PAR-5: the OCR concurrency knobs reach every app workload, the same way.

OCR runs on the API (uploads, /ocr) AND on the workers (connector ingestion,
workflow OCR steps), so every deployment carries OCR_MAX_CONCURRENCY /
OCR_PAGE_CONCURRENCY / OCR_VISION_CONCURRENCY / OCR_RENDER_DPI from the place
the API reads them (like the EGRESS-CFG ingestion settings), and pins
Tesseract's OpenMP to one thread (OMP_THREAD_LIMIT=1): pages run side by side,
and N tesseracts each spawning one thread per core oversubscribe the CPU.
"""

from __future__ import annotations

import os
import re

import pytest

from tests.infra.test_vault_key_distribution import (
    HELM_DIR,
    INFRA,
    LEGACY_HELM_DIR,
    _compose,
    _config_map_refs,
    _expand,
    _helm_blocks,
    _helm_values,
    _is_app_service,
    _k8s_app_workloads,
    _k8s_docs,
    _kustomized,
)

OCR_KEYS = ("OCR_MAX_CONCURRENCY", "OCR_PAGE_CONCURRENCY", "OCR_VISION_CONCURRENCY",
            "OCR_RENDER_DPI", "OMP_THREAD_LIMIT")
_DEFAULTS = {"OCR_MAX_CONCURRENCY": "0", "OCR_PAGE_CONCURRENCY": "0",
             "OCR_VISION_CONCURRENCY": "4", "OCR_RENDER_DPI": "300", "OMP_THREAD_LIMIT": "1"}
_HELM_EXPR = {
    "OCR_MAX_CONCURRENCY": "{{ .Values.ocr.maxConcurrency | quote }}",
    "OCR_PAGE_CONCURRENCY": "{{ .Values.ocr.pageConcurrency | quote }}",
    "OCR_VISION_CONCURRENCY": "{{ .Values.ocr.visionConcurrency | quote }}",
    "OCR_RENDER_DPI": "{{ .Values.ocr.renderDpi | quote }}",
    "OMP_THREAD_LIMIT": "{{ .Values.ocr.ompThreadLimit | quote }}",
}
_HELM_OCR_VALUES = {"maxConcurrency": 0, "pageConcurrency": 0, "visionConcurrency": 4,
                    "renderDpi": 300, "ompThreadLimit": 1}


def test_settings_defaults_match_the_deployments() -> None:
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["ocr_max_concurrency"].default == 0
    assert fields["ocr_page_concurrency"].default == 0
    assert fields["ocr_vision_concurrency"].default == 4
    assert fields["ocr_render_dpi"].default == 300


def test_image_pins_tesseract_openmp_to_one_thread() -> None:
    from tests._paths import BACKEND_ROOT

    dockerfile = (BACKEND_ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split(" AS runtime", 1)[1]
    assert re.search(r"^\s*(ENV\s+)?OMP_THREAD_LIMIT=1\b", runtime, re.M)


def test_k8s_every_app_workload_gets_the_ocr_settings_from_the_api_config_map() -> None:
    docs = _k8s_docs()
    config_maps = {
        d["metadata"]["name"]: dict(d.get("data") or {})
        for f, d in docs
        if d.get("kind") == "ConfigMap" and f in _kustomized()
    }
    workloads = [w for w in _k8s_app_workloads() if w[1] != "agentverse-db-migration"]
    api = next(c for _f, n, c in workloads if n == "agentverse-backend")
    carrying = [m for m in _config_map_refs(api) if set(OCR_KEYS) <= set(config_maps.get(m, {}))]
    assert len(carrying) == 1
    assert {k: config_maps[carrying[0]][k] for k in OCR_KEYS} == _DEFAULTS
    for fname, name, container in workloads:
        assert carrying[0] in _config_map_refs(container), f"{fname}:{name}"
        explicit = {e["name"] for e in container.get("env") or []} & set(OCR_KEYS)
        assert not explicit, f"{fname}:{name} overrides the shared value: {explicit}"


def test_helm_every_app_workload_gets_the_ocr_settings_from_one_config_map() -> None:
    values = _helm_values(HELM_DIR)
    assert values["ocr"] == _HELM_OCR_VALUES
    text = (HELM_DIR / "configmaps.yaml").read_text()
    for key, expr in _HELM_EXPR.items():
        assert re.search(rf"^\s+{key}: {re.escape(expr)}\s*$", text, re.M), key
    blocks = {c: _expand(b) for c, b in _helm_blocks().items() if c != "frontend"}
    shared = '{{ include "agentverse.fullname" . }}-config'
    for comp, block in blocks.items():
        main = block.split("\n      containers:\n", 1)[-1]
        assert shared in _config_map_refs(main), comp
        for key in OCR_KEYS:
            assert f"- name: {key}" not in block, f"{comp} overrides {key}"
    assert not set(values["backend"].get("env") or {}) & set(OCR_KEYS)


def test_legacy_helm_carries_the_ocr_settings_in_its_config_map() -> None:
    values = _helm_values(LEGACY_HELM_DIR)
    assert values["ocr"] == _HELM_OCR_VALUES
    text = (LEGACY_HELM_DIR / "configmap.yaml").read_text()
    for key, expr in _HELM_EXPR.items():
        assert re.search(rf"^\s+{key}: {re.escape(expr)}\s*$", text, re.M), key


def test_compose_prod_every_app_service_gets_the_ocr_settings() -> None:
    services = {n: s for n, s in _compose("docker-compose.prod.yml").items() if _is_app_service(s)}
    assert {"backend", "worker", "schedule-worker", "subgoal-worker", "beat"} <= set(services)
    expected = {k: f"${{{k}:-{v}}}" for k, v in _DEFAULTS.items()}
    for name, svc in services.items():
        env = svc["environment"]
        assert {k: env.get(k) for k in OCR_KEYS} == expected, name


def test_env_example_documents_the_ocr_settings() -> None:
    text = (INFRA.parent / ".env.example").read_text()
    for key in OCR_KEYS:
        assert any(re.match(rf"#?\s*{key}=", ln) for ln in text.splitlines()), key


def test_celery_worker_shares_the_cpus_between_its_prefork_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker_init (parent, before the fork) records the pool size; each child
    then sizes its OCR pool to its share of the CPUs."""
    from app.ocr import concurrency as oc
    from app.scaling import celery_app as ca

    mp = monkeypatch
    mp.delenv(oc.WORKER_PROCESSES_ENV, raising=False)

    class _Prefork:
        concurrency = 4
        pool_cls = type("TaskPool", (), {"__module__": "celery.concurrency.prefork"})

    ca._on_worker_init_ocr_share(sender=_Prefork())
    assert os.environ[oc.WORKER_PROCESSES_ENV] == "4"

    mp.delenv(oc.WORKER_PROCESSES_ENV, raising=False)

    class _Threads:
        concurrency = 8
        pool_cls = type("TaskPool", (), {"__module__": "celery.concurrency.thread"})

    ca._on_worker_init_ocr_share(sender=_Threads())  # one process: one shared pool
    assert oc.WORKER_PROCESSES_ENV not in os.environ

    class _Unresolved:  # what worker_init really sees: the -P name, not yet a class
        concurrency = 2
        pool_cls = "prefork"

    ca._on_worker_init_ocr_share(sender=_Unresolved())
    assert os.environ[oc.WORKER_PROCESSES_ENV] == "2"
