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
    "APP_DB_PASSWORD",  # NF-16: the app role, not the owner's DATABASE_PASSWORD
    "MAINTENANCE_DB_PASSWORD",
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
    # The owner password never reaches an app workload (NF-16).
    assert all("DATABASE_PASSWORD" not in env for env in secrets.values())


def test_helm_secret_provides_every_app_secret() -> None:
    text = (HELM_DIR / "secrets.yaml").read_text()
    for key in _HELM_APP_SECRETS | _API_ONLY | {"DATABASE_PASSWORD"}:
        assert re.search(rf"^\s+{key}:", text, re.M), key


LEGACY_HELM_DIR = INFRA.parent / "helm" / "agentverse" / "templates"


def _legacy_env(fname: str) -> dict[str, tuple[str, str]]:
    text = (LEGACY_HELM_DIR / fname).read_text()
    helpers = (LEGACY_HELM_DIR / "_helpers.tpl").read_text()
    for helper in ("agentverse.appSecretEnv", "agentverse.migrationEnv"):
        match = re.search(
            r'\{\{-? define "' + re.escape(helper) + r'" -?\}\}(.*?)\{\{-? end -?\}\}',
            helpers,
            re.S,
        )
        assert match, f"legacy chart has no {helper} helper"
        body = match.group(1)
        text = re.sub(
            r'\{\{-? include "' + re.escape(helper) + r'" \.(?: \| nindent \d+)? -?\}\}',
            lambda _m, b=body: "\n" + b,
            text,
        )
    return _secret_env(text)


def test_legacy_helm_workers_get_the_api_secrets_and_the_vault_key() -> None:
    api = _legacy_env("deployment.yaml")
    assert api.get("VAULT_MASTER_KEY") == ("agentverse-secrets", "master-encryption-key")
    assert "ANTHROPIC_API_KEY" in api
    # The vault never read MASTER_ENCRYPTION_KEY: the API ran on the dev key.
    assert "MASTER_ENCRYPTION_KEY" not in api
    shared = {
        k: v
        for k, v in api.items()
        if k not in {"MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD"}
    }
    for fname in (
        "worker-deployment.yaml",
        "subgoal-worker-deployment.yaml",
        "beat-deployment.yaml",
    ):
        assert _legacy_env(fname) == shared, fname


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


# ── NF-16: the three database roles (app / maintenance / migration) ──────────
#
# DATABASE_URL = least-privilege app role (NOSUPERUSER, NOBYPASSRLS),
# MAINTENANCE_DATABASE_URL = BYPASSRLS role for cross-tenant system jobs (beat
# scans run on workers; the API's startup warm-up), MIGRATION_DATABASE_URL =
# schema owner, ONLY for the migrate job (alembic + APP_DB_USER provisioning).


def _helm_template(name: str) -> str:
    return _expand((HELM_DIR / name).read_text())


def test_helm_app_workloads_get_app_and_maintenance_roles_but_never_the_owner() -> None:
    blocks = {c: _expand(b) for c, b in _helm_blocks().items() if c != "frontend"}
    for comp, block in blocks.items():
        plain = dict(_PLAIN_ENV.findall(block))
        assert "MAINTENANCE_DATABASE_URL" in plain, comp
        assert "MIGRATION_DATABASE_URL" not in plain, f"{comp} holds the owner DSN"
        assert plain["DATABASE_URL"].startswith(
            "postgresql+asyncpg://{{ .Values.postgresql.appUsername }}:$(APP_DB_PASSWORD)@"
        ), comp
        assert plain["MAINTENANCE_DATABASE_URL"].startswith(
            "postgresql+asyncpg://{{ include \"agentverse.maintenanceUsername\" . }}:"
            "$(MAINTENANCE_DB_PASSWORD)@"
        ), comp
        refs = _secret_env(block)
        assert refs["APP_DB_PASSWORD"][1] == "APP_DB_PASSWORD", comp
        assert refs["MAINTENANCE_DB_PASSWORD"][1] == "MAINTENANCE_DB_PASSWORD", comp
    # The app role cannot run DDL: the API no longer migrates on start.
    code = "\n".join(
        ln for ln in blocks["backend"].splitlines() if not ln.lstrip().startswith("#")
    )
    assert "alembic" not in code
    assert re.search(r'command: \["uvicorn"', blocks["backend"])


