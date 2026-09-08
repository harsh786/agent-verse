# Community 129

> 39 nodes · cohesion 0.09

## Key Concepts

- **few_shot_cot.py** (20 connections) — `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- **FewShotCoTRuntime** (12 connections) — `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- **ReasoningExample** (11 connections) — `agent-verse-backend/app/agent/patterns/reasoning_contracts.py`
- **DataClassifier (regex-based PII/PHI/PCI/SECRET classification)** (10 connections) — `agent-verse-backend/app/data_classification/classifier.py`
- **ReasoningExampleSource (Protocol)** (8 connections) — `agent-verse-backend/app/agent/reasoning_example_source.py`
- **.execute()** (7 connections) — `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- **data_classification/classifier.py** (7 connections) — `agent-verse-backend/app/data_classification/classifier.py`
- **DataClass enum + DataClassification (safe_for_prompt gate)** (7 connections) — `agent-verse-backend/app/data_classification/schema.py`
- **.__init__()** (6 connections) — `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- **reasoning_example_source.py** (6 connections) — `agent-verse-backend/app/agent/reasoning_example_source.py`
- **InMemoryReasoningExampleSource** (6 connections) — `agent-verse-backend/app/agent/reasoning_example_source.py`
- **data_classification/schema.py** (6 connections) — `agent-verse-backend/app/data_classification/schema.py`
- **DataClassification** (6 connections) — `agent-verse-backend/app/data_classification/schema.py`
- **.classify_or_safe_fallback()** (4 connections) — `agent-verse-backend/app/data_classification/classifier.py`
- **redaction.py** (4 connections) — `agent-verse-backend/app/data_classification/redaction.py`
- **.create_runtime()** (3 connections) — `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- **._safe_examples()** (3 connections) — `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- **._save()** (3 connections) — `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- **.classify()** (3 connections) — `agent-verse-backend/app/data_classification/classifier.py`
- **Redactor (entity redaction by DataClass)** (3 connections) — `agent-verse-backend/app/data_classification/redaction.py`
- **.highest_sensitivity()** (3 connections) — `agent-verse-backend/app/data_classification/schema.py`
- **Any** (2 connections)
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/reasoning_example_source.py`
- **.retrieve()** (2 connections) — `agent-verse-backend/app/agent/reasoning_example_source.py`
- **.retrieve()** (2 connections) — `agent-verse-backend/app/agent/reasoning_example_source.py`
- *... and 14 more nodes in this community*

## Relationships

- [Community 53](Community_53.md) (10 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (7 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (4 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (3 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (3 shared connections)
- [Runtime Profile & Sandbox](Runtime_Profile_&_Sandbox.md) (3 shared connections)
- [Agent Pattern Base](Agent_Pattern_Base.md) (2 shared connections)
- [Community 78](Community_78.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- `agent-verse-backend/app/agent/patterns/reasoning_contracts.py`
- `agent-verse-backend/app/agent/reasoning_example_source.py`
- `agent-verse-backend/app/data_classification/classifier.py`
- `agent-verse-backend/app/data_classification/redaction.py`
- `agent-verse-backend/app/data_classification/schema.py`
- `agent-verse-backend/app/guardrails_v2/engine.py`
- `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- `agent-verse-backend/app/tenancy/domain_role_templates.py`

## Audit Trail

- EXTRACTED: 87 (89%)
- INFERRED: 11 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*