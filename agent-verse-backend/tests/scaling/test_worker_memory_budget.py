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
    assert preload == {"worker": True, "subgoal-worker": False, "workflow-worker": False}
