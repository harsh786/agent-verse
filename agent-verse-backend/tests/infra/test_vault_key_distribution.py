"""BYOK-1: every process that touches the vault gets the SAME master key.

The API encrypts a tenant's BYOK LLM key with the vault master key; the Celery
worker that runs the goal (and the workflow worker, the sub-goal pool, beat)
decrypts it. A workload deployed without the key — or with it from another
Secret — decrypts with a different key (or none), and every BYOK run failed with
"Tenant LLM API key could not be decrypted".

This parses every deployment description in the repo: the raw k8s manifests,
the helm chart's app workloads, and the docker-compose files, and fails when an
app workload (API, any Celery worker, beat) lacks the key or sources it from a
different place than the API does.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2] / "infra"
K8S_DIR = INFRA / "k8s"
HELM_DIR = INFRA / "helm" / "agentverse" / "templates"

# Every name the vault reads its master key from (app.providers.vault.get_vault).
VAULT_KEY_NAMES = ("VAULT_MASTER_KEY", "AGENTVERSE_VAULT_KEY")
CANONICAL = "VAULT_MASTER_KEY"

_BACKEND_IMAGE = re.compile(r"agentverse[-/]backend")


# ── k8s ───────────────────────────────────────────────────────────────────────


def _k8s_docs() -> list[tuple[str, dict[str, Any]]]:
    docs: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(K8S_DIR.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict):
                docs.append((path.name, doc))
    return docs


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any] | None:
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
        return dict((spec.get("template") or {}).get("spec") or {})
    if kind == "CronJob":
        job = (spec.get("jobTemplate") or {}).get("spec") or {}
        return dict((job.get("template") or {}).get("spec") or {})
    return None


def _secret_keys(
    docs: list[tuple[str, dict[str, Any]]], kustomized: set[str]
) -> dict[str, set[str]]:
    """Secret name -> keys it provides (ExternalSecret targets; dev Secret only if deployed)."""
    keys: dict[str, set[str]] = {}
    for fname, doc in docs:
        if doc.get("kind") == "ExternalSecret":
            target = ((doc.get("spec") or {}).get("target") or {}).get("name") or doc["metadata"][
                "name"
            ]
            provided = {d["secretKey"] for d in (doc["spec"].get("data") or [])}
            keys.setdefault(target, set()).update(provided)
        elif doc.get("kind") == "Secret" and fname in kustomized:
            provided = set((doc.get("stringData") or {}) | (doc.get("data") or {}))
            keys.setdefault(doc["metadata"]["name"], set()).update(provided)
    return keys


def _kustomized() -> set[str]:
    kust = yaml.safe_load((K8S_DIR / "kustomization.yaml").read_text())
    return set(kust.get("resources") or [])


def _container_vault_source(
    container: dict[str, Any], secret_keys: dict[str, set[str]]
) -> dict[str, tuple[str, str]]:
    """{env var name: (secret, key)} for every vault-key variable the container gets."""
    sources: dict[str, tuple[str, str]] = {}
    for ref in container.get("envFrom") or []:
        secret = (ref.get("secretRef") or {}).get("name")
        if secret:
            for name in VAULT_KEY_NAMES:
                if name in secret_keys.get(secret, set()):
                    sources[name] = (secret, name)
        if (ref.get("configMapRef") or {}).get("name"):
            pass  # a config map must never carry the key (checked below)
    for env in container.get("env") or []:
        if env.get("name") in VAULT_KEY_NAMES:
            ref = (env.get("valueFrom") or {}).get("secretKeyRef")
            if ref is None:
                sources[env["name"]] = ("<literal>", str(env.get("value")))
            else:
                sources[env["name"]] = (ref["name"], ref["key"])
    return sources


def _k8s_app_workloads() -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for fname, doc in _k8s_docs():
        pod = _pod_spec(doc)
        if not pod:
            continue
        for container in pod.get("containers") or []:
            if _BACKEND_IMAGE.search(str(container.get("image", ""))):
                out.append((fname, doc["metadata"]["name"], container))
    return out


def test_k8s_every_backend_workload_gets_the_api_vault_key() -> None:
    docs = _k8s_docs()
    secret_keys = _secret_keys(docs, _kustomized())
    workloads = _k8s_app_workloads()
    names = {name for _f, name, _c in workloads}
    # The workloads the user report is about must be in scope of this check.
    assert {
        "agentverse-backend",
        "agentverse-backend-blue",
        "agentverse-backend-green",
        "agentverse-worker",
        "agentverse-subgoal-worker",
        "agentverse-beat",
    } <= names

    sources = {
        name: _container_vault_source(container, secret_keys) for _f, name, container in workloads
    }
    api = sources["agentverse-backend"]
    assert CANONICAL in api, f"API has no {CANONICAL}: {api}"
    assert api[CANONICAL][0] != "<literal>"
    missing = [n for n, s in sources.items() if CANONICAL not in s]
    assert not missing, f"workloads without {CANONICAL}: {missing}"
    different = {n: s for n, s in sources.items() if s != api}
    assert not different, f"vault key sourced differently from the API {api}: {different}"


def test_k8s_config_maps_never_carry_the_vault_key() -> None:
    for fname, doc in _k8s_docs():
        if doc.get("kind") == "ConfigMap":
            assert not set(doc.get("data") or {}) & set(VAULT_KEY_NAMES), fname


# ── helm ──────────────────────────────────────────────────────────────────────


def _helm_define(name: str) -> str:
    text = (HELM_DIR / "_helpers.tpl").read_text()
    match = re.search(
        r'\{\{-? define "' + re.escape(name) + r'" -?\}\}(.*?)\{\{-? end -?\}\}', text, re.S
    )
    assert match, f"helper {name!r} is not defined"
    return match.group(1)


def _helm_blocks() -> dict[str, str]:
    """component -> template text of each app Deployment."""
    text = (HELM_DIR / "app-workloads.yaml").read_text()
    blocks: dict[str, str] = {}
    for block in re.split(r"^---\s*$", text, flags=re.M):
        if "kind: Deployment" not in block:
            continue
        comp = re.search(r"app\.kubernetes\.io/component: ([\w-]+)", block)
        assert comp
        blocks[comp.group(1)] = block
    return blocks


def _expand(block: str) -> str:
    """Inline the vault env helper (the only include this check needs rendered)."""
    return block.replace(
        '{{- include "agentverse.vaultEnv" . | nindent 8 }}', _helm_define("agentverse.vaultEnv")
    )


def _helm_vault_sources(block: str) -> dict[str, tuple[str, str]]:
    sources: dict[str, tuple[str, str]] = {}
    pattern = re.compile(
        r"- name: (\w+)\s+valueFrom:\s+secretKeyRef:\s+name: (\{\{[^}]+\}\}|\S+)\s+key: (\w+)"
        r"(?:\s+optional: (\w+))?"
    )
    for name, secret, key, _optional in pattern.findall(block):
        if name in (*VAULT_KEY_NAMES, "VAULT_PREVIOUS_MASTER_KEYS"):
            sources[name] = (secret.strip(), key)
    return sources


def test_helm_every_app_workload_gets_the_api_vault_key() -> None:
    blocks = {c: _expand(b) for c, b in _helm_blocks().items()}
    app = {c: b for c, b in blocks.items() if c != "frontend"}
    assert {"backend", "worker", "subgoal-worker", "beat"} <= set(app)
    sources = {c: _helm_vault_sources(b) for c, b in app.items()}
    api = sources["backend"]
    assert api.get(CANONICAL) == ('{{ include "agentverse.secretName" . }}', CANONICAL)
    # Rotation companion: the previous keys reach every process too.
    assert "VAULT_PREVIOUS_MASTER_KEYS" in api
    different = {c: s for c, s in sources.items() if s != api}
    assert not different, f"vault key sourced differently from the API {api}: {different}"


def test_helm_secret_provides_every_vault_variable() -> None:
    text = (HELM_DIR / "secrets.yaml").read_text()
    assert "VAULT_MASTER_KEY:" in text
    assert "VAULT_PREVIOUS_MASTER_KEYS:" in text


# ── docker compose ────────────────────────────────────────────────────────────


def _compose(name: str) -> dict[str, Any]:
    return dict(yaml.safe_load((INFRA / name).read_text())["services"])


def _is_app_service(svc: dict[str, Any]) -> bool:
    """Builds the backend image and runs the API / a Celery worker / beat."""
    build = svc.get("build")
    context = build.get("context") if isinstance(build, dict) else build
    if context != "..":
        return False
    command = svc.get("command") or []
    joined = command if isinstance(command, str) else " ".join(map(str, command))
    # Alembic migrations do not touch the vault (no migration calls get_vault()).
    return "alembic" not in joined


def _compose_vault_source(svc: dict[str, Any]) -> tuple[str, ...]:
    env = svc.get("environment") or {}
    if isinstance(env, list):
        env = dict(e.split("=", 1) if "=" in e else (e, None) for e in env)
    env_files = svc.get("env_file") or []
    if isinstance(env_files, str):
        env_files = [env_files]
    explicit = tuple(f"{k}={env[k]}" for k in VAULT_KEY_NAMES if k in env)
    return explicit + tuple(f"env_file={f}" for f in env_files)


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.prod.yml", "docker-compose.e2e.yml"]
)
def test_compose_every_app_service_gets_the_same_vault_key(compose_file: str) -> None:
    services = {n: s for n, s in _compose(compose_file).items() if _is_app_service(s)}
    assert services, compose_file
    sources = {name: _compose_vault_source(svc) for name, svc in services.items()}
    assert all(sources.values()), f"{compose_file}: services without a vault key: {sources}"
    assert len(set(sources.values())) == 1, f"{compose_file}: vault key differs: {sources}"


def test_compose_prod_requires_the_vault_key() -> None:
    """Production compose must refuse to start without the key (``:?``), not pass ''."""
    services = {n: s for n, s in _compose("docker-compose.prod.yml").items() if _is_app_service(s)}
    assert {"backend", "worker", "subgoal-worker", "beat"} <= set(services)
    for name, svc in services.items():
        env = svc["environment"]
        assert str(env.get(CANONICAL, "")).startswith("${VAULT_MASTER_KEY:?"), name
        assert "AGENTVERSE_VAULT_KEY" not in env, name  # one name, one value
        assert "VAULT_PREVIOUS_MASTER_KEYS" in env, name


def test_compose_dev_every_app_service_reads_the_backend_env_file() -> None:
    services = {n: s for n, s in _compose("docker-compose.yml").items() if _is_app_service(s)}
    assert {"backend", "worker", "subgoal-worker", "workflow-worker", "beat"} <= set(services)
    for name, svc in services.items():
        assert "../.env" in (svc.get("env_file") or []), name
        env = svc.get("environment") or {}
        # An explicit value would override ../.env on some services only.
        assert not set(env) & set(VAULT_KEY_NAMES), name
