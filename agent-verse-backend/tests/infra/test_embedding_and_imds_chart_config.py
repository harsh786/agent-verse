"""BUG A / BUG B chart settings reach EVERY app workload (both Helm charts).

BUG B: the cluster had no embedder, so every ingestion path answered 503
"embedding provider not configured". The fix is chart config: NVIDIA_API_KEY (a
Secret, never the ConfigMap) + NVIDIA_EMBED_MODEL / the other embedding settings
(the shared ConfigMap), on the API and every worker / beat — the worker ingests
documents and the API embeds queries, so both must embed with the same model.

BUG A, defence in depth: AWS_EC2_METADATA_DISABLED=true on every app workload
(tenant S3 / Kinesis clients never consult IMDS anyway: app/net/aws_clients.py).

helm is not installed here, so the shared ConfigMap template is rendered by a
small evaluator of exactly the constructs it uses (``with`` / ``if`` / ``range`` /
``quote``) and the result is parsed as YAML; the workloads are checked with the
helper-expansion used by tests/infra/test_vault_key_distribution.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.infra.test_vault_key_distribution import (
    HELM_DIR,
    LEGACY_HELM_DIR,
    _config_map_refs,
    _expand,
    _helm_blocks,
    _legacy_env,
)

EMBEDDING_KEYS = (
    "NVIDIA_EMBED_MODEL",
    "NVIDIA_EMBED_DIM",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
)
SECRET_KEYS = ("NVIDIA_API_KEY", "EMBEDDING_API_KEY")
APP_COMPONENTS = {
    "backend",
    "worker",
    "subgoal-worker",
    "schedule-worker",
    "maintenance-worker",
    "beat",
}
_OWNER_EMBEDDING = {
    "nvidiaEmbedModel": "nvidia/nemotron-3-embed-1b",
    "nvidiaEmbedDim": 2048,
    "baseUrl": "",
    "model": "",
    "dim": 2048,
}

# ── a tiny evaluator for the config map template ──────────────────────────────

_TAG = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}")


def _lookup(expr: str, root: dict[str, Any], ctx: Any) -> Any:
    expr = expr.strip()
    if expr.startswith("include "):
        return "<include>"
    if expr.startswith(".Values."):
        value: Any = root
        path = expr[len(".Values."):].split(".")
    elif expr.startswith("."):
        value = ctx
        path = [p for p in expr[1:].split(".") if p]
    else:
        raise AssertionError(f"unsupported template expression {expr!r}")
    for part in path:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _value(expr: str, root: dict[str, Any], ctx: Any, local: dict[str, Any]) -> str:
    pipeline = [p.strip() for p in expr.split("|")]
    head = pipeline[0]
    if head.startswith("include "):
        return "include-placeholder"  # names / labels: not what these tests check
    value = local[head] if head in local else _lookup(head, root, ctx)
    for fn in pipeline[1:]:
        if fn.startswith("default "):
            value = value if value not in (None, "") else json.loads(fn.split(" ", 1)[1])
        elif fn == "quote":
            value = json.dumps("" if value is None else str(value).lower()
                               if isinstance(value, bool) else str(value))
        else:
            raise AssertionError(f"unsupported pipeline function {fn!r}")
    return str(value)


def _render(lines: list[str], root: dict[str, Any], ctx: Any,
            local: dict[str, Any] | None = None) -> list[str]:
    local = local or {}
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        control = re.fullmatch(r"\s*\{\{-?\s*(with|if|range)\s+(.*?)\s*-?\}\}\s*", line)
        if control:
            depth, j = 1, i + 1
            while depth:
                if re.fullmatch(r"\s*\{\{-?\s*(with|if|range)\b.*", lines[j]):
                    depth += 1
                elif re.fullmatch(r"\s*\{\{-?\s*end\s*-?\}\}\s*", lines[j]):
                    depth -= 1
                j += 1
            body = lines[i + 1:j - 1]
            kind, expr = control.groups()
            if kind == "range":
                names, source = expr.split(":=")
                key_var, val_var = (n.strip() for n in names.split(","))
                for k, v in sorted((_lookup(source, root, ctx) or {}).items()):
                    out += _render(body, root, ctx, {**local, key_var: k, val_var: v})
            else:
                value = _lookup(expr, root, ctx)
                if value not in (None, "", {}, [], False, 0):
                    out += _render(body, root, value if kind == "with" else ctx, local)
            i = j
            continue
        if re.fullmatch(r"\s*\{\{-?\s*/\*.*\*/\s*-?\}\}\s*", line):
            i += 1
            continue
        out.append(_TAG.sub(lambda m: _value(m.group(1), root, ctx, local), line))
        i += 1
    return out


def _config_map_data(template: Path, values: dict[str, Any]) -> dict[str, str]:
    first = re.split(r"^---\s*$", template.read_text(), flags=re.M)[0]
    rendered = "\n".join(_render(first.splitlines(), values, values))
    doc = yaml.safe_load(rendered)
    assert doc["kind"] == "ConfigMap"
    return {k: str(v) for k, v in (doc.get("data") or {}).items()}


_CHARTS = {
    "infra": (HELM_DIR / "configmaps.yaml", HELM_DIR.parent / "values.yaml"),
    "legacy": (LEGACY_HELM_DIR / "configmap.yaml", LEGACY_HELM_DIR.parent / "values.yaml"),
}


def _values(chart: str, **embedding: Any) -> dict[str, Any]:
    values = dict(yaml.safe_load(_CHARTS[chart][1].read_text()))
    if embedding:
        values["embedding"] = {**values["embedding"], **embedding}
    return values


# ── the shared config map ─────────────────────────────────────────────────────


@pytest.mark.parametrize("chart", sorted(_CHARTS))
def test_defaults_disable_imds_and_render_no_empty_embedding_settings(chart: str) -> None:
    values = _values(chart)
    assert values["embedding"] == {
        "nvidiaEmbedModel": "", "nvidiaEmbedDim": "", "baseUrl": "", "model": "", "dim": "",
    }
    data = _config_map_data(_CHARTS[chart][0], values)
    assert data["AWS_EC2_METADATA_DISABLED"] == "true"
    # Empty = not rendered: an EMBEDDING_DIM="" would not even parse as an int.
    assert not set(EMBEDDING_KEYS) & set(data), data


@pytest.mark.parametrize("chart", sorted(_CHARTS))
def test_the_owners_embedding_values_reach_the_config_map(chart: str) -> None:
    data = _config_map_data(_CHARTS[chart][0], _values(chart, **_OWNER_EMBEDDING))
    assert data["NVIDIA_EMBED_MODEL"] == "nvidia/nemotron-3-embed-1b"
    assert data["NVIDIA_EMBED_DIM"] == "2048"
    assert data["EMBEDDING_DIM"] == "2048"
    assert "EMBEDDING_BASE_URL" not in data  # empty: NVIDIA's endpoint applies

    generic = _config_map_data(
        _CHARTS[chart][0],
        _values(chart, baseUrl="http://embed.internal:8000/v1", model="Qwen/Qwen3-Embedding-0.6B",
                dim=1024),
    )
    assert generic["EMBEDDING_BASE_URL"] == "http://embed.internal:8000/v1"
    assert generic["EMBEDDING_MODEL"] == "Qwen/Qwen3-Embedding-0.6B"
    assert generic["EMBEDDING_DIM"] == "1024"


@pytest.mark.parametrize("chart", sorted(_CHARTS))
def test_no_key_ever_lands_in_the_config_map(chart: str) -> None:
    template = _CHARTS[chart][0].read_text()
    for key in (*SECRET_KEYS, "VAULT_MASTER_KEY", "OPENAI_API_KEY"):
        assert not re.search(rf"^\s+{key}:", template, re.M), key
    assert "nvidiaApiKey" not in template and "embeddingApiKey" not in template


# ── every app workload: the config map + the keys from the Secret ─────────────


def test_infra_helm_every_app_workload_gets_the_settings_and_the_keys() -> None:
    blocks = {c: _expand(b) for c, b in _helm_blocks().items() if c != "frontend"}
    assert set(blocks) >= APP_COMPONENTS, sorted(blocks)
    shared = '{{ include "agentverse.fullname" . }}-config'
    pattern = re.compile(
        r"- name: (\w+)\s+valueFrom:\s+secretKeyRef:\s+name: (\{\{[^}]+\}\})\s+key: (\w+)"
        r"\s+optional: (\w+)"
    )
    for comp, block in blocks.items():
        main = block.split("\n      containers:\n", 1)[-1]
        assert shared in _config_map_refs(main), comp
        refs = {n: (sec, key, opt) for n, sec, key, opt in pattern.findall(main)}
        for name in SECRET_KEYS:
            assert refs.get(name) == ('{{ include "agentverse.secretName" . }}', name, "true"), (
                comp, name, refs.get(name))
        for key in (*EMBEDDING_KEYS, "AWS_EC2_METADATA_DISABLED"):
            assert f"- name: {key}" not in main, f"{comp} overrides the shared {key}"


def test_infra_helm_secret_and_values_carry_the_embedding_keys() -> None:
    secret = (HELM_DIR / "secrets.yaml").read_text()
    assert re.search(r"^\s+NVIDIA_API_KEY: \{\{ \.Values\.secrets\.nvidiaApiKey ", secret, re.M)
    assert re.search(
        r"^\s+EMBEDDING_API_KEY: \{\{ \.Values\.secrets\.embeddingApiKey ", secret, re.M
    )
    values = _values("infra")
    assert values["secrets"]["nvidiaApiKey"] == ""  # never a real key in the chart
    assert values["secrets"]["embeddingApiKey"] == ""


_LEGACY_APP_FILES = (
    "deployment.yaml",
    "worker-deployment.yaml",
    "subgoal-worker-deployment.yaml",
    "schedule-worker-deployment.yaml",
    "maintenance-worker-deployment.yaml",
    "beat-deployment.yaml",
)


@pytest.mark.parametrize("fname", _LEGACY_APP_FILES)
def test_legacy_helm_every_app_workload_gets_the_settings_and_the_keys(fname: str) -> None:
    env = _legacy_env(fname)
    assert env["NVIDIA_API_KEY"] == ("agentverse-secrets", "nvidia-api-key")
    assert env["EMBEDDING_API_KEY"] == ("agentverse-secrets", "embedding-api-key")
    text = (LEGACY_HELM_DIR / fname).read_text()
    assert "agentverse-config" in _config_map_refs(text), fname


def test_legacy_helm_external_secret_maps_the_nvidia_key() -> None:
    template = (LEGACY_HELM_DIR / "externalsecret.yaml").read_text()
    assert '"nvidiaApiKey" "nvidia-api-key"' in template
    assert '"embeddingApiKey" "embedding-api-key"' in template
    values = _values("legacy")
    assert values["externalSecrets"]["secrets"]["nvidiaApiKey"]["remoteRef"]["key"] == (
        "agentverse/nvidia-api-key"
    )


# ── the backend reads exactly these names ─────────────────────────────────────


def test_the_backend_builds_the_nvidia_embedder_from_these_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.providers.embedder_factory import resolve_embedder

    for name in ("EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_API_KEY", "VOYAGE_API_KEY",
                 "OPENAI_API_KEY", "GOOGLE_API_KEY", "SENTENCE_TRANSFORMERS_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key-placeholder")
    monkeypatch.setenv("NVIDIA_EMBED_MODEL", "nvidia/nemotron-3-embed-1b")
    monkeypatch.setenv("NVIDIA_EMBED_DIM", "2048")
    monkeypatch.setenv("EMBEDDING_DIM", "2048")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    resolution = resolve_embedder(settings)
    assert resolution.embedder is not None, resolution.reason()
    assert resolution.provider == "dedicated"
    assert resolution.model == "nvidia/nemotron-3-embed-1b"
    assert resolution.dimension == 2048
    assert settings.embedding_base_url == "https://integrate.api.nvidia.com/v1"
