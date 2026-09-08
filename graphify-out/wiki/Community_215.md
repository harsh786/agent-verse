# Community 215

> 28 nodes · cohesion 0.11

## Key Concepts

- **ConsensusVerifier (3-way verification)** (16 connections) — `agent-verse-backend/app/agent/consensus.py`
- **agent/consensus.py** (12 connections) — `agent-verse-backend/app/agent/consensus.py`
- **DebateOrchestrator (propose-critique-vote)** (12 connections) — `agent-verse-backend/app/agent/debate.py`
- **_run_judge (LLM judge)** (10 connections) — `agent-verse-backend/app/agent/consensus.py`
- **agent/debate.py** (8 connections) — `agent-verse-backend/app/agent/debate.py`
- **.run()** (7 connections) — `agent-verse-backend/app/agent/debate.py`
- **ConsensusResult** (6 connections) — `agent-verse-backend/app/agent/consensus.py`
- **.emit()** (6 connections) — `agent-verse-backend/app/api/observability.py`
- **.verify()** (4 connections) — `agent-verse-backend/app/agent/consensus.py`
- **VerifierVote** (4 connections) — `agent-verse-backend/app/agent/consensus.py`
- **DebateResult** (4 connections) — `agent-verse-backend/app/agent/debate.py`
- **VERIFIER_SYSTEM prompt** (4 connections) — `agent-verse-backend/app/agent/prompts.py`
- **Any** (3 connections)
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/consensus.py`
- **AgentProposal** (2 connections) — `agent-verse-backend/app/agent/debate.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/debate.py`
- **Any** (2 connections)
- **JUDGE_RUBRIC_SYSTEM prompt** (2 connections) — `agent-verse-backend/app/agent/prompts.py`
- **.__post_init__()** (1 connections) — `agent-verse-backend/app/agent/consensus.py`
- **3-Way Consensus Verification for high-stakes goals. For goals touching…** (1 connections) — `agent-verse-backend/app/agent/consensus.py`
- **Runs up to 3-way verification and returns a ConsensusResult. Falls back…** (1 connections) — `agent-verse-backend/app/agent/consensus.py`
- **Run all configured verifiers and compute majority verdict.** (1 connections) — `agent-verse-backend/app/agent/consensus.py`
- **Run the LLM judge with a rubric-scored prompt.** (1 connections) — `agent-verse-backend/app/agent/consensus.py`
- **.__post_init__()** (1 connections) — `agent-verse-backend/app/agent/consensus.py`
- **Debate/voting pattern — N agents independently propose solutions, critique each…** (1 connections) — `agent-verse-backend/app/agent/debate.py`
- *... and 3 more nodes in this community*

## Relationships

- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (6 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Community 169](Community_169.md) (3 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (3 shared connections)
- [Community 105](Community_105.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 170](Community_170.md) (2 shared connections)
- [Community 67](Community_67.md) (2 shared connections)
- [Community 51](Community_51.md) (2 shared connections)
- [Community 246](Community_246.md) (2 shared connections)
- [Community 111](Community_111.md) (1 shared connections)
- [Community 277](Community_277.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/consensus.py`
- `agent-verse-backend/app/agent/debate.py`
- `agent-verse-backend/app/agent/prompts.py`
- `agent-verse-backend/app/api/observability.py`

## Audit Trail

- EXTRACTED: 59 (78%)
- INFERRED: 17 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*