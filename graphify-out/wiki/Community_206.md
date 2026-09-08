# Community 206

> 29 nodes · cohesion 0.09

## Key Concepts

- **feature_flags.py** (11 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **FeatureFlagService** (9 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **_run_org_intelligence_cron()** (7 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **_run_org_digest_cron()** (6 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **_run_org_twin_sync()** (6 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **is_feature_enabled()** (5 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **org_digest_cron()** (5 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **org_intelligence_cron()** (5 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **org_twin_sync()** (4 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **.enable()** (3 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **.enable_phase()** (3 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **get_feature_flags()** (3 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **Any** (3 connections)
- **.disable()** (2 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **.is_enabled()** (2 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **.get_all()** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **PART 44 — Feature Flag Infrastructure. PART 43 — Org Celery Cron Tasks. Feature…** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **org-intelligence-cron: Every 15 minutes. Detects bottlenecks, generates…** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **org-digest-cron: Daily at 06:00 UTC. Generates "While You Were Away" digests…** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **org-twin-sync: Event-driven, triggered on org events. Updates the digital twin…** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **Simple feature flag service backed by in-memory dict. In production: backed by…** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **Check if a feature flag is enabled.** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **Enable a feature flag.** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- **Disable a feature flag.** (1 connections) — `agent-verse-backend/app/org/feature_flags.py`
- *... and 4 more nodes in this community*

## Relationships

- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (10 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (3 shared connections)
- [Community 291](Community_291.md) (2 shared connections)
- [Community 359](Community_359.md) (2 shared connections)
- [Community 74](Community_74.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/org/feature_flags.py`
- `agent-verse-backend/app/scaling/tasks.py`

## Audit Trail

- EXTRACTED: 53 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*