def test_helm_migrate_job_runs_as_owner_and_provisions_the_app_role() -> None:
    job = _helm_template("migrate-job.yaml")
    assert "kind: Job" in job
    assert '"alembic", "upgrade", "head"' in job
    plain = dict(_PLAIN_ENV.findall(job))
    assert plain["MIGRATION_DATABASE_URL"].startswith(
        "postgresql+asyncpg://{{ .Values.postgresql.username }}:$(DATABASE_PASSWORD)@"
    )
    assert plain["APP_DB_USER"] == "{{ .Values.postgresql.appUsername | quote }}"
    assert "DATABASE_URL" in plain
    refs = _secret_env(job)
    assert {"DATABASE_PASSWORD", "APP_DB_PASSWORD"} <= set(refs)
    secret = (HELM_DIR / "secrets.yaml").read_text()
    for key in ("APP_DB_PASSWORD", "MAINTENANCE_DB_PASSWORD"):
        assert re.search(rf"^\s+{key}:", secret, re.M), key


def _k8s_env_names(container: dict[str, Any], secret_keys: dict[str, set[str]]) -> set[str]:
    names = {e["name"] for e in container.get("env") or []}
    for ref in container.get("envFrom") or []:
        sec = (ref.get("secretRef") or {}).get("name")
        if sec:
            names |= secret_keys.get(sec, set())
    return names


def test_k8s_roles_reach_the_right_workloads() -> None:
    docs = _k8s_docs()
    secret_keys = _secret_keys(docs, _kustomized())
    for fname, name, container in _k8s_app_workloads():
        names = _k8s_env_names(container, secret_keys)
        literal_db = [
            e.get("value", "")
            for e in container.get("env") or []
            if e.get("name") == "DATABASE_URL" and "value" in e
        ]
        assert not literal_db, f"{name}: DATABASE_URL hard-coded (owner role?): {literal_db}"
        if name == "agentverse-db-migration":
            assert {"MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD", "DATABASE_URL"} <= (
                names
            ), f"{fname}: {sorted(names)}"
        else:
            assert "MAINTENANCE_DATABASE_URL" in names, f"{fname}:{name}"
            assert "MIGRATION_DATABASE_URL" not in names, f"{fname}:{name} holds the owner DSN"


def _pgbouncer() -> tuple[dict[str, Any], dict[str, Any]]:
    docs = [d for _f, d in _k8s_docs() if _f == "pgbouncer-deployment.yaml"]
    deploy = next(d for d in docs if d.get("kind") == "Deployment")
    scripts = next(d for d in docs if d.get("kind") == "ConfigMap")
    return deploy, scripts


