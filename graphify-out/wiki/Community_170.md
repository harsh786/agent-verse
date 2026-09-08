# Community 170

> 32 nodes · cohesion 0.09

## Key Concepts

- **GroundingChecker (two-pass)** (15 connections) — `agent-verse-backend/app/agent/grounding.py`
- **grounding.py** (13 connections) — `agent-verse-backend/app/agent/grounding.py`
- **check_grounding** (8 connections) — `agent-verse-backend/app/agent/grounding.py`
- **exfil_guard.py** (7 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **annotate_ungrounded** (7 connections) — `agent-verse-backend/app/agent/grounding.py`
- **.check()** (7 connections) — `agent-verse-backend/app/agent/grounding.py`
- **GroundingResult** (6 connections) — `agent-verse-backend/app/agent/grounding.py`
- **check_tool_output_for_injection** (5 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **._sync_check()** (5 connections) — `agent-verse-backend/app/agent/grounding.py`
- **wrap_tool_output_as_untrusted** (4 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **Claim** (4 connections) — `agent-verse-backend/app/agent/grounding.py`
- **extract_claims** (4 connections) — `agent-verse-backend/app/agent/grounding.py`
- **extract_claims_structured** (4 connections) — `agent-verse-backend/app/agent/grounding.py`
- **.check_async()** (4 connections) — `agent-verse-backend/app/agent/grounding.py`
- **deterministic_ground** (3 connections) — `agent-verse-backend/app/agent/grounding.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/grounding.py`
- **Data Exfiltration Guard ======================= Detects when an agent is about…** (1 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **Scan tool output for indirect prompt injection attempts. Returns a warning…** (1 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **Wrap tool output in untrusted-content delimiters. This prevents the LLM from…** (1 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **Any** (1 connections)
- **Claim Grounding Checker ======================= After each executor step,…** (1 connections) — `agent-verse-backend/app/agent/grounding.py`
- **Annotate step output with [UNGROUNDED CLAIM] markers for the verifier.** (1 connections) — `agent-verse-backend/app/agent/grounding.py`
- **A concrete claim extracted from step output.** (1 connections) — `agent-verse-backend/app/agent/grounding.py`
- **Extract concrete claims as Claim dataclasses. Wraps the existing…** (1 connections) — `agent-verse-backend/app/agent/grounding.py`
- **Check which claims appear in *tool_output* by case-insensitive substring.…** (1 connections) — `agent-verse-backend/app/agent/grounding.py`
- *... and 7 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (6 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (4 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (3 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (3 shared connections)
- [Community 171](Community_171.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 215](Community_215.md) (2 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/exfil_guard.py`
- `agent-verse-backend/app/agent/grounding.py`
- `agent-verse-backend/app/agent/prompts.py`

## Audit Trail

- EXTRACTED: 62 (90%)
- INFERRED: 7 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*