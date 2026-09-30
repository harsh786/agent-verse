"""Static checks on the backend image build so a code-only change stays a small layer.

The venv (PyTorch & co.) is gigabytes; if it shares a COPY layer with the source, every
.py edit re-ships the whole venv and fills the Docker disk. These tests pin the layer
split, the build-context exclusions, and the CPU-only PyTorch source.
"""
from __future__ import annotations

import re
import tomllib

from tests._paths import BACKEND_ROOT

DOCKERFILE = (BACKEND_ROOT / "Dockerfile").read_text()


def _runtime_stage() -> str:
    stages = re.split(r"(?m)^FROM ", DOCKERFILE)
    runtime = [s for s in stages if re.match(r"\S+ AS runtime", s)]
    assert runtime, "Dockerfile must have a stage named 'runtime'"
    return runtime[0]


def _instructions(stage: str) -> list[str]:
    joined = re.sub(r"\\\n", " ", stage)
    return [ln.strip() for ln in joined.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_builder_installs_dependencies_only_from_lockfiles() -> None:
    builder = DOCKERFILE.split(" AS runtime")[0]
    copies = [i for i in _instructions(builder) if i.startswith("COPY")]
    assert copies == ["COPY pyproject.toml uv.lock /app/"], copies
    assert "--no-install-project" in builder
    assert "--frozen" in builder


def test_runtime_copies_venv_and_source_in_separate_layers() -> None:
    copies = [i for i in _instructions(_runtime_stage()) if i.startswith("COPY")]
    assert not any(re.search(r"--from=builder\S*\s.*\s/app\s+/app$", c) for c in copies), (
        "the whole builder /app must not be copied as one layer"
    )
    venv = [i for i, c in enumerate(copies) if "--from=builder" in c and "/app/.venv" in c]
    source = [i for i, c in enumerate(copies) if "--from=builder" not in c and " app " in f"{c} "]
    assert venv, "runtime must copy /app/.venv from the builder in its own layer"
    assert source, "runtime must copy the application source from the build context"
    assert venv[0] < source[0], "venv layer must come before the (frequently changing) source"


def test_runtime_makes_source_importable_without_installing_the_project() -> None:
    runtime = _runtime_stage()
    assert re.search(r"PYTHONPATH=/app\b", runtime)
    assert "alembic upgrade head" in runtime
    assert "uvicorn app.main:app" in runtime


def test_dockerignore_keeps_heavy_and_local_state_out_of_context() -> None:
    patterns = {
        ln.strip().rstrip("/")
        for ln in (BACKEND_ROOT / ".dockerignore").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    }
    for required in (
        ".venv", ".git", "tests", "node_modules", "htmlcov", ".coverage",
        "**/__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".env",
    ):
        assert required in patterns, f".dockerignore must exclude {required}"


def test_linux_torch_comes_from_the_cpu_only_index() -> None:
    pyproject = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())
    uv = pyproject["tool"]["uv"]
    index = {i["name"]: i for i in uv["index"]}
    cpu = [i for i in index.values() if i["url"].rstrip("/") == "https://download.pytorch.org/whl/cpu"]
    assert cpu and cpu[0].get("explicit") is True
    sources = uv["sources"]["torch"]
    assert any(
        s["index"] == cpu[0]["name"] and "linux" in s.get("marker", "") for s in sources
    ), sources


def test_lockfile_has_no_cuda_packages() -> None:
    lock = tomllib.loads((BACKEND_ROOT / "uv.lock").read_text())
    names = {p["name"] for p in lock["package"]}
    cuda = sorted(n for n in names if n.startswith(("nvidia-", "cuda-")) or n == "triton")
    assert not cuda, f"CUDA wheels would be installed in the CPU-only image: {cuda}"
