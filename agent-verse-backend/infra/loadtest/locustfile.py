"""Locust load test for AgentVerse API.

Run headless smoke:
    locust -f infra/loadtest/locustfile.py --headless -u 50 -r 10 -t 1m \
        --host http://localhost:8000

Or with the web UI:
    locust -f infra/loadtest/locustfile.py --host http://localhost:8000
"""
import json
import os

from locust import HttpUser, task, between


API_KEY = os.getenv("API_KEY", "test-key")


class AgentVerseUser(HttpUser):
    wait_time = between(0.1, 0.5)
    host = os.getenv("BASE_URL", "http://localhost:8000")

    def on_start(self) -> None:
        self.client.headers.update({"X-API-Key": API_KEY})

    @task(3)
    def submit_goal(self) -> None:
        """Submit a dry-run goal — tests the hot submission path."""
        self.client.post(
            "/goals",
            json={"goal": "Find open tickets", "priority": "low", "dry_run": True},
            name="/goals [POST]",
        )

    @task(2)
    def list_goals(self) -> None:
        self.client.get("/goals?limit=10", name="/goals [GET]")

    @task(1)
    def get_cost_usage(self) -> None:
        self.client.get("/billing/usage", name="/billing/usage")
