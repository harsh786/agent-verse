"""BYOK-4: a tenant BYOK key saved through the API is decrypted and used by a
SEPARATE Celery worker process — for a goal and for a workflow LLM step — and a
worker with another VAULT_MASTER_KEY fails with the fingerprint-mismatch error.

Real Postgres + Redis (testcontainers, the e2e_full harness), the booted API
(``create_app(manage_pools=True)``) and real ``celery worker`` subprocesses built
exactly as in production (worker_init vault check, worker LLM config store,
workflow worker runner). The tenant's provider is an OpenAI-compatible endpoint
served by a local fake HTTP LLM that records the ``Authorization`` header: the
tenant's own key arriving there proves the worker decrypted the API's
ciphertext and used it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests._worker_procs import node_name, worker_process

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

API = "/api/v1"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
QUEUES = (
    "goals.free,goals.starter,goals.professional,goals.enterprise,"
    "workflows.free,workflows.starter,workflows.professional,workflows.enterprise"
)
TERMINAL = {"complete", "failed", "cancelled", "timed_out"}
WRONG_KEY = "byok4-worker-has-a-different-master-key-" + "z" * 16
_ANSWER = json.dumps(
    {
        "steps": ["Answer the question briefly"],
        "success": True,
        "complete": True,
        "reason": "done",
        "answer": "ok",
    }
)


class _FakeLLM:
    """OpenAI-compatible /v1/chat/completions that records every Authorization header."""

    def __init__(self) -> None:
        self.auth_headers: list[str] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                return

            def _json(self, code: int, body: dict[str, Any]) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                outer.auth_headers.append(self.headers.get("Authorization", ""))
                self._json(200, {"object": "list", "data": [{"id": "byok-model"}]})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.auth_headers.append(self.headers.get("Authorization", ""))
                if not self.path.endswith("/chat/completions"):
                    self._json(404, {"error": {"message": "not found"}})
                    return
                self._json(
                    200,
                    {
                        "id": f"cmpl-{uuid.uuid4().hex[:8]}",
                        "object": "chat.completion",
                        "created": 0,
                        "model": body.get("model") or "byok-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": _ANSWER},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                    },
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _FakeLLM:
        self._thread.start()
        return self

    def __exit__(self, *_a: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(scope="module")
def fake_llm() -> Iterator[_FakeLLM]:
    with _FakeLLM() as server:
        yield server


@pytest.fixture(autouse=True)
def _allow_local_llm_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # The tenant base_url points at the local fake LLM: the operator allowlist
    # for private LLM hosts (read per call by the API and by the worker env).
    monkeypatch.setenv("TENANT_LLM_ALLOWED_PRIVATE_HOSTS", "127.0.0.1")


def _worker_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["ENVIRONMENT"] = "development"
    env["TENANT_LLM_ALLOWED_PRIVATE_HOSTS"] = "127.0.0.1"
    # No platform LLM: only the tenant's own key can serve these runs.
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
        env.pop(name, None)
    env.update(extra or {})
    assert env.get("DATABASE_URL") and env.get("REDIS_URL"), "app fixture must export DSNs"
    return env


def _worker(tmp: Path, name: str, extra: dict[str, str] | None = None) -> Any:
    return worker_process(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.scaling.celery_app",
            "worker",
            "-Q",
            QUEUES,
            "--loglevel=info",
            "--concurrency=1",
            "-n",
            node_name(name),
        ],
        cwd=tmp,
        env=_worker_env(extra),
        log_path=tmp / "worker.log",
        ready_timeout=120.0,
        name=name,
    )


async def _save_byok(tenant_client: Any, port: int, api_key: str) -> None:
    resp = await tenant_client.put(
        "/tenants/me/llm",
        json={
            "provider": "openai_compatible",
            "api_key": api_key,
            "base_url": f"http://127.0.0.1:{port}/v1",
            "default_model": "byok-model",
        },
    )
    assert resp.status_code == 200, resp.text


async def _tenant_id(tenant_client: Any) -> str:
    resp = await tenant_client.get("/tenants/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return str(body.get("tenant_id") or body.get("id"))


async def _stored_row(owner_dsn: str, tenant_id: str) -> tuple[str, str | None]:
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT encrypted_key, vault_key_fingerprint FROM tenant_llm_configs "
            "WHERE tenant_id = $1",
            tenant_id,
        )
    finally:
        await conn.close()
    assert row is not None, "PUT /tenants/me/llm did not persist the config"
    return str(row[0]), row[1]


async def _run_goal(tenant_client: Any, timeout: float = 150.0) -> dict[str, Any]:
    resp = await tenant_client.post("/goals", json={"goal": "Say hello to the team"})
    assert resp.status_code == 202, resp.text
    goal_id = resp.json()["goal_id"]
    deadline = asyncio.get_event_loop().time() + timeout
    goal: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"/goals/{goal_id}")
        if got.status_code == 200:
            goal = got.json()
            if goal.get("status") in TERMINAL:
                return goal
        await asyncio.sleep(1.0)
    raise AssertionError(f"goal never reached a terminal status: {goal!r}")


async def _run_llm_workflow(tenant_client: Any, timeout: float = 120.0) -> dict[str, Any]:
    resp = await tenant_client.post(
        f"{API}/workflows",
        json={
            "name": f"byok-{uuid.uuid4().hex[:8]}",
            "description": "",
            "definition": {
                "name": "BYOK llm step",
                "steps": [
                    {
                        "id": "draft",
                        "type": "llm",
                        "prompt": "Write one line for {{inputs.topic}}",
                        "on_failure": "abort",
                    }
                ],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    wid = resp.json()["id"]
    trig = await tenant_client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {"topic": "BYOK"}, "dry_run": False}
    )
    assert trig.status_code == 202, trig.text
    run_id = str(trig.json()["run_id"])
    deadline = asyncio.get_event_loop().time() + timeout
    run: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"{API}/runs/{run_id}")
        if got.status_code == 200:
            run = got.json()
            if str(run.get("status")) in TERMINAL | {"paused"}:
                steps = await tenant_client.get(f"{API}/runs/{run_id}/steps")
                run["_steps"] = steps.json() if steps.status_code == 200 else steps.text
                return run
        await asyncio.sleep(0.5)
    raise AssertionError(f"workflow run never settled: {run!r}")


async def test_worker_decrypts_the_api_saved_byok_key_for_goals_and_workflows(
    app: Any,
    tenant_client: Any,
    owner_dsn: str,
    fake_llm: _FakeLLM,
    tmp_path: Path,
) -> None:
    from app.providers.vault import get_vault

    tenant_key = f"sk-byok4-{uuid.uuid4().hex}"
    await _save_byok(tenant_client, fake_llm.port, tenant_key)
    tenant_id = await _tenant_id(tenant_client)

    # The API encrypted it (never stored in clear) and recorded its key fingerprint.
    ciphertext, fingerprint = await _stored_row(owner_dsn, tenant_id)
    assert tenant_key not in ciphertext
    assert fingerprint == get_vault().fingerprint()

    fake_llm.auth_headers.clear()
    with _worker(tmp_path, "byok4ok") as handle:
        goal = await _run_goal(tenant_client)
        run = await _run_llm_workflow(tenant_client)
        log = Path(handle.as_dict()["log_path"]).read_text()

    bearer = f"Bearer {tenant_key}"
    assert bearer in fake_llm.auth_headers, (
        f"the tenant key never reached the LLM endpoint; headers={fake_llm.auth_headers!r}\n"
        f"goal={goal!r}\nrun={run!r}\n--- worker log ---\n{log[-4000:]}"
    )
    # Only the tenant's key was ever sent (no platform key, no FakeProvider).
    assert set(fake_llm.auth_headers) == {bearer}
    assert "could not be decrypted" not in json.dumps(goal)
    assert goal.get("status") in TERMINAL
    assert run.get("status") == "complete", f"run={run!r}\n--- worker log ---\n{log[-4000:]}"
    assert "vault_canary_mismatch" not in log and "refuses to start" not in log


async def _goal_error(owner_dsn: str, goal_id: str) -> str:
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        return str(
            await conn.fetchval("SELECT error_message FROM goals WHERE id = $1", goal_id) or ""
        )
    finally:
        await conn.close()


async def test_worker_with_another_vault_key_fails_with_the_fingerprint_mismatch(
    app: Any,
    tenant_client: Any,
    owner_dsn: str,
    fake_llm: _FakeLLM,
    tmp_path: Path,
) -> None:
    from app.providers.vault import CredentialVault, get_vault

    tenant_key = f"sk-byok4-{uuid.uuid4().hex}"
    await _save_byok(tenant_client, fake_llm.port, tenant_key)
    api_fp = get_vault().fingerprint()
    worker_fp = CredentialVault(WRONG_KEY).fingerprint()

    fake_llm.auth_headers.clear()
    # Development: the worker starts (canary mismatch logged) so the run's error
    # can be checked; in production it refuses to start (next test).
    with _worker(tmp_path, "byok4bad", {"VAULT_MASTER_KEY": WRONG_KEY}) as handle:
        goal = await _run_goal(tenant_client)
        run = await _run_llm_workflow(tenant_client)
        log = Path(handle.as_dict()["log_path"]).read_text()

    assert f"Bearer {tenant_key}" not in fake_llm.auth_headers
    # The goal row's error (what the UI / GET shows as the failure reason).
    goal_text = await _goal_error(owner_dsn, str(goal["goal_id"]))
    run_text = json.dumps(run)
    for text in (goal_text, run_text):
        assert "vault key mismatch" in text, f"{text}\n--- worker log ---\n{log[-4000:]}"
        assert api_fp in text and worker_fp in text
        assert WRONG_KEY not in text and tenant_key not in text
    assert goal["status"] == "failed"
    assert run["status"] == "failed"
    assert "vault_canary_mismatch" in log


async def test_production_worker_with_another_vault_key_refuses_to_start(
    app: Any, tmp_path: Path
) -> None:
    from app.providers.vault import CredentialVault, get_vault

    env = _worker_env(
        {"ENVIRONMENT": "production", "VAULT_MASTER_KEY": WRONG_KEY, "SECRET_KEY": "x" * 48}
    )
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.scaling.celery_app",
            "worker",
            "-Q",
            QUEUES,
            "--loglevel=info",
            "--concurrency=1",
            "-n",
            node_name("byok4prod"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out[-4000:]
    assert "refuses to start" in out, out[-4000:]
    assert "vault key mismatch" in out
    assert get_vault().fingerprint() in out and CredentialVault(WRONG_KEY).fingerprint() in out
    assert WRONG_KEY not in out
