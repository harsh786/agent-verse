"""The code-sandbox runner is deployed, locked down and wired in compose and BOTH charts.

Owner report: workflow ``code`` steps could never run in the deployed workers —
no Docker daemon in a worker container, and the unsandboxed fallback is off.
The fix is a dedicated runner (app/sandbox/runner.py) the app reaches over the
internal network with a shared secret; the host Docker socket is never mounted
into an app container (that would be a host-root escape).

helm is not installed here, so the chart templates are rendered by a small
evaluator of exactly the Go-template constructs they use (if / with / include /
toYaml / quote / nindent / default), with the chart's own values.yaml, and the
result is parsed as YAML and checked as Kubernetes objects.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[2]
INFRA_CHART = BACKEND / "infra" / "helm" / "agentverse"
LEGACY_CHART = BACKEND / "helm" / "agentverse"
COMPOSE_FILES = (
    BACKEND / "infra" / "docker-compose.yml",
    BACKEND / "infra" / "docker-compose.prod.yml",
)
RELEASE = "rel"
ALLOWED_CAPS = {"SETUID", "SETGID", "KILL"}

# ── a tiny Go-template evaluator ──────────────────────────────────────────────

_TAG = re.compile(r"\{\{(-?)\s*(.*?)\s*(-?)\}\}", re.S)


def _tokens(text: str) -> list[tuple[str, str]]:
    """[("text", s) | ("action", body)] with {{- / -}} whitespace trimming applied."""
    out: list[tuple[str, str]] = []
    pos = 0
    trim_next = False
    for m in _TAG.finditer(text):
        chunk = text[pos : m.start()]
        if trim_next:
            chunk = chunk.lstrip()
        if m.group(1) == "-":
            chunk = chunk.rstrip()
        out.append(("text", chunk))
        body = m.group(2)
        if not (body.startswith("/*") and body.endswith("*/")):
            out.append(("action", body))
        trim_next = m.group(3) == "-"
        pos = m.end()
    tail = text[pos:]
    out.append(("text", tail.lstrip() if trim_next else tail))
    return out


def _parse(
    tokens: list[tuple[str, str]], i: int = 0, stop: tuple[str, ...] = ()
) -> tuple[list[Any], int, str]:
    nodes: list[Any] = []
    while i < len(tokens):
        kind, body = tokens[i]
        if kind == "text":
            nodes.append(("text", body))
            i += 1
            continue
        word = body.split(" ", 1)[0]
        if word in stop:
            return nodes, i, word
        if word in ("if", "with"):
            then, i, ended = _parse(tokens, i + 1, ("else", "end"))
            other: list[Any] = []
            if ended == "else":
                other, i, _ = _parse(tokens, i + 1, ("end",))
            nodes.append((word, body.split(" ", 1)[1], then, other))
            i += 1
            continue
        nodes.append(("action", body))
        i += 1
    return nodes, i, ""


def _lookup(path: str, dot: Any) -> Any:
    value = dot
    for part in [p for p in path.split(".") if p]:
        value = value.get(part) if isinstance(value, dict) else None
    return value


class Chart:
    def __init__(self, chart: Path, values: dict[str, Any], helpers: dict[str, str]) -> None:
        self.chart = chart
        self.root = {
            "Values": values,
            "Release": {"Name": RELEASE, "Service": "Helm"},
            "Chart": {"Name": "agentverse", "Version": "1.0.0", "AppVersion": "1.0.0"},
        }
        self.helpers = helpers  # name -> already-rendered text (stubs for name/labels)
        self.defines = (chart / "templates" / "_helpers.tpl").read_text()

    def _define(self, name: str) -> list[Any]:
        tokens = _tokens(self.defines)
        for i, (kind, body) in enumerate(tokens):
            if kind == "action" and body == f'define "{name}"':
                nodes, _end, ended = _parse(tokens, i + 1, ("end",))
                assert ended == "end", name
                return nodes
        raise AssertionError(f"helper {name!r} is not defined")

    def _command(self, cmd: str, dot: Any, piped: Any, has_piped: bool) -> Any:
        parts = cmd.split()
        head = parts[0]
        if head == "include":
            name = json.loads(parts[1])
            if name in self.helpers:
                return self.helpers[name]
            return self._render(self._define(name), dot)
        if head == "toYaml":
            value = self._value(parts[1], dot)
            return yaml.safe_dump(value, default_flow_style=False, sort_keys=False).rstrip("\n")
        if head == "quote":
            return json.dumps("" if piped is None else str(piped))
        if head == "nindent":
            pad = " " * int(parts[1])
            return "\n" + "\n".join(pad + line if line else line for line in str(piped).split("\n"))
        if head == "default":
            fallback = self._value(parts[1], dot)
            return piped if piped not in (None, "", [], {}) else fallback
        assert not has_piped, f"unsupported pipeline function {cmd!r}"
        return self._value(cmd, dot)

    def _value(self, expr: str, dot: Any) -> Any:
        if expr == ".":
            return dot
        if expr.startswith('"'):
            return json.loads(expr)
        if expr.startswith("."):
            return _lookup(expr, dot)
        if re.fullmatch(r"-?\d+", expr):
            return int(expr)
        raise AssertionError(f"unsupported template expression {expr!r}")

    def _eval(self, pipeline: str, dot: Any) -> Any:
        value: Any = None
        has = False
        for cmd in (c.strip() for c in pipeline.split("|")):
            value = self._command(cmd, dot, value, has)
            has = True
        return value

    def _render(self, nodes: list[Any], dot: Any) -> str:
        out: list[str] = []
        for node in nodes:
            if node[0] == "text":
                out.append(node[1])
            elif node[0] == "action":
                value = self._eval(node[1], dot)
                out.append("" if value is None else str(value))
            else:
                kind, expr, then, other = node
                value = self._eval(expr, dot)
                truthy = value not in (None, False, "", 0, [], {})
                if truthy:
                    out.append(self._render(then, value if kind == "with" else dot))
                else:
                    out.append(self._render(other, dot))
        return "".join(out)

    def render(self, template: str) -> list[dict[str, Any]]:
        nodes, _i, _e = _parse(_tokens((self.chart / "templates" / template).read_text()))
        text = self._render(nodes, self.root)
        return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]

    def render_helper(self, name: str) -> Any:
        return yaml.safe_load(self._render(self._define(name), self.root) or "[]")


def _values(chart: Path, **overrides: Any) -> dict[str, Any]:
    values = copy.deepcopy(yaml.safe_load((chart / "values.yaml").read_text()))
    for dotted, value in overrides.items():
        node = values
        *parents, leaf = dotted.split(".")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return values


def _infra(**overrides: Any) -> Chart:
    labels = (
        "app.kubernetes.io/name: agentverse\napp.kubernetes.io/instance: rel\n"
        "app.kubernetes.io/part-of: agentverse"
    )
    return Chart(
        INFRA_CHART,
        _values(INFRA_CHART, **overrides),
        {
            "agentverse.fullname": f"{RELEASE}-agentverse",
            "agentverse.secretName": f"{RELEASE}-agentverse-secrets",
            "agentverse.labels": labels,
            "agentverse.selectorLabels": (
                "app.kubernetes.io/name: agentverse\napp.kubernetes.io/instance: rel"
            ),
        },
    )


def _legacy(**overrides: Any) -> Chart:
    values = _values(LEGACY_CHART, **overrides)
    image = f"{values['global']['imageRegistry']}/{values['codeSandbox']['image']['name']}"
    return Chart(
        LEGACY_CHART,
        values,
        {
            "agentverse.labels": "app.kubernetes.io/managed-by: Helm\napp.kubernetes.io/instance: rel",
            "agentverse.codeSandboxImage": f"{image}:{values['codeSandbox']['image']['tag']}",
        },
    )


CHARTS = {"infra": _infra, "legacy": _legacy}


def _by_kind(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    kinds = {d["kind"]: d for d in docs}
    assert len(kinds) == len(docs), [d["kind"] for d in docs]
    return kinds


def _matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())


# ── charts ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("chart_name", sorted(CHARTS))
def test_chart_renders_a_locked_down_runner(chart_name: str) -> None:
    chart = CHARTS[chart_name]()
    docs = _by_kind(chart.render("code-sandbox.yaml"))
    assert set(docs) == {"Deployment", "Service", "NetworkPolicy"}

    deploy = docs["Deployment"]
    pod = deploy["spec"]["template"]["spec"]
    pod_labels = deploy["spec"]["template"]["metadata"]["labels"]
    assert _matches(deploy["spec"]["selector"]["matchLabels"], pod_labels)
    # A service-account token would be readable by the programs.
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    (container,) = pod["containers"]
    sc = container["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["capabilities"]["drop"] == ["ALL"]
    assert set(sc["capabilities"]["add"]) <= ALLOWED_CAPS
    assert "privileged" not in sc
    # No host access of any kind.
    assert not pod.get("hostNetwork") and not pod.get("hostPID")
    for vol in pod["volumes"]:
        assert set(vol) <= {"name", "emptyDir"}, vol
    mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols[mounts["/sandbox"]]["emptyDir"]["medium"] == "Memory"
    assert vols[mounts["/sandbox"]]["emptyDir"]["sizeLimit"]
    # The shared secret comes from the chart's Secret, never a literal.
    env = {e["name"]: e for e in container["env"]}
    token_ref = env["CODE_SANDBOX_TOKEN"]["valueFrom"]["secretKeyRef"]
    assert "optional" not in token_ref  # the runner never starts without it
    assert "value" not in env["CODE_SANDBOX_TOKEN"]
    # Only its own settings: no platform secret reaches the runner.
    assert set(env) <= {
        "CODE_SANDBOX_TOKEN",
        "CODE_SANDBOX_PORT",
        "CODE_SANDBOX_WORKDIR",
        "CODE_SANDBOX_MAX_CONCURRENCY",
        "CODE_SANDBOX_MEMORY_MB",
        "CODE_SANDBOX_MAX_TIMEOUT_SECONDS",
    }
    assert "envFrom" not in container
    assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["resources"]["limits"]["memory"]

    svc = docs["Service"]
    assert svc["spec"]["type"] == "ClusterIP"
    assert _matches(svc["spec"]["selector"], pod_labels)
    assert svc["spec"]["ports"][0]["targetPort"] == "http"

    policy = docs["NetworkPolicy"]["spec"]
    assert _matches(policy["podSelector"]["matchLabels"], pod_labels)
    assert set(policy["policyTypes"]) == {"Ingress", "Egress"}
    assert policy["egress"] == []  # deny ALL egress, DNS included
    (rule,) = policy["ingress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8080}]
    (peer,) = rule["from"]
    (expr,) = peer["podSelector"]["matchExpressions"]
    assert expr["operator"] == "In"
    callers = set(expr["values"])
    if chart_name == "infra":
        assert callers == {"backend", "worker", "subgoal-worker"}
    else:
        assert callers == {"agentverse-backend", "agentverse-worker", "agentverse-subgoal-worker"}


@pytest.mark.parametrize("chart_name", sorted(CHARTS))
def test_app_env_points_at_the_runner_service_with_the_same_secret(chart_name: str) -> None:
    chart = CHARTS[chart_name]()
    docs = _by_kind(chart.render("code-sandbox.yaml"))
    env = {e["name"]: e for e in chart.render_helper("agentverse.codeSandboxEnv")}
    assert set(env) == {"CODE_SANDBOX_URL", "CODE_SANDBOX_TOKEN"}

    svc = docs["Service"]
    port = svc["spec"]["ports"][0]["port"]
    url = env["CODE_SANDBOX_URL"]["value"]
    assert url.startswith(f"http://{svc['metadata']['name']}")
    assert url.endswith(f":{port}")

    runner_env = {
        e["name"]: e for e in docs["Deployment"]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    runner_ref = runner_env["CODE_SANDBOX_TOKEN"]["valueFrom"]["secretKeyRef"]
    app_ref = env["CODE_SANDBOX_TOKEN"]["valueFrom"]["secretKeyRef"]
    assert (app_ref["name"], app_ref["key"]) == (runner_ref["name"], runner_ref["key"])


@pytest.mark.parametrize("chart_name", sorted(CHARTS))
def test_disabled_runner_renders_nothing(chart_name: str) -> None:
    chart = CHARTS[chart_name](**{"codeSandbox.enabled": False})
    assert chart.render("code-sandbox.yaml") == []
    assert chart.render_helper("agentverse.codeSandboxEnv") in (None, [])


def test_infra_chart_command_override_runs_the_runner_from_the_backend_image() -> None:
    chart = _infra(**{"codeSandbox.command": ["python", "-m", "app.sandbox.runner"]})
    deploy = _by_kind(chart.render("code-sandbox.yaml"))["Deployment"]
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "app.sandbox.runner"]


def _infra_workload_blocks() -> dict[str, str]:
    text = (INFRA_CHART / "templates" / "app-workloads.yaml").read_text()
    blocks: dict[str, str] = {}
    for block in re.split(r"^---\s*$", text, flags=re.M):
        if "kind: Deployment" in block:
            comp = re.search(r"app\.kubernetes\.io/component: ([\w-]+)", block)
            assert comp
            blocks[comp.group(1)] = block
    return blocks


_INCLUDE = 'include "agentverse.codeSandboxEnv"'


def test_infra_chart_code_executing_workloads_get_the_runner_env() -> None:
    blocks = _infra_workload_blocks()
    for comp in ("backend", "worker", "subgoal-worker"):
        main = blocks[comp].split("containers:", 1)[1]  # not the wait-for-schema init
        assert _INCLUDE in main, comp
    secret = (INFRA_CHART / "templates" / "secrets.yaml").read_text()
    assert re.search(
        r"^\s+CODE_SANDBOX_TOKEN: \{\{ \.Values\.secrets\.codeSandboxToken", secret, re.M
    )
    values = yaml.safe_load((INFRA_CHART / "values.yaml").read_text())
    assert len(values["secrets"]["codeSandboxToken"]) >= 16


def test_legacy_chart_code_executing_workloads_get_the_runner_env() -> None:
    for fname in ("deployment.yaml", "worker-deployment.yaml", "subgoal-worker-deployment.yaml"):
        text = (LEGACY_CHART / "templates" / fname).read_text()
        main = text.split("containers:", 1)[1]
        assert _INCLUDE in main, fname
    ext = (LEGACY_CHART / "templates" / "externalsecret.yaml").read_text()
    assert "secretKey: code-sandbox-token" in ext
    assert "externalSecrets.secrets.codeSandboxToken" in ext
    values = yaml.safe_load((LEGACY_CHART / "values.yaml").read_text())
    assert values["externalSecrets"]["secrets"]["codeSandboxToken"]["remoteRef"]["key"]


@pytest.mark.parametrize("chart", [INFRA_CHART, LEGACY_CHART], ids=["infra", "legacy"])
def test_no_chart_mounts_a_docker_socket(chart: Path) -> None:
    for template in (chart / "templates").glob("*.yaml"):
        text = template.read_text()
        assert "docker.sock" not in text, template.name


# ── compose ───────────────────────────────────────────────────────────────────


def _compose(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _env(svc: dict[str, Any]) -> dict[str, str]:
    env = svc.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env)
    return {k: str(v) for k, v in env.items()}


def _networks(svc: dict[str, Any]) -> set[str]:
    # A list or a mapping of network names: set() takes either.
    return set(svc.get("networks") or [])


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_runner_is_isolated_and_locked_down(path: Path) -> None:
    compose = _compose(path)
    svc = compose["services"]["code-sandbox"]
    assert svc["build"]["dockerfile"] == "Dockerfile.sandbox"
    assert svc.get("restart")
    # Its only network is internal: no route to the host or the internet.
    assert _networks(svc) == {"code-sandbox"}
    assert compose["networks"]["code-sandbox"]["internal"] is True
    assert "ports" not in svc  # never published
    assert "volumes" not in svc  # no host paths, no Docker socket
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert set(svc["cap_add"]) <= ALLOWED_CAPS
    assert any(o.startswith("no-new-privileges") for o in svc["security_opt"])
    assert any(t.startswith("/sandbox:") for t in svc["tmpfs"])
    assert "privileged" not in svc and "network_mode" not in svc
    limits = svc["deploy"]["resources"]["limits"]
    assert limits["memory"] and limits["pids"]
    env = _env(svc)
    assert "CODE_SANDBOX_TOKEN" in env
    # Nothing of the platform's configuration.
    assert not {"DATABASE_URL", "REDIS_URL", "VAULT_MASTER_KEY"} & set(env)
    assert "env_file" not in svc


@pytest.mark.parametrize(
    ("path", "callers"),
    [
        (COMPOSE_FILES[0], ("backend", "worker", "subgoal-worker", "workflow-worker")),
        (COMPOSE_FILES[1], ("backend", "worker", "subgoal-worker")),
    ],
    ids=["dev", "prod"],
)
def test_compose_callers_reach_the_runner_without_a_docker_socket(
    path: Path, callers: tuple[str, ...]
) -> None:
    compose = _compose(path)
    runner_token = _env(compose["services"]["code-sandbox"])["CODE_SANDBOX_TOKEN"]
    for name in callers:
        svc = compose["services"][name]
        env = _env(svc)
        # A literal or a ${VAR:-default} whose default is the runner service.
        assert (
            env["CODE_SANDBOX_URL"].rstrip("}").rstrip("/").endswith("http://code-sandbox:8080")
        ), name
        assert env["CODE_SANDBOX_TOKEN"] == runner_token, name
        assert {"code-sandbox", "default"} <= _networks(svc), name
    for name, svc in compose["services"].items():
        if name in {"promtail"}:
            continue  # log shipper (read-only), not an app container
        for vol in svc.get("volumes") or []:
            assert "docker.sock" not in str(vol), f"{path.name}:{name} mounts the Docker socket"
        assert not svc.get("group_add"), f"{path.name}:{name}"


def test_sandbox_dockerfile_is_the_slim_base_plus_the_runner_only() -> None:
    text = (BACKEND / "Dockerfile.sandbox").read_text()
    instructions = [
        line.split()[0]
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and not line.startswith(" ")
    ]
    assert re.search(r"^FROM python:3\.12-slim\s*$", text, re.M)
    assert instructions.count("FROM") == 1
    run_lines = " ".join(re.findall(r"^RUN (.+)$", text, re.M))
    assert "pip" not in run_lines and "apt-get" not in run_lines  # no downloads at build
    copies = re.findall(r"^COPY (.+)$", text, re.M)
    assert copies == ["app/sandbox/runner.py /opt/code-sandbox/runner.py"]
    assert not re.search(r"^COPY --chmod", text, re.M)  # BuildKit-only (Minikube/colima)
