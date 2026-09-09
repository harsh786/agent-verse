"""Locust load test for AgentVerse API.

Run headless smoke:
    locust -f infra/loadtest/locustfile.py --headless -u 50 -r 10 -t 1m \
        --host http://localhost:8000

Or with the web UI:
    locust -f infra/loadtest/locustfile.py --host http://localhost:8000
"""
import os

from locust import HttpUser, between, task

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


class ComplianceUser(HttpUser):
    """Load test for compliance endpoints (DPDP, GST, policy-rules)."""
    wait_time = between(0.5, 2.0)
    host = os.getenv("BASE_URL", "http://localhost:8000")

    def on_start(self) -> None:
        self.client.headers.update({"X-API-Key": API_KEY, "Content-Type": "application/json"})

    @task(3)
    def get_policy_rules(self) -> None:
        """List tenant policy rules — read-heavy path."""
        self.client.get("/governance/policy-rules", name="/governance/policy-rules [GET]")

    @task(2)
    def evaluate_policy_rules(self) -> None:
        """Dry-run policy evaluation — used before every tool call."""
        self.client.post(
            "/governance/policy-rules/evaluate",
            json={"tool_name": "send_email", "arguments": {"to": "test@company.com"}},
            name="/governance/policy-rules/evaluate [POST]",
        )

    @task(1)
    def get_dpdp_consents(self) -> None:
        """List DPDP consents for a data principal."""
        self.client.get("/compliance/dpdp/consent/test-customer-001",
                        name="/compliance/dpdp/consent [GET]")

    @task(1)
    def get_gst_invoices(self) -> None:
        """List tenant GST invoices."""
        self.client.get("/billing/gst/invoices", name="/billing/gst/invoices [GET]")

    @task(1)
    def get_sla_plan(self) -> None:
        """Get SLA for current plan — called on every settings page load."""
        self.client.get("/sla/my-plan", name="/sla/my-plan [GET]")

    @task(1)
    def get_public_status(self) -> None:
        """Public status page — no auth needed."""
        self.client.get("/status", name="/status [GET]", headers={})  # no auth


class AuthenticatedLoadUser(HttpUser):
    """Combined load test covering goal + compliance endpoints."""
    wait_time = between(0.2, 1.0)
    host = os.getenv("BASE_URL", "http://localhost:8000")

    def on_start(self) -> None:
        self.client.headers.update({"X-API-Key": API_KEY, "Content-Type": "application/json"})

    @task(5)
    def submit_goal(self) -> None:
        self.client.post("/goals",
            json={"goal": "Find open tickets", "priority": "low", "dry_run": True},
            name="/goals [POST]")

    @task(3)
    def list_goals(self) -> None:
        self.client.get("/goals?limit=10", name="/goals [GET]")

    @task(2)
    def policy_evaluate(self) -> None:
        self.client.post("/governance/policy-rules/evaluate",
            json={"tool_name": "jira_search", "arguments": {}},
            name="/policy-rules/evaluate [POST]")

    @task(1)
    def gst_invoices(self) -> None:
        self.client.get("/billing/gst/invoices", name="/gst/invoices [GET]")

    @task(1)
    def public_status(self) -> None:
        self.client.get("/status", name="/status [GET]", headers={})
