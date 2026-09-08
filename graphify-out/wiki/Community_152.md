# Community 152

> 35 nodes · cohesion 0.08

## Key Concepts

- **AlertRouter** (12 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **SLOTracker** (7 connections) — `agent-verse-backend/app/observability/slo_tracker.py`
- **.evaluate()** (5 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **.send_alert()** (5 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **alert_router.py** (4 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **._build_payload()** (4 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **FiredAlert** (4 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **metrics.py (Prometheus counters/histograms)** (4 connections) — `agent-verse-backend/app/observability/metrics.py`
- **slo_tracker.py** (4 connections) — `agent-verse-backend/app/observability/slo_tracker.py`
- **.burn_rate()** (4 connections) — `agent-verse-backend/app/observability/slo_tracker.py`
- **.register_rule()** (3 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **AlertRule** (3 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **SLODefinition** (3 connections) — `agent-verse-backend/app/observability/slo_tracker.py`
- **SLOStatus** (3 connections) — `agent-verse-backend/app/observability/slo_tracker.py`
- **.record_event()** (3 connections) — `agent-verse-backend/app/observability/slo_tracker.py`
- **.summary()** (3 connections) — `agent-verse-backend/app/observability/slo_tracker.py`
- **.list_rules()** (2 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **._matches()** (2 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **.remove_rule()** (1 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **Any** (1 connections)
- **Alert Router — threshold-based metric alerting with webhook delivery. Supports…** (1 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **POST the alert as a JSON payload to *webhook_url*. The payload is compatible…** (1 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **Evaluate metric values against registered rules and fire alerts. Thread-safe…** (1 connections) — `agent-verse-backend/app/observability/alert_router.py`
- **Add or replace a rule with the same name.** (1 connections) — `agent-verse-backend/app/observability/alert_router.py`
- *... and 10 more nodes in this community*

## Relationships

- [Community 87](Community_87.md) (1 shared connections)
- [Community 345](Community_345.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/observability/alert_router.py`
- `agent-verse-backend/app/observability/metrics.py`
- `agent-verse-backend/app/observability/slo_tracker.py`
- `agent-verse-backend/app/triggers/metrics.py`

## Audit Trail

- EXTRACTED: 42 (89%)
- INFERRED: 4 (9%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*