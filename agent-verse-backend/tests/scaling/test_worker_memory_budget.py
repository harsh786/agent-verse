"""L-03: Celery workers must fit their memory limit (no OOM-killed workflow jobs).

The live ``workflow-worker`` and ``subgoal-worker`` ran at 90-97 % of their
1 GiB limit and workflow jobs were SIGKILLed (OOMKilled=true). Every prefork
child warmed the cross-encoder at ``worker_process_init`` (torch +
sentence-transformers + the model, ~450 MB per child on top of the ~150 MB
worker app), in every worker pool — also the ones that rarely rerank — while
``worker_max_memory_per_child`` (500 MB) was below what a warm child needs and
``concurrency x cap`` did not fit the container.

Now: importing the worker app loads no ML stack, children load models lazily
on first use unless a pool opts in (``WORKER_PRELOAD_RETRIEVAL_MODELS``), and
every worker manifest declares a per-child cap whose budget fits its limit.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.scaling import celery_app as celery_mod

BACKEND = Path(__file__).resolve().parents[2]
INFRA = BACKEND / "infra"

_HEAVY_MODULES = ("torch", "sentence_transformers", "transformers", "torchaudio")

# Memory budget of one prefork worker (MB). A pool fits its container when
#   limit >= PARENT + concurrency * (per-child cap + TASK_OVERSHOOT)
# (Celery checks --max-memory-per-child only after a task, so a child can exceed
# its cap by what that one task allocates). Measured on the dev host: worker app
# imported 154 MB RSS; + torch 320 MB; + sentence-transformers 520 MB; + the
# cross-encoder loaded and one 32-pair predict ~610 MB — so a child that has
# reranked needs ~600 MB, under the 700 MB cap (it is not recycled per search).
_PARENT_MB = 300
_TASK_OVERSHOOT_MB = 100
_MIN_CHILD_CAP_MB = 650  # below this a child that reranked is recycled after every task


def test_importing_the_worker_app_loads_no_ml_stack() -> None:
    """``celery -A app.scaling.celery_app worker`` must not import torch & co."""
    code = (
        "import json, sys\n"
        "from app.scaling.celery_app import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        f"print(json.dumps([m for m in {_HEAVY_MODULES!r} if m in sys.modules]))\n"
    )
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://nouser:nopass@127.0.0.1:1/none",
        "REDIS_URL": "redis://127.0.0.1:1/0",
    }
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    loaded = json.loads(out.stdout.strip().splitlines()[-1])
    assert loaded == [], f"worker app import pulled in {loaded}"


def _settings(preload: bool) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(worker_preload_retrieval_models=preload)


def test_worker_children_do_not_preload_models_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    assert Settings.model_fields["worker_preload_retrieval_models"].default is False
    calls: list[int] = []
    monkeypatch.setattr(celery_mod, "_preload_retrieval_models", lambda: calls.append(1))
    monkeypatch.setattr("app.core.config.get_settings", lambda: _settings(False))

    celery_mod._on_worker_process_init()

    assert calls == []


def test_a_pool_can_opt_into_the_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(celery_mod, "_preload_retrieval_models", lambda: calls.append(1))
    monkeypatch.setattr("app.core.config.get_settings", lambda: _settings(True))

    celery_mod._on_worker_process_init()

    assert calls == [1]


def test_default_child_cap_keeps_a_warm_child() -> None:
    cap_mb = celery_mod.celery_app.conf.worker_max_memory_per_child / 1000
    assert cap_mb >= _MIN_CHILD_CAP_MB
    assert celery_mod.celery_app.conf.worker_max_tasks_per_child == 100


# ── manifests ────────────────────────────────────────────────────────────────


def _mb(value: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMG]i?)?B?", str(value).strip(), re.IGNORECASE)
    assert m, f"unparseable memory size {value!r}"
    num, unit = float(m.group(1)), (m.group(2) or "").upper().rstrip("I")
    factor = {"": 1 / 1_000_000, "K": 1 / 1024, "M": 1.0, "G": 1024.0}[unit]
    return num * factor


def _flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _argv(command: Any) -> list[str]:
    return shlex.split(command) if isinstance(command, str) else [str(a) for a in command]


def _compose_workers(path: Path) -> list[tuple[str, list[str], str | None, dict[str, Any]]]:
    services = yaml.safe_load(path.read_text())["services"]
    out = []
    for name, svc in services.items():
        argv = _argv(svc.get("command") or [])
        if "celery" in argv and "worker" in argv:
            limit = (((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}).get(
                "memory"
            )
            out.append((name, argv, limit, svc.get("environment") or {}))
    return out


def _k8s_workers(path: Path) -> list[tuple[str, list[str], str | None]]:
    out = []
    for doc in yaml.safe_load_all(path.read_text()):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for c in doc["spec"]["template"]["spec"]["containers"]:
            argv = [str(a) for a in c.get("command", []) + c.get("args", [])]
            if "worker" in argv:
                out.append((c["name"], argv, c["resources"]["limits"]["memory"]))
    return out


def _assert_fits(where: str, argv: list[str], limit: str | None) -> None:
    concurrency = _flag(argv, "--concurrency")
    cap_kb = _flag(argv, "--max-memory-per-child")
    assert concurrency, f"{where}: set --concurrency explicitly"
    assert cap_kb, f"{where}: set --max-memory-per-child explicitly"
    assert limit, f"{where}: no memory limit"
    cap_mb = int(cap_kb) / 1000
    assert cap_mb >= _MIN_CHILD_CAP_MB, f"{where}: a warm child ({cap_mb} MB cap) recycles"
    budget = _PARENT_MB + int(concurrency) * (cap_mb + _TASK_OVERSHOOT_MB)
    assert _mb(limit) >= budget, (
        f"{where}: limit {limit} < {budget:.0f} MB "
        f"({_PARENT_MB} + {concurrency} x ({cap_mb:.0f} + {_TASK_OVERSHOOT_MB}))"
    )


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_compose_worker_pools_fit_their_memory_limit(compose: str) -> None:
    workers = _compose_workers(INFRA / compose)
    assert workers
    for name, argv, limit, _env in workers:
        _assert_fits(f"{compose}:{name}", argv, limit)


def test_k8s_worker_pools_fit_their_memory_limit() -> None:
    workers = _k8s_workers(INFRA / "k8s" / "worker-deployment.yaml")
    assert workers
    for name, argv, limit in workers:
        _assert_fits(f"k8s:{name}", argv, limit)


def test_only_the_main_dev_worker_preloads_retrieval_models() -> None:
    """Workflow / sub-goal pools load the reranker lazily (only if they search)."""
    preload = {
        name: str(env.get("WORKER_PRELOAD_RETRIEVAL_MODELS", "false")).lower() == "true"
        for name, _argv, _limit, env in _compose_workers(INFRA / "docker-compose.yml")
    }
    assert preload == {
        "worker": True,
        "subgoal-worker": False,
        "workflow-worker": False,
        "schedule-worker": False,
        "maintenance-worker": False,
    }


# ── D4: Helm charts use the same per-pod budget as compose/k8s ──────────────
# Owner decision D4 (2026-10-05): 4 worker processes per pod, a 3.5Gi limit, an
# explicit 700 MB per-child cap and lazy model loading; capacity scales with pods.
# ``helm template`` renders the charts when helm is installed; otherwise the
# worker command lines are rendered from the values files by substituting the
# ``{{ .Values.<pool>.<key> }}`` placeholders (every overlay merged on values.yaml).

_MAINTAINED_CHART = INFRA / "helm" / "agentverse"
_LEGACY_CHART = BACKEND / "helm" / "agentverse"
_HELM_POOLS = ("worker", "subgoalWorker")
_PLACEHOLDER = re.compile(r"\{\{-?\s*\.Values\.(\w+)\.(\w+)\s*(\|\s*quote\s*)?-?\}\}")


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _chart_values(chart: Path) -> list[tuple[str, dict[str, Any]]]:
    base = yaml.safe_load((chart / "values.yaml").read_text()) or {}
    out = [("values.yaml", base)]
    for overlay in sorted(chart.glob("values-*.yaml")):
        out.append((overlay.name, _merge(base, yaml.safe_load(overlay.read_text()) or {})))
    return out


def _subst(text: str, values: dict[str, Any]) -> str:
    def _one(m: re.Match[str]) -> str:
        value = values[m.group(1)][m.group(2)]
        if isinstance(value, bool):
            value = str(value).lower()
        return json.dumps(str(value)) if m.group(3) else str(value)

    return _PLACEHOLDER.sub(_one, text)


def _maintained_pool(values: dict[str, Any], pool: str) -> tuple[list[str], str, dict[str, str]]:
    """Render one pool's argv / memory limit / env from app-workloads.yaml."""
    template = (_MAINTAINED_CHART / "templates" / "app-workloads.yaml").read_text()
    block = template.split(f"{{{{- if .Values.{pool}.enabled }}}}", 1)[1].split("{{- end }}")[0]
    args_line = next(ln for ln in block.splitlines() if ln.strip().startswith("args:"))
    argv = ["celery", *yaml.safe_load(_subst(args_line.split("args:", 1)[1], values))]
    env: dict[str, str] = {}
    lines = block.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"\s*- name: (WORKER_\w+)$", ln)
        if m:
            env[m.group(1)] = yaml.safe_load(_subst(lines[i + 1].split("value:", 1)[1], values))
    return argv, str(values[pool]["resources"]["limits"]["memory"]), env


