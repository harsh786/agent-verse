#!/usr/bin/env python3
"""GitHub Actions entrypoint for AgentVerse goal execution."""
import asyncio
import json
import os
import sys
import time
import urllib.request

import httpx

API_KEY = os.environ["AGENTVERSE_API_KEY"]
BASE_URL = os.environ.get("AGENTVERSE_BASE_URL", "http://localhost:8000").rstrip("/")
GOAL = os.environ["AGENTVERSE_GOAL"]
TIMEOUT = int(os.environ.get("AGENTVERSE_TIMEOUT", "300"))
FAIL_ON_ERROR = os.environ.get("AGENTVERSE_FAIL_ON_ERROR", "true").lower() == "true"
# A goal paused for human approval is neither done nor failed: by default the
# step reports status=waiting_human with a warning; set this to fail the step.
FAIL_ON_WAITING_HUMAN = (
    os.environ.get("AGENTVERSE_FAIL_ON_WAITING_HUMAN", "false").lower() == "true"
)
#: Consecutive retryable (5xx/429/network) polling errors before giving up.
MAX_POLL_ERRORS = 3

HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def wait_for_completion_sse(goal_id: str) -> dict | None:
    """Wait for goal completion using SSE (efficient) with polling fallback.

    Returns a result dict on terminal event, or None if SSE is unavailable.
    """
    start = time.time()
    url = f"{BASE_URL}/goals/{goal_id}/stream"

    try:
        req = urllib.request.Request(
            url,
            headers={"X-API-Key": API_KEY, "Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            for line in resp:
                if time.time() - start > TIMEOUT:
                    break
                line = line.decode("utf-8").strip()
                if line.startswith("data: "):
                    try:
                        evt = json.loads(line[6:])
                        etype = evt.get("type", "")
                        if etype in ("goal_complete", "goal_finished"):
                            return {"status": "complete", "goal_id": goal_id}
                        elif etype in ("goal_failed", "goal_error", "goal_rejected"):
                            return {
                                "status": "failed",
                                "goal_id": goal_id,
                                "error": evt.get("reason"),
                            }
                        elif etype == "goal_cancelled":
                            return {"status": "cancelled", "goal_id": goal_id}
                        elif etype == "goal_waiting_human":
                            return {
                                "status": "waiting_human",
                                "goal_id": goal_id,
                                "error": evt.get("reason"),
                            }
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        # Not fatal (we fall back to polling), but never silent.
        print(f"::warning::SSE wait unavailable, polling instead: {exc}")

    return None


def _write_output(name: str, value: str) -> None:
    """Append one GitHub Actions output; multi-line values use a delimiter
    (a raw newline in ``name=value`` corrupted every later output)."""
    github_output = os.environ.get("GITHUB_OUTPUT", "/dev/null")
    with open(github_output, "a") as f:
        if "\n" in value:
            delim = f"AGENTVERSE_EOF_{os.urandom(8).hex()}"
            f.write(f"{name}<<{delim}\n{value}\n{delim}\n")
        else:
            f.write(f"{name}={value}\n")


async def _write_completion_outputs(client: httpx.AsyncClient, goal_id: str, data: dict) -> None:
    """The goal's answer and cost. GET /goals/{id} has no ``result``/``cost_usd``
    fields (the action always reported an empty result and 0.0): the answer is
    ``result_artifact.summary`` and the cost comes from the cost-metrics API."""
    artifact = data.get("result_artifact") or {}
    result = str(data.get("result") or artifact.get("summary") or "")[:2000]
    cost = 0.0
    try:
        resp = await client.get(f"{BASE_URL}/goals/{goal_id}/cost-metrics")
        if resp.is_success:
            cost = float(resp.json().get("total_cost_usd") or 0.0)
        else:
            print(f"::warning::cost metrics unavailable: HTTP {resp.status_code}")
    except Exception as exc:
        print(f"::warning::cost metrics unavailable: {exc}")
    _write_output("status", "complete")
    _write_output("result", result)
    _write_output("cost-usd", f"{cost}")


def _finish(status: str, *, fail: bool) -> None:
    """Record the final status and fail the step when asked to."""
    _write_output("status", status)
    if fail:
        sys.exit(1)


def _report_waiting_human(goal_id: str) -> None:
    print(
        f"::warning::Goal {goal_id} is waiting for human approval. Approve or reject it "
        f"in AgentVerse (pending approvals: {BASE_URL}/governance/approvals)."
    )
    _finish("waiting_human", fail=FAIL_ON_WAITING_HUMAN)


def _http_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("error") or body
    except ValueError:
        detail = resp.text
    return str(detail)[:300]


async def main() -> None:
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        # Submit goal
        resp = await client.post(f"{BASE_URL}/goals", json={"goal": GOAL})
        resp.raise_for_status()
        goal_id = resp.json()["goal_id"]
        print(f"::notice::Goal submitted: {goal_id}")
        _write_output("goal-id", goal_id)

        # Try SSE-based waiting first (efficient)
        sse_result = wait_for_completion_sse(goal_id)
        if sse_result is not None:
            status = sse_result.get("status", "unknown")
            if status == "complete":
                print("::notice::Goal completed successfully (SSE)")
                # Fetch full result for output
                resp = await client.get(f"{BASE_URL}/goals/{goal_id}")
                data = resp.json() if resp.is_success else {}
                await _write_completion_outputs(client, goal_id, data)
                return
            if status in {"failed", "cancelled"}:
                print(f"::error::Goal {status}: {goal_id}")
                _finish(status, fail=FAIL_ON_ERROR)
                return
            if status == "waiting_human":
                _report_waiting_human(goal_id)
                return

        # Fallback: poll for status
        start = time.time()
        errors = 0
        while time.time() - start < TIMEOUT:
            await asyncio.sleep(5)
            try:
                resp = await client.get(f"{BASE_URL}/goals/{goal_id}")
            except httpx.TransportError as exc:
                errors += 1
                if errors >= MAX_POLL_ERRORS:
                    print(f"::error::Could not reach AgentVerse while polling {goal_id}: {exc}")
                    _finish("error", fail=FAIL_ON_ERROR)
                    return
                continue
            if resp.status_code >= 500 or resp.status_code == 429:
                errors += 1
                if errors >= MAX_POLL_ERRORS:
                    print(
                        f"::error::AgentVerse returned HTTP {resp.status_code} polling goal "
                        f"{goal_id} ({MAX_POLL_ERRORS} attempts): {_http_detail(resp)}"
                    )
                    _finish("error", fail=FAIL_ON_ERROR)
                    return
                continue
            if not resp.is_success:
                # 401/403/404: retrying cannot help — fail now with the reason.
                print(
                    f"::error::AgentVerse returned HTTP {resp.status_code} polling goal "
                    f"{goal_id}: {_http_detail(resp)}"
                )
                _finish("error", fail=FAIL_ON_ERROR)
                return
            errors = 0
            data = resp.json()
            status = data.get("status", "unknown")

            if status == "complete":
                print("::notice::Goal completed successfully")
                await _write_completion_outputs(client, goal_id, data)
                return

            if status in {"failed", "cancelled"}:
                print(f"::error::Goal {status}: {goal_id}")
                _finish(status, fail=FAIL_ON_ERROR)
                return

            if status == "waiting_human":
                _report_waiting_human(goal_id)
                return

        print(f"::error::Goal timed out after {TIMEOUT}s")
        _finish("timeout", fail=FAIL_ON_ERROR)


if __name__ == "__main__":
    asyncio.run(main())
