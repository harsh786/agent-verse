# Self-Refine & Model Routing

> 212 nodes · cohesion 0.02

## Key Concepts

- **CompletionRequest** (187 connections) — `agent-verse-backend/app/providers/base.py`
- **Message** (162 connections) — `agent-verse-backend/app/providers/base.py`
- **LLMProvider (Protocol)** (58 connections) — `agent-verse-backend/app/providers/base.py`
- **app/providers/base.py** (42 connections) — `agent-verse-backend/app/providers/base.py`
- **EmbedRequest** (33 connections) — `agent-verse-backend/app/providers/base.py`
- **CompletionResponse** (23 connections) — `agent-verse-backend/app/providers/base.py`
- **VoyageProvider** (20 connections) — `agent-verse-backend/app/providers/voyage_provider.py`
- **EmbedResponse** (19 connections) — `agent-verse-backend/app/providers/base.py`
- **GeminiProvider** (17 connections) — `agent-verse-backend/app/providers/gemini_provider.py`
- **app/providers/__init__.py** (16 connections) — `agent-verse-backend/app/providers/__init__.py`
- **openai_compatible.py** (15 connections) — `agent-verse-backend/app/providers/openai_compatible.py`
- **meta_agent.py** (14 connections) — `agent-verse-backend/app/intelligence/meta_agent.py`
- **anthropic_provider.py** (13 connections) — `agent-verse-backend/app/providers/anthropic_provider.py`
- **LocalEmbedProvider** (11 connections) — `agent-verse-backend/app/providers/voyage_provider.py`
- **ShadowRouter** (10 connections) — `agent-verse-backend/app/ai_router/shadow_router.py`
- **multi_turn_eval.py** (10 connections) — `agent-verse-backend/app/evals/multi_turn_eval.py`
- **NLIChecker** (10 connections) — `agent-verse-backend/app/intelligence/nli_checker.py`
- **browser_agent.py** (10 connections) — `agent-verse-backend/app/perception/browser_agent.py`
- **fake.py** (10 connections) — `agent-verse-backend/app/providers/fake.py`
- **.evaluate()** (9 connections) — `agent-verse-backend/app/evals/multi_turn_eval.py`
- **vision_parser.py** (9 connections) — `agent-verse-backend/app/ingestion/parsers/vision_parser.py`
- **claim_decomposer.py** (9 connections) — `agent-verse-backend/app/intelligence/claim_decomposer.py`
- **ClaimDecomposer** (9 connections) — `agent-verse-backend/app/intelligence/claim_decomposer.py`
- **MetaAgentPlanner** (9 connections) — `agent-verse-backend/app/intelligence/meta_agent.py`
- **ollama_provider.py** (9 connections) — `agent-verse-backend/app/providers/ollama_provider.py`
- *... and 187 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (35 shared connections)
- [Federated RAG Search](Federated_RAG_Search.md) (26 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (18 shared connections)
- [Community 55](Community_55.md) (15 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (11 shared connections)
- [Community 366](Community_366.md) (10 shared connections)
- [Community 151](Community_151.md) (10 shared connections)
- [Adaptive RAG Pattern](Adaptive_RAG_Pattern.md) (10 shared connections)
- [Community 111](Community_111.md) (9 shared connections)
- [Community 102](Community_102.md) (9 shared connections)
- [Community 341](Community_341.md) (8 shared connections)
- [Community 61](Community_61.md) (8 shared connections)

## Source Files

- `agent-verse-backend/app/agent/nodes/reasoning_mixin.py`
- `agent-verse-backend/app/agent/patterns/self_refine.py`
- `agent-verse-backend/app/ai_router/shadow_router.py`
- `agent-verse-backend/app/evals/multi_turn_eval.py`
- `agent-verse-backend/app/guardrails_v2/toxicity.py`
- `agent-verse-backend/app/ingestion/parsers/vision_parser.py`
- `agent-verse-backend/app/intelligence/claim_decomposer.py`
- `agent-verse-backend/app/intelligence/eval_runner.py`
- `agent-verse-backend/app/intelligence/guardrail_engine.py`
- `agent-verse-backend/app/intelligence/meta_agent.py`
- `agent-verse-backend/app/intelligence/nli_checker.py`
- `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- `agent-verse-backend/app/mcp/tool_intelligence.py`
- `agent-verse-backend/app/org/advanced_services.py`
- `agent-verse-backend/app/perception/browser_agent.py`
- `agent-verse-backend/app/providers/__init__.py`
- `agent-verse-backend/app/providers/anthropic_provider.py`
- `agent-verse-backend/app/providers/base.py`
- `agent-verse-backend/app/providers/fake.py`
- `agent-verse-backend/app/providers/gemini_provider.py`

## Audit Trail

- EXTRACTED: 835 (99%)
- INFERRED: 8 (1%)
- AMBIGUOUS: 1 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*