def _legacy_pool(values: dict[str, Any], pool: str) -> tuple[list[str], str, dict[str, str]]:
    name = "worker-deployment.yaml" if pool == "worker" else "subgoal-worker-deployment.yaml"
    text = (_LEGACY_CHART / "templates" / name).read_text()
    command = text.split("command:", 1)[1].split("envFrom:", 1)[0]
    argv = [str(a) for a in yaml.safe_load(_subst(command, values))]
    env: dict[str, str] = {}
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"\s*- name: (WORKER_\w+)$", ln)
        if m:
            env[m.group(1)] = yaml.safe_load(_subst(lines[i + 1].split("value:", 1)[1], values))
    return argv, str(values[pool]["resources"]["limits"]["memory"]), env


def _helm_rendered(chart: Path, values_file: str) -> list[tuple[str, list[str], str, dict[str, str]]]:
    files = ["-f", str(chart / "values.yaml")]
    if values_file != "values.yaml":
        files += ["-f", str(chart / values_file)]
    out = subprocess.run(
        ["helm", "template", "d4", str(chart), *files],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout
    pools = []
    for doc in yaml.safe_load_all(out):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for c in doc["spec"]["template"]["spec"]["containers"]:
            argv = [str(a) for a in c.get("command", []) + c.get("args", [])]
            if "worker" in argv:
                env = {e["name"]: str(e.get("value")) for e in c.get("env", []) if "value" in e}
                pools.append((c["name"], argv, str(c["resources"]["limits"]["memory"]), env))
    return pools


def _helm_pools(chart: Path) -> list[tuple[str, list[str], str, dict[str, str]]]:
    import shutil

    render = _maintained_pool if chart == _MAINTAINED_CHART else _legacy_pool
    pools = []
    for values_file, values in _chart_values(chart):
        if shutil.which("helm"):
            for name, argv, limit, env in _helm_rendered(chart, values_file):
                pools.append((f"{values_file}:{name}", argv, limit, env))
            continue
        for pool in _HELM_POOLS:
            if (values.get(pool) or {}).get("enabled", True) is False:
                continue
            argv, limit, env = render(values, pool)
            pools.append((f"{values_file}:{pool}", argv, limit, env))
    return pools


@pytest.mark.parametrize("chart", [_MAINTAINED_CHART, _LEGACY_CHART], ids=["infra", "legacy"])
def test_helm_worker_pools_fit_their_memory_limit(chart: Path) -> None:
    pools = _helm_pools(chart)
    assert pools
    for where, argv, limit, env in pools:
        _assert_fits(f"helm {chart.parent.parent.name}/{where}", argv, limit)
        assert _flag(argv, "--concurrency") == "4", f"{where}: D4 runs 4 processes per pod"
        assert int(_flag(argv, "--max-memory-per-child") or 0) == 700_000, where
        assert _flag(argv, "--max-tasks-per-child") == "100", where
        assert _mb(limit) == 3.5 * 1024, f"{where}: D4 sets a 3.5Gi limit, got {limit}"
        # L-03: production pools load the reranker lazily (no per-child preload).
        assert env.get("WORKER_PRELOAD_RETRIEVAL_MODELS") == "false", where


@pytest.mark.parametrize("chart", [_MAINTAINED_CHART, _LEGACY_CHART], ids=["infra", "legacy"])
def test_helm_values_document_the_worker_budget(chart: Path) -> None:
    text = (chart / "values.yaml").read_text()
    for needle in ("D4", "L-03", "3.5Gi", "700"):
        assert needle in text, f"{chart}/values.yaml: worker budget comment lacks {needle!r}"
