"""a08-F197-01 / a08-F197-02: unused service code stays removed.

* ``app.services.webhook_service`` (``OutboundWebhookService``: in-memory
  deliveries + DLQ, raw httpx with no SSRF guard) and ``app.services.persistence``
  (fire-and-forget writers that "never raise") had no importer in ``app/``.
  Outbound delivery is the workflow callback / notification paths; goals and
  audit events persist through their DB-backed services.
* ``usage_service._usage_service`` was a module singleton nobody used: the app
  binds its own ``UsageService`` on ``app.state`` and workers build a DB-backed
  one per metering call.
"""

from __future__ import annotations

import importlib.util


def test_unused_service_modules_are_gone() -> None:
    assert importlib.util.find_spec("app.services.webhook_service") is None
    assert importlib.util.find_spec("app.services.persistence") is None


def test_no_unused_usage_service_singleton() -> None:
    from app.services import usage_service

    assert not hasattr(usage_service, "_usage_service")
