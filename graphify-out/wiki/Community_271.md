# Community 271

> 24 nodes · cohesion 0.18

## Key Concepts

- **FailureClass enum** (13 connections) — `agent-verse-backend/app/recovery/failure_classifier.py`
- **failure_classifier.py** (8 connections) — `agent-verse-backend/app/recovery/failure_classifier.py`
- **FailureResult** (8 connections) — `agent-verse-backend/app/recovery/failure_classifier.py`
- **RecoveryTrace** (8 connections) — `agent-verse-backend/app/recovery/recovery_trace.py`
- **recovery_policy.py** (7 connections) — `agent-verse-backend/app/recovery/recovery_policy.py`
- **recovery_trace.py** (7 connections) — `agent-verse-backend/app/recovery/recovery_trace.py`
- **escalation_policy.py** (6 connections) — `agent-verse-backend/app/recovery/escalation_policy.py`
- **RecoveryPolicy** (6 connections) — `agent-verse-backend/app/recovery/recovery_policy.py`
- **RecoveryAction** (5 connections) — `agent-verse-backend/app/recovery/recovery_policy.py`
- **EscalationPolicy** (4 connections) — `agent-verse-backend/app/recovery/escalation_policy.py`
- **RecoveryTraceEntry** (4 connections) — `agent-verse-backend/app/recovery/recovery_trace.py`
- **retry_strategy_selector.py** (4 connections) — `agent-verse-backend/app/recovery/retry_strategy_selector.py`
- **RetryStrategySelector** (4 connections) — `agent-verse-backend/app/recovery/retry_strategy_selector.py`
- **.decide()** (3 connections) — `agent-verse-backend/app/recovery/escalation_policy.py`
- **FailureClassifier** (3 connections) — `agent-verse-backend/app/recovery/failure_classifier.py`
- **.select()** (3 connections) — `agent-verse-backend/app/recovery/recovery_policy.py`
- **RetryStrategy** (3 connections) — `agent-verse-backend/app/recovery/retry_strategy_selector.py`
- **.select()** (3 connections) — `agent-verse-backend/app/recovery/retry_strategy_selector.py`
- **EscalationDecision** (2 connections) — `agent-verse-backend/app/recovery/escalation_policy.py`
- **.classify()** (2 connections) — `agent-verse-backend/app/recovery/failure_classifier.py`
- **EscalationPolicy — determines when to escalate a failed goal to human.** (1 connections) — `agent-verse-backend/app/recovery/escalation_policy.py`
- **RecoveryTrace — observability trace for recovery decisions.** (1 connections) — `agent-verse-backend/app/recovery/recovery_trace.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/recovery/recovery_trace.py`
- **FailureClass** (1 connections)

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Community 1017](Community_1017.md) (2 shared connections)
- [Community 492](Community_492.md) (1 shared connections)
- [Community 746](Community_746.md) (1 shared connections)
- [Community 1051](Community_1051.md) (1 shared connections)
- [Community 587](Community_587.md) (1 shared connections)
- [Community 304](Community_304.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/recovery/escalation_policy.py`
- `agent-verse-backend/app/recovery/failure_classifier.py`
- `agent-verse-backend/app/recovery/recovery_policy.py`
- `agent-verse-backend/app/recovery/recovery_trace.py`
- `agent-verse-backend/app/recovery/retry_strategy_selector.py`

## Audit Trail

- EXTRACTED: 47 (81%)
- INFERRED: 10 (17%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*