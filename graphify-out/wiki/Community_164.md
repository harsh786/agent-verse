# Community 164

> 33 nodes · cohesion 0.09

## Key Concepts

- **PromptCompressor** (15 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **Tokenizer** (13 connections) — `agent-verse-backend/app/agent/tokenizer.py`
- **prompt_compressor.py** (7 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **.compress()** (6 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **tokenizer.py** (6 connections) — `agent-verse-backend/app/agent/tokenizer.py`
- **.compress_messages()** (4 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **.compress_rag_context()** (4 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **._emit_tokens_saved()** (4 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **._truncate_context_blocks()** (4 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **record_prompt_tokens_saved()** (4 connections) — `agent-verse-backend/app/observability/metrics.py`
- **._cap_tool_list()** (3 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **count_tokens** (3 connections) — `agent-verse-backend/app/agent/tokenizer.py`
- **.count()** (3 connections) — `agent-verse-backend/app/agent/tokenizer.py`
- **.truncate_to_tokens()** (3 connections) — `agent-verse-backend/app/agent/tokenizer.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **.stats()** (2 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **Any** (2 connections)
- **.is_accurate()** (2 connections) — `agent-verse-backend/app/agent/tokenizer.py`
- **Prompt Compressor ================= Reduces system prompt token count before…** (1 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **Compress a list of {role, content} message dicts.** (1 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **Truncate only [Relevant context] / [Knowledge base context] / [Visual context]…** (1 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **Truncate [context] / [Relevant context] blocks that exceed max token size.** (1 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **If [Available tools] section has > _MAX_TOOL_LIST_ITEMS, trim it.** (1 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **Stateless heuristic prompt compressor. Usage: compressor = PromptCompressor()…** (1 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- **Return a compressed version of text with ~15-30% fewer tokens.** (1 connections) — `agent-verse-backend/app/agent/prompt_compressor.py`
- *... and 8 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 138](Community_138.md) (2 shared connections)
- [Community 144](Community_144.md) (2 shared connections)
- [Community 203](Community_203.md) (1 shared connections)
- [Community 292](Community_292.md) (1 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/prompt_compressor.py`
- `agent-verse-backend/app/agent/tokenizer.py`
- `agent-verse-backend/app/observability/metrics.py`

## Audit Trail

- EXTRACTED: 49 (86%)
- INFERRED: 7 (12%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*