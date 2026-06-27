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
                        elif etype in ("goal_failed", "goal_error"):
                            return {
                                "status": "failed",
                                "goal_id": goal_id,
                                "error": evt.get("reason"),
                            }
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass

    return None


async def main() -> None:
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        # Submit goal
        resp = await client.post(f"{BASE_URL}/goals", json={"goal": GOAL})
        resp.raise_for_status()
        goal_id = resp.json()["goal_id"]
        print(f"::notice::Goal submitted: {goal_id}")

        # Set output
        github_output = os.environ.get("GITHUB_OUTPUT", "/dev/null")
        with open(github_output, "a") as f:
            f.write(f"goal-id={goal_id}\n")

        # Try SSE-based waiting first (efficient)
        sse_result = wait_for_completion_sse(goal_id)
        if sse_result is not None:
            status = sse_result.get("status", "unknown")
            if status == "complete":
                print("::notice::Goal completed successfully (SSE)")
                # Fetch full result for output
                resp = await client.get(f"{BASE_URL}/goals/{goal_id}")
                data = resp.json() if resp.is_success else {}
                with open(github_output, "a") as f:
                    f.write("status=complete\n")
                    result = (data.get("result") or "")[:2000]
                    f.write(f"result={result}\n")
                    f.write(f"cost-usd={data.get('cost_usd', 0.0)}\n")
                return
            if status in {"failed", "cancelled"}:
                print(f"::error::Goal {status}: {goal_id}")
                with open(github_output, "a") as f:
                    f.write(f"status={status}\n")
                if FAIL_ON_ERROR:
                    sys.exit(1)
                return

        # Fallback: poll for status
        start = time.time()
        while time.time() - start < TIMEOUT:
            await asyncio.sleep(5)
            resp = await client.get(f"{BASE_URL}/goals/{goal_id}")
            data = resp.json()
            status = data.get("status", "unknown")

            if status == "complete":
                print("::notice::Goal completed successfully")
                with open(github_output, "a") as f:
                    f.write("status=complete\n")
                    result = (data.get("result") or "")[:2000]
                    f.write(f"result={result}\n")
                    f.write(f"cost-usd={data.get('cost_usd', 0.0)}\n")
                return

            if status in {"failed", "cancelled"}:
                print(f"::error::Goal {status}: {goal_id}")
                with open(github_output, "a") as f:
                    f.write(f"status={status}\n")
                if FAIL_ON_ERROR:
                    sys.exit(1)
                return

        print(f"::error::Goal timed out after {TIMEOUT}s")
        with open(github_output, "a") as f:
            f.write("status=timeout\n")
        if FAIL_ON_ERROR:
            sys.exit(1)


asyncio.run(main())
