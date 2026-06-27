#!/usr/bin/env python3
"""GitHub Actions entrypoint for AgentVerse goal execution."""
import asyncio
import os
import sys
import time

import httpx

API_KEY = os.environ["AGENTVERSE_API_KEY"]
BASE_URL = os.environ.get("AGENTVERSE_BASE_URL", "http://localhost:8000").rstrip("/")
GOAL = os.environ["AGENTVERSE_GOAL"]
TIMEOUT = int(os.environ.get("AGENTVERSE_TIMEOUT", "300"))
FAIL_ON_ERROR = os.environ.get("AGENTVERSE_FAIL_ON_ERROR", "true").lower() == "true"

HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


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

        # Poll until complete
        start = time.time()
        while time.time() - start < TIMEOUT:
            await asyncio.sleep(5)
            resp = await client.get(f"{BASE_URL}/goals/{goal_id}")
            data = resp.json()
            status = data.get("status", "unknown")

            if status == "complete":
                print("::notice::Goal completed successfully")
                with open(github_output, "a") as f:
                    f.write(f"status=complete\n")
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