def test_k8s_pgbouncer_knows_the_app_role() -> None:
    """NF-16 follow-up: app pods connect as APP_DB_USER through pgBouncer.

    The image writes only DB_USER (the owner) into its auth file; the same
    wrapper compose uses (infra/pgbouncer/add-app-user.sh) adds APP_DB_USER with
    the password from agentverse-secrets — the Secret the app's DATABASE_URL and
    the migration Job (which creates the role with it) read too.
    """
    deploy, scripts = _pgbouncer()
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    assert container["image"].startswith("edoburu/pgbouncer:")
    script = (INFRA / "pgbouncer" / "add-app-user.sh").read_text()
    assert scripts["data"]["add-app-user.sh"] == script, "k8s copy drifted from compose's"
    assert container["command"] == ["/bin/sh", "/opt/agentverse/add-app-user.sh"]
    mounts = {m["mountPath"] for m in container.get("volumeMounts") or []}
    assert "/opt/agentverse" in mounts
    env = {e["name"]: e for e in container["env"]}
    for name in ("APP_DB_USER", "APP_DB_PASSWORD", "DB_PASSWORD"):
        ref = env[name]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "agentverse-secrets", name
    secret_keys = _secret_keys(_k8s_docs(), _kustomized())["agentverse-secrets"]
    assert {"APP_DB_USER", "APP_DB_PASSWORD", "POSTGRES_PASSWORD"} <= secret_keys
    assert env["APP_DB_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "APP_DB_PASSWORD"
    assert env["AUTH_TYPE"]["value"] == "scram-sha-256"
    assert env["LISTEN_PORT"]["value"] == "5432"  # the Service / NetworkPolicy port
    ignored = env["IGNORE_STARTUP_PARAMETERS"]["value"]
    for param in ("statement_timeout", "idle_in_transaction_session_timeout"):
        assert param in ignored  # asyncpg sends them (app/db/session.py)


def test_k8s_dev_secret_app_dsn_matches_the_app_role() -> None:
    docs = [d for f, d in _k8s_docs() if f == "secrets.yaml"]
    main = next(d for d in docs if d["metadata"]["name"] == "agentverse-secrets")["stringData"]
    from sqlalchemy.engine import make_url

    url = make_url(main["DATABASE_URL"])
    assert url.username == main["APP_DB_USER"]
    assert url.host == "pgbouncer"
    migration = next(
        d for d in docs if d["metadata"]["name"] == "agentverse-migration-secrets"
    )["stringData"]
    # The role's credentials live in ONE Secret; the owner DSN alone is separate.
    assert set(migration) == {"MIGRATION_DATABASE_URL"}


_WAIT_CMD = '["python", "-m", "app.db.wait_for_schema"]'


def test_helm_app_pods_wait_for_the_migrated_schema_instead_of_crash_looping() -> None:
    """NF-16 follow-up: the migrate Job is not a hook, so on a first install the
    app pods start with it; they wait in Init until the schema (read as the app
    role) is at this image's alembic head."""
    blocks = {c: _expand(b) for c, b in _helm_blocks().items() if c != "frontend"}
    for comp, block in blocks.items():
        assert "initContainers:" in block, comp
        init, main = block.split("\n      containers:\n", 1)
        init = init.split("initContainers:", 1)[1]
        assert "- name: wait-for-schema" in init, comp
        assert f"command: {_WAIT_CMD}" in init, comp
        image = re.compile(r"image: (.+)")
        assert image.findall(init) == image.findall(main)[:1], comp  # same code + migrations
        db = {n: v for n, v in _PLAIN_ENV.findall(init) if n == "DATABASE_URL"}
        assert db and db == {n: v for n, v in _PLAIN_ENV.findall(main) if n == "DATABASE_URL"}
    assert "initContainers" not in _helm_template("migrate-job.yaml")  # it must not wait on itself


def test_k8s_app_pods_wait_for_the_migrated_schema() -> None:
    for fname, doc in _k8s_docs():
        pod = _pod_spec(doc)
        if not pod or doc.get("kind") != "Deployment":
            continue
        main = (pod.get("containers") or [{}])[0]
        if not _BACKEND_IMAGE.search(str(main.get("image", ""))):
            continue
        name = doc["metadata"]["name"]
        inits = {c["name"]: c for c in pod.get("initContainers") or []}
        assert "wait-for-schema" in inits, f"{fname}:{name}"
        wait = inits["wait-for-schema"]
        assert wait["command"] == ["python", "-m", "app.db.wait_for_schema"], name
        assert wait["image"] == main["image"], name

        def refs(c: dict[str, Any]) -> set[str]:
            return {
                (r.get("secretRef") or {}).get("name", "")
                for r in c.get("envFrom") or []
                if r.get("secretRef")
            }

        assert refs(main) and refs(wait) >= refs(main), name  # same DATABASE_URL
    job = next(d for f, d in _k8s_docs() if f == "migration-job.yaml")
    assert not (_pod_spec(job) or {}).get("initContainers")


def test_legacy_helm_roles() -> None:
    api = _legacy_env("deployment.yaml")
    assert api["MAINTENANCE_DATABASE_URL"] == ("agentverse-secrets", "maintenance-database-url")
    # The legacy API image migrates on start (owner DSN, API only).
    assert api["MIGRATION_DATABASE_URL"] == ("agentverse-secrets", "migration-database-url")
    for fname in ("worker-deployment.yaml", "subgoal-worker-deployment.yaml",
                  "beat-deployment.yaml"):
        env = _legacy_env(fname)
        assert "MAINTENANCE_DATABASE_URL" in env, fname
        assert "MIGRATION_DATABASE_URL" not in env, fname


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


# ── EGRESS-CFG: the operator egress allowlist reaches every app workload ──────
#
# app.ingestion.connector_egress reads INGESTION_ALLOW_INTERNAL_SOURCES +
# INGESTION_INTERNAL_SOURCE_ALLOWLIST (operator-only; tenant config can never
# widen them). Ingestion runs on the API (validate_connection) AND on the
# workers / beat-queued tasks, so each app workload must get both settings from
# the SAME place the API does, and every deployment must ship them OFF.

EGRESS_KEYS = ("INGESTION_ALLOW_INTERNAL_SOURCES", "INGESTION_INTERNAL_SOURCE_ALLOWLIST")
_EGRESS_HELM_VALUES = {
    "INGESTION_ALLOW_INTERNAL_SOURCES": "{{ .Values.ingestion.allowInternalSources | quote }}",
    "INGESTION_INTERNAL_SOURCE_ALLOWLIST": (
        "{{ .Values.ingestion.internalSourceAllowlist | quote }}"
    ),
}


def _config_map_refs(container_or_block: dict[str, Any] | str) -> list[str]:
    if isinstance(container_or_block, str):
        return [m.strip() for m in re.findall(r"- configMapRef:\s+name: (.+)", container_or_block)]
    return [
        (r.get("configMapRef") or {})["name"]
        for r in container_or_block.get("envFrom") or []
        if r.get("configMapRef")
    ]


def test_k8s_every_app_workload_gets_the_egress_settings_from_the_api_config_map() -> None:
    docs = _k8s_docs()
    config_maps = {
        d["metadata"]["name"]: dict(d.get("data") or {})
        for f, d in docs
        if d.get("kind") == "ConfigMap" and f in _kustomized()
    }
    secret_keys = _secret_keys(docs, _kustomized())
    workloads = [w for w in _k8s_app_workloads() if w[1] != "agentverse-db-migration"]
    api = next(c for _f, n, c in workloads if n == "agentverse-backend")
    api_maps = _config_map_refs(api)
    carrying = [m for m in api_maps if set(EGRESS_KEYS) <= set(config_maps.get(m, {}))]
    assert len(carrying) == 1, f"API config maps {api_maps}: none/several carry {EGRESS_KEYS}"
    source = config_maps[carrying[0]]
    # Default OFF, empty allowlist.
    assert source["INGESTION_ALLOW_INTERNAL_SOURCES"] == "false"
    assert source["INGESTION_INTERNAL_SOURCE_ALLOWLIST"] == ""
    # No Secret competes with the config map for the same names.
    for name, keys in secret_keys.items():
        assert not keys & set(EGRESS_KEYS), f"secret {name} also sets {EGRESS_KEYS}"
    names = {n for _f, n, _c in workloads}
    assert {"agentverse-worker", "agentverse-subgoal-worker", "agentverse-beat"} <= names
    for fname, name, container in workloads:
        assert carrying[0] in _config_map_refs(container), f"{fname}:{name} lacks {carrying[0]}"
        explicit = {e["name"] for e in container.get("env") or []} & set(EGRESS_KEYS)
        assert not explicit, f"{fname}:{name} overrides the shared value: {explicit}"


def _helm_values(chart: Path) -> dict[str, Any]:
    return dict(yaml.safe_load((chart.parent / "values.yaml").read_text()))


def _assert_helm_egress_config_map(config_map_text: str) -> None:
    for key, expr in _EGRESS_HELM_VALUES.items():
        assert re.search(rf"^\s+{key}: {re.escape(expr)}\s*$", config_map_text, re.M), key


def test_helm_every_app_workload_gets_the_egress_settings_from_one_config_map() -> None:
    values = _helm_values(HELM_DIR)
    # The allowlist escape hatch ships OFF; private network access ships ON
    # (owner decision 2026-10-06, ALLOW_PRIVATE_NETWORK_ACCESS).
    assert values["ingestion"] == {
        "allowInternalSources": False,
        "internalSourceAllowlist": "",
        "allowPrivateNetworkAccess": True,
    }
    cm = (HELM_DIR / "configmaps.yaml").read_text()
    _assert_helm_egress_config_map(cm)
    assert re.search(
        r"^\s+ALLOW_PRIVATE_NETWORK_ACCESS: "
        + re.escape("{{ .Values.ingestion.allowPrivateNetworkAccess | quote }}"),
        cm,
        re.M,
    )
    blocks = {c: _expand(b) for c, b in _helm_blocks().items() if c != "frontend"}
    assert {"backend", "worker", "subgoal-worker", "schedule-worker", "beat"} <= set(blocks)
    shared = '{{ include "agentverse.fullname" . }}-config'
    for comp, block in blocks.items():
        # The main container (after initContainers) reads the shared config map.
        main = block.split("\n      containers:\n", 1)[-1]
        assert shared in _config_map_refs(main), comp
        for key in EGRESS_KEYS:
            assert f"- name: {key}" not in block, f"{comp} overrides {key}"
    # backend.env is rendered into the same config map; a duplicate key there
    # would make the rendered YAML ambiguous.
    assert not set(values["backend"].get("env") or {}) & set(EGRESS_KEYS)


def test_legacy_helm_every_app_workload_gets_the_egress_settings_from_one_config_map() -> None:
    values = _helm_values(LEGACY_HELM_DIR)
    assert values["ingestion"] == {
        "allowInternalSources": False,
        "internalSourceAllowlist": "",
        "allowPrivateNetworkAccess": True,
    }
    _assert_helm_egress_config_map((LEGACY_HELM_DIR / "configmap.yaml").read_text())
    for fname in (
        "deployment.yaml",
        "worker-deployment.yaml",
        "subgoal-worker-deployment.yaml",
        "schedule-worker-deployment.yaml",
        "beat-deployment.yaml",
    ):
        text = (LEGACY_HELM_DIR / fname).read_text()
        assert _config_map_refs(text) == ["agentverse-config"], fname
        for key in EGRESS_KEYS:
            assert f"- name: {key}" not in text, f"{fname} overrides {key}"


def test_compose_prod_every_app_service_gets_the_egress_settings_off_by_default() -> None:
    services = {n: s for n, s in _compose("docker-compose.prod.yml").items() if _is_app_service(s)}
    assert {"backend", "worker", "schedule-worker", "subgoal-worker", "beat"} <= set(services)
    expected = {
        "INGESTION_ALLOW_INTERNAL_SOURCES": "${INGESTION_ALLOW_INTERNAL_SOURCES:-false}",
        "INGESTION_INTERNAL_SOURCE_ALLOWLIST": "${INGESTION_INTERNAL_SOURCE_ALLOWLIST:-}",
    }
    for name, svc in services.items():
        env = svc["environment"]
        assert {k: env.get(k) for k in EGRESS_KEYS} == expected, name


def test_compose_dev_never_pins_the_egress_settings_per_service() -> None:
    """Dev compose: every app service reads ../.env (asserted above); an explicit
    value on one service would make that service's egress policy differ."""
    services = {n: s for n, s in _compose("docker-compose.yml").items() if _is_app_service(s)}
    for name, svc in services.items():
        assert not set(svc.get("environment") or {}) & set(EGRESS_KEYS), name


def test_env_example_documents_the_egress_settings_off() -> None:
    text = (INFRA.parent / ".env.example").read_text()
    for key in EGRESS_KEYS:
        lines = [ln for ln in text.splitlines() if re.match(rf"#?\s*{key}=", ln)]
        assert lines, f".env.example does not document {key}"
        assert all(ln.startswith("#") for ln in lines), f"{key} must ship commented (off)"
    assert "# INGESTION_ALLOW_INTERNAL_SOURCES=false" in text
