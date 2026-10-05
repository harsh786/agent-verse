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


_ENV_HELPERS = ("agentverse.appSecretEnv", "agentverse.vaultEnv")


def _expand(block: str) -> str:
    """Inline the env helpers (nested: appSecretEnv includes vaultEnv)."""
    for _ in range(3):
        for name in _ENV_HELPERS:
            block = re.sub(
                r'\{\{-? include "' + re.escape(name) + r'" \.(?: \| nindent \d+)? -?\}\}',
                lambda _m, n=name: "\n" + _helm_define(n),
                block,
            )
    return block


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


# ── NF-15: every app workload gets the app secrets the API gets ──────────────

# API-only secrets: nothing a worker / beat runs reads them.
_API_ONLY = {"PLATFORM_ADMIN_KEY"}
# What every app process needs (app.core.config Settings + os.getenv readers):
# DB / Redis, MinIO (artifacts, training exports), JWT, the vault key pair,
# goal / stream tokens (HITL links), manifest signing, provider keys, SMTP.
_HELM_APP_SECRETS = {
    "DATABASE_PASSWORD",
    "REDIS_PASSWORD",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "JWT_SECRET",
    "VAULT_MASTER_KEY",
    "VAULT_PREVIOUS_MASTER_KEYS",
    "GOAL_TOKEN_SECRET",
    "MANIFEST_SIGNING_SECRET",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "VOYAGE_API_KEY",
    "GOOGLE_API_KEY",
    "SMTP_USER",
    "SMTP_PASSWORD",
}
_SECRET_REF = re.compile(
    r"- name: (\w+)\s+valueFrom:\s+secretKeyRef:\s+name: (\{\{[^}]+\}\}|\S+)\s+key: ([\w-]+)"
)
_PLAIN_ENV = re.compile(r"- name: (\w+)\s+value: (.+)")


def _secret_env(block: str) -> dict[str, tuple[str, str]]:
    return {n: (sec.strip(), key) for n, sec, key in _SECRET_REF.findall(block)}


def test_helm_every_app_workload_gets_the_api_app_secrets() -> None:
    blocks = {c: _expand(b) for c, b in _helm_blocks().items() if c != "frontend"}
    assert {"backend", "worker", "subgoal-worker", "beat"} <= set(blocks)
    secrets = {c: _secret_env(b) for c, b in blocks.items()}
    api = secrets["backend"]
    assert set(api) == _HELM_APP_SECRETS | _API_ONLY, sorted(set(api) ^ _HELM_APP_SECRETS)
    shared = {k: v for k, v in api.items() if k not in _API_ONLY}
    for comp, env in secrets.items():
        if comp == "backend":
            continue
        assert env == shared, f"{comp}: differs from the API: {set(env) ^ set(shared)}"
    # DATABASE_URL / REDIS_URL are built the same way everywhere.
    urls = {
        c: {n: v for n, v in _PLAIN_ENV.findall(b) if n in {"DATABASE_URL", "REDIS_URL"}}
        for c, b in blocks.items()
    }
    assert all(u == urls["backend"] and len(u) == 2 for u in urls.values()), urls


def test_helm_secret_provides_every_app_secret() -> None:
    text = (HELM_DIR / "secrets.yaml").read_text()
    for key in _HELM_APP_SECRETS | _API_ONLY:
        assert re.search(rf"^\s+{key}:", text, re.M), key


LEGACY_HELM_DIR = INFRA.parent / "helm" / "agentverse" / "templates"


def _legacy_env(fname: str) -> dict[str, tuple[str, str]]:
    text = (LEGACY_HELM_DIR / fname).read_text()
    helpers = (LEGACY_HELM_DIR / "_helpers.tpl").read_text()
    match = re.search(
        r'\{\{-? define "agentverse.appSecretEnv" -?\}\}(.*?)\{\{-? end -?\}\}', helpers, re.S
    )
    assert match, "legacy chart has no agentverse.appSecretEnv helper"
    text = re.sub(
        r'\{\{-? include "agentverse.appSecretEnv" \.(?: \| nindent \d+)? -?\}\}',
        lambda _m: "\n" + match.group(1),
        text,
    )
    return _secret_env(text)


def test_legacy_helm_workers_get_the_api_secrets_and_the_vault_key() -> None:
    api = _legacy_env("deployment.yaml")
    assert api.get("VAULT_MASTER_KEY") == ("agentverse-secrets", "master-encryption-key")
    assert "ANTHROPIC_API_KEY" in api
    # The vault never read MASTER_ENCRYPTION_KEY: the API ran on the dev key.
    assert "MASTER_ENCRYPTION_KEY" not in api
    for fname in (
        "worker-deployment.yaml",
        "subgoal-worker-deployment.yaml",
        "beat-deployment.yaml",
    ):
        assert _legacy_env(fname) == api, fname


def test_k8s_every_backend_workload_gets_the_api_secret_sources() -> None:
    """envFrom secretRefs + every explicit secretKeyRef of the API reach each workload."""
    workloads = _k8s_app_workloads()

    def refs(container: dict[str, Any]) -> set[str]:
        return {
            (r.get("secretRef") or {}).get("name", "")
            for r in container.get("envFrom") or []
            if r.get("secretRef")
        }

    api = next(c for _f, n, c in workloads if n == "agentverse-backend")
    api_refs = refs(api)
    assert api_refs
    for fname, name, container in workloads:
        assert refs(container) >= api_refs, f"{fname}:{name} lacks {api_refs - refs(container)}"


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
