# Community 240

> 26 nodes · cohesion 0.11

## Key Concepts

- **EntityVersionManager** (11 connections) — `agent-verse-backend/app/org/versioning.py`
- **EntityVersion** (8 connections) — `agent-verse-backend/app/org/versioning.py`
- **versioning.py** (6 connections) — `agent-verse-backend/app/org/versioning.py`
- **.create_version()** (5 connections) — `agent-verse-backend/app/org/versioning.py`
- **.select_ab_version()** (5 connections) — `agent-verse-backend/app/org/versioning.py`
- **._config()** (4 connections) — `agent-verse-backend/app/org/versioning.py`
- **.get_ab_variants()** (4 connections) — `agent-verse-backend/app/org/versioning.py`
- **.get_current()** (4 connections) — `agent-verse-backend/app/org/versioning.py`
- **.deploy()** (3 connections) — `agent-verse-backend/app/org/versioning.py`
- **VersioningConfig** (3 connections) — `agent-verse-backend/app/org/versioning.py`
- **.list_versions()** (2 connections) — `agent-verse-backend/app/org/versioning.py`
- **VersionStrategy** (2 connections) — `agent-verse-backend/app/org/versioning.py`
- **.is_stable()** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **.deprecate()** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **Any** (1 connections)
- **StrEnum** (1 connections)
- **SUPPLEMENT L — Versioning Strategy for all entities. Versioned entities: agents…** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **Manages versions for all org entities. In production: backed by DB table with…** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **Create a new version for an entity.** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **Mark a version as deployed.** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **Return the latest deployed non-deprecated version.** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **Return all A/B test variants for an entity.** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **Select A/B variant based on request hash for deterministic routing.** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- **A versioned snapshot of any org entity.** (1 connections) — `agent-verse-backend/app/org/versioning.py`
- *... and 1 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/org/versioning.py`

## Audit Trail

- EXTRACTED: 36 